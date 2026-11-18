import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import TEMP_PASSWORD_EXPIRED_MESSAGE
from app.core.authentication import authenticate_user
from app.core.authentication import create_session
from app.core.authentication import elevate_session
from app.core.authentication import hash_password
from app.core.authentication import is_temp_password_expired
from app.core.authentication import require_user_session
from app.core.authentication import (
    require_user_session_allow_password_change,
)
from app.core.authentication import revoke_user_sessions
from app.core.authentication import utc_now
from app.core.authentication import revoke_elevation
from app.core.authentication import revoke_session
from app.core.authentication import verify_password
from app.core.config import settings
from app.core.observability import get_request_id
from app.core.rate_limiting import auth_rate_limiter
from app.core.rate_limiting import elevation_rate_limit_targets
from app.core.rate_limiting import login_rate_limit_targets
from app.core.rate_limiting import password_change_rate_limit_targets
from app.database.database import get_db
from app.schemas.auth import AuthSessionResponse
from app.schemas.auth import AuthUserResponse
from app.schemas.auth import ChangePasswordRequest
from app.schemas.auth import ElevationRequest
from app.schemas.auth import ElevationResponse
from app.schemas.auth import LoginRequest


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

security_logger = logging.getLogger(
    "auneron.security"
)


def _session_response(
    authenticated: AuthenticatedSession,
) -> AuthSessionResponse:
    return AuthSessionResponse(
        user=AuthUserResponse.model_validate(
            authenticated.user
        ),
        authenticated_at=(
            authenticated.session.created_at
        ),
        expires_at=(
            authenticated.session.expires_at
        ),
        elevated_until=(
            authenticated.session.elevated_until
        ),
    )


def _set_session_cookie(
    response: Response,
    token: str,
) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=(
            settings.auth_session_ttl_minutes
            * 60
        ),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )


def _delete_session_cookie(
    response: Response,
) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _raise_rate_limited(
    *,
    area: str,
    retry_after: int,
) -> None:
    security_logger.warning(
        "auth_rate_limit_exceeded",
        extra={
            "event": "auth.rate_limit.exceeded",
            "request_id": get_request_id(),
            "area": area,
            "retry_after_seconds": retry_after,
        },
    )

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "Muitas tentativas de autenticação. "
            "Aguarde e tente novamente."
        ),
        headers={
            "Retry-After": str(
                max(1, retry_after)
            ),
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/login",
    response_model=AuthSessionResponse,
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    targets = login_rate_limit_targets(
        request,
        str(payload.email),
    )

    retry_after = (
        auth_rate_limiter.retry_after(
            targets
        )
    )

    if retry_after is not None:
        _raise_rate_limited(
            area="login",
            retry_after=retry_after,
        )

    user = authenticate_user(
        db,
        email=str(payload.email),
        password=payload.password,
    )

    if user is None:
        retry_after = (
            auth_rate_limiter.record_failure(
                targets
            )
        )

        security_logger.warning(
            "auth_login_failed",
            extra={
                "event": "auth.login.failed",
                "request_id": get_request_id(),
                "reason": "invalid_credentials",
            },
        )

        if retry_after is not None:
            _raise_rate_limited(
                area="login",
                retry_after=retry_after,
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha inválidos.",
            headers={
                "WWW-Authenticate": "Session",
                "Cache-Control": "no-store",
            },
        )

    auth_rate_limiter.clear(
        targets,
        scopes={"login_account"},
    )

    if is_temp_password_expired(user):
        security_logger.warning(
            "auth_login_temp_password_expired",
            extra={
                "event": (
                    "auth.login.temp_password_expired"
                ),
                "request_id": get_request_id(),
                "user_id": user.id,
            },
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=TEMP_PASSWORD_EXPIRED_MESSAGE,
            headers={
                "WWW-Authenticate": "Session",
                "Cache-Control": "no-store",
            },
        )

    raw_token, auth_session = (
        create_session(
            db,
            user,
        )
    )

    _set_session_cookie(
        response,
        raw_token,
    )
    response.headers[
        "Cache-Control"
    ] = "no-store"

    security_logger.info(
        "auth_login_success",
        extra={
            "event": "auth.login.success",
            "request_id": get_request_id(),
            "user_id": user.id,
        },
    )

    return AuthSessionResponse(
        user=AuthUserResponse.model_validate(
            user
        ),
        authenticated_at=(
            auth_session.created_at
        ),
        expires_at=(
            auth_session.expires_at
        ),
        elevated_until=(
            auth_session.elevated_until
        ),
    )


@router.get(
    "/me",
    response_model=AuthSessionResponse,
)
def me(
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_user_session_allow_password_change
    ),
):
    response.headers[
        "Cache-Control"
    ] = "no-store"

    return _session_response(
        authenticated
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout(
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_user_session_allow_password_change
    ),
    db: Session = Depends(get_db),
):
    revoke_session(
        db,
        authenticated.session,
    )

    _delete_session_cookie(
        response
    )
    response.headers[
        "Cache-Control"
    ] = "no-store"

    security_logger.info(
        "auth_logout",
        extra={
            "event": "auth.logout",
            "request_id": get_request_id(),
            "user_id": authenticated.user.id,
        },
    )


