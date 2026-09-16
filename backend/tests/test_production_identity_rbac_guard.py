"""
PR-2 -- Production Identity / RBAC Guard. Testes obrigatorios do
Executable Contract (congelado com Tomaz em 16/09/2026): developer
continua integralmente funcional em development/test; em production,
os tres pontos que ja espelham o padrao existente de role=system
(authenticate_user, create_session, require_user_session) bloqueiam o
papel; create_user.py rejeita antes de qualquer prompt/escrita; o
startup detecta e reporta sem jamais mutar/apagar/reatribuir; nenhum
outro papel e afetado.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import authentication
from app.core.authentication import ProductionRoleNotAllowedError
from app.core.authentication import authenticate_user
from app.core.authentication import check_production_developer_roles
from app.core.authentication import create_session
from app.core.authentication import hash_password
from app.core.authentication import hash_session_token
from app.core.authentication import utc_now
from app.models.auth_session import AuthSession
from app.models.user import User


TEST_PASSWORD = "Senha-Forte-Auneron-123!"


def _make_user(
    db_session: Session,
    *,
    email: str,
    role: str,
    active: bool = True,
) -> User:
    user = User(
        name="Usuário PR-2 Teste",
        email=email.lower(),
        password_hash=hash_password(TEST_PASSWORD),
        role=role,
        active=active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_raw_session(
    db_session: Session,
    user: User,
) -> str:
    """
    Cria uma AuthSession diretamente via ORM, contornando
    create_session() -- simula uma sessão já existente antes de o
    ambiente virar produção (restore/promoção de banco), exatamente o
    cenário do Q1 do Freeze.
    """

    from secrets import token_urlsafe

    raw_token = token_urlsafe(32)
    db_session.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(raw_token),
            expires_at=utc_now() + timedelta(hours=1),
        )
    )
    db_session.commit()
    return raw_token


# ---------------------------------------------------------------------
# authenticate_user -- ponto primário, mesma UX de role=system
# ---------------------------------------------------------------------


def test_authenticate_user_rejects_developer_in_production(
    monkeypatch,
    db_session: Session,
) -> None:
    user = _make_user(
        db_session,
        email="developer.prod.auth@example.com",
        role="developer",
    )
    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )

    result = authenticate_user(
        db_session,
        email=user.email,
        password=TEST_PASSWORD,
    )

    assert result is None


def test_authenticate_user_accepts_developer_outside_production(
    db_session: Session,
) -> None:
    user = _make_user(
        db_session,
        email="developer.dev.auth@example.com",
        role="developer",
    )

    result = authenticate_user(
        db_session,
        email=user.email,
        password=TEST_PASSWORD,
    )

    assert result is not None
    assert result.id == user.id


# ---------------------------------------------------------------------
# create_session -- defense-in-depth, mesmo padrão de role=system
# ---------------------------------------------------------------------


def test_create_session_raises_for_developer_in_production(
    monkeypatch,
    db_session: Session,
) -> None:
    user = _make_user(
        db_session,
        email="developer.prod.session@example.com",
        role="developer",
    )
    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )

    before = db_session.execute(
        text("SELECT COUNT(*) FROM auth_sessions")
    ).scalar_one()

    try:
        create_session(db_session, user)
        raised = False
    except ProductionRoleNotAllowedError:
        raised = True

    after = db_session.execute(
        text("SELECT COUNT(*) FROM auth_sessions")
    ).scalar_one()

    assert raised is True
    assert before == after


def test_create_session_succeeds_for_developer_outside_production(
    db_session: Session,
) -> None:
    user = _make_user(
        db_session,
        email="developer.dev.session@example.com",
        role="developer",
    )

    raw_token, auth_session = create_session(db_session, user)

    assert raw_token
    assert auth_session.user_id == user.id


# ---------------------------------------------------------------------
# require_user_session -- prova do Q1: sessão pré-existente
# ---------------------------------------------------------------------


def test_preexisting_developer_session_is_rejected_in_production(
    monkeypatch,
    service_client: TestClient,
    db_session: Session,
) -> None:
    user = _make_user(
        db_session,
        email="developer.legacy@example.com",
        role="developer",
    )
    raw_token = _make_raw_session(db_session, user)

    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )

    service_client.cookies.set(
        authentication.settings.auth_cookie_name, raw_token
    )
    response = service_client.get("/auth/me")

    assert response.status_code == 401


def test_preexisting_developer_session_is_accepted_outside_production(
    service_client: TestClient,
    db_session: Session,
) -> None:
    user = _make_user(
        db_session,
        email="developer.legacy.ok@example.com",
        role="developer",
    )
    raw_token = _make_raw_session(db_session, user)

    service_client.cookies.set(
        authentication.settings.auth_cookie_name, raw_token
    )
    response = service_client.get("/auth/me")

    assert response.status_code == 200


# ---------------------------------------------------------------------
# create_user.py -- rejeita antes de qualquer prompt/escrita
# ---------------------------------------------------------------------


def test_create_user_script_rejects_developer_in_production(
    monkeypatch,
    db_session: Session,
) -> None:
    import scripts.create_user as create_user_script

    monkeypatch.setattr(
        create_user_script.settings, "environment", "production"
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "create_user.py",
            "--name",
            "Operador",
            "--email",
            "operador.prod@example.com",
            "--role",
            "developer",
        ],
    )

    before = db_session.execute(
        text("SELECT COUNT(*) FROM users")
    ).scalar_one()

    with patch.object(
        create_user_script, "read_password"
    ) as fake_read_password:
        try:
            create_user_script.main()
            exited = False
        except SystemExit:
            exited = True

    after = db_session.execute(
        text("SELECT COUNT(*) FROM users")
    ).scalar_one()

    assert exited is True
    assert fake_read_password.called is False
    assert before == after


def test_create_user_script_allows_developer_outside_production(
    monkeypatch,
    db_session: Session,
) -> None:
    import scripts.create_user as create_user_script

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_user.py",
            "--name",
            "Operador Dev",
            "--email",
            "operador.dev.pr2@example.com",
            "--role",
            "developer",
        ],
    )

    with patch.object(
        create_user_script,
        "read_password",
        return_value=TEST_PASSWORD,
    ):
        create_user_script.main()

    created = (
        db_session.query(User)
        .filter(User.email == "operador.dev.pr2@example.com")
        .one_or_none()
    )
    assert created is not None
    assert created.role == "developer"


# ---------------------------------------------------------------------
# startup -- detecta e reporta, nunca muta
# ---------------------------------------------------------------------


def test_startup_check_detects_and_logs_without_mutation(
    monkeypatch,
    caplog,
    db_session: Session,
) -> None:
    import logging

    user_a = _make_user(
        db_session,
        email="developer.startup.a@example.com",
        role="developer",
    )
    _make_user(
        db_session,
        email="developer.startup.b@example.com",
        role="developer",
    )
    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )

    before_role = db_session.execute(
        text(
            "SELECT role FROM users WHERE id = :id"
        ),
        {"id": user_a.id},
    ).scalar_one()

    with caplog.at_level(logging.WARNING):
        count = check_production_developer_roles()

    db_session.expire_all()
    after_role = db_session.execute(
        text(
            "SELECT role FROM users WHERE id = :id"
        ),
        {"id": user_a.id},
    ).scalar_one()

    assert count >= 2
    assert before_role == after_role == "developer"

    matching = [
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "production.developer_role_detected"
    ]
    assert len(matching) == 1
    assert matching[0].count == count

    for record in caplog.records:
        message = record.getMessage()
        assert "developer.startup.a@example.com" not in message
        assert "developer.startup.b@example.com" not in message
        assert "Usuário PR-2 Teste" not in message


def test_startup_check_noop_when_no_developer_in_production(
    monkeypatch,
    caplog,
) -> None:
    import logging

    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )

    with caplog.at_level(logging.WARNING):
        count = check_production_developer_roles()

    assert count == 0
    assert not any(
        getattr(record, "event", None)
        == "production.developer_role_detected"
        for record in caplog.records
    )


def test_startup_check_noop_outside_production(
    db_session: Session,
    caplog,
) -> None:
    import logging

    _make_user(
        db_session,
        email="developer.notprod@example.com",
        role="developer",
    )

    with caplog.at_level(logging.WARNING):
        count = check_production_developer_roles()

    assert count == 0
    assert not any(
        getattr(record, "event", None)
        == "production.developer_role_detected"
        for record in caplog.records
    )


# ---------------------------------------------------------------------
# Outros papéis não são afetados em produção
# ---------------------------------------------------------------------


def test_other_roles_authenticate_normally_in_production(
    monkeypatch,
    db_session: Session,
) -> None:
    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )

    for role in (
        "viewer",
        "analyst",
        "manager",
        "executive",
        "administrator",
    ):
        user = _make_user(
            db_session,
            email=f"{role}.prod.unaffected@example.com",
            role=role,
        )
        result = authenticate_user(
            db_session,
            email=user.email,
            password=TEST_PASSWORD,
        )
        assert result is not None, (
            f"role={role} não deveria ser afetado pelo guard "
            "de produção"
        )


def test_system_role_remains_blocked_in_production_same_as_before(
    monkeypatch,
    db_session: Session,
) -> None:
    monkeypatch.setattr(
        authentication.settings, "environment", "production"
    )
    user = _make_user(
        db_session,
        email="system.prod@example.com",
        role="system",
    )

    result = authenticate_user(
        db_session,
        email=user.email,
        password=TEST_PASSWORD,
    )

    assert result is None
