from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.authorization import has_permission
from app.core.skill_errors import SkillAuthorizationError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillScopeNotFoundError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.models.account import Account
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.user import User
from app.repositories.skill_repository import SkillRepository


RESERVED_ACCOUNT_FIELD = "account_id"
RESERVED_USER_FIELD = "subject_user_id"


@dataclass(frozen=True)
class SkillExecutionGrant:
    skill: SkillDefinition
    version: SkillVersion
    capabilities: tuple[SkillCapability, ...]
    account_id: int | None
    subject_user_id: int | None


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
        raise SkillValidationError(
            f"{field_name} inválido."
        )
    return value


def _required_scope_id(
    payload: Any,
    *,
    field_name: str,
) -> int:
    if not isinstance(payload, dict):
        raise SkillValidationError(
            "Skill com escopo account/user exige "
            "input_payload em formato de objeto JSON."
        )

    if field_name not in payload:
        raise SkillValidationError(
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
        raise SkillValidationError(
            f"{field_name} é campo reservado e exige "
            "capability de escopo correspondente."
        )


def _inaccessible_scope() -> None:
    raise SkillScopeNotFoundError(
        "Recurso inexistente ou não acessível."
    )


def _require_mode_authority(
    *,
    role: str,
    version: SkillVersion,
    capabilities: tuple[SkillCapability, ...],
    session_elevated: bool,
) -> None:
    if version.execution_mode == "read_only":
        if any(
            capability.access_mode != "read"
            for capability in capabilities
        ):
            raise SkillStateError(
                "Skill read_only declara acesso incompatível."
            )
    elif version.execution_mode == "mutating":
        if not has_permission(
            role,
            "skill:execute_mutating",
        ):
            raise SkillAuthorizationError(
                "Ator sem autoridade para skill mutating."
            )
    elif version.execution_mode == "external":
        if not has_permission(
            role,
            "skill:execute_external",
        ):
            raise SkillAuthorizationError(
                "Ator sem autoridade para skill external."
            )
        if not session_elevated:
            raise SkillAuthorizationError(
                "Execução external exige sessão elevada."
            )
    else:
        raise SkillStateError(
            "execution_mode publicado é inválido."
        )

    external_capabilities = tuple(
        capability
        for capability in capabilities
        if capability.resource_scope == "external"
    )
    if external_capabilities:
        if version.execution_mode != "external":
            raise SkillStateError(
                "Capability external exige execution_mode external."
            )
        if not has_permission(
            role,
            "skill:execute_external",
        ):
            raise SkillAuthorizationError(
                "Ator sem autoridade para recurso external."
            )
        if not session_elevated:
            raise SkillAuthorizationError(
                "Recurso external exige sessão elevada."
            )


def _authorize_account(
    *,
    db: Session,
    role: str,
    account_id: int,
    capabilities: tuple[SkillCapability, ...],
) -> None:
    account_capabilities = tuple(
        capability
        for capability in capabilities
        if capability.resource_scope == "account"
    )
    requires_manage = any(
        capability.access_mode in {
            "write",
            "execute",
        }
        for capability in account_capabilities
    )
    permission = (
        "clients.manage"
        if requires_manage
        else "clients.view"
    )

    if not has_permission(role, permission):
        _inaccessible_scope()

    if db.get(Account, account_id) is None:
        _inaccessible_scope()


def _authorize_user(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    subject_user_id: int,
) -> None:
    if subject_user_id == actor_user_id:
        return

    if not has_permission(
        role,
        "skill:execute_user_scope",
    ):
        _inaccessible_scope()

    subject = db.get(
        User,
        subject_user_id,
    )
    if subject is None or not subject.active:
        _inaccessible_scope()


def authorize_skill_execution(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    session_elevated: bool,
    version_id: int,
    input_payload: Any,
    repository: SkillRepository | None = None,
) -> SkillExecutionGrant:
    """
    Resolve a autoridade de uma execução explícita.

    O ator vem da sessão autenticada. Capabilities são declarações
    de recursos, nunca concessões. `account_id` e `subject_user_id`
    são campos reservados do próprio input executado, de modo que
    a autorização e o handler observem a mesma identidade de recurso.
    """
    normalized_actor_user_id = _positive_id(
        actor_user_id,
        field_name="actor_user_id",
    )
    normalized_version_id = _positive_id(
        version_id,
        field_name="version_id",
    )
    if not isinstance(session_elevated, bool):
        raise SkillValidationError(
            "session_elevated deve ser booleano."
        )

    if not has_permission(
        role,
        "skill:execute",
    ):
        raise SkillAuthorizationError(
            "Ator sem permissão para executar skills."
        )

    skill_repository = (
        repository
        if repository is not None
        else SkillRepository(db)
    )
    version = skill_repository.get_version(
        normalized_version_id
    )

    if (
        version is None
        or version.status != "published"
    ):
        raise SkillNotFoundError(
            "Versão de skill inexistente ou não acessível."
        )

    skill = skill_repository.get_skill(
        version.skill_id
    )
    if (
        skill is None
        or skill.status != "active"
    ):
        raise SkillNotFoundError(
            "Skill inexistente ou não acessível."
        )

    capabilities = tuple(
        skill_repository.list_capabilities(
            version.id
        )
    )

    _require_mode_authority(
        role=role,
        version=version,
        capabilities=capabilities,
        session_elevated=session_elevated,
    )

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
            input_payload,
            field_name=RESERVED_ACCOUNT_FIELD,
        )
        _authorize_account(
            db=db,
            role=role,
            account_id=account_id,
            capabilities=capabilities,
        )
    else:
        _reject_unbound_reserved_field(
            input_payload,
            field_name=RESERVED_ACCOUNT_FIELD,
        )
        account_id = None

    if user_scoped:
        subject_user_id = _required_scope_id(
            input_payload,
            field_name=RESERVED_USER_FIELD,
        )
        _authorize_user(
            db=db,
            role=role,
            actor_user_id=normalized_actor_user_id,
            subject_user_id=subject_user_id,
        )
    else:
        _reject_unbound_reserved_field(
            input_payload,
            field_name=RESERVED_USER_FIELD,
        )
        subject_user_id = None

    return SkillExecutionGrant(
        skill=skill,
        version=version,
        capabilities=capabilities,
        account_id=account_id,
        subject_user_id=subject_user_id,
    )
