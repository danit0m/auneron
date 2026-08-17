from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import has_permission
from app.core.work_authorization import authorize_work_assignee
from app.core.work_authorization import authorize_work_scope
from app.core.work_errors import WorkAuthorizationError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkValidationError
from app.models.account import Account
from app.models.user import User


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


def test_work_role_matrix_uses_least_privilege() -> None:
    assert has_permission("viewer", "work:read")
    assert not has_permission("viewer", "work:create")

    assert has_permission("analyst", "work:create")
    assert has_permission("analyst", "work:update")
    assert has_permission("analyst", "work:comment")
    assert not has_permission(
        "analyst",
        "work:manage_dependencies",
    )
    assert not has_permission("analyst", "work:assign")

    assert has_permission(
        "manager",
        "work:manage_dependencies",
    )
    assert has_permission(
        "executive",
        "work:manage_recurrence",
    )
    assert has_permission("manager", "work:assign")


@pytest.mark.parametrize(
    "role",
    [
        "administrator",
        "developer",
    ],
)
def test_privileged_roles_receive_work_scope_capabilities(
    role: str,
) -> None:
    assert has_permission(role, "work:read_user_scope")
    assert has_permission(role, "work:manage_user_scope")
    assert has_permission(role, "work:read_global")
    assert has_permission(role, "work:manage_global")


@pytest.mark.parametrize(
    "role",
    [
        "viewer",
        "analyst",
        "manager",
        "executive",
    ],
)
def test_regular_roles_do_not_receive_privileged_work_scopes(
    role: str,
) -> None:
    assert not has_permission(role, "work:read_user_scope")
    assert not has_permission(role, "work:manage_user_scope")
    assert not has_permission(role, "work:read_global")
    assert not has_permission(role, "work:manage_global")


def test_viewer_can_read_existing_account_scope() -> None:
    authorize_work_scope(
        db=_database(),
        role="viewer",
        actor_user_id=10,
        operation="read",
        scope_type="account",
        account_id=42,
    )


def test_account_mutation_requires_clients_manage() -> None:
    with pytest.raises(WorkAuthorizationError):
        authorize_work_scope(
            db=_database(),
            role="viewer",
            actor_user_id=10,
            operation="create",
            scope_type="account",
            account_id=42,
        )

    authorize_work_scope(
        db=_database(),
        role="analyst",
        actor_user_id=10,
        operation="create",
        scope_type="account",
        account_id=42,
    )


def test_missing_account_is_opaque() -> None:
    with pytest.raises(WorkNotFoundError):
        authorize_work_scope(
            db=_database(account_exists=False),
            role="viewer",
            actor_user_id=10,
            operation="read",
            scope_type="account",
            account_id=999,
        )


def test_global_scope_requires_privileged_capability() -> None:
    with pytest.raises(WorkNotFoundError):
        authorize_work_scope(
            db=_database(),
            role="manager",
            actor_user_id=10,
            operation="read",
            scope_type="global",
        )

    authorize_work_scope(
        db=_database(),
        role="administrator",
        actor_user_id=10,
        operation="read",
        scope_type="global",
    )

    with pytest.raises(WorkNotFoundError):
        authorize_work_scope(
            db=_database(),
            role="manager",
            actor_user_id=10,
            operation="update",
            scope_type="global",
        )

    authorize_work_scope(
        db=_database(),
        role="developer",
        actor_user_id=10,
        operation="update",
        scope_type="global",
    )


def test_user_can_access_own_scope() -> None:
    authorize_work_scope(
        db=_database(),
        role="analyst",
        actor_user_id=10,
        operation="create",
        scope_type="user",
        subject_user_id=10,
    )


def test_cross_user_scope_requires_explicit_capability() -> None:
    with pytest.raises(WorkNotFoundError):
        authorize_work_scope(
            db=_database(),
            role="manager",
            actor_user_id=10,
            operation="read",
            scope_type="user",
            subject_user_id=11,
        )

    authorize_work_scope(
        db=_database(),
        role="administrator",
        actor_user_id=10,
        operation="read",
        scope_type="user",
        subject_user_id=11,
    )
    authorize_work_scope(
        db=_database(),
        role="developer",
        actor_user_id=10,
        operation="update",
        scope_type="user",
        subject_user_id=11,
    )


def test_missing_cross_user_scope_is_opaque() -> None:
    with pytest.raises(WorkNotFoundError):
        authorize_work_scope(
            db=_database(user_exists=False),
            role="administrator",
            actor_user_id=10,
            operation="read",
            scope_type="user",
            subject_user_id=999,
        )


def test_self_assignment_is_allowed_without_assign_capability() -> None:
    authorize_work_assignee(
        db=_database(),
        role="analyst",
        actor_user_id=10,
        assignee_user_id=10,
    )


def test_third_party_assignment_requires_assign_capability() -> None:
    with pytest.raises(WorkAuthorizationError):
        authorize_work_assignee(
            db=_database(),
            role="analyst",
            actor_user_id=10,
            assignee_user_id=11,
        )

    authorize_work_assignee(
        db=_database(),
        role="manager",
        actor_user_id=10,
        assignee_user_id=11,
    )


@pytest.mark.parametrize(
    ("user_exists", "user_active"),
    [
        (False, True),
        (True, False),
    ],
)
def test_invalid_assignee_is_opaque(
    user_exists: bool,
    user_active: bool,
) -> None:
    with pytest.raises(WorkNotFoundError):
        authorize_work_assignee(
            db=_database(
                user_exists=user_exists,
                user_active=user_active,
            ),
            role="manager",
            actor_user_id=10,
            assignee_user_id=99,
        )


@pytest.mark.parametrize(
    ("scope_type", "account_id", "subject_user_id"),
    [
        ("global", 1, None),
        ("account", None, None),
        ("account", 1, 2),
        ("user", None, None),
        ("user", 1, 2),
        ("unknown", None, None),
    ],
)
def test_malformed_scope_is_rejected(
    scope_type: str,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    with pytest.raises(WorkValidationError):
        authorize_work_scope(
            db=_database(),
            role="administrator",
            actor_user_id=10,
            operation="read",
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
