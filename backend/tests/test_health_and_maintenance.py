from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.authentication import utc_now
from app.core.config import settings
from app.core.session_maintenance import (
    cleanup_auth_sessions,
)
from app.models.auth_session import AuthSession
from app.models.user import User


def create_user(
    db_session: Session,
) -> User:
    user = User(
        name="Usuário Maintenance",
        email="maintenance@example.com",
        password_hash=hash_password(
            "Senha-Manutencao-Auneron-123!"
        ),
        role="viewer",
        active=True,
    )

    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    return user


def create_session(
    db_session: Session,
    *,
    user_id: int,
    token_hash: str,
    expires_at,
    created_at=None,
    revoked_at=None,
) -> AuthSession:
    auth_session = AuthSession(
        user_id=user_id,
        token_hash=token_hash,
        created_at=created_at,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )

    db_session.add(auth_session)
    db_session.commit()
    db_session.refresh(auth_session)

    return auth_session


def test_health_is_liveness_probe(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/health"
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "api",
        "version": settings.app_version,
    }


def test_ready_reports_database_online(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/ready"
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "online",
    }


def test_ready_returns_503_when_database_is_offline(
    unauthenticated_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.health."
        "check_database_connection",
        lambda: False,
    )

    response = unauthenticated_client.get(
        "/ready"
    )

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database": "offline",
    }


def test_session_cleanup_removes_expired_and_old_revoked(
    db_session: Session,
) -> None:
    user = create_user(
        db_session
    )
    now = utc_now()

    active = create_session(
        db_session,
        user_id=user.id,
        token_hash="a" * 64,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=2),
    )
    expired = create_session(
        db_session,
        user_id=user.id,
        token_hash="b" * 64,
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(minutes=1),
    )
    recent_revoked = create_session(
        db_session,
        user_id=user.id,
        token_hash="c" * 64,
        created_at=now - timedelta(hours=2),
        expires_at=now + timedelta(hours=2),
        revoked_at=now - timedelta(hours=1),
    )
    old_revoked = create_session(
        db_session,
        user_id=user.id,
        token_hash="d" * 64,
        created_at=(
            now
            - timedelta(
                hours=(
                    settings
                    .auth_revoked_session_retention_hours
                    + 2
                )
            )
        ),
        expires_at=now + timedelta(hours=2),
        revoked_at=(
            now
            - timedelta(
                hours=(
                    settings
                    .auth_revoked_session_retention_hours
                    + 1
                )
            )
        ),
    )

    deleted = cleanup_auth_sessions(
        db_session,
        now=now,
    )

    assert deleted == 2

    remaining_ids = {
        row.id
        for row in (
            db_session.query(
                AuthSession
            ).all()
        )
    }

    assert active.id in remaining_ids
    assert recent_revoked.id in remaining_ids
    assert expired.id not in remaining_ids
    assert old_revoked.id not in remaining_ids


def test_session_cleanup_is_idempotent(
    db_session: Session,
) -> None:
    user = create_user(
        db_session
    )
    now = utc_now()

    create_session(
        db_session,
        user_id=user.id,
        token_hash="e" * 64,
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(minutes=5),
    )

    first = cleanup_auth_sessions(
        db_session,
        now=now,
    )
    second = cleanup_auth_sessions(
        db_session,
        now=now,
    )

    assert first == 1
    assert second == 0
