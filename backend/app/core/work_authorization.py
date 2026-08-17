from typing import Literal

from sqlalchemy.orm import Session

from app.core.authorization import Permission
from app.core.authorization import has_permission
from app.core.work_errors import WorkAuthorizationError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkValidationError
from app.models.account import Account
from app.models.user import User


WorkOperation = Literal[
    "read",
    "create",
    "update",
    "comment",
    "dependency",
    "recurrence",
]

WORK_OPERATION_PERMISSIONS: dict[
    WorkOperation,
    Permission,
] = {
    "read": "work:read",
    "create": "work:create",
    "update": "work:update",
    "comment": "work:comment",
    "dependency": "work:manage_dependencies",
    "recurrence": "work:manage_recurrence",
}


def authorize_work_scope(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    operation: WorkOperation,
    scope_type: str,
    account_id: int | None = None,
    subject_user_id: int | None = None,
) -> None:
    permission = WORK_OPERATION_PERMISSIONS[
        operation
    ]

    if not has_permission(role, permission):
        raise WorkAuthorizationError(
            "Ator sem permissão para a operação de trabalho."
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
            db=db,
            role=role,
            operation=operation,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
        return

    if scope_type == "user":
        _authorize_user_scope(
            db=db,
            role=role,
            actor_user_id=actor_user_id,
            operation=operation,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
        return

    raise WorkValidationError(
        "Escopo de trabalho inválido."
    )


def authorize_work_assignee(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    assignee_user_id: int | None,
) -> None:
    if assignee_user_id is None:
        return

    if not _is_positive_integer(assignee_user_id):
        raise WorkValidationError(
            "assignee_user_id inválido."
        )

    if (
        assignee_user_id != actor_user_id
        and not has_permission(role, "work:assign")
    ):
        raise WorkAuthorizationError(
            "Ator sem permissão para atribuir trabalho a terceiros."
        )

    assignee = db.get(User, assignee_user_id)

    if assignee is None or not assignee.active:
        _raise_inaccessible_resource()


def _authorize_global_scope(
    *,
    role: str,
    operation: WorkOperation,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    if account_id is not None or subject_user_id is not None:
        raise WorkValidationError(
            "Escopo global inválido."
        )

    permission: Permission = (
        "work:read_global"
        if operation == "read"
        else "work:manage_global"
    )

    if not has_permission(role, permission):
        _raise_inaccessible_resource()


def _authorize_account_scope(
    *,
    db: Session,
    role: str,
    operation: WorkOperation,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    if (
        not _is_positive_integer(account_id)
        or subject_user_id is not None
    ):
        raise WorkValidationError(
            "Escopo account inválido."
        )

    permission: Permission = (
        "clients.view"
        if operation == "read"
        else "clients.manage"
    )

    if not has_permission(role, permission):
        _raise_inaccessible_resource()

    if db.get(Account, account_id) is None:
        _raise_inaccessible_resource()


def _authorize_user_scope(
    *,
    db: Session,
    role: str,
    actor_user_id: int,
    operation: WorkOperation,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    if (
        account_id is not None
        or not _is_positive_integer(subject_user_id)
    ):
        raise WorkValidationError(
            "Escopo user inválido."
        )

    if subject_user_id == actor_user_id:
        return

    permission: Permission = (
        "work:read_user_scope"
        if operation == "read"
        else "work:manage_user_scope"
    )

    if has_permission(role, permission):
        subject = db.get(User, subject_user_id)

        if subject is not None and subject.active:
            return

    _raise_inaccessible_resource()


def _is_positive_integer(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    )


def _raise_inaccessible_resource() -> None:
    raise WorkNotFoundError(
        "Trabalho inexistente ou não acessível."
    )
