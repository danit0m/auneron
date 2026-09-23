import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.database import SessionLocal
from app.repositories.business_effect_verification_repository import (
    BusinessEffectVerificationRepository,
)
from app.services.business_effect_verification_service import (
    BusinessEffectVerificationService,
)

maintenance_loop_logger = logging.getLogger(
    "auneron.business_effect_verification_maintenance"
)


@dataclass(frozen=True)
class BusinessEffectVerificationRecoverySummary:
    candidate_count: int
    verified_count: int
    contradicted_count: int
    pending_count: int
    unverifiable_count: int
    failure_count: int


def run_business_effect_verification_recovery(
    *,
    limit: int | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    repository_factory: Callable[
        [Session],
        BusinessEffectVerificationRepository,
    ] = BusinessEffectVerificationRepository,
    service_factory: Callable[
        [Session],
        BusinessEffectVerificationService,
    ] = BusinessEffectVerificationService,
) -> BusinessEffectVerificationRecoverySummary:
    """
    Reavalia, sem executar/despachar nenhuma Skill, o efeito de negocio
    esperado de todo ApprovalConsumption consumido dos dois corredores
    DW-3 V1 que ainda nao tem BusinessEffectVerification terminal.
    """
    effective_limit = (
        settings.work_skill_recovery_batch_size
        if limit is None
        else limit
    )
    if (
        isinstance(effective_limit, bool)
        or not isinstance(effective_limit, int)
        or effective_limit < 1
        or effective_limit > 1000
    ):
        raise ValueError(
            "limit inválido para Business Effect "
            "Verification recovery."
        )

    db = session_factory()
    try:
        repository = repository_factory(db)
        service = service_factory(db)
        consumption_ids = (
            repository.list_recovery_candidate_consumption_ids(
                limit=effective_limit
            )
        )

        verified_count = 0
        contradicted_count = 0
        pending_count = 0
        unverifiable_count = 0
        failure_count = 0

        for consumption_id in consumption_ids:
            try:
                outcome = service.verify(consumption_id)
            except Exception:
                db.rollback()
                failure_count += 1
                maintenance_loop_logger.exception(
                    "business_effect_verification_"
                    "recovery_failed",
                    extra={
                        "event": (
                            "business_effect_verification"
                            ".recovery_failed"
                        ),
                        "approval_consumption_id": (
                            consumption_id
                        ),
                    },
                )
                continue

            result = outcome.verification.result
            if result == "verified":
                verified_count += 1
            elif result == "contradicted":
                contradicted_count += 1
            elif result == "unverifiable":
                unverifiable_count += 1
            else:
                pending_count += 1

            maintenance_loop_logger.info(
                "business_effect_verification_recovered",
                extra={
                    "event": (
                        "business_effect_verification"
                        ".recovered"
                    ),
                    "approval_consumption_id": (
                        consumption_id
                    ),
                    "skill_key": (
                        outcome.verification.skill_key
                    ),
                    "result": result,
                    "duplicate": outcome.duplicate,
                },
            )

        summary = BusinessEffectVerificationRecoverySummary(
            candidate_count=len(consumption_ids),
            verified_count=verified_count,
            contradicted_count=contradicted_count,
            pending_count=pending_count,
            unverifiable_count=unverifiable_count,
            failure_count=failure_count,
        )
        maintenance_loop_logger.info(
            "business_effect_verification_"
            "recovery_completed",
            extra={
                "event": (
                    "business_effect_verification"
                    ".recovery_completed"
                ),
                "candidate_count": summary.candidate_count,
                "verified_count": summary.verified_count,
                "contradicted_count": (
                    summary.contradicted_count
                ),
                "pending_count": summary.pending_count,
                "unverifiable_count": (
                    summary.unverifiable_count
                ),
                "failure_count": summary.failure_count,
            },
        )
        return summary
    finally:
        db.close()


async def run_business_effect_verification_recovery_async(
) -> BusinessEffectVerificationRecoverySummary:
    worker = asyncio.create_task(
        asyncio.to_thread(
            run_business_effect_verification_recovery
        )
    )

    try:
        return await asyncio.shield(
            worker
        )
    except asyncio.CancelledError:
        try:
            await worker
        except Exception:
            maintenance_loop_logger.exception(
                "business_effect_verification_"
                "shutdown_drain_failed",
                extra={
                    "event": (
                        "business_effect_verification"
                        ".shutdown_drain_failed"
                    ),
                },
            )
        raise


async def business_effect_verification_maintenance_loop(
) -> None:
    while True:
        await asyncio.sleep(
            settings.work_skill_recovery_interval_seconds
        )
        try:
            await (
                run_business_effect_verification_recovery_async()
            )
        except Exception as error:
            maintenance_loop_logger.exception(
                "business_effect_verification_"
                "maintenance_failed",
                extra={
                    "event": (
                        "business_effect_verification"
                        ".maintenance_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
