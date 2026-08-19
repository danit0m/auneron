import hashlib
import json
import re
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.skill_errors import SkillConflictError
from app.core.skill_errors import SkillImmutableError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.models.skill import AgentSkillBinding
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.repositories.skill_repository import SkillRepository


SKILL_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,127}$"
)
CAPABILITY_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,159}$"
)
INTERNAL_HANDLER_PATTERN = re.compile(
    r"^app\.skills(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+"
    r":[a-zA-Z_][a-zA-Z0-9_]*$"
)
PLUGIN_HANDLER_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,127}:"
    r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,190}$"
)

RUNTIME_KINDS = frozenset({
    "internal_python",
    "plugin",
})
EXECUTION_MODES = frozenset({
    "read_only",
    "mutating",
    "external",
})
ACCESS_MODES = frozenset({
    "read",
    "write",
    "execute",
})
RESOURCE_SCOPES = frozenset({
    "internal",
    "account",
    "user",
    "external",
})
SKILL_STATUSES = frozenset({
    "active",
    "disabled",
    "retired",
})

MAX_JSON_BYTES = 64 * 1024
MAX_CONFIGURATION_BYTES = 32 * 1024
MAX_JSON_DEPTH = 10
MAX_CAPABILITIES = 64

SENSITIVE_KEY_PARTS = frozenset({
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
})

IDENTITY_CONSTRAINTS = frozenset({
    "uq_skills_skill_key",
    "uq_skill_versions_skill_version",
    "uq_skill_versions_skill_digest",
    "uq_agent_skill_bindings_agent_version",
})


@dataclass(frozen=True)
class CapabilityInput:
    capability_key: str
    access_mode: str
    resource_scope: str
    required: bool = True


@dataclass(frozen=True)
class PublicationResult:
    version: SkillVersion
    capabilities: tuple[SkillCapability, ...]


@dataclass(frozen=True)
class ResolvedSkillBinding:
    binding: AgentSkillBinding
    skill: SkillDefinition
    version: SkillVersion
    capabilities: tuple[SkillCapability, ...]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise SkillValidationError(
            f"{field_name} deve ser texto."
        )
    normalized = value.strip()
    if not normalized:
        raise SkillValidationError(
            f"{field_name} não pode ser vazio."
        )
    if len(normalized) > max_length:
        raise SkillValidationError(
            f"{field_name} excede {max_length} caracteres."
        )
    return normalized


def _positive_id(
    value: int,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise SkillValidationError(
            f"{field_name} inválido."
        )
    return value


def _optional_positive_id(
    value: int | None,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None
    return _positive_id(
        value,
        field_name=field_name,
    )


def _bounded_integer(
    value: int,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise SkillValidationError(
            f"{field_name} deve estar entre "
            f"{minimum} e {maximum}."
        )
    return value


def _normalized_choice(
    value: str,
    *,
    field_name: str,
    allowed: frozenset[str],
) -> str:
    normalized = _required_text(
        value,
        field_name=field_name,
        max_length=32,
    ).lower()
    if normalized not in allowed:
        raise SkillValidationError(
            f"{field_name} inválido."
        )
    return normalized


def _json_depth(
    value: Any,
    *,
    current_depth: int = 1,
) -> int:
    if isinstance(value, dict):
        if not value:
            return current_depth
        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
            )
            for child in value.values()
        )
    if isinstance(value, list):
        if not value:
            return current_depth
        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
            )
            for child in value
        )
    return current_depth


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if any(
                part in normalized
                for part in SENSITIVE_KEY_PARTS
            ):
                return True
            if _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(
            _contains_sensitive_key(child)
            for child in value
        )
    return False


