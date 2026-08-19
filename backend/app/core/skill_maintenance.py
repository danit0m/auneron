import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.database import SessionLocal
from app.services.skill_runtime import SkillRuntimeService


skill_maintenance_logger = logging.getLogger(
    "auneron.skill.maintenance"
)


def recover_stale_skill_invocations(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    recovered = SkillRuntimeService(
        db
    ).recover_stale_invocations(
        now=now,
        stale_after_seconds=(
            settings.skill_stale_running_seconds
        ),
        limit=(
            settings.skill_recovery_batch_size
        ),
    )
    return len(recovered)


def run_skill_invocation_recovery() -> int:
    db = SessionLocal()

    try:
        recovered = (
            recover_stale_skill_invocations(
                db
            )
        )

        skill_maintenance_logger.info(
            "skill_invocation_recovery_completed",
            extra={
                "event": (
                    "skill.invocation.recovery"
                ),
                "recovered_invocations": recovered,
            },
        )
        return recovered
    except Exception as error:
        db.rollback()

        skill_maintenance_logger.error(
            "skill_invocation_recovery_failed",
            extra={
                "event": (
                    "skill.invocation.recovery"
                ),
                "error_type": (
                    type(error).__name__
                ),
            },
        )
        return 0
    finally:
        db.close()


async def skill_invocation_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings
            .skill_recovery_interval_seconds
        )

        await asyncio.to_thread(
            run_skill_invocation_recovery
        )
