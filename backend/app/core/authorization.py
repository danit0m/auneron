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
    "memory:read",
    "memory:create",
    "memory:evidence",
    "memory:supersede",
    "memory:invalidate",
    "memory:archive",
    "memory:history",
    "memory:read_user_scope",
    "memory:read_global",
    "memory:manage_global",
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
        "memory:read",
    }),
    "analyst": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "memory:read",
        "memory:create",
        "memory:evidence",
        "memory:history",
    }),
    "manager": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
        "memory:read",
        "memory:create",
        "memory:evidence",
        "memory:supersede",
        "memory:invalidate",
        "memory:archive",
        "memory:history",
    }),
    "executive": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
        "memory:read",
        "memory:create",
        "memory:evidence",
        "memory:supersede",
        "memory:invalidate",
        "memory:archive",
        "memory:history",
    }),
    "administrator": frozenset({
        "dashboard.view",
        "clients.view",
        "clients.manage",
        "imports.execute",
        "brain.view",
        "executive.view",
        "administration.ai-operations",
        "memory:read",
        "memory:create",
        "memory:evidence",
        "memory:supersede",
        "memory:invalidate",
        "memory:archive",
        "memory:history",
        "memory:read_user_scope",
        "memory:read_global",
        "memory:manage_global",
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
        "memory:read",
        "memory:create",
        "memory:evidence",
        "memory:supersede",
        "memory:invalidate",
        "memory:archive",
        "memory:history",
        "memory:read_user_scope",
        "memory:read_global",
        "memory:manage_global",
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