def _normalized_json_object(
    value: Mapping[str, Any] | None,
    *,
    field_name: str,
    max_bytes: int = MAX_JSON_BYTES,
    reject_sensitive_keys: bool = True,
) -> dict[str, Any]:
    if value is None:
        normalized_input: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        normalized_input = value
    else:
        raise SkillValidationError(
            f"{field_name} deve ser um objeto JSON."
        )
    try:
        serialized = json.dumps(
            normalized_input,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        normalized = json.loads(serialized)
    except (TypeError, ValueError) as error:
        raise SkillValidationError(
            f"{field_name} não é serializável em JSON."
        ) from error
    if not isinstance(normalized, dict):
        raise SkillValidationError(
            f"{field_name} deve ser um objeto JSON."
        )
    if len(serialized.encode("utf-8")) > max_bytes:
        raise SkillValidationError(
            f"{field_name} excede {max_bytes} bytes."
        )
    if _json_depth(normalized) > MAX_JSON_DEPTH:
        raise SkillValidationError(
            f"{field_name} excede profundidade JSON "
            f"{MAX_JSON_DEPTH}."
        )
    if (
        reject_sensitive_keys
        and _contains_sensitive_key(normalized)
    ):
        raise SkillValidationError(
            f"{field_name} contém chave sensível."
        )
    return normalized


def _canonical_digest(
    *,
    version: str,
    runtime_kind: str,
    handler_reference: str,
    execution_mode: str,
    manifest: dict[str, Any],
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    timeout_seconds: int,
    max_output_bytes: int,
) -> str:
    envelope = {
        "execution_mode": execution_mode,
        "handler_reference": handler_reference,
        "input_schema": input_schema,
        "manifest": manifest,
        "max_output_bytes": max_output_bytes,
        "output_schema": output_schema,
        "runtime_kind": runtime_kind,
        "timeout_seconds": timeout_seconds,
        "version": version,
    }
    serialized = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def _constraint_name(
    error: IntegrityError,
) -> str | None:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    return getattr(
        diagnostic,
        "constraint_name",
        None,
    )


class SkillService:
    """
    Fronteira transacional do catálogo de Agent Skills.

    Publicação congela o contrato executável e suas capabilities.
    Nenhum método desta classe executa handlers ou concede autoridade.
    """

    def __init__(
        self,
        db: Session,
        repository: SkillRepository | None = None,
    ) -> None:
        self.db = db
        self.repository = repository or SkillRepository(db)

    def register_skill(
        self,
        *,
        skill_key: str,
        provider: str,
        display_name: str,
        description: str,
        created_by_user_id: int | None = None,
    ) -> SkillDefinition:
        normalized_key = _required_text(
            skill_key,
            field_name="skill_key",
            max_length=128,
        ).lower()
        if not SKILL_KEY_PATTERN.fullmatch(normalized_key):
            raise SkillValidationError("skill_key inválida.")
        skill = SkillDefinition(
            skill_key=normalized_key,
            provider=_required_text(
                provider,
                field_name="provider",
                max_length=128,
            ),
            display_name=_required_text(
                display_name,
                field_name="display_name",
                max_length=160,
            ),
            description=_required_text(
                description,
                field_name="description",
                max_length=7000,
            ),
            status="active",
            created_by_user_id=_optional_positive_id(
                created_by_user_id,
                field_name="created_by_user_id",
            ),
        )
        try:
            if self.repository.find_skill_by_key(
                normalized_key,
                for_update=True,
            ) is not None:
                raise SkillConflictError(
                    "skill_key já registrada."
                )
            self.repository.add_skill(skill)
            self.db.commit()
            return skill
        except IntegrityError as error:
            self.db.rollback()
            self._raise_integrity_conflict(error)
        except Exception:
            self.db.rollback()
            raise

    def create_draft_version(
        self,
        *,
        skill_id: int,
        version: str,
        runtime_kind: str,
        handler_reference: str,
        execution_mode: str,
        manifest: Mapping[str, Any] | None = None,
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
        timeout_seconds: int = 30,
        max_output_bytes: int = 65536,
        created_by_user_id: int | None = None,
    ) -> SkillVersion:
        contract = self._normalized_contract(
            version=version,
            runtime_kind=runtime_kind,
            handler_reference=handler_reference,
            execution_mode=execution_mode,
            manifest=manifest,
            input_schema=input_schema,
            output_schema=output_schema,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        draft = SkillVersion(
            skill_id=_positive_id(
                skill_id,
                field_name="skill_id",
            ),
            **contract,
            status="draft",
            published_at=None,
            retired_at=None,
            created_by_user_id=_optional_positive_id(
                created_by_user_id,
                field_name="created_by_user_id",
            ),
        )
        try:
            skill = self.repository.lock_skill(draft.skill_id)
            if skill is None:
                raise SkillNotFoundError(
                    "Skill não encontrada."
                )
            if skill.status != "active":
                raise SkillStateError(
                    "A skill precisa estar ativa para criar versão."
                )
            self.repository.add_version(draft)
            self.db.commit()
            return draft
        except IntegrityError as error:
            self.db.rollback()
            self._raise_integrity_conflict(error)
        except Exception:
            self.db.rollback()
            raise

    def replace_draft_contract(
        self,
        version_id: int,
        *,
        version: str,
        runtime_kind: str,
        handler_reference: str,
        execution_mode: str,
        manifest: Mapping[str, Any] | None = None,
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
        timeout_seconds: int = 30,
        max_output_bytes: int = 65536,
    ) -> SkillVersion:
        normalized_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        contract = self._normalized_contract(
            version=version,
            runtime_kind=runtime_kind,
            handler_reference=handler_reference,
            execution_mode=execution_mode,
            manifest=manifest,
            input_schema=input_schema,
            output_schema=output_schema,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        try:
            draft = self.repository.lock_version(normalized_id)
            if draft is None:
                raise SkillNotFoundError(
                    "Versão de skill não encontrada."
                )
            self._require_draft(draft)
            for field_name, value in contract.items():
                setattr(draft, field_name, value)
            self.db.flush()
            self.db.commit()
            return draft
        except IntegrityError as error:
            self.db.rollback()
            self._raise_integrity_conflict(error)
        except Exception:
            self.db.rollback()
            raise

    def publish_version(
        self,
        version_id: int,
        *,
        capabilities: Sequence[CapabilityInput] = (),
        published_at: datetime | None = None,
    ) -> PublicationResult:
        normalized_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        normalized_capabilities = (
            self._normalized_capabilities(capabilities)
        )
        publication_time = self._aware_datetime(
            published_at or _utc_now(),
            field_name="published_at",
        )
        try:
            version = self.repository.lock_version(normalized_id)
            if version is None:
                raise SkillNotFoundError(
                    "Versão de skill não encontrada."
                )
            self._require_draft(version)
            skill = self.repository.lock_skill(version.skill_id)
            if skill is None:
                raise SkillNotFoundError(
                    "Skill não encontrada."
                )
            if skill.status != "active":
                raise SkillStateError(
                    "A skill precisa estar ativa para publicação."
                )
            existing = self.repository.list_capabilities(
                normalized_id
            )
            if existing:
                self.repository.delete_capabilities(existing)
            persisted: list[SkillCapability] = []
            for item in normalized_capabilities:
                capability = SkillCapability(
                    skill_version_id=normalized_id,
                    capability_key=item.capability_key,
                    access_mode=item.access_mode,
                    resource_scope=item.resource_scope,
                    required=item.required,
                )
                self.repository.add_capability(capability)
                persisted.append(capability)
            version.status = "published"
            version.published_at = publication_time
            version.retired_at = None
            self.db.flush()
            self.db.commit()
            return PublicationResult(
                version=version,
                capabilities=tuple(persisted),
            )
        except IntegrityError as error:
            self.db.rollback()
            self._raise_integrity_conflict(error)
        except Exception:
            self.db.rollback()
            raise

    def retire_version(
        self,
        version_id: int,
        *,
        retired_at: datetime | None = None,
    ) -> SkillVersion:
        normalized_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        retirement_time = self._aware_datetime(
            retired_at or _utc_now(),
            field_name="retired_at",
        )
        try:
            version = self.repository.lock_version(normalized_id)
            if version is None:
                raise SkillNotFoundError(
                    "Versão de skill não encontrada."
                )
            if version.status != "published":
                raise SkillStateError(
                    "Somente versão publicada pode ser aposentada."
                )
            if (
                version.published_at is None
                or retirement_time < version.published_at
            ):
                raise SkillValidationError(
                    "retired_at não pode preceder published_at."
                )
            bindings = self.repository.list_bindings_for_version(
                normalized_id,
                enabled_only=True,
                for_update=True,
            )
            for binding in bindings:
                binding.enabled = False
            version.status = "retired"
            version.retired_at = retirement_time
            self.db.flush()
            self.db.commit()
            return version
        except Exception:
            self.db.rollback()
            raise

    def delete_draft_version(
        self,
        version_id: int,
    ) -> None:
        normalized_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        try:
            version = self.repository.lock_version(normalized_id)
            if version is None:
                raise SkillNotFoundError(
                    "Versão de skill não encontrada."
                )
            self._require_draft(version)
            bindings = self.repository.list_bindings_for_version(
                normalized_id,
                for_update=True,
            )
            if bindings:
                raise SkillConflictError(
                    "Versão vinculada não pode ser excluída."
                )
            self.repository.delete_version(version)
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            self._raise_integrity_conflict(error)
        except Exception:
            self.db.rollback()
            raise

    def bind_agent(
        self,
        *,
        agent_name: str,
        version_id: int,
        priority: int = 100,
        enabled: bool = True,
        configuration: Mapping[str, Any] | None = None,
        created_by_user_id: int | None = None,
    ) -> AgentSkillBinding:
        normalized_agent = _required_text(
            agent_name,
            field_name="agent_name",
            max_length=160,
        )
        normalized_version_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        if not isinstance(enabled, bool):
            raise SkillValidationError(
                "enabled deve ser booleano."
            )
        binding = AgentSkillBinding(
            agent_name=normalized_agent,
            skill_version_id=normalized_version_id,
            priority=_bounded_integer(
                priority,
                field_name="priority",
                minimum=1,
                maximum=1000,
            ),
            enabled=enabled,
            configuration=_normalized_json_object(
                configuration,
                field_name="configuration",
                max_bytes=MAX_CONFIGURATION_BYTES,
            ),
            created_by_user_id=_optional_positive_id(
                created_by_user_id,
                field_name="created_by_user_id",
            ),
        )
        try:
            version = self.repository.lock_version(
                normalized_version_id
            )
            self._require_bindable_version(version)
            if self.repository.find_binding(
                agent_name=normalized_agent,
                version_id=normalized_version_id,
                for_update=True,
            ) is not None:
                raise SkillConflictError(
                    "Binding de agente já registrado."
                )
            self.repository.add_binding(binding)
            self.db.commit()
            return binding
        except IntegrityError as error:
            self.db.rollback()
            self._raise_integrity_conflict(error)
        except Exception:
            self.db.rollback()
            raise

    def set_binding_enabled(
        self,
        binding_id: int,
        *,
        enabled: bool,
    ) -> AgentSkillBinding:
        normalized_id = _positive_id(
            binding_id,
            field_name="binding_id",
        )
        if not isinstance(enabled, bool):
            raise SkillValidationError(
                "enabled deve ser booleano."
            )
        try:
            binding = self.repository.lock_binding(normalized_id)
            if binding is None:
                raise SkillNotFoundError(
                    "Binding não encontrado."
                )
            if enabled:
                version = self.repository.lock_version(
                    binding.skill_version_id
                )
                self._require_bindable_version(version)
            binding.enabled = enabled
            self.db.flush()
            self.db.commit()
            return binding
        except Exception:
            self.db.rollback()
            raise

    def set_skill_status(
        self,
        skill_id: int,
        *,
        status: str,
    ) -> SkillDefinition:
        normalized_id = _positive_id(
            skill_id,
            field_name="skill_id",
        )
        normalized_status = _normalized_choice(
            status,
            field_name="status",
            allowed=SKILL_STATUSES,
        )
        try:
            skill = self.repository.lock_skill(normalized_id)
            if skill is None:
                raise SkillNotFoundError(
                    "Skill não encontrada."
                )
            if skill.status == "retired":
                raise SkillStateError(
                    "Skill aposentada não pode mudar de status."
                )
            skill.status = normalized_status
            if normalized_status != "active":
                for version in self.repository.list_versions(
                    normalized_id
                ):
                    bindings = (
                        self.repository.list_bindings_for_version(
                            version.id,
                            enabled_only=True,
                            for_update=True,
                        )
                    )
                    for binding in bindings:
                        binding.enabled = False
            self.db.flush()
            self.db.commit()
            return skill
        except Exception:
            self.db.rollback()
            raise

    def resolve_agent_bindings(
        self,
        agent_name: str,
    ) -> tuple[ResolvedSkillBinding, ...]:
        normalized_agent = _required_text(
            agent_name,
            field_name="agent_name",
            max_length=160,
        )
        resolved: list[ResolvedSkillBinding] = []
        for binding in self.repository.list_bindings_for_agent(
            normalized_agent,
            enabled_only=True,
        ):
            version = self.repository.get_version(
                binding.skill_version_id
            )
            if version is None or version.status != "published":
                continue
            skill = self.repository.get_skill(version.skill_id)
            if skill is None or skill.status != "active":
                continue
            resolved.append(
                ResolvedSkillBinding(
                    binding=binding,
                    skill=skill,
                    version=version,
                    capabilities=tuple(
                        self.repository.list_capabilities(
                            version.id
                        )
                    ),
                )
            )
        return tuple(resolved)

    def _normalized_contract(
        self,
        *,
        version: str,
        runtime_kind: str,
        handler_reference: str,
        execution_mode: str,
        manifest: Mapping[str, Any] | None,
        input_schema: Mapping[str, Any] | None,
        output_schema: Mapping[str, Any] | None,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> dict[str, Any]:
        normalized_version = _required_text(
            version,
            field_name="version",
            max_length=32,
        )
        normalized_runtime = _normalized_choice(
            runtime_kind,
            field_name="runtime_kind",
            allowed=RUNTIME_KINDS,
        )
        normalized_handler = _required_text(
            handler_reference,
            field_name="handler_reference",
            max_length=320,
        )
        handler_pattern = (
            INTERNAL_HANDLER_PATTERN
            if normalized_runtime == "internal_python"
            else PLUGIN_HANDLER_PATTERN
        )
        if not handler_pattern.fullmatch(normalized_handler):
            raise SkillValidationError(
                "handler_reference inválida para runtime_kind."
            )
        normalized_mode = _normalized_choice(
            execution_mode,
            field_name="execution_mode",
            allowed=EXECUTION_MODES,
        )
        normalized_manifest = _normalized_json_object(
            manifest,
            field_name="manifest",
        )
        normalized_input = _normalized_json_object(
            input_schema,
            field_name="input_schema",
            reject_sensitive_keys=False,
        )
        normalized_output = _normalized_json_object(
            output_schema,
            field_name="output_schema",
            reject_sensitive_keys=False,
        )
        normalized_timeout = _bounded_integer(
            timeout_seconds,
            field_name="timeout_seconds",
            minimum=1,
            maximum=300,
        )
        normalized_output_limit = _bounded_integer(
            max_output_bytes,
            field_name="max_output_bytes",
            minimum=1024,
            maximum=1048576,
        )
        digest = _canonical_digest(
            version=normalized_version,
            runtime_kind=normalized_runtime,
            handler_reference=normalized_handler,
            execution_mode=normalized_mode,
            manifest=normalized_manifest,
            input_schema=normalized_input,
            output_schema=normalized_output,
            timeout_seconds=normalized_timeout,
            max_output_bytes=normalized_output_limit,
        )
        return {
            "version": normalized_version,
            "runtime_kind": normalized_runtime,
            "handler_reference": normalized_handler,
            "execution_mode": normalized_mode,
            "manifest_digest": digest,
            "manifest": normalized_manifest,
            "input_schema": normalized_input,
            "output_schema": normalized_output,
            "timeout_seconds": normalized_timeout,
            "max_output_bytes": normalized_output_limit,
        }

    @staticmethod
    def _normalized_capabilities(
        capabilities: Sequence[CapabilityInput],
    ) -> tuple[CapabilityInput, ...]:
        if (
            isinstance(capabilities, (str, bytes))
            or not isinstance(capabilities, Sequence)
        ):
            raise SkillValidationError(
                "capabilities deve ser uma sequência."
            )
        if len(capabilities) > MAX_CAPABILITIES:
            raise SkillValidationError(
                f"capabilities excede {MAX_CAPABILITIES} itens."
            )
        normalized: list[CapabilityInput] = []
        identities: set[tuple[str, str, str]] = set()
        for item in capabilities:
            if not isinstance(item, CapabilityInput):
                raise SkillValidationError(
                    "Capability inválida."
                )
            key = _required_text(
                item.capability_key,
                field_name="capability_key",
                max_length=160,
            ).lower()
            if not CAPABILITY_KEY_PATTERN.fullmatch(key):
                raise SkillValidationError(
                    "capability_key inválida."
                )
            access_mode = _normalized_choice(
                item.access_mode,
                field_name="access_mode",
                allowed=ACCESS_MODES,
            )
            resource_scope = _normalized_choice(
                item.resource_scope,
                field_name="resource_scope",
                allowed=RESOURCE_SCOPES,
            )
            if not isinstance(item.required, bool):
                raise SkillValidationError(
                    "required deve ser booleano."
                )
            identity = (key, access_mode, resource_scope)
            if identity in identities:
                raise SkillValidationError(
                    "Capability duplicada."
                )
            identities.add(identity)
            normalized.append(
                CapabilityInput(
                    capability_key=key,
                    access_mode=access_mode,
                    resource_scope=resource_scope,
                    required=item.required,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _aware_datetime(
        value: datetime,
        *,
        field_name: str,
    ) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise SkillValidationError(
                f"{field_name} deve possuir timezone."
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _require_draft(version: SkillVersion) -> None:
        if version.status != "draft":
            raise SkillImmutableError(
                "Versão publicada ou aposentada é imutável."
            )

    def _require_bindable_version(
        self,
        version: SkillVersion | None,
    ) -> SkillVersion:
        if version is None:
            raise SkillNotFoundError(
                "Versão de skill não encontrada."
            )
        if version.status != "published":
            raise SkillStateError(
                "Binding exige versão publicada."
            )
        skill = self.repository.lock_skill(version.skill_id)
        if skill is None:
            raise SkillNotFoundError(
                "Skill não encontrada."
            )
        if skill.status != "active":
            raise SkillStateError(
                "Binding exige skill ativa."
            )
        return version

    @staticmethod
    def _raise_integrity_conflict(
        error: IntegrityError,
    ) -> None:
        constraint = _constraint_name(error)
        if constraint in IDENTITY_CONSTRAINTS:
            raise SkillConflictError(
                "Identidade de skill já registrada."
            ) from error
        raise SkillConflictError(
            "Operação de skill conflita com o banco."
        ) from error
