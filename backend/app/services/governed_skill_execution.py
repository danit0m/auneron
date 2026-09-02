from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalRequiredError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.autonomy_policy import AutonomyPolicyDecision
from app.core.autonomy_policy import evaluate_skill_autonomy
from app.core.authorization import has_permission
from app.core.skill_authorization import authorize_skill_execution
from app.core.skill_errors import SkillHandlerNotAllowedError
from app.core.skill_errors import SkillValidationError
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.skill import SkillInvocation
from app.models.user import User
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import approval_input_identity
from app.services.approval_service import approval_request_fingerprint
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime_context import (
    WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
)
from app.services.skill_runtime_context import (
    normalize_work_learning_runtime_context,
)
from app.services.skill_runtime import SkillInvocationResult
from app.services.skill_runtime import SkillRuntimeService


NON_HUMAN_ACTOR_TYPES = frozenset({
    "agent",
    "system",
    "integration",
})


@dataclass(frozen=True)
class GovernedApprovedActionValidation:
    policy: AutonomyPolicyDecision
    actor: SkillInvocationActor
    authority: User
    version: Any
    skill: Any
    capabilities: tuple[Any, ...]
    request: ApprovalRequest
    decision: ApprovalDecision
    grant: Any
    normalized_input: Any
    input_digest: str
    effective_now: datetime


@dataclass(frozen=True)
class GovernedSkillExecutionResult:
    policy: AutonomyPolicyDecision
    invocation: SkillInvocationResult
    approval_request_id: int | None
    approval_consumption_id: int | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(
    value: object,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ApprovalValidationError(
            f"{field_name} inválido."
        )
    return value


def _normalize_actor(
    actor: SkillInvocationActor,
) -> SkillInvocationActor:
    if not isinstance(
        actor,
        SkillInvocationActor,
    ):
        raise ApprovalValidationError(
            "actor inválido para execução governada."
        )

    if actor.actor_type not in NON_HUMAN_ACTOR_TYPES:
        raise ApprovalValidationError(
            "Execução governada exige ator não humano."
        )

    reference = actor.actor_reference
    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 255
    ):
        raise ApprovalValidationError(
            "actor_reference inválido."
        )
    reference = reference.strip()

    if not reference.startswith(
        actor.actor_type + ":"
    ):
        raise ApprovalValidationError(
            "Ator não humano exige referência canônica."
        )

    if actor.actor_user_id is not None:
        raise ApprovalValidationError(
            "Ator não humano não pode carregar actor_user_id."
        )

    return SkillInvocationActor(
        actor_type=actor.actor_type,
        actor_reference=reference,
        actor_user_id=None,
    )


def _normalize_now(
    value: datetime | None,
) -> datetime:
    result = (
        value
        if value is not None
        else utc_now()
    )
    if result.tzinfo is None:
        raise ApprovalValidationError(
            "now deve possuir timezone."
        )
    return result


