from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import has_permission
from app.core.skill_authorization import authorize_skill_execution
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


def _database(
    *,
    account_exists: bool = True,
    user_exists: bool = True,
    user_active: bool = True,
) -> Session:
    db = Mock(spec=Session)

    def get(model: type[object], _: int) -> object | None:
        if model is Account:
            return object() if account_exists else None
        if model is User:
            if not user_exists:
                return None
            user = Mock(spec=User)
            user.active = user_active
            return user
        raise AssertionError("Unexpected model lookup.")

    db.get.side_effect = get
    return db


def _repository(
    *,
    execution_mode: str = "read_only",
    capabilities: tuple[
        tuple[str, str, str, bool],
        ...,
    ] = (),
    version_status: str = "published",
    skill_status: str = "active",
) -> SkillRepository:
    repository = Mock(
        spec=SkillRepository
    )

    version = Mock(
        spec=SkillVersion
    )
    version.id = 7
    version.skill_id = 3
    version.status = version_status
    version.execution_mode = execution_mode

    skill = Mock(
        spec=SkillDefinition
    )
    skill.id = 3
    skill.status = skill_status

    rows = []
    for (
        key,
        access_mode,
        resource_scope,
        required,
    ) in capabilities:
        capability = Mock(
            spec=SkillCapability
        )
        capability.capability_key = key
        capability.access_mode = access_mode
        capability.resource_scope = (
            resource_scope
        )
        capability.required = required
        rows.append(capability)

    repository.get_version.return_value = version
    repository.get_skill.return_value = skill
    repository.list_capabilities.return_value = rows
    return repository


def _authorize(
    *,
    role: str = "analyst",
    execution_mode: str = "read_only",
    capabilities: tuple[
        tuple[str, str, str, bool],
        ...,
    ] = (),
    payload: object = None,
    elevated: bool = False,
    actor_user_id: int = 10,
    database: Session | None = None,
    repository: SkillRepository | None = None,
):
    return authorize_skill_execution(
        db=database or _database(),
        role=role,
        actor_user_id=actor_user_id,
        session_elevated=elevated,
        version_id=7,
        input_payload=(
            {}
            if payload is None
            else payload
        ),
        repository=(
            repository
            or _repository(
                execution_mode=execution_mode,
                capabilities=capabilities,
            )
        ),
    )


def test_skill_role_matrix_uses_least_privilege() -> None:
    assert not has_permission(
        "viewer",
        "skill:execute",
    )
    assert has_permission(
        "analyst",
        "skill:execute",
    )
    assert not has_permission(
        "analyst",
        "skill:execute_mutating",
    )
    assert has_permission(
        "manager",
        "skill:execute_mutating",
    )
    assert has_permission(
        "executive",
        "skill:execute_mutating",
    )
    assert not has_permission(
        "manager",
        "skill:execute_external",
    )
    assert has_permission(
        "administrator",
        "skill:execute_external",
    )
    assert has_permission(
        "developer",
        "skill:execute_user_scope",
    )


def test_viewer_cannot_execute_even_read_only() -> None:
    with pytest.raises(
        SkillAuthorizationError
    ):
        _authorize(
            role="viewer"
        )


def test_analyst_can_execute_read_only_internal_skill() -> None:
    grant = _authorize(
        role="analyst",
        capabilities=(
            (
                "memory.read",
                "read",
                "internal",
                True,
            ),
        ),
    )

    assert grant.version.id == 7
    assert grant.account_id is None
    assert grant.subject_user_id is None


def test_analyst_cannot_execute_mutating_skill() -> None:
    with pytest.raises(
        SkillAuthorizationError
    ):
        _authorize(
            role="analyst",
            execution_mode="mutating",
        )


@pytest.mark.parametrize(
    "role",
    [
        "manager",
        "executive",
        "administrator",
        "developer",
    ],
)
def test_authorized_roles_can_execute_mutating_skill(
    role: str,
) -> None:
    grant = _authorize(
        role=role,
        execution_mode="mutating",
    )

    assert grant.version.execution_mode == (
        "mutating"
    )


