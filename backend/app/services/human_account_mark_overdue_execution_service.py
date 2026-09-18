"""
P1.2B -- Human Account Mark-Overdue Execution Service V1.

Executor estreito e novo, especifico da skill account.mark_overdue,
para o corredor humano materializado por
HumanAccountMarkOverdueMaterializationService. Nao chama nenhuma das
pecas agent-only protegidas pelo Architecture Freeze:
GovernedSkillExecutionService (nem .execute() nem
.validate_approved_action_only(), que exige estruturalmente um ator
nao-humano via _normalize_actor -- fronteira arquitetural existente,
nao contornada), WorkSkillExecutionService.configure_with_existing_
approval() (exige requester_actor_type=="agent"), nem
AccountMarkOverdueExecutionService (exige actor_reference.startswith
("agent:")). Nenhum ator humano e convertido em "agent" para atravessar
essas fronteiras.

Segue o mesmo padrao de validacao manual (ADR 009) ja usado por
AccountMarkPaidExecutionService -- as mesmas 4 verificacoes (status
aprovado, decisao valida, versao/input/fingerprint batem, nao expirada)
e reuso de authorize_skill_execution() para RBAC/escopo, sem alterar o
metodo compartilhado.

Consumo de ApprovalConsumption segue o precedente ja existente de
AccountMarkPaidExecutionService: consumer_actor_type="system" (nao
"agent", nao "user" -- CHECK constraint ck_approval_consumptions_
actor_type_valid so permite agent/system/integration). A identidade
humana real fica nos campos de autoridade
(authority_user_id/authority_reference/authority_role) e, sobretudo,
em AccountEvent.actor_type="user" -- o registro de auditoria financeira
que responde "sob qual autoria a mutacao foi executada".

Identidade do episodio: recomputada a partir de account_id + due_date
via work_key_for_episode() (autoridade unica no modulo de
materializacao) -- nunca recebe work_item_id/approval_request_id do
cliente. O WorkItem canonico e a unica fonte da ApprovalRequest
associada (context_data["approval_request_id"]).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalExpiredError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalRequiredError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.autonomy_policy import classify_skill_risk
from app.core.skill_authorization import authorize_skill_execution
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.skill import SkillInvocation
from app.models.user import User
from app.models.work import WorkEvent
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository
from app.repositories.work_repository import WorkRepository
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import approval_input_identity
from app.services.approval_service import approval_request_fingerprint
from app.services.human_account_mark_overdue_materialization_service import (
    SKILL_KEY,
)
from app.services.human_account_mark_overdue_materialization_service import (
    work_key_for_episode,
)
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import _canonical_json
from app.services.skill_runtime import _digest_bytes
from app.services.skill_runtime import _fingerprint
from app.services.work_service import TERMINAL_STATUSES
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService

PROVIDER = "auneron.core"
HANDLER_REFERENCE = "app.skills.account:mark_overdue"
CAPABILITY_KEY = "account.status.mark_overdue"
PROTOCOL = "auneron.human_pilot.account_mark_overdue.v1"
CONSUMER_ACTOR_TYPE = "system"
CONSUMER_REFERENCE = "system:human_account_mark_overdue_execution"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(value: Any, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ApprovalValidationError(f"{field_name} inválido.")
    return value


class HumanMarkOverdueWorkItemNotFoundError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class HumanMarkOverdueApprovalMissingError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class HumanMarkOverdueApprovalRejectedError(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(
            f"A aprovação humana não foi concedida (status={status})."
        )


@dataclass(frozen=True)
class HumanAccountMarkOverdueExecutionResult:
    work_item_id: int
    approval_request_id: int
    approval_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: dict[str, Any]


class HumanAccountMarkOverdueExecutionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.approvals = ApprovalRepository(db)
        self.skills = SkillRepository(db)
        self.works = WorkRepository(db)
        self.work_service = WorkManagerService(db)

    @staticmethod
    def _receipt_key(approval_request_id: int) -> str:
        return (
            "effect:human_account_mark_overdue:approval:"
            f"{approval_request_id}"
        )

    def _validate_catalog(self, *, skill, version, capabilities) -> None:
        if (
            skill.skill_key != SKILL_KEY
            or skill.provider != PROVIDER
            or skill.status != "active"
            or version.status != "published"
            or version.runtime_kind != "internal_python"
            or version.execution_mode != "mutating"
            or version.handler_reference != HANDLER_REFERENCE
        ):
            raise ApprovalStateError(
                "Catálogo da skill account.mark_overdue divergente "
                "do esperado."
            )
        matches = [
            capability
            for capability in capabilities
            if capability.capability_key == CAPABILITY_KEY
            and capability.access_mode == "write"
            and capability.resource_scope == "account"
        ]
        if len(matches) != 1:
            raise ApprovalStateError(
                "Capability account.status.mark_overdue ausente ou "
                "ambígua."
            )
        if any(
            capability.resource_scope == "external"
            for capability in capabilities
        ):
            raise ApprovalStateError(
                "Capability external não é permitida em "
                "account.mark_overdue."
            )

    def _cancel_work_item(
        self, work_item, *, reason: str
    ) -> None:
        if work_item.status in TERMINAL_STATUSES:
            return

        self.work_service.transition_status(
            work_item.id,
            expected_version=work_item.version,
            actor=WorkActor(
                actor_type="system",
                actor_reference=(
                    f"system:work:{work_item.id}"
                ),
                actor_user_id=None,
            ),
            status="cancelled",
            reason=reason,
        )

    def execute(
        self,
        *,
        account_id: int,
        due_date: date,
        authority_user_id: int,
        now: datetime | None = None,
    ) -> HumanAccountMarkOverdueExecutionResult:
        normalized_account_id = _positive_id(
            account_id, field_name="account_id"
        )
        normalized_authority_id = _positive_id(
            authority_user_id, field_name="authority_user_id"
        )

        effective_now = now if now is not None else _utc_now()
        if effective_now.tzinfo is None:
            raise ApprovalValidationError(
                "now deve possuir timezone."
            )

        authority = self.db.get(User, normalized_authority_id)
        if authority is None or not authority.active:
            raise ApprovalAuthorizationError(
                "Usuário-autoridade inexistente ou inativo."
            )

        canonical_key = work_key_for_episode(
            normalized_account_id, due_date
        )
        work_item = self.works.find_by_key(
            scope_type="account",
            work_key=canonical_key,
            account_id=normalized_account_id,
            for_update=True,
        )

        if work_item is None:
            raise HumanMarkOverdueWorkItemNotFoundError(
                "Nenhuma materialização encontrada para este "
                "episódio."
            )

        context = (
            work_item.context_data
            if isinstance(work_item.context_data, dict)
            else {}
        )
        approval_request_id = context.get("approval_request_id")
        if approval_request_id is None:
            raise HumanMarkOverdueApprovalMissingError(
                "WorkItem canônico sem ApprovalRequest associada."
            )

        request = self.approvals.lock_request(approval_request_id)
        if request is None:
            raise ApprovalNotFoundError(
                "Solicitação de aprovação não encontrada."
            )

        receipt_key = self._receipt_key(request.id)
        receipt = self.works.find_event_by_idempotency_key(
            work_item_id=work_item.id,
            idempotency_key=receipt_key,
        )
        if receipt is not None:
            existing_consumption = (
                self.approvals.get_consumption_by_request(
                    request.id
                )
            )
            account = self.db.get(Account, normalized_account_id)
            invocation = (
                self.db.get(
                    SkillInvocation,
                    existing_consumption.skill_invocation_id,
                )
                if existing_consumption is not None
                and existing_consumption.skill_invocation_id
                is not None
                else None
            )
            if (
                existing_consumption is None
                or existing_consumption.status != "consumed"
                or account is None
                or account.status != "atrasado"
                or invocation is None
                or invocation.status != "succeeded"
            ):
                raise ApprovalConsumptionConflictError(
                    "Recibo de execução e estado atual divergem."
                )
            output = (
                invocation.output_payload
                if isinstance(invocation.output_payload, dict)
                else {}
            )
            return HumanAccountMarkOverdueExecutionResult(
                work_item_id=work_item.id,
                approval_request_id=request.id,
                approval_consumption_id=existing_consumption.id,
                invocation_id=invocation.id,
                invocation_status=invocation.status,
                duplicate=True,
                output=output,
            )

        if request.status == "pending":
            raise ApprovalRequiredError(
                "A execução exige aprovação humana aprovada."
            )
        if request.status in ("rejected", "expired", "cancelled"):
            self._cancel_work_item(
                work_item,
                reason=(
                    f"Approval humana em estado '{request.status}'."
                ),
            )
            raise HumanMarkOverdueApprovalRejectedError(
                request.status
            )
        if request.status != "approved":
            raise ApprovalStateError(
                "Estado de aprovação inesperado."
            )

        version = self.skills.get_version(request.skill_version_id)
        if version is None or version.status != "published":
            raise ApprovalNotFoundError(
                "Versão de skill inexistente ou não publicada."
            )
        skill = self.skills.get_skill(version.skill_id)
        if skill is None or skill.status != "active":
            raise ApprovalNotFoundError("Skill inexistente ou inativa.")
        capabilities = tuple(
            self.skills.list_capabilities(version.id)
        )
        self._validate_catalog(
            skill=skill, version=version, capabilities=capabilities
        )

        if request.action_type != "skill_execution":
            raise ApprovalStateError(
                "Tipo de ação aprovado não é executável por Skill."
            )
        if request.expires_at <= effective_now:
            raise ApprovalExpiredError(
                "A aprovação aprovada expirou antes da execução."
            )
        decision = self.approvals.get_decision(request.id)
        if decision is None or decision.decision != "approved":
            raise ApprovalStateError(
                "Decisão de aprovação ausente ou inconsistente."
            )

        input_payload = {
            "account_id": normalized_account_id,
            "due_date": due_date.isoformat(),
        }
        normalized_input, input_digest = approval_input_identity(
            input_payload
        )
        if request.input_digest != input_digest:
            raise ApprovalConsumptionConflictError(
                "Input informado na execução diverge do input "
                "aprovado."
            )

        risk_level, required_permission = classify_skill_risk(
            version=version, capabilities=capabilities
        )
        if (
            request.risk_level != risk_level
            or request.required_permission != required_permission
        ):
            raise ApprovalStateError(
                "Política atual diverge da aprovação persistida."
            )

        original_requester = ApprovalRequester(
            actor_type=request.requester_actor_type,
            actor_reference=request.requester_reference,
            actor_user_id=request.requester_user_id,
        )
        expected_fingerprint = approval_request_fingerprint(
            version=version,
            requester=original_requester,
            input_digest=input_digest,
        )
        if request.request_fingerprint != expected_fingerprint:
            raise ApprovalConsumptionConflictError(
                "Identidade da solicitação diverge da aprovação "
                "persistida."
            )

        sensitive_verified = (
            request.required_permission
            == "approval:decide_sensitive"
            and decision.sensitive_elevation_verified
        )
        grant = authorize_skill_execution(
            db=self.db,
            role=authority.role,
            actor_user_id=authority.id,
            session_elevated=sensitive_verified,
            version_id=version.id,
            input_payload=normalized_input,
        )
        if (
            grant.account_id != request.target_account_id
            or grant.account_id != normalized_account_id
        ):
            raise ApprovalStateError(
                "Escopo atual diverge da ação aprovada."
            )

        if work_item.status == "ready":
            work_item = self.work_service.transition_status(
                work_item.id,
                expected_version=work_item.version,
                actor=WorkActor(
                    actor_type="system",
                    actor_reference=(
                        f"system:work:{work_item.id}"
                    ),
                    actor_user_id=None,
                ),
                status="in_progress",
            ).work_item
        elif work_item.status != "in_progress":
            raise ApprovalStateError(
                "WorkItem deve estar ready/in_progress para "
                "execução."
            )

        account = self.db.execute(
            select(Account)
            .where(Account.id == normalized_account_id)
            .with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise ApprovalNotFoundError("Conta inexistente.")
        if account.status == "atrasado":
            raise ApprovalConsumptionConflictError(
                "Conta já está atrasada sem recibo de execução "
                "correspondente."
            )
        if account.status != "aberto":
            raise ApprovalStateError(
                "Apenas contas 'aberto' podem transicionar."
            )
        if account.vencimento != due_date:
            raise ApprovalConsumptionConflictError(
                "Vencimento da conta mudou após a aprovação."
            )
        if account.vencimento >= date.today():
            raise ApprovalStateError("Conta não está atrasada.")

        runtime_key = f"approval:{request.id}"
        if (
            self.approvals.get_consumption_by_request(request.id)
            is not None
        ):
            raise ApprovalConsumptionConflictError(
                "Consumo existente sem recibo correspondente."
            )
        if (
            self.skills.find_invocation_by_idempotency(
                version_id=version.id,
                actor_type=CONSUMER_ACTOR_TYPE,
                actor_reference=CONSUMER_REFERENCE,
                idempotency_key=runtime_key,
            )
            is not None
        ):
            raise ApprovalConsumptionConflictError(
                "Invocation existente sem recibo correspondente."
            )

        actor = SkillInvocationActor(
            actor_type=CONSUMER_ACTOR_TYPE,
            actor_reference=CONSUMER_REFERENCE,
            actor_user_id=None,
        )
        output = {
            "action": SKILL_KEY,
            "account_id": account.id,
            "previous_status": "aberto",
            "new_status": "atrasado",
            "changed": True,
        }
        normalized_output, output_bytes = _canonical_json(
            output,
            field_name="output",
            max_bytes=version.max_output_bytes,
        )
        invocation = SkillInvocation(
            skill_version_id=version.id,
            actor_type=CONSUMER_ACTOR_TYPE,
            actor_reference=CONSUMER_REFERENCE,
            actor_user_id=None,
            idempotency_key=runtime_key,
            request_fingerprint=_fingerprint(
                version=version,
                actor=actor,
                normalized_input=normalized_input,
            ),
            input_digest=input_digest,
            status="succeeded",
            output_payload=normalized_output,
            output_digest=_digest_bytes(output_bytes),
            output_bytes=len(output_bytes),
            error_code=None,
            duration_ms=0,
            started_at=effective_now,
            finished_at=effective_now,
        )
        try:
            self.skills.add_invocation(invocation)
            consumption = ApprovalConsumption(
                approval_request_id=request.id,
                approval_decision_id=decision.id,
                skill_invocation_id=invocation.id,
                consumer_actor_type=CONSUMER_ACTOR_TYPE,
                consumer_reference=CONSUMER_REFERENCE,
                authority_user_id=authority.id,
                authority_reference=f"user:{authority.id}",
                authority_role=authority.role,
                runtime_idempotency_key=runtime_key,
                request_fingerprint=request.request_fingerprint,
                input_digest=input_digest,
                status="consumed",
                error_code=None,
                reserved_at=effective_now,
                finalized_at=effective_now,
            )
            self.approvals.add_consumption(consumption)
            account.status = "atrasado"
            self.db.add(
                AccountEvent(
                    account_id=account.id,
                    event_type="status_changed",
                    actor_type="user",
                    actor_reference=f"user:{authority.id}",
                    actor_user_id=authority.id,
                    previous_status="aberto",
                    new_status="atrasado",
                    idempotency_key=(
                        f"account_event:{receipt_key}"
                    ),
                )
            )
            self.works.add_event(
                WorkEvent(
                    work_item_id=work_item.id,
                    event_type="system_note",
                    actor_type=CONSUMER_ACTOR_TYPE,
                    actor_reference=CONSUMER_REFERENCE,
                    actor_user_id=None,
                    idempotency_key=receipt_key,
                    event_data={
                        "kind": "business_effect_receipt",
                        "protocol": PROTOCOL,
                        "action": SKILL_KEY,
                        "account_id": account.id,
                        "due_date": due_date.isoformat(),
                        "work_key": canonical_key,
                        "approval_request_id": request.id,
                        "skill_invocation_id": invocation.id,
                        "input_digest": input_digest,
                        "previous_status": "aberto",
                        "new_status": "atrasado",
                    },
                    created_at=effective_now,
                )
            )
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise ApprovalConsumptionConflictError(
                "Conflito concorrente ao registrar o efeito de "
                "negócio."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        fresh_work = self.works.lock_by_id(work_item.id)
        if fresh_work is not None and fresh_work.status == "in_progress":
            self.work_service.transition_status(
                fresh_work.id,
                expected_version=fresh_work.version,
                actor=WorkActor(
                    actor_type="system",
                    actor_reference=(
                        f"system:work:{fresh_work.id}"
                    ),
                    actor_user_id=None,
                ),
                status="completed",
            )

        return HumanAccountMarkOverdueExecutionResult(
            work_item_id=work_item.id,
            approval_request_id=request.id,
            approval_consumption_id=consumption.id,
            invocation_id=invocation.id,
            invocation_status="succeeded",
            duplicate=False,
            output=output,
        )