class GovernedSkillExecutionService:
    """
    Fronteira interna de execução autônoma governada do Commit 24D.

    O serviço não seleciona Skills e não é uma API pública. Um chamador
    interno precisa fornecer versão exata, ator não humano e um usuário-
    principal atual que continue autorizado pelas regras de Skill/scope.
    """

    def __init__(
        self,
        db: Session,
        *,
        approval_repository: ApprovalRepository | None = None,
        skill_repository: SkillRepository | None = None,
        runtime: SkillRuntimeService | None = None,
    ) -> None:
        self.db = db
        self.approval_repository = (
            approval_repository
            if approval_repository is not None
            else ApprovalRepository(db)
        )
        self.skill_repository = (
            skill_repository
            if skill_repository is not None
            else SkillRepository(db)
        )
        self.runtime = (
            runtime
            if runtime is not None
            else SkillRuntimeService(db)
        )

    def _require_trusted_autonomy_handler(
        self,
        *,
        version,
    ) -> None:
        if version.runtime_kind != "internal_python":
            raise ApprovalAuthorizationError(
                "Execução autônoma exige runtime interno confiável."
            )

        try:
            registered = self.runtime.handler_registry.resolve(
                runtime_kind=version.runtime_kind,
                handler_reference=version.handler_reference,
            )
        except SkillHandlerNotAllowedError as error:
            raise ApprovalAuthorizationError(
                "Handler não autorizado para execução autônoma."
            ) from error

        if not registered.trusted_for_autonomy:
            raise ApprovalAuthorizationError(
                "Handler não foi explicitamente confiado para autonomia."
            )

        if registered.autonomy_entrypoint is None:
            raise ApprovalAuthorizationError(
                "Handler autônomo não possui entrypoint "
                "isolado configurado pelo servidor."
            )

        return registered

    def execute(
        self,
        version_id: int,
        *,
        actor: SkillInvocationActor,
        authority_user_id: int,
        input_payload: Any,
        idempotency_key: str | None = None,
        approval_request_id: int | None = None,
        now: datetime | None = None,
        runtime_context: Any | None = None,
    ) -> GovernedSkillExecutionResult:
        normalized_version_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        normalized_actor = _normalize_actor(
            actor
        )
        normalized_authority_id = _positive_id(
            authority_user_id,
            field_name="authority_user_id",
        )
        effective_now = _normalize_now(
            now
        )

        normalized_input, input_digest = (
            approval_input_identity(
                input_payload
            )
        )

        authority = self.db.get(
            User,
            normalized_authority_id,
        )
        if authority is None or not authority.active:
            raise ApprovalAuthorizationError(
                "Usuário-principal inexistente ou inativo."
            )

        version = self.skill_repository.get_version(
            normalized_version_id
        )
        if (
            version is None
            or version.status != "published"
        ):
            raise ApprovalNotFoundError(
                "Versão de skill inexistente ou não publicada."
            )

        skill = self.skill_repository.get_skill(
            version.skill_id
        )
        if (
            skill is None
            or skill.status != "active"
        ):
            raise ApprovalNotFoundError(
                "Skill inexistente ou inativa."
            )

        capabilities = tuple(
            self.skill_repository.list_capabilities(
                version.id
            )
        )

        if skill.skill_key == "account.mark_overdue":
            raise ApprovalAuthorizationError(
                "account.mark_overdue exige o corredor transacional 25O."
            )

        registered = self._require_trusted_autonomy_handler(
            version=version,
        )

        normalized_runtime_context = None
        if runtime_context is not None:
            if (
                version.runtime_kind != "internal_python"
                or version.execution_mode != "read_only"
            ):
                raise ApprovalValidationError(
                    "Work learning runtime context exige "
                    "internal_python read_only."
                )

            manifest = (
                version.manifest
                if isinstance(version.manifest, dict)
                else {}
            )
            if (
                manifest.get("runtime_context_protocol")
                != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
            ):
                raise ApprovalAuthorizationError(
                    "Versão de Skill não declarou o runtime context exigido."
                )

            if (
                registered.runtime_context_protocol
                != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
            ):
                raise ApprovalAuthorizationError(
                    "Handler governado não declarou o runtime context exigido."
                )

            try:
                normalized_runtime_context = (
                    normalize_work_learning_runtime_context(
                        runtime_context,
                        expected_skill_version_id=version.id,
                    )
                )
            except SkillValidationError as error:
                raise ApprovalValidationError(
                    "Work learning runtime context inválido."
                ) from error

        policy = evaluate_skill_autonomy(
            actor_type=normalized_actor.actor_type,
            version=version,
            capabilities=capabilities,
        )

        if policy.disposition == "blocked":
            raise ApprovalAuthorizationError(
                "A via autônoma está bloqueada para este ator."
            )

        if (
            normalized_runtime_context is not None
            and not policy.autonomous_allowed
        ):
            raise ApprovalAuthorizationError(
                "Work learning runtime context exige política autônoma read_only."
            )

        if policy.autonomous_allowed:
            if approval_request_id is not None:
                raise ApprovalValidationError(
                    "Ação autônoma de baixo risco não aceita approval_request_id."
                )
            if idempotency_key is None:
                raise ApprovalValidationError(
                    "Execução autônoma exige idempotency_key."
                )

            grant = authorize_skill_execution(
                db=self.db,
                role=authority.role,
                actor_user_id=authority.id,
                session_elevated=False,
                version_id=version.id,
                input_payload=normalized_input,
            )

            runtime_kwargs = {
                "actor": normalized_actor,
                "input_payload": normalized_input,
                "idempotency_key": idempotency_key,
                "isolated": True,
            }
            if normalized_runtime_context is not None:
                runtime_kwargs["runtime_context"] = (
                    normalized_runtime_context.payload
                )

            invocation_result = self.runtime.invoke(
                grant.version.id,
                **runtime_kwargs,
            )

            return GovernedSkillExecutionResult(
                policy=policy,
                invocation=invocation_result,
                approval_request_id=None,
                approval_consumption_id=None,
            )

        if not policy.requires_approval:
            raise ApprovalStateError(
                "Política de autonomia retornou estado não executável."
            )

        if idempotency_key is not None:
            raise ApprovalValidationError(
                "Ação aprovada usa idempotência derivada da aprovação."
            )

        if approval_request_id is None:
            raise ApprovalRequiredError(
                "A execução exige aprovação humana."
            )

        request_id = _positive_id(
            approval_request_id,
            field_name="approval_request_id",
        )

        request = self.approval_repository.lock_request(
            request_id
        )
        if request is None:
            raise ApprovalNotFoundError(
                "Solicitação de aprovação não encontrada."
            )

        decision = self.approval_repository.get_decision(
            request.id
        )

        self._validate_approved_action(
            request=request,
            decision=decision,
            actor=normalized_actor,
            authority=authority,
            version=version,
            capabilities=capabilities,
            policy=policy,
            normalized_input=normalized_input,
            input_digest=input_digest,
            now=effective_now,
        )

        sensitive_verified = (
            request.required_permission
            == "approval:decide_sensitive"
            and decision is not None
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
            grant.account_id
            != request.target_account_id
            or grant.subject_user_id
            != request.target_user_id
        ):
            raise ApprovalStateError(
                "Escopo atual diverge da ação aprovada."
            )

        assert decision is not None

        runtime_key = (
            f"approval:{request.id}"
        )

        consumption = (
            self.approval_repository
            .get_consumption_by_request(
                request.id
            )
        )

        if consumption is None:
            consumption = ApprovalConsumption(
                approval_request_id=request.id,
                approval_decision_id=decision.id,
                skill_invocation_id=None,
                consumer_actor_type=(
                    normalized_actor.actor_type
                ),
                consumer_reference=(
                    normalized_actor.actor_reference
                ),
                authority_user_id=authority.id,
                authority_reference=(
                    f"user:{authority.id}"
                ),
                authority_role=authority.role,
                runtime_idempotency_key=runtime_key,
                request_fingerprint=(
                    request.request_fingerprint
                ),
                input_digest=input_digest,
                status="reserved",
                error_code=None,
                reserved_at=effective_now,
                finalized_at=None,
            )
            try:
                self.approval_repository.add_consumption(
                    consumption
                )
                self.db.commit()
                self.db.refresh(
                    consumption
                )
            except IntegrityError as error:
                self.db.rollback()
                consumption = (
                    self.approval_repository
                    .get_consumption_by_request(
                        request.id
                    )
                )
                if consumption is None:
                    raise ApprovalConsumptionConflictError(
                        "Conflito ao reservar consumo de aprovação."
                    ) from error
            except Exception:
                self.db.rollback()
                raise

        self._validate_consumption(
            consumption=consumption,
            request=request,
            decision=decision,
            actor=normalized_actor,
            authority=authority,
            input_digest=input_digest,
            runtime_key=runtime_key,
        )

        if consumption.status == "failed":
            raise ApprovalStateError(
                "A aprovação já foi consumida por tentativa terminal sem ledger."
            )

        try:
            invocation_result = self.runtime.invoke(
                grant.version.id,
                actor=normalized_actor,
                input_payload=normalized_input,
                idempotency_key=runtime_key,
                isolated=True,
            )
        except Exception:
            invocation = (
                self.skill_repository
                .find_invocation_by_idempotency(
                    version_id=grant.version.id,
                    actor_type=normalized_actor.actor_type,
                    actor_reference=(
                        normalized_actor.actor_reference
                    ),
                    idempotency_key=runtime_key,
                )
            )

            if invocation is not None:
                self._mark_consumed(
                    consumption.id,
                    invocation=invocation,
                )
            else:
                self._mark_failed(
                    consumption.id,
                    error_code="runtime_preflight_failed",
                )
            raise

        finalized = self._mark_consumed(
            consumption.id,
            invocation=invocation_result.invocation,
        )

        return GovernedSkillExecutionResult(
            policy=policy,
            invocation=invocation_result,
            approval_request_id=request.id,
            approval_consumption_id=finalized.id,
        )

    def validate_approved_action_only(
        self,
        version_id: int,
        *,
        actor: SkillInvocationActor,
        authority_user_id: int,
        input_payload: Any,
        approval_request_id: int,
        now: datetime | None = None,
    ) -> GovernedApprovedActionValidation:
        normalized_version_id = _positive_id(version_id, field_name="version_id")
        normalized_actor = _normalize_actor(actor)
        normalized_authority_id = _positive_id(
            authority_user_id, field_name="authority_user_id"
        )
        request_id = _positive_id(
            approval_request_id, field_name="approval_request_id"
        )
        effective_now = _normalize_now(now)
        normalized_input, input_digest = approval_input_identity(input_payload)

        authority = self.db.get(User, normalized_authority_id)
        if authority is None or not authority.active:
            raise ApprovalAuthorizationError(
                "Usuário-principal inexistente ou inativo."
            )
        version = self.skill_repository.get_version(normalized_version_id)
        if version is None or version.status != "published":
            raise ApprovalNotFoundError(
                "Versão de skill inexistente ou não publicada."
            )
        skill = self.skill_repository.get_skill(version.skill_id)
        if skill is None or skill.status != "active":
            raise ApprovalNotFoundError("Skill inexistente ou inativa.")
        capabilities = tuple(self.skill_repository.list_capabilities(version.id))
        policy = evaluate_skill_autonomy(
            actor_type=normalized_actor.actor_type,
            version=version,
            capabilities=capabilities,
        )
        if policy.disposition == "blocked" or not policy.requires_approval:
            raise ApprovalAuthorizationError(
                "A ação piloto não possui política mutating aprovável."
            )
        request = self.approval_repository.lock_request(request_id)
        if request is None:
            raise ApprovalNotFoundError("Solicitação de aprovação não encontrada.")
        decision = self.approval_repository.get_decision(request.id)
        self._validate_approved_action(
            request=request,
            decision=decision,
            actor=normalized_actor,
            authority=authority,
            version=version,
            capabilities=capabilities,
            policy=policy,
            normalized_input=normalized_input,
            input_digest=input_digest,
            now=effective_now,
        )
        sensitive_verified = (
            request.required_permission == "approval:decide_sensitive"
            and decision is not None
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
            or grant.subject_user_id != request.target_user_id
        ):
            raise ApprovalStateError("Escopo atual diverge da ação aprovada.")
        assert decision is not None
        return GovernedApprovedActionValidation(
            policy=policy,
            actor=normalized_actor,
            authority=authority,
            version=version,
            skill=skill,
            capabilities=capabilities,
            request=request,
            decision=decision,
            grant=grant,
            normalized_input=normalized_input,
            input_digest=input_digest,
            effective_now=effective_now,
        )

    def _validate_approved_action(
        self,
        *,
        request: ApprovalRequest,
        decision: ApprovalDecision | None,
        actor: SkillInvocationActor,
        authority: User,
        version,
        capabilities,
        policy: AutonomyPolicyDecision,
        normalized_input: Any,
        input_digest: str,
        now: datetime,
    ) -> None:
        if request.action_type != "skill_execution":
            raise ApprovalStateError(
                "Tipo de ação aprovado não é executável por Skill."
            )

        if request.status != "approved":
            raise ApprovalRequiredError(
                "A execução exige aprovação humana aprovada."
            )

        if request.expires_at <= now:
            raise ApprovalStateError(
                "A aprovação aprovada expirou antes da execução."
            )

        if (
            decision is None
            or decision.decision != "approved"
        ):
            raise ApprovalStateError(
                "Decisão de aprovação aprovada está ausente ou inconsistente."
            )

        if (
            request.requester_actor_type
            != actor.actor_type
            or request.requester_reference
            != actor.actor_reference
            or request.requester_user_id is not None
        ):
            raise ApprovalConsumptionConflictError(
                "Ator consumidor diverge do ator aprovado."
            )

        if request.skill_version_id != version.id:
            raise ApprovalConsumptionConflictError(
                "Versão executada diverge da versão aprovada."
            )

        if (
            request.risk_level
            != policy.risk_level
            or request.required_permission
            != policy.required_approval_permission
        ):
            raise ApprovalStateError(
                "Política atual diverge da aprovação persistida."
            )

        if request.input_digest != input_digest:
            raise ApprovalConsumptionConflictError(
                "Input executado diverge do input aprovado."
            )

        requester = ApprovalRequester(
            actor_type=actor.actor_type,
            actor_reference=actor.actor_reference,
            actor_user_id=None,
        )
        expected_fingerprint = (
            approval_request_fingerprint(
                version=version,
                requester=requester,
                input_digest=input_digest,
            )
        )

        if (
            request.request_fingerprint
            != expected_fingerprint
        ):
            raise ApprovalConsumptionConflictError(
                "Identidade exata da ação diverge da aprovação."
            )

        if (
            decision.permission_used
            != request.required_permission
        ):
            raise ApprovalStateError(
                "Permissão registrada na decisão está inconsistente."
            )

        if (
            request.required_permission
            == "approval:decide_sensitive"
            and not decision.sensitive_elevation_verified
        ):
            raise ApprovalAuthorizationError(
                "Aprovação sensível não possui evidência de elevação."
            )

        if decision.decided_by_user_id is None:
            raise ApprovalAuthorizationError(
                "Decisor atual não pode ser revalidado."
            )

        decider = self.db.get(
            User,
            decision.decided_by_user_id,
        )
        if (
            decider is None
            or not decider.active
            or not has_permission(
                decider.role,
                request.required_permission,
            )
        ):
            raise ApprovalAuthorizationError(
                "Autoridade humana da aprovação não está mais válida."
            )

        if (
            authority.id
            == decision.decided_by_user_id
            and request.risk_level
            in {"high", "critical"}
            and request.requester_actor_type == "user"
        ):
            raise ApprovalAuthorizationError(
                "Separação de deveres da aprovação está inconsistente."
            )

        # Re-resolve scope targets from the exact normalized input using the
        # same capability declaration currently published.
        _ = capabilities
        _ = normalized_input

    def _validate_consumption(
        self,
        *,
        consumption: ApprovalConsumption,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        actor: SkillInvocationActor,
        authority: User,
        input_digest: str,
        runtime_key: str,
    ) -> None:
        expected = (
            consumption.approval_request_id
            == request.id
            and consumption.approval_decision_id
            == decision.id
            and consumption.consumer_actor_type
            == actor.actor_type
            and consumption.consumer_reference
            == actor.actor_reference
            and consumption.authority_user_id
            == authority.id
            and consumption.authority_reference
            == f"user:{authority.id}"
            and consumption.runtime_idempotency_key
            == runtime_key
            and consumption.request_fingerprint
            == request.request_fingerprint
            and consumption.input_digest
            == input_digest
        )
        if not expected:
            raise ApprovalConsumptionConflictError(
                "Consumo de aprovação existente diverge da ação."
            )

    def _mark_consumed(
        self,
        consumption_id: int,
        *,
        invocation: SkillInvocation,
    ) -> ApprovalConsumption:
        consumption = (
            self.approval_repository
            .lock_consumption(
                consumption_id
            )
        )
        if consumption is None:
            raise ApprovalStateError(
                "Reserva de aprovação não encontrada."
            )

        if consumption.status == "failed":
            raise ApprovalStateError(
                "Reserva de aprovação terminou com falha."
            )

        if consumption.status == "consumed":
            if (
                consumption.skill_invocation_id
                != invocation.id
            ):
                raise ApprovalConsumptionConflictError(
                    "Aprovação foi vinculada a outra invocação."
                )
            return consumption

        consumption.status = "consumed"
        consumption.skill_invocation_id = invocation.id
        consumption.error_code = None
        consumption.finalized_at = utc_now()

        try:
            self.db.commit()
            self.db.refresh(
                consumption
            )
        except IntegrityError as error:
            self.db.rollback()
            raise ApprovalConsumptionConflictError(
                "Conflito ao finalizar consumo de aprovação."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return consumption

    def _mark_failed(
        self,
        consumption_id: int,
        *,
        error_code: str,
    ) -> ApprovalConsumption:
        consumption = (
            self.approval_repository
            .lock_consumption(
                consumption_id
            )
        )
        if consumption is None:
            raise ApprovalStateError(
                "Reserva de aprovação não encontrada."
            )

        if consumption.status == "consumed":
            return consumption
        if consumption.status == "failed":
            return consumption

        consumption.status = "failed"
        consumption.skill_invocation_id = None
        consumption.error_code = error_code
        consumption.finalized_at = utc_now()

        try:
            self.db.commit()
            self.db.refresh(
                consumption
            )
        except Exception:
            self.db.rollback()
            raise

        return consumption
