from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import approval_input_identity
from app.services.approval_service import approval_request_fingerprint
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import _canonical_json, _digest_bytes, _fingerprint

SKILL_KEY = "account.mark_paid"
PROVIDER = "auneron.core"
HANDLER_REFERENCE = "app.skills.account:mark_paid"
CAPABILITY_KEY = "account.status.mark_paid"
CONSUMER_ACTOR_TYPE = "system"
CONSUMER_REFERENCE = "system:account_mark_paid_execution"
VALID_EXPECTED_STATUSES = {"aberto", "atrasado"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ApprovalValidationError(f"{field_name} inválido.")
    return value


@dataclass(frozen=True)
class AccountMarkPaidExecutionResult:
    approval_request_id: int
    approval_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: dict[str, Any]


class AccountMarkPaidExecutionService:
    """
    Execucao governada de account.mark_paid (Fatia 1, peca 4/N).

    Fluxo simplificado (ADR do design doc, secao "AccountMarkPaidExecutionService"):
    humano solicita (rota generica POST /approvals/skill-executions/{version_id})
    -> outro humano aprova (rota generica POST /approvals/{id}/decision)
    -> este servico executa, apos validacao manual da aprovacao.

    A validacao manual substitui GovernedSkillExecutionService.validate_approved_action_only()
    porque aquele metodo (via _normalize_actor/evaluate_skill_autonomy) exige
    estruturalmente um ator nao-humano, o que jamais sera o caso aqui (o
    requester desta skill e sempre um humano). Decisao registrada como
    "ADR 009" no design doc: replicar manualmente apenas 4 verificacoes
    (status aprovado, decisao valida, versao/input/fingerprint batem, nao
    expirada) e reusar authorize_skill_execution() separadamente para
    RBAC/escopo, sem alterar o metodo compartilhado.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.approvals = ApprovalRepository(db)
        self.skills = SkillRepository(db)

    @staticmethod
    def _runtime_key(approval_request_id: int) -> str:
        return f"approval:{approval_request_id}"

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
                "Catálogo da skill account.mark_paid divergente do esperado."
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
                "Capability account.status.mark_paid ausente ou ambígua."
            )
        if any(capability.resource_scope == "external" for capability in capabilities):
            raise ApprovalStateError(
                "Capability external não é permitida em account.mark_paid."
            )

    def execute(
        self,
        *,
        account_id: int,
        approval_request_id: int,
        expected_status: str,
        authority_user_id: int,
        now: datetime | None = None,
    ) -> AccountMarkPaidExecutionResult:
        normalized_account_id = _positive_id(account_id, field_name="account_id")
        normalized_request_id = _positive_id(
            approval_request_id, field_name="approval_request_id"
        )
        normalized_authority_id = _positive_id(
            authority_user_id, field_name="authority_user_id"
        )
        if expected_status not in VALID_EXPECTED_STATUSES:
            raise ApprovalValidationError("expected_status inválido.")

        effective_now = now if now is not None else _utc_now()
        if effective_now.tzinfo is None:
            raise ApprovalValidationError("now deve possuir timezone.")

        authority = self.db.get(User, normalized_authority_id)
        if authority is None or not authority.active:
            raise ApprovalAuthorizationError(
                "Usuário-autoridade inexistente ou inativo."
            )

        request = self.approvals.lock_request(normalized_request_id)
        if request is None:
            raise ApprovalNotFoundError(
                "Solicitação de aprovação não encontrada."
            )

        # Idempotencia: ApprovalConsumption e unico por approval_request_id
        # (uq_approval_consumptions_request). Nao ha WorkItem/WorkEvent
        # aqui, entao o proprio consumo e o recibo do efeito de negocio.
        existing_consumption = self.approvals.get_consumption_by_request(
            request.id
        )
        if existing_consumption is not None:
            if (
                existing_consumption.status != "consumed"
                or existing_consumption.skill_invocation_id is None
            ):
                raise ApprovalConsumptionConflictError(
                    "Consumo existente em estado inconsistente."
                )
            invocation = self.db.get(
                SkillInvocation, existing_consumption.skill_invocation_id
            )
            account = self.db.get(Account, normalized_account_id)
            if (
                invocation is None
                or invocation.status != "succeeded"
                or account is None
                or account.status != "pago"
            ):
                raise ApprovalConsumptionConflictError(
                    "Recibo de consumo e estado atual da conta divergem."
                )
            output = (
                invocation.output_payload
                if isinstance(invocation.output_payload, dict)
                else {}
            )
            return AccountMarkPaidExecutionResult(
                approval_request_id=request.id,
                approval_consumption_id=existing_consumption.id,
                invocation_id=invocation.id,
                invocation_status=invocation.status,
                duplicate=True,
                output=output,
            )

        version = self.skills.get_version(request.skill_version_id)
        if version is None or version.status != "published":
            raise ApprovalNotFoundError(
                "Versão de skill inexistente ou não publicada."
            )
        skill = self.skills.get_skill(version.skill_id)
        if skill is None or skill.status != "active":
            raise ApprovalNotFoundError("Skill inexistente ou inativa.")
        capabilities = tuple(self.skills.list_capabilities(version.id))
        self._validate_catalog(skill=skill, version=version, capabilities=capabilities)

        # --- Validacao manual da aprovacao (ADR 009) ---
        if request.action_type != "skill_execution":
            raise ApprovalStateError(
                "Tipo de ação aprovado não é executável por Skill."
            )
        if request.status != "approved":
            raise ApprovalRequiredError(
                "A execução exige aprovação humana aprovada."
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
            "expected_status": expected_status,
        }
        normalized_input, input_digest = approval_input_identity(input_payload)
        if request.input_digest != input_digest:
            raise ApprovalConsumptionConflictError(
                "Input informado na execução diverge do input aprovado."
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
                "Identidade da solicitação diverge da aprovação persistida."
            )
        # --- fim da validacao manual ---

        sensitive_verified = (
            request.required_permission == "approval:decide_sensitive"
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
            raise ApprovalStateError("Escopo atual diverge da ação aprovada.")

        account = self.db.execute(
            select(Account)
            .where(Account.id == normalized_account_id)
            .with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise ApprovalNotFoundError("Conta inexistente.")
        if account.status == "pago":
            raise ApprovalConsumptionConflictError(
                "Conta já está paga sem recibo de consumo correspondente."
            )
        if account.status != expected_status:
            raise ApprovalConsumptionConflictError(
                "Status atual da conta diverge do status aprovado."
            )

        runtime_key = self._runtime_key(request.id)
        actor = SkillInvocationActor(
            actor_type=CONSUMER_ACTOR_TYPE,
            actor_reference=CONSUMER_REFERENCE,
            actor_user_id=None,
        )
        previous_status = account.status
        output = {
            "action": SKILL_KEY,
            "account_id": account.id,
            "previous_status": previous_status,
            "new_status": "pago",
            "changed": True,
        }
        normalized_output, output_bytes = _canonical_json(
            output, field_name="output", max_bytes=version.max_output_bytes
        )
        invocation = SkillInvocation(
            skill_version_id=version.id,
            actor_type=CONSUMER_ACTOR_TYPE,
            actor_reference=CONSUMER_REFERENCE,
            actor_user_id=None,
            idempotency_key=runtime_key,
            request_fingerprint=_fingerprint(
                version=version, actor=actor, normalized_input=normalized_input
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
            account.status = "pago"
            self.db.add(
                AccountEvent(
                    account_id=account.id,
                    event_type="status_changed",
                    actor_type="user",
                    actor_reference=f"user:{authority.id}",
                    actor_user_id=authority.id,
                    previous_status=previous_status,
                    new_status="pago",
                    idempotency_key=f"account_event:{runtime_key}",
                )
            )
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise ApprovalConsumptionConflictError(
                "Conflito concorrente ao registrar o efeito de negócio."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return AccountMarkPaidExecutionResult(
            approval_request_id=request.id,
            approval_consumption_id=consumption.id,
            invocation_id=invocation.id,
            invocation_status="succeeded",
            duplicate=False,
            output=output,
        )
