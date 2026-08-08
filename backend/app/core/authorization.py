from typing import Literal


UserRole = Literal[
    "viewer",
    "analyst",
    "manager",
    "executive",
    "administrator",
    "developer",
]

Permission = Literal[
    "dashboard.view",
    "clients.view",
    "clients.manage",
    "imports.execute",
    "executive.view",
    "brain.view",
    "administration.ai-operations",
    "developer.ui-showcase",
]

USER_ROLES: tuple[UserRole, ...] = (
    "viewer",
    "analyst",
    "manager",
    "executive",
    "administrator",
    "developer",
)

ROLE_PERMISSIONS: dict[
    UserRole,
    frozenset[Permission],
] = {
    "viewer": frozenset({
        "dashboard.view",
        "clients.view",
    }),
    "analyst": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
    }),
    "manager": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
    }),
    "executive": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
    }),
    "administrator": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
        "administration.ai-operations",
    }),
    "developer": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
        "administration.ai-operations",
        "developer.ui-showcase",
    }),
}


def has_permission(
    role: str,
    permission: Permission,
) -> bool:
    if role not in ROLE_PERMISSIONS:
        return False

    return permission in ROLE_PERMISSIONS[
        role  # type: ignore[index]
    ]
