from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import authenticate_user
from app.core.authentication import create_session
from app.core.authentication import elevate_session
from app.core.authentication import require_user_session
from app.core.authentication import revoke_elevation
from app.core.authentication import revoke_session
from app.core.authentication import verify_password
from app.core.config import settings
from app.database.database import get_db
from app.schemas.auth import AuthSessionResponse
from app.schemas.auth import AuthUserResponse
from app.schemas.auth import ElevationRequest
from app.schemas.auth import ElevationResponse
from app.schemas.auth import LoginRequest


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
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


@router.post(
    "/login",
    response_model=AuthSessionResponse,
)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    user = authenticate_user(
        db,
        email=str(payload.email),
        password=payload.password,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha inválidos.",
            headers={
                "WWW-Authenticate": "Session",
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
    authenticated: AuthenticatedSession = Depends(
        require_user_session
    ),
):
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
        require_user_session
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


@router.post(
    "/elevate",
    response_model=ElevationResponse,
)
def elevate(
    payload: ElevationRequest,
    authenticated: AuthenticatedSession = Depends(
        require_user_session
    ),
    db: Session = Depends(get_db),
):
    if not verify_password(
        payload.password,
        authenticated.user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Credencial elevada inválida."
            ),
        )

    elevated_until = elevate_session(
        db,
        authenticated.session,
    )

    return ElevationResponse(
        elevated_until=elevated_until
    )


@router.post(
    "/elevation/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_current_elevation(
    authenticated: AuthenticatedSession = Depends(
        require_user_session
    ),
    db: Session = Depends(get_db),
):
    revoke_elevation(
        db,
        authenticated.session,
    )
