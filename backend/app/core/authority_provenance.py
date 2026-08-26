from dataclasses import dataclass
from typing import Literal

from app.core.authentication import AuthenticatedSession


AUTHORITY_PROVENANCE_SOURCE = "authenticated_http_session"
AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH = 128


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