@router.post(
    "/elevate",
    response_model=ElevationResponse,
)
def elevate(
    payload: ElevationRequest,
    request: Request,
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_user_session
    ),
    db: Session = Depends(get_db),
):
    targets = (
        elevation_rate_limit_targets(
            request,
            authenticated.user.id,
        )
    )

    retry_after = (
        auth_rate_limiter.retry_after(
            targets
        )
    )

    if retry_after is not None:
        _raise_rate_limited(
            area="elevation",
            retry_after=retry_after,
        )

    if not verify_password(
        payload.password,
        authenticated.user.password_hash,
    ):
        retry_after = (
            auth_rate_limiter.record_failure(
                targets
            )
        )

        security_logger.warning(
            "auth_elevation_failed",
            extra={
                "event": "auth.elevation.failed",
                "request_id": get_request_id(),
                "user_id": (
                    authenticated.user.id
                ),
                "reason": "invalid_credentials",
            },
        )

        if retry_after is not None:
            _raise_rate_limited(
                area="elevation",
                retry_after=retry_after,
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Credencial elevada inválida."
            ),
            headers={
                "Cache-Control": "no-store",
            },
        )

    auth_rate_limiter.clear(
        targets,
        scopes={"elevation_user"},
    )

    elevated_until = elevate_session(
        db,
        authenticated.session,
    )
    response.headers[
        "Cache-Control"
    ] = "no-store"

    security_logger.info(
        "auth_elevation_success",
        extra={
            "event": "auth.elevation.success",
            "request_id": get_request_id(),
            "user_id": authenticated.user.id,
        },
    )

    return ElevationResponse(
        elevated_until=elevated_until
    )


@router.post(
    "/elevation/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_current_elevation(
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_user_session
    ),
    db: Session = Depends(get_db),
):
    revoke_elevation(
        db,
        authenticated.session,
    )
    response.headers[
        "Cache-Control"
    ] = "no-store"

    security_logger.info(
        "auth_elevation_revoked",
        extra={
            "event": "auth.elevation.revoked",
            "request_id": get_request_id(),
            "user_id": authenticated.user.id,
        },
    )


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_user_session_allow_password_change
    ),
    db: Session = Depends(get_db),
):
    user = authenticated.user

    targets = password_change_rate_limit_targets(
        request,
        user.id,
    )

    retry_after = (
        auth_rate_limiter.retry_after(
            targets
        )
    )

    if retry_after is not None:
        _raise_rate_limited(
            area="password_change",
            retry_after=retry_after,
        )

    if not verify_password(
        payload.current_password,
        user.password_hash,
    ):
        retry_after = (
            auth_rate_limiter.record_failure(
                targets
            )
        )

        security_logger.warning(
            "auth_password_change_failed",
            extra={
                "event": "auth.password_change.failed",
                "request_id": get_request_id(),
                "user_id": user.id,
                "reason": "invalid_credentials",
            },
        )

        if retry_after is not None:
            _raise_rate_limited(
                area="password_change",
                retry_after=retry_after,
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha atual inválida.",
            headers={
                "Cache-Control": "no-store",
            },
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "A nova senha precisa ser diferente "
                "da senha atual."
            ),
            headers={
                "Cache-Control": "no-store",
            },
        )

    auth_rate_limiter.clear(
        targets,
        scopes={"password_change_user"},
    )

    user.password_hash = hash_password(
        payload.new_password
    )
    user.must_change_password = False
    user.temp_password_expires_at = None
    user.password_changed_at = utc_now()

    revoke_user_sessions(
        db,
        user.id,
        except_session_id=authenticated.session.id,
    )

    db.commit()

    response.headers[
        "Cache-Control"
    ] = "no-store"

    security_logger.info(
        "auth_password_changed",
        extra={
            "event": "auth.password_changed",
            "request_id": get_request_id(),
            "user_id": user.id,
        },
    )