def test_external_execution_requires_permission_and_elevation() -> None:
    with pytest.raises(
        SkillAuthorizationError
    ):
        _authorize(
            role="manager",
            execution_mode="external",
            elevated=True,
        )

    with pytest.raises(
        SkillAuthorizationError
    ):
        _authorize(
            role="developer",
            execution_mode="external",
            elevated=False,
        )

    grant = _authorize(
        role="developer",
        execution_mode="external",
        elevated=True,
    )
    assert grant.version.execution_mode == (
        "external"
    )


def test_read_only_cannot_declare_write_capability() -> None:
    with pytest.raises(
        SkillStateError
    ):
        _authorize(
            capabilities=(
                (
                    "account.write",
                    "write",
                    "account",
                    True,
                ),
            ),
            payload={"account_id": 1},
        )


def test_external_capability_requires_external_mode() -> None:
    with pytest.raises(
        SkillStateError
    ):
        _authorize(
            role="developer",
            execution_mode="mutating",
            elevated=True,
            capabilities=(
                (
                    "provider.call",
                    "execute",
                    "external",
                    False,
                ),
            ),
        )


def test_account_scope_is_bound_to_runtime_payload() -> None:
    grant = _authorize(
        role="analyst",
        capabilities=(
            (
                "account.read",
                "read",
                "account",
                True,
            ),
        ),
        payload={
            "account_id": 42,
            "query": "open",
        },
    )

    assert grant.account_id == 42


def test_account_scope_missing_or_inaccessible_is_rejected() -> None:
    capability = (
        (
            "account.read",
            "read",
            "account",
            True,
        ),
    )

    with pytest.raises(
        SkillValidationError
    ):
        _authorize(
            capabilities=capability,
            payload={"query": "open"},
        )

    with pytest.raises(
        SkillScopeNotFoundError
    ):
        _authorize(
            capabilities=capability,
            payload={"account_id": 999},
            database=_database(
                account_exists=False
            ),
        )


def test_reserved_account_id_without_capability_is_rejected() -> None:
    with pytest.raises(
        SkillValidationError
    ):
        _authorize(
            payload={"account_id": 1},
        )


def test_user_scope_self_is_allowed_but_cross_user_is_opaque() -> None:
    capability = (
        (
            "profile.read",
            "read",
            "user",
            True,
        ),
    )

    self_grant = _authorize(
        role="analyst",
        actor_user_id=10,
        capabilities=capability,
        payload={"subject_user_id": 10},
    )
    assert self_grant.subject_user_id == 10

    with pytest.raises(
        SkillScopeNotFoundError
    ):
        _authorize(
            role="manager",
            actor_user_id=10,
            capabilities=capability,
            payload={"subject_user_id": 20},
        )


def test_administrator_can_use_active_cross_user_scope() -> None:
    capability = (
        (
            "profile.read",
            "read",
            "user",
            False,
        ),
    )

    grant = _authorize(
        role="administrator",
        actor_user_id=10,
        capabilities=capability,
        payload={"subject_user_id": 20},
        database=_database(
            user_exists=True,
            user_active=True,
        ),
    )

    assert grant.subject_user_id == 20


def test_cross_user_missing_or_inactive_target_is_opaque() -> None:
    capability = (
        (
            "profile.read",
            "read",
            "user",
            True,
        ),
    )

    for database in (
        _database(
            user_exists=False
        ),
        _database(
            user_exists=True,
            user_active=False,
        ),
    ):
        with pytest.raises(
            SkillScopeNotFoundError
        ):
            _authorize(
                role="developer",
                actor_user_id=10,
                capabilities=capability,
                payload={
                    "subject_user_id": 20
                },
                database=database,
            )


def test_draft_or_disabled_skill_is_not_discoverable() -> None:
    draft_repository = _repository(
        version_status="draft"
    )
    with pytest.raises(
        SkillNotFoundError
    ):
        _authorize(
            repository=draft_repository
        )

    disabled_repository = _repository(
        skill_status="disabled"
    )
    with pytest.raises(
        SkillNotFoundError
    ):
        _authorize(
            repository=disabled_repository
        )


def test_optional_capability_is_still_authorized() -> None:
    grant = _authorize(
        role="analyst",
        capabilities=(
            (
                "account.read",
                "read",
                "account",
                False,
            ),
        ),
        payload={"account_id": 7},
    )

    assert len(grant.capabilities) == 1
    assert grant.capabilities[0].required is False
