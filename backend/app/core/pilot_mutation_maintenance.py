from __future__ import annotations

import asyncio
import logging

from app.core.authorization import has_permission
from app.core.config import settings
from app.core.pilot_mutation_errors import PilotMutationAuthorizationError
from app.database.database import SessionLocal
from app.models.authenticated_advisory_proposal import AuthenticatedAdvisoryProposal
from app.models.user import User
from app.repositories.work_repository import WorkRepository
from app.repositories.work_skill_execution_repository import WorkSkillExecutionRepository
from app.services.account_mark_overdue_execution_service import AccountMarkOverdueExecutionService
from app.services.approval_service import approval_input_identity
from app.services.work_service import WorkActor, WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService

logger = logging.getLogger("auneron.pilot_mutation")


def _reconstruct(item) -> dict:
    context = item.context_data if isinstance(item.context_data, dict) else {}
    if context.get("protocol") != "auneron.pilot.account_mark_overdue.v1" or context.get("action_type") != "account.mark_overdue":
        raise ValueError("Not a pilot Work.")
    due = context.get("expected_due_date")
    if not isinstance(due, str) or not due:
        raise ValueError("Missing expected_due_date.")
    payload = {"account_id": item.account_id, "expected_status": "aberto", "expected_due_date": due}
    _, digest = approval_input_identity(payload)
    if digest != context.get("input_digest"):
        raise ValueError("Reconstructed digest mismatch.")
    return payload


def run_pilot_mutation_recovery(*, limit: int | None = None) -> int:
    effective_limit = settings.work_skill_recovery_batch_size if limit is None else limit
    if isinstance(effective_limit, bool) or not isinstance(effective_limit, int) or effective_limit < 1 or effective_limit > 1000:
        raise ValueError("Invalid pilot recovery limit.")
    recovered = 0
    with SessionLocal() as db:
        repo = WorkSkillExecutionRepository(db)
        work_repo = WorkRepository(db)
        work_service = WorkManagerService(db)
        work_exec = WorkSkillExecutionService(db)
        effect = AccountMarkOverdueExecutionService(db)
        for work_id in repo.list_pilot_recovery_candidate_work_ids(limit=effective_limit):
            try:
                item = work_repo.lock_by_id(work_id)
                if item is None:
                    continue
                context = item.context_data if isinstance(item.context_data, dict) else {}
                proposal_id = context.get("proposal_id")
                approval_request_id = context.get("approval_request_id")
                skill_version_id = context.get("skill_version_id")
                if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (proposal_id, approval_request_id, skill_version_id)):
                    raise ValueError("Malformed pilot identity.")
                proposal = db.get(AuthenticatedAdvisoryProposal, proposal_id)
                if proposal is None:
                    raise ValueError("Proposal missing.")
                payload = _reconstruct(item)
                request = work_exec.approval_repository.get_request(approval_request_id)
                if request is None or request.requester_actor_type != "agent" or not request.requester_reference.startswith("agent:"):
                    raise ValueError("Approval missing/invalid actor.")
                if item.status == "backlog":
                    item = work_service.transition_status(
                        item.id, expected_version=item.version,
                        actor=WorkActor(actor_type="system", actor_reference=f"system:work:{item.id}", actor_user_id=None),
                        status="ready", idempotency_key=f"work:{item.id}:pilot:ready",
                    ).work_item
                decision = work_exec.approval_repository.get_decision(
                    approval_request_id
                )
                if decision is None or decision.decided_by_user_id is None:
                    raise PilotMutationAuthorizationError(
                        "Decisor da aprovacao nao pode ser revalidado."
                    )
                decider = db.get(User, decision.decided_by_user_id)
                if (
                    decider is None
                    or not decider.active
                    or not has_permission(decider.role, request.required_permission)
                ):
                    raise PilotMutationAuthorizationError(
                        "Autoridade humana da aprovacao nao esta mais valida."
                    )
                if repo.get_by_work_item(item.id) is None:
                    work_exec.configure_with_existing_approval(
                        item.id, version_id=skill_version_id,
                        authority_user_id=decider.id,
                        input_payload=payload, approval_request_id=approval_request_id,
                    )
                effect.execute(
                    work_item_id=item.id, approval_request_id=approval_request_id,
                    authority_user_id=decider.id,
                    actor_reference=request.requester_reference, input_payload=payload,
                )
                recovered += 1
            except Exception as error:
                db.rollback()
                logger.warning("pilot_mutation_recovery_failed", extra={"event": "pilot.mutation.recovery_failed", "work_item_id": work_id, "error_type": type(error).__name__})
    return recovered


async def run_pilot_mutation_recovery_async() -> int:
    return await asyncio.to_thread(run_pilot_mutation_recovery)


async def pilot_mutation_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(settings.work_skill_recovery_interval_seconds)
        await run_pilot_mutation_recovery_async()
