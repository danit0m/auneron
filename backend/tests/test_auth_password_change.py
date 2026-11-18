"""
B1: troca obrigatoria de senha. Login com temporaria vencida recusado;
must_change_password bloqueia toda rota autenticada (403
password_change_required) exceto /auth/me, /auth/logout e
/auth/change-password; change-password valida, grava e revoga as outras
sessoes.
"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.models.auth_session import AuthSession
from app.models.user import User

EMAIL = "troca.senha@example.com"
TEMP = "Temporaria-Auneron-123!"
NEW = "Nova-Senha-Auneron-456!"


def _user(db: Session, *, must_change=True, expires_in_minutes=30, role="administrator") -> User:
    user = User(
        name="Usuario Troca",
        email=EMAIL,
        password_hash=hash_password(TEMP),
        role=role,
        active=True,
        must_change_password=must_change,
        temp_password_expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes) if must_change else None
        ),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, password: str = TEMP):
    return client.post("/auth/login", json={"email": EMAIL, "password": password})


def _change(client: TestClient, current: str = TEMP, new: str = NEW):
    return client.post("/auth/change-password", json={"current_password": current, "new_password": new})


def test_login_with_expired_temp_password_is_refused_with_clear_message(service_client, db_session):
    _user(db_session, expires_in_minutes=-1)

    response = _login(service_client)

    assert response.status_code == 401
    assert "venceu" in response.json()["detail"]
    assert db_session.query(AuthSession).count() == 0


def test_login_with_valid_temp_password_works_and_me_exposes_the_flag(service_client, db_session):
    _user(db_session)

    login = _login(service_client)
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    me = service_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["must_change_password"] is True


def test_me_reports_false_for_regular_user(service_client, db_session):
    _user(db_session, must_change=False)
    assert _login(service_client).status_code == 200
    assert service_client.get("/auth/me").json()["user"]["must_change_password"] is False


def test_must_change_blocks_authenticated_routes_with_specific_code(service_client, db_session):
    _user(db_session)
    _login(service_client)

    for response in (
        service_client.get("/accounts"),
        service_client.post("/auth/elevate", json={"password": TEMP}),
        service_client.post("/auth/elevation/revoke"),
    ):
        assert response.status_code == 403, response.request.url
        assert response.json()["detail"]["code"] == "password_change_required"


def test_exceptions_me_and_change_password_stay_reachable(service_client, db_session):
    _user(db_session)
    _login(service_client)

    assert service_client.get("/auth/me").status_code == 200
    assert _change(service_client).status_code == 204


def test_logout_is_reachable_while_change_is_pending(service_client, db_session):
    _user(db_session)
    _login(service_client)
    assert service_client.post("/auth/logout").status_code == 204
    assert service_client.get("/auth/me").status_code == 401


def test_session_with_temp_password_expired_midway_is_401_everywhere(service_client, db_session):
    user = _user(db_session)
    _login(service_client)
    user.temp_password_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    assert service_client.get("/auth/me").status_code == 401
    assert _change(service_client).status_code == 401


def test_change_password_happy_path_unblocks_and_updates_fields(service_client, db_session):
    user = _user(db_session)
    _login(service_client)

    assert _change(service_client).status_code == 204

    db_session.expire_all()
    user = db_session.get(User, user.id)
    assert user.must_change_password is False
    assert user.temp_password_expires_at is None
    assert user.password_changed_at is not None
    assert service_client.get("/accounts").status_code == 200
    assert service_client.get("/auth/me").json()["user"]["must_change_password"] is False

    service_client.post("/auth/logout")
    assert _login(service_client, TEMP).status_code == 401
    assert _login(service_client, NEW).status_code == 200


def test_change_password_revokes_the_other_sessions_only(service_client, db_session):
    user = _user(db_session)
    with TestClient(service_client.app) as other:
        other.headers.update(service_client.headers)
        assert _login(other).status_code == 200
        assert _login(service_client).status_code == 200

        assert _change(service_client).status_code == 204

        assert service_client.get("/auth/me").status_code == 200
        assert other.get("/auth/me").status_code == 401
    db_session.expire_all()
    active = (
        db_session.query(AuthSession)
        .filter(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .count()
    )
    assert active == 1


def test_change_password_wrong_current_is_401_and_changes_nothing(service_client, db_session):
    user = _user(db_session)
    _login(service_client)

    response = _change(service_client, current="Errada-Auneron-000!")

    assert response.status_code == 401
    db_session.expire_all()
    assert db_session.get(User, user.id).must_change_password is True


def test_change_password_rejects_short_and_equal_new_password(service_client, db_session):
    _user(db_session)
    _login(service_client)

    assert _change(service_client, new="curta123").status_code == 422
    assert _change(service_client, new=TEMP).status_code == 422
    assert service_client.get("/auth/me").json()["user"]["must_change_password"] is True


def test_change_password_requires_authentication(service_client):
    assert _change(service_client).status_code == 401


def test_change_password_rate_limits_repeated_wrong_current_password(service_client, db_session):
    _user(db_session)
    _login(service_client)

    statuses = [_change(service_client, current=f"Errada-Auneron-{i}!!").status_code for i in range(30)]

    assert 429 in statuses
    assert set(statuses) <= {401, 429}
