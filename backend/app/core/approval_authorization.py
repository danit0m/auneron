from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalElevationRequiredError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.authorization import has_permission
from app.models.approval import ApprovalRequest


def visible_required_permissions(
    role: str,
) -> tuple[str, ...]:
    if not has_permission(
        role,
        "approval:read",
    ):
        return ()

    permissions: list[str] = []
    if has_permission(
        role,
        "approval:decide",
    ):
        permissions.append(
            "approval:decide"
        )
    if has_permission(
        role,
        "approval:decide_sensitive",
    ):
        permissions.append(
            "approval:decide_sensitive"
        )

    return tuple(permissions)


def authorize_approval_read(
    *,
    role: str,
    request: ApprovalRequest,
) -> None:
    if (
        request.required_permission
        not in visible_required_permissions(role)
    ):
        raise ApprovalNotFoundError(
            "Solicitação de aprovação inexistente "
            "ou não acessível."
        )


def authorize_approval_decision(
    *,
    role: str,
    session_elevated: bool,
    request: ApprovalRequest,
) -> None:
    authorize_approval_read(
        role=role,
        request=request,
    )

    if not has_permission(
        role,
        request.required_permission,
    ):
        raise ApprovalAuthorizationError(
            "Ator sem autoridade para decidir "
            "esta aprovação."
        )

    if (
        request.required_permission
        == "approval:decide_sensitive"
        and not session_elevated
    ):
        raise ApprovalElevationRequiredError(
            "Aprovação sensível exige sessão elevada."
        )
