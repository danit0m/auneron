from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import has_permission
from app.core.memory_authorization import authorize_memory_scope
from app.core.memory_errors import MemoryAuthorizationError
from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryValidationError
from app.models.account import Account
from app.models.user import User


def _database(
    *,
    account_exists: bool = True,
    user_exists: bool = True,
) -> Session:
    db = Mock(spec=Session)

    def get(model: type[object], _: int) -> object | None:
        if model is Account:
            return object() if account_exists else None

        if model is User:
            return object() if user_exists else None

        raise AssertionError("Unexpected model lookup.")

    db.get.side_effect = get
    return db


def test_memory_role_matrix_uses_least_privilege() -> None:
    assert has_permission("viewer", "memory:read")
    assert not has_permission("viewer", "memory:create")

    assert has_permission("analyst", "memory:create")
    assert has_permission("analyst", "memory:evidence")
    assert has_permission("analyst", "memory:history")
    assert not has_permission("analyst", "memory:supersede")

    assert has_permission("manager", "memory:supersede")
    assert has_permission("executive", "memory:invalidate")
    assert has_permission("manager", "memory:archive")


@pytest.mark.parametrize(
    "role",
    [
        "administrator",
        "developer",
    ],
)
def test_privileged_roles_receive_scope_capabilities(
    role: str,
) -> None:
    assert has_permission(role, "memory:read_user_scope")
    assert has_permission(role, "memory:read_global")
    assert has_permission(role, "memory:manage_global")


@pytest.mark.parametrize(
    "role",
    [
        "viewer",
        "analyst",
        "manager",
        "executive",
    ],
)
def test_regular_roles_do_not_receive_privileged_scopes(
    role: str,
) -> None:
    assert not has_permission(role, "memory:read_user_scope")
    assert not has_permission(role, "memory:read_global")
    assert not has_permission(role, "memory:manage_global")


def test_viewer_can_read_existing_account_scope() -> None:
    authorize_memory_scope(
        db=_database(),
        role="viewer",
        actor_user_id=10,
        operation="read",
        scope_type="account",
        account_id=42,
    )


def test_account_write_requires_clients_manage() -> None:
    with pytest.raises(MemoryAuthorizationError):
        authorize_memory_scope(
            db=_database(),
            role="viewer",
            actor_user_id=10,
            operation="create",
            scope_type="account",
            account_id=42,
        )

    authorize_memory_scope(
        db=_database(),
        role="analyst",
        actor_user_id=10,
        operation="create",
        scope_type="account",
        account_id=42,
    )


def test_missing_account_is_opaque() -> None:
    with pytest.raises(MemoryNotFoundError):
        authorize_memory_scope(
            db=_database(account_exists=False),
            role="viewer",
            actor_user_id=10,
            operation="read",
            scope_type="account",
            account_id=999,
        )


def test_global_read_requires_global_capability() -> None:
    with pytest.raises(MemoryNotFoundError):
        authorize_memory_scope(
            db=_database(),
            role="viewer",
            actor_user_id=10,
            operation="read",
            scope_type="global",
        )

    authorize_memory_scope(
        db=_database(),
        role="administrator",
        actor_user_id=10,
        operation="read",
        scope_type="global",
    )


def test_global_write_requires_manage_global() -> None:
    with pytest.raises(MemoryNotFoundError):
        authorize_memory_scope(
            db=_database(),
            role="manager",
            actor_user_id=10,
            operation="archive",
            scope_type="global",
        )

    authorize_memory_scope(
        db=_database(),
        role="developer",
        actor_user_id=10,
        operation="archive",
        scope_type="global",
    )


def test_user_can_access_own_scope() -> None:
    authorize_memory_scope(
        db=_database(),
        role="analyst",
        actor_user_id=10,
        operation="create",
        scope_type="user",
        subject_user_id=10,
    )


def test_cross_user_read_requires_scope_capability() -> None:
    with pytest.raises(MemoryNotFoundError):
        authorize_memory_scope(
            db=_database(),
            role="manager",
            actor_user_id=10,
            operation="read",
            scope_type="user",
            subject_user_id=11,
        )

    authorize_memory_scope(
        db=_database(),
        role="administrator",
        actor_user_id=10,
        operation="read",
        scope_type="user",
        subject_user_id=11,
    )


def test_cross_user_write_is_not_available_in_v1() -> None:
    with pytest.raises(MemoryNotFoundError):
        authorize_memory_scope(
            db=_database(),
            role="administrator",
            actor_user_id=10,
            operation="create",
            scope_type="user",
            subject_user_id=11,
        )


def test_missing_cross_user_scope_is_opaque() -> None:
    with pytest.raises(MemoryNotFoundError):
        authorize_memory_scope(
            db=_database(user_exists=False),
            role="administrator",
            actor_user_id=10,
            operation="read",
            scope_type="user",
            subject_user_id=999,
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
    with pytest.raises(MemoryValidationError):
        authorize_memory_scope(
            db=_database(),
            role="administrator",
            actor_user_id=10,
            operation="read",
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
