from typing import Literal

from sqlalchemy.orm import Session

from app.core.authorization import Permission
from app.core.authorization import has_permission
from app.core.memory_errors import MemoryAuthorizationError
from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryValidationError
from app.models.account import Account
from app.models.user import User


MemoryOperation = Literal[
    "read",
    "create",
    "evidence",
    "supersede",
    "invalidate",
    "archive",
    "history",
]

MEMORY_OPERATION_PERMISSIONS: dict[
    MemoryOperation,
    Permission,
] = {
    "read": "memory:read",
    "create": "memory:create",
    "evidence": "memory:evidence",
    "supersede": "memory:supersede",
    "invalidate": "memory:invalidate",
    "archive": "memory:archive",
    "history": "memory:history",
}

READ_OPERATIONS = frozenset({
    "read",
    "history",
})


def authorize_memory_scope(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    operation: MemoryOperation,
    scope_type: str,
    account_id: int | None = None,
    subject_user_id: int | None = None,
) -> None:
    permission = MEMORY_OPERATION_PERMISSIONS[
        operation
    ]

    if not has_permission(role, permission):
        raise MemoryAuthorizationError(
            "Ator sem permissao para a operacao de memoria."
        )

    if scope_type == "global":
        _authorize_global_scope(
            role=role,
            operation=operation,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
        return

    if scope_type == "account":
        _authorize_account_scope(
            role=role,
            operation=operation,
            account_id=account_id,
            subject_user_id=subject_user_id,
            db=db,
        )
        return

    if scope_type == "user":
        _authorize_user_scope(
            role=role,
            actor_user_id=actor_user_id,
            operation=operation,
            account_id=account_id,
            subject_user_id=subject_user_id,
            db=db,
        )
        return

    raise MemoryValidationError(
        "Escopo de memoria invalido."
    )


def _authorize_global_scope(
    *,
    role: str,
    operation: MemoryOperation,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    if account_id is not None or subject_user_id is not None:
        raise MemoryValidationError(
            "Escopo global invalido."
        )

    scope_permission: Permission = (
        "memory:read_global"
        if operation in READ_OPERATIONS
        else "memory:manage_global"
    )

    if not has_permission(role, scope_permission):
        _raise_inaccessible_scope()


def _authorize_account_scope(
    *,
    db: Session,
    role: str,
    operation: MemoryOperation,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    if (
        not _is_positive_integer(account_id)
        or subject_user_id is not None
    ):
        raise MemoryValidationError(
            "Escopo account invalido."
        )

    account_permission: Permission = (
        "clients.view"
        if operation in READ_OPERATIONS
        else "clients.manage"
    )

    if not has_permission(role, account_permission):
        _raise_inaccessible_scope()

    if db.get(Account, account_id) is None:
        _raise_inaccessible_scope()


def _authorize_user_scope(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    operation: MemoryOperation,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    if (
        account_id is not None
        or not _is_positive_integer(subject_user_id)
    ):
        raise MemoryValidationError(
            "Escopo user invalido."
        )

    if subject_user_id == actor_user_id:
        return

    if (
        operation in READ_OPERATIONS
        and has_permission(
            role,
            "memory:read_user_scope",
        )
    ):
        if db.get(User, subject_user_id) is not None:
            return

    _raise_inaccessible_scope()


def _is_positive_integer(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    )


def _raise_inaccessible_scope() -> None:
    raise MemoryNotFoundError(
        "Memoria inexistente ou nao acessivel."
    )
