import asyncio
import logging
from datetime import datetime
from datetime import timedelta

from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.authentication import utc_now
from app.core.config import settings
from app.database.database import SessionLocal
from app.models.auth_session import AuthSession


maintenance_logger = logging.getLogger(
    "auneron.maintenance"
)


def cleanup_auth_sessions(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    effective_now = now or utc_now()
    revoked_cutoff = (
        effective_now
        - timedelta(
            hours=(
                settings
                .auth_revoked_session_retention_hours
            )
        )
    )

    deleted = (
        db.query(AuthSession)
        .filter(
            or_(
                AuthSession.expires_at
                <= effective_now,
                and_(
                    AuthSession.revoked_at
                    .is_not(None),
                    AuthSession.revoked_at
                    <= revoked_cutoff,
                ),
            )
        )
        .delete(
            synchronize_session=False
        )
    )

    db.commit()

    return int(deleted)


def run_auth_session_cleanup() -> int:
    db = SessionLocal()

    try:
        deleted = cleanup_auth_sessions(
            db
        )

        maintenance_logger.info(
            "auth_session_cleanup_completed",
            extra={
                "event": (
                    "auth.session.cleanup"
                ),
                "deleted_sessions": deleted,
            },
        )

        return deleted
    except Exception as error:
        db.rollback()

        maintenance_logger.error(
            "auth_session_cleanup_failed",
            extra={
                "event": (
                    "auth.session.cleanup"
                ),
                "error_type": (
                    type(error).__name__
                ),
            },
        )

        return 0
    finally:
        db.close()


async def auth_session_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings
            .auth_session_cleanup_interval_seconds
        )

        await asyncio.to_thread(
            run_auth_session_cleanup
        )
