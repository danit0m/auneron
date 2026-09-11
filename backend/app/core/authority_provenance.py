from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authorization import has_permission
from app.models.user import User


AUTHORITY_PROVENANCE_SOURCE = "authenticated_http_session"
AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH = 128

SYSTEM_PRINCIPAL_PROVENANCE_SOURCE = "system_principal"
SYSTEM_PRINCIPAL_CANONICAL_EMAIL = "sistema.vencimentos@auneron.core"


class SystemPrincipalError(Exception):
    """Base error for the non-interactive system_principal provenance."""


class SystemPrincipalUnavailableError(SystemPrincipalError):
    """
    The canonical system_principal identity does not exist yet in this
    installation. Runtime never provisions it as a side effect -- this
    is a fail-closed error, not a trigger to create the row here.
    """


class SystemPrincipalIntegrityError(SystemPrincipalError):
    """
    A User row exists at the canonical system_principal identity, but
    does not match the expected shape (role, active, permission). This
    never self-corrects silently.
    """


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
        raise ValueError(
            f"{field_name} must be a positive integer."
        )
    return value


def _normalize_request_id(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise TypeError(
            "request_id must be a string or None."
        )

    normalized = value.strip()

    if (
        not normalized
        or len(normalized)
        > AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH
    ):
        raise ValueError(
            "request_id must be non-blank and at most "
            f"{AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH} characters."
        )

    return normalized


@dataclass(frozen=True)
class AuthorityProvenance:
    """
    Immutable server-derived reference to an authenticated user/session.

    This value is provenance only. It grants no authority, carries no role,
    permission set, scope, elevation state, payload or execution intent.
    Any future consumer must reload and reauthorize current authority.
    """

    authority_user_id: int
    auth_session_id: int
    request_id: str | None = None
    source: Literal[
        "authenticated_http_session"
    ] = AUTHORITY_PROVENANCE_SOURCE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authority_user_id",
            _positive_id(
                self.authority_user_id,
                field_name="authority_user_id",
            ),
        )
        object.__setattr__(
            self,
            "auth_session_id",
            _positive_id(
                self.auth_session_id,
                field_name="auth_session_id",
            ),
        )
        object.__setattr__(
            self,
            "request_id",
            _normalize_request_id(
                self.request_id
            ),
        )

        if (
            self.source
            != AUTHORITY_PROVENANCE_SOURCE
        ):
            raise ValueError(
                "source must be authenticated_http_session."
            )


def authority_provenance_from_authenticated_session(
    authenticated: AuthenticatedSession,
    *,
    request_id: str | None = None,
) -> AuthorityProvenance:
    """
    Derive provenance only from the already-authenticated server session.

    The returned IDs are references for future current-authority reload and
    reauthorization. They are not an authorization grant.
    """

    if not isinstance(
        authenticated,
        AuthenticatedSession,
    ):
        raise TypeError(
            "authenticated must be an AuthenticatedSession."
        )

    authority_user_id = _positive_id(
        authenticated.user.id,
        field_name="authenticated.user.id",
    )
    auth_session_id = _positive_id(
        authenticated.session.id,
        field_name="authenticated.session.id",
    )
    session_user_id = _positive_id(
        authenticated.session.user_id,
        field_name="authenticated.session.user_id",
    )

    if (
        session_user_id
        != authority_user_id
    ):
        raise ValueError(
            "Authenticated session does not belong to the authenticated user."
        )

    return AuthorityProvenance(
        authority_user_id=authority_user_id,
        auth_session_id=auth_session_id,
        request_id=request_id,
    )


@dataclass(frozen=True)
class SystemPrincipalProvenance:
    """
    Immutable server-derived reference to the non-interactive system
    principal. This is a sibling type of AuthorityProvenance, not a
    generalization of it -- it never carries an auth_session_id, and
    a consumer cannot construct one without going through
    resolve_system_principal() below.

    Like AuthorityProvenance, this value is provenance only. It grants
    no authority, carries no role, permission set, scope, elevation
    state, payload or execution intent. Any future consumer must
    reload and reauthorize current authority.
    """

    authority_user_id: int
    source: Literal[
        "system_principal"
    ] = SYSTEM_PRINCIPAL_PROVENANCE_SOURCE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authority_user_id",
            _positive_id(
                self.authority_user_id,
                field_name="authority_user_id",
            ),
        )

        if (
            self.source
            != SYSTEM_PRINCIPAL_PROVENANCE_SOURCE
        ):
            raise ValueError(
                "source must be system_principal."
            )


def resolve_system_principal(
    db: Session,
) -> SystemPrincipalProvenance:
    """
    Resolve the canonical system_principal by identity (email), never
    by a literal primary key. Fail-closed: this never creates or
    repairs the row. Provisioning a missing/malformed system_principal
    is a separate, explicit bootstrap/deploy step -- not a runtime
    side effect of resolving it.
    """
    user = (
        db.query(User)
        .filter(
            User.email
            == SYSTEM_PRINCIPAL_CANONICAL_EMAIL
        )
        .one_or_none()
    )

    if user is None:
        raise SystemPrincipalUnavailableError(
            "Canonical system_principal "
            f"'{SYSTEM_PRINCIPAL_CANONICAL_EMAIL}' does not exist."
        )

    if (
        user.role != "system"
        or not user.active
        or not has_permission(
            user.role,
            "clients.detect_overdue",
        )
    ):
        raise SystemPrincipalIntegrityError(
            "system_principal identity does not match the expected "
            "shape (role=system, active=true, clients.detect_overdue)."
        )

    return SystemPrincipalProvenance(
        authority_user_id=user.id,
    )
