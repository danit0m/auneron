import json
import math
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from hashlib import sha256
from typing import Any
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalElevationRequiredError
from app.core.approval_errors import ApprovalExpiredError
from app.core.approval_errors import ApprovalIdempotencyConflictError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.autonomy_policy import classify_skill_risk
from app.core.authorization import has_permission
from app.core.config import settings
from app.models.account import Account
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.user import User
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository


ApprovalActorType = Literal[
    "user",
    "agent",
    "system",
    "integration",
]

ApprovalDecisionValue = Literal[
    "approved",
    "rejected",
]

MAX_APPROVAL_INPUT_BYTES = 64 * 1024
MAX_APPROVAL_JSON_DEPTH = 32


@dataclass(frozen=True)
class ApprovalRequester:
    actor_type: ApprovalActorType
    actor_reference: str
    actor_user_id: int | None = None


@dataclass(frozen=True)
class ApprovalCreationResult:
    request: ApprovalRequest
    duplicate: bool


@dataclass(frozen=True)
class ApprovalDecisionResult:
    request: ApprovalRequest
    decision: ApprovalDecision


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


def _bounded_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ApprovalValidationError(
            f"{field_name} deve ser texto."
        )

    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(
            ord(character) < 32
            for character in normalized
        )
    ):
        raise ApprovalValidationError(
            f"{field_name} inválido."
        )

    return normalized


