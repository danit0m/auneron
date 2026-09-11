from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.database.database import SessionLocal
from app.services.overdue_detection_service import OverdueDetectionRunResult
from app.services.overdue_detection_service import OverdueDetectionService


logger = logging.getLogger("auneron.overdue_detection")


def run_overdue_detection() -> OverdueDetectionRunResult:
    """
    Run one governed overdue observation pass.

    This opens its own transaction scope like the other maintenance workers.
    The service may create system-principal proposals and ApprovalRequests,
    but never mutates Account.status or executes the mutating Skill directly.
    """
    with SessionLocal() as db:
        result = OverdueDetectionService(db).run_scan()
        logger.info(
            "overdue_detection_completed",
            extra={
                "event": "overdue.detection.completed",
                **result.as_dict(),
            },
        )
        return result


async def run_overdue_detection_async() -> OverdueDetectionRunResult:
    return await asyncio.to_thread(run_overdue_detection)


async def overdue_detection_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings.work_skill_recovery_interval_seconds
        )
        try:
            await run_overdue_detection_async()
        except Exception as error:
            logger.exception(
                "overdue_detection_maintenance_failed",
                extra={
                    "event": "overdue.detection.maintenance_failed",
                    "error_type": type(error).__name__,
                },
            )
