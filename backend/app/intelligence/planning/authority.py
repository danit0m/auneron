from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from typing import Final

from app.intelligence.gateway.profiles import is_production_environment
from app.intelligence.planning.canonical import DOMAIN_CONTEXT
from app.intelligence.planning.canonical import digest


# Mirror of core.authorization.UserRole (verified by a test; this
# package must not import the authorization module).
KNOWN_ROLES: Final[frozenset[str]] = frozenset(
    {
        "viewer",
        "analyst",
        "manager",
        "executive",
        "administrator",
        "developer",
        "system",
    }
)


class AuthorityContextError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountAccess:
    readable: bool
    manageable: bool


@dataclass(frozen=True)
class SyntheticAuthorityContext:
    """
    LABORATORY-ONLY authority snapshot for admissibility evaluation. It
    is plain data supplied by the caller: it consults no RBAC, no
    database and no session, executes nothing, and CANNOT be built in
    production. Nothing derived from it is an authorisation.

    `initiator_ref` is the human (or system principal) on whose behalf
    the plan is proposed -- the "I" of I / R / D / X. It is recorded so
    that a future execution gate can compare it with the approver; this
    module does not enforce I != D.
    """

    initiator_ref: str
    role: str
    permissions: frozenset[str]
    accounts: Mapping[str, AccountAccess]
    session_elevated: bool = False
    synthetic: bool = True

    def __post_init__(self) -> None:
        if is_production_environment():
            raise AuthorityContextError(
                "A synthetic authority context is forbidden in production."
            )

        if not self.synthetic:
            raise AuthorityContextError("Only synthetic contexts exist.")

        if not isinstance(self.initiator_ref, str) or not self.initiator_ref.strip():
            raise AuthorityContextError("initiator_ref is required.")

        if self.role not in KNOWN_ROLES:
            raise AuthorityContextError("Unknown role.")

        if not isinstance(self.permissions, frozenset):
            raise AuthorityContextError("permissions must be a frozenset.")

        for access in self.accounts.values():
            if not isinstance(access, AccountAccess):
                raise AuthorityContextError("Invalid account access entry.")

    def holds(self, permission: str) -> bool:
        return permission in self.permissions

    def access_to(self, account_ref: str) -> AccountAccess | None:
        """None for an unknown OR an inaccessible account: callers must
        not be able to tell the two apart."""
        access = self.accounts.get(account_ref)

        if access is None or not (access.readable or access.manageable):
            return None

        return access

    def context_digest(self) -> str:
        material: dict[str, Any] = {
            "initiator_ref": self.initiator_ref,
            "role": self.role,
            "permissions": sorted(self.permissions),
            "session_elevated": self.session_elevated,
            "accounts": {
                ref: {
                    "readable": access.readable,
                    "manageable": access.manageable,
                }
                for ref, access in sorted(self.accounts.items())
            },
        }
        return digest(DOMAIN_CONTEXT, material)