def _optional_note(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    return _bounded_text(
        value,
        field_name="decision_note",
        max_length=500,
    )


def _validate_json_value(
    value: Any,
    *,
    depth: int = 0,
) -> None:
    if depth > MAX_APPROVAL_JSON_DEPTH:
        raise ApprovalValidationError(
            "input_payload excede a profundidade permitida."
        )

    if value is None or isinstance(
        value,
        (
            str,
            bool,
            int,
        ),
    ):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ApprovalValidationError(
                "input_payload contém número não finito."
            )
        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(
                item,
                depth=depth + 1,
            )
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ApprovalValidationError(
                    "input_payload exige chaves de objeto em texto."
                )
            _validate_json_value(
                item,
                depth=depth + 1,
            )
        return

    raise ApprovalValidationError(
        "input_payload contém tipo não suportado por JSON."
    )


def _canonical_json(
    value: Any,
) -> tuple[Any, bytes]:
    _validate_json_value(value)

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ApprovalValidationError(
            "input_payload não pode ser serializado com segurança."
        ) from error

    if len(serialized) > MAX_APPROVAL_INPUT_BYTES:
        raise ApprovalValidationError(
            "input_payload excede o limite de 64 KiB."
        )

    normalized = json.loads(
        serialized.decode("utf-8")
    )
    return normalized, serialized


def _digest(
    value: bytes,
) -> str:
    return sha256(value).hexdigest()


def _request_fingerprint(
    *,
    version: SkillVersion,
    requester: ApprovalRequester,
    input_digest: str,
) -> str:
    material = {
        "action_type": "skill_execution",
        "skill_version_id": version.id,
        "manifest_digest": version.manifest_digest,
        "requester_actor_type": requester.actor_type,
        "requester_reference": requester.actor_reference,
        "requester_user_id": requester.actor_user_id,
        "input_digest": input_digest,
    }
    serialized = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _digest(serialized)



def approval_input_identity(
    value: Any,
) -> tuple[Any, str]:
    normalized, serialized = _canonical_json(
        value
    )
    return normalized, _digest(
        serialized
    )


def approval_request_fingerprint(
    *,
    version: SkillVersion,
    requester: ApprovalRequester,
    input_digest: str,
) -> str:
    return _request_fingerprint(
        version=version,
        requester=requester,
        input_digest=input_digest,
    )

def _required_scope_id(
    payload: Any,
    *,
    field_name: str,
) -> int:
    if not isinstance(payload, dict):
        raise ApprovalValidationError(
            "Skill com escopo account/user exige "
            "input_payload em formato de objeto JSON."
        )

    if field_name not in payload:
        raise ApprovalValidationError(
            f"{field_name} é obrigatório para "
            "o escopo declarado pela skill."
        )

    return _positive_id(
        payload[field_name],
        field_name=field_name,
    )


def _reject_unbound_reserved_field(
    payload: Any,
    *,
    field_name: str,
) -> None:
    if (
        isinstance(payload, dict)
        and field_name in payload
    ):
        raise ApprovalValidationError(
            f"{field_name} é campo reservado e exige "
            "capability de escopo correspondente."
        )


class ApprovalService:
    """
    Fundação persistente de aprovação do Commit 24A.

    Esta camada registra e decide solicitações exatas. Ela não executa
    Skills, não cria SkillInvocation e não transforma Work, Memory,
    binding ou conteúdo gerado por modelo em autoridade.
    """

    def __init__(
        self,
        db: Session,
        repository: ApprovalRepository | None = None,
        skill_repository: SkillRepository | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else ApprovalRepository(db)
        )
        self.skill_repository = (
            skill_repository
            if skill_repository is not None
            else SkillRepository(db)
        )

    def create_skill_execution_request(
        self,
        *,
        version_id: int,
        requester: ApprovalRequester,
        input_payload: Any,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ApprovalCreationResult:
        normalized_version_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        normalized_requester = self._normalize_requester(
            requester
        )
        normalized_idempotency_key = _bounded_text(
            idempotency_key,
            field_name="idempotency_key",
            max_length=255,
        )
        normalized_input, input_bytes = _canonical_json(
            input_payload
        )

        version, skill = self._require_published_version(
            normalized_version_id
        )
        capabilities = tuple(
            self.skill_repository.list_capabilities(
                version.id
            )
        )

        risk_level, required_permission = classify_skill_risk(
            version=version,
            capabilities=capabilities,
        )

        target_account_id, target_user_id = (
            self._resolve_scope_targets(
                payload=normalized_input,
                capabilities=capabilities,
            )
        )

        input_digest = _digest(
            input_bytes
        )
        request_fingerprint = _request_fingerprint(
            version=version,
            requester=normalized_requester,
            input_digest=input_digest,
        )

        existing = (
            self.repository.find_request_by_idempotency(
                requester_actor_type=(
                    normalized_requester.actor_type
                ),
                requester_reference=(
                    normalized_requester.actor_reference
                ),
                idempotency_key=normalized_idempotency_key,
            )
        )

        if existing is not None:
            if (
                existing.request_fingerprint
                != request_fingerprint
            ):
                raise ApprovalIdempotencyConflictError(
                    "Chave idempotente conflita com outra ação."
                )
            return ApprovalCreationResult(
                request=existing,
                duplicate=True,
            )

        effective_now = (
            now
            if now is not None
            else utc_now()
        )
        if effective_now.tzinfo is None:
            raise ApprovalValidationError(
                "now deve possuir timezone."
            )

        request = ApprovalRequest(
            action_type="skill_execution",
            skill_version_id=version.id,
            requester_actor_type=(
                normalized_requester.actor_type
            ),
            requester_reference=(
                normalized_requester.actor_reference
            ),
            requester_user_id=(
                normalized_requester.actor_user_id
            ),
            idempotency_key=normalized_idempotency_key,
            request_fingerprint=request_fingerprint,
            input_digest=input_digest,
            risk_level=risk_level,
            required_permission=required_permission,
            status="pending",
            target_account_id=target_account_id,
            target_user_id=target_user_id,
            expires_at=(
                effective_now
                + timedelta(
                    minutes=(
                        settings.approval_request_ttl_minutes
                    )
                )
            ),
            resolved_at=None,
            created_at=effective_now,
        )

        try:
            self.repository.add_request(
                request
            )
            self.db.commit()
            self.db.refresh(
                request
            )
        except IntegrityError as error:
            self.db.rollback()

            existing = (
                self.repository.find_request_by_idempotency(
                    requester_actor_type=(
                        normalized_requester.actor_type
                    ),
                    requester_reference=(
                        normalized_requester.actor_reference
                    ),
                    idempotency_key=normalized_idempotency_key,
                )
            )
            if (
                existing is not None
                and existing.request_fingerprint
                == request_fingerprint
            ):
                return ApprovalCreationResult(
                    request=existing,
                    duplicate=True,
                )
            raise ApprovalIdempotencyConflictError(
                "Conflito ao registrar solicitação de aprovação."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        # `skill` is intentionally resolved as part of the executable
        # catalog state, but it grants no approval authority.
        _ = skill

        return ApprovalCreationResult(
            request=request,
            duplicate=False,
        )

    def decide(
        self,
        request_id: int,
        *,
        decider_user_id: int,
        decision: ApprovalDecisionValue,
        decision_note: str | None = None,
        sensitive_elevation_verified: bool = False,
        now: datetime | None = None,
    ) -> ApprovalDecisionResult:
        normalized_request_id = _positive_id(
            request_id,
            field_name="request_id",
        )
        normalized_decider_id = _positive_id(
            decider_user_id,
            field_name="decider_user_id",
        )
        if decision not in {
            "approved",
            "rejected",
        }:
            raise ApprovalValidationError(
                "decision inválida."
            )
        if not isinstance(
            sensitive_elevation_verified,
            bool,
        ):
            raise ApprovalValidationError(
                "sensitive_elevation_verified deve ser booleano."
            )

        normalized_note = _optional_note(
            decision_note
        )

        effective_now = (
            now
            if now is not None
            else utc_now()
        )
        if effective_now.tzinfo is None:
            raise ApprovalValidationError(
                "now deve possuir timezone."
            )

        decider = self.db.get(
            User,
            normalized_decider_id,
        )
        if decider is None or not decider.active:
            raise ApprovalAuthorizationError(
                "Decisor inexistente ou inativo."
            )

        request = self.repository.lock_request(
            normalized_request_id
        )
        if request is None:
            raise ApprovalNotFoundError(
                "Solicitação de aprovação não encontrada."
            )

        if request.status != "pending":
            raise ApprovalStateError(
                "Solicitação de aprovação já está em estado terminal."
            )

        if request.expires_at <= effective_now:
            request.status = "expired"
            request.resolved_at = effective_now
            try:
                self.db.commit()
                self.db.refresh(
                    request
                )
            except Exception:
                self.db.rollback()
                raise
            raise ApprovalExpiredError(
                "Solicitação de aprovação expirou."
            )

        if not has_permission(
            decider.role,
            request.required_permission,
        ):
            raise ApprovalAuthorizationError(
                "Decisor sem autoridade para esta aprovação."
            )

        if (
            request.required_permission
            == "approval:decide_sensitive"
            and not sensitive_elevation_verified
        ):
            raise ApprovalElevationRequiredError(
                "Aprovação sensível exige autenticação elevada."
            )

        if (
            request.risk_level in {
                "high",
                "critical",
            }
            and request.requester_actor_type == "user"
            and request.requester_user_id
            == decider.id
        ):
            raise ApprovalAuthorizationError(
                "Ações sensíveis exigem separação entre "
                "solicitante e decisor."
            )

        decision_record = ApprovalDecision(
            approval_request_id=request.id,
            decision=decision,
            decided_by_user_id=decider.id,
            decided_by_reference=f"user:{decider.id}",
            decided_by_role=decider.role,
            permission_used=request.required_permission,
            decision_note=normalized_note,
            sensitive_elevation_verified=(
                sensitive_elevation_verified
            ),
            created_at=effective_now,
        )
        request.status = decision
        request.resolved_at = effective_now

        try:
            self.repository.add_decision(
                decision_record
            )
            self.db.commit()
            self.db.refresh(
                request
            )
            self.db.refresh(
                decision_record
            )
        except IntegrityError as error:
            self.db.rollback()
            raise ApprovalStateError(
                "Solicitação já possui decisão terminal."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return ApprovalDecisionResult(
            request=request,
            decision=decision_record,
        )

    def get_request(
        self,
        request_id: int,
    ) -> ApprovalRequest:
        normalized_request_id = _positive_id(
            request_id,
            field_name="request_id",
        )
        request = self.repository.get_request(
            normalized_request_id
        )
        if request is None:
            raise ApprovalNotFoundError(
                "Solicitação de aprovação não encontrada."
            )
        return request

    def get_decision(
        self,
        request_id: int,
    ) -> ApprovalDecision | None:
        normalized_request_id = _positive_id(
            request_id,
            field_name="request_id",
        )
        return self.repository.get_decision(
            normalized_request_id
        )


    def list_requests(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        risk_levels: tuple[str, ...] | None = None,
        required_permissions: tuple[str, ...] | None = None,
        after_id: int | None = None,
        limit: int = 51,
    ) -> list[ApprovalRequest]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 101
        ):
            raise ApprovalValidationError(
                "limit inválido."
            )

        normalized_after_id = (
            None
            if after_id is None
            else _positive_id(
                after_id,
                field_name="after_id",
            )
        )

        allowed_statuses = {
            "pending",
            "approved",
            "rejected",
            "expired",
            "cancelled",
        }
        allowed_risk_levels = {
            "low",
            "medium",
            "high",
            "critical",
        }
        allowed_permissions = {
            "approval:decide",
            "approval:decide_sensitive",
        }

        if (
            statuses is not None
            and (
                not statuses
                or any(
                    status not in allowed_statuses
                    for status in statuses
                )
            )
        ):
            raise ApprovalValidationError(
                "status de aprovação inválido."
            )

        if (
            risk_levels is not None
            and (
                not risk_levels
                or any(
                    risk not in allowed_risk_levels
                    for risk in risk_levels
                )
            )
        ):
            raise ApprovalValidationError(
                "risk_level inválido."
            )

        if (
            required_permissions is not None
            and any(
                permission not in allowed_permissions
                for permission in required_permissions
            )
        ):
            raise ApprovalValidationError(
                "required_permissions inválido."
            )

        return self.repository.list_requests(
            statuses=statuses,
            risk_levels=risk_levels,
            required_permissions=(
                required_permissions
            ),
            after_id=normalized_after_id,
            limit=limit,
        )
    def _normalize_requester(
        self,
        requester: ApprovalRequester,
    ) -> ApprovalRequester:
        if not isinstance(
            requester,
            ApprovalRequester,
        ):
            raise ApprovalValidationError(
                "requester inválido."
            )

        if requester.actor_type not in {
            "user",
            "agent",
            "system",
            "integration",
        }:
            raise ApprovalValidationError(
                "requester.actor_type inválido."
            )

        reference = _bounded_text(
            requester.actor_reference,
            field_name="requester.actor_reference",
            max_length=255,
        )

        if requester.actor_type == "user":
            user_id = _positive_id(
                requester.actor_user_id,
                field_name="requester.actor_user_id",
            )
            if reference != f"user:{user_id}":
                raise ApprovalValidationError(
                    "requester de usuário exige referência canônica."
                )

            user = self.db.get(
                User,
                user_id,
            )
            if user is None or not user.active:
                raise ApprovalValidationError(
                    "Usuário solicitante inexistente ou inativo."
                )

            return ApprovalRequester(
                actor_type="user",
                actor_reference=reference,
                actor_user_id=user_id,
            )

        if requester.actor_user_id is not None:
            raise ApprovalValidationError(
                "Ator não humano não pode carregar actor_user_id."
            )

        return ApprovalRequester(
            actor_type=requester.actor_type,
            actor_reference=reference,
            actor_user_id=None,
        )

    def _require_published_version(
        self,
        version_id: int,
    ) -> tuple[
        SkillVersion,
        SkillDefinition,
    ]:
        version = self.skill_repository.get_version(
            version_id
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

        return version, skill

    def _resolve_scope_targets(
        self,
        *,
        payload: Any,
        capabilities: tuple[
            SkillCapability,
            ...,
        ],
    ) -> tuple[
        int | None,
        int | None,
    ]:
        account_scoped = any(
            capability.resource_scope == "account"
            for capability in capabilities
        )
        user_scoped = any(
            capability.resource_scope == "user"
            for capability in capabilities
        )

        if account_scoped:
            account_id = _required_scope_id(
                payload,
                field_name="account_id",
            )
            if self.db.get(
                Account,
                account_id,
            ) is None:
                raise ApprovalNotFoundError(
                    "Conta alvo inexistente."
                )
        else:
            _reject_unbound_reserved_field(
                payload,
                field_name="account_id",
            )
            account_id = None

        if user_scoped:
            target_user_id = _required_scope_id(
                payload,
                field_name="subject_user_id",
            )
            target_user = self.db.get(
                User,
                target_user_id,
            )
            if (
                target_user is None
                or not target_user.active
            ):
                raise ApprovalNotFoundError(
                    "Usuário alvo inexistente ou inativo."
                )
        else:
            _reject_unbound_reserved_field(
                payload,
                field_name="subject_user_id",
            )
            target_user_id = None

        return (
            account_id,
            target_user_id,
        )
