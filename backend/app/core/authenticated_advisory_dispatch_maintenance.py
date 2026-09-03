from __future__ import annotations

import asyncio
import logging
import re

from app.core.authentication import AuthenticatedSession
from app.core.authentication import utc_now
from app.core.config import settings
from app.database.database import SessionLocal
from app.models.auth_session import AuthSession
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.user import User
from app.repositories.approval_repository import ApprovalRepository
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovalBridgeService,
)

logger = logging.getLogger("auneron.advisory_dispatch")

_ADVISORY_IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^advisory:(\d+):(\d+)$"
)


def _parse_advisory_identity(
    idempotency_key: str,
) -> tuple[int, int]:
    match = _ADVISORY_IDEMPOTENCY_KEY_PATTERN.fullmatch(
        idempotency_key
    )
    if match is None:
        raise ValueError(
            "ApprovalRequest de ator 'agent' com idempotency_key fora "
            "do formato 'advisory:<proposal_id>:<binding_id>'."
        )
    return int(match.group(1)), int(match.group(2))


def _log_orphaned(
    *,
    request_id: int,
    proposal_id: int,
    binding_id: int,
    reason: str,
) -> None:
    logger.error(
        "advisory_dispatch_permanently_orphaned",
        extra={
            "event": "advisory.dispatch.permanently_orphaned",
            "approval_request_id": request_id,
            "proposal_id": proposal_id,
            "binding_id": binding_id,
            "reason": reason,
        },
    )


def run_advisory_dispatch_recovery(
    *,
    limit: int | None = None,
) -> int:
    """
    Varre ApprovalRequest aprovados, originados da ponte advisory
    (25M), que ainda nao tem ApprovalConsumption -- ou seja, aprovados
    mas nunca despachados -- e chama dispatch_approved() para cada
    um, usando a AuthSession original que criou a proposal.

    Se essa sessao original ja estiver revogada ou expirada, o
    despacho e permanentemente impossivel para aquele pedido; isso e
    registrado em nivel ERROR com um evento proprio
    (advisory.dispatch.permanently_orphaned), para ficar visivel em
    qualquer leitura de log. Nao ha canal de alerta real ainda --
    Principio 3 do documento de governanca continua em aberto.
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
        raise ValueError("Invalid advisory dispatch recovery limit.")

    dispatched = 0

    with SessionLocal() as db:
        approval_repository = ApprovalRepository(db)
        bridge = AuthenticatedAdvisoryProposalApprovalBridgeService(db)

        candidates = (
            approval_repository
            .list_approved_agent_requests_without_consumption(
                limit=effective_limit
            )
        )

        for request in candidates:
            try:
                proposal_id, binding_id = _parse_advisory_identity(
                    request.idempotency_key
                )

                if request.target_account_id is None:
                    raise ValueError(
                        "ApprovalRequest advisory sem target_account_id "
                        f"(request_id={request.id})."
                    )

                proposal = db.get(
                    AuthenticatedAdvisoryProposal,
                    proposal_id,
                )
                if proposal is None:
                    raise ValueError(
                        f"Proposal {proposal_id} nao encontrada "
                        f"(request_id={request.id})."
                    )

                auth_session = db.get(
                    AuthSession,
                    proposal.auth_session_id,
                )
                if auth_session is None:
                    _log_orphaned(
                        request_id=request.id,
                        proposal_id=proposal_id,
                        binding_id=binding_id,
                        reason="original_session_missing",
                    )
                    continue

                if (
                    auth_session.revoked_at is not None
                    or auth_session.expires_at <= utc_now()
                ):
                    _log_orphaned(
                        request_id=request.id,
                        proposal_id=proposal_id,
                        binding_id=binding_id,
                        reason=(
                            "original_session_revoked_or_expired"
                        ),
                    )
                    continue

                user = db.get(
                    User,
                    proposal.authority_user_id,
                )
                if user is None or not user.active:
                    _log_orphaned(
                        request_id=request.id,
                        proposal_id=proposal_id,
                        binding_id=binding_id,
                        reason="original_user_unavailable",
                    )
                    continue

                authenticated = AuthenticatedSession(
                    user=user,
                    session=auth_session,
                )

                bridge.dispatch_approved(
                    proposal_id=proposal_id,
                    authenticated=authenticated,
                    binding_id=binding_id,
                    input_payload={
                        "account_id": request.target_account_id,
                    },
                    approval_request_id=request.id,
                )
                dispatched += 1
            except Exception as error:
                db.rollback()
                logger.warning(
                    "advisory_dispatch_recovery_failed",
                    extra={
                        "event": "advisory.dispatch.recovery_failed",
                        "approval_request_id": request.id,
                        "error_type": type(error).__name__,
                    },
                )

    return dispatched


async def run_advisory_dispatch_recovery_async() -> int:
    return await asyncio.to_thread(
        run_advisory_dispatch_recovery
    )


async def advisory_dispatch_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings.work_skill_recovery_interval_seconds
        )
        await run_advisory_dispatch_recovery_async()
