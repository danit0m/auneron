from base64 import urlsafe_b64decode
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from hashlib import scrypt
from hashlib import sha256
from secrets import compare_digest
from secrets import token_bytes
from secrets import token_urlsafe

from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authorization import Permission
from app.core.authorization import has_permission
from app.core.config import settings
from app.database.database import get_db
from app.models.auth_session import AuthSession
from app.models.user import User


PASSWORD_SCHEME = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
AUTH_COOKIE_NAME = settings.auth_cookie_name


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _encode_bytes(value: bytes) -> str:
    return urlsafe_b64encode(
        value
    ).decode("ascii").rstrip("=")


def _decode_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(
        value + padding
    )


def hash_password(password: str) -> str:
    salt = token_bytes(16)
    digest = scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )

    return "$".join([
        PASSWORD_SCHEME,
        str(SCRYPT_N),
        str(SCRYPT_R),
        str(SCRYPT_P),
        _encode_bytes(salt),
        _encode_bytes(digest),
    ])


def verify_password(
    password: str,
    stored_hash: str,
) -> bool:
    try:
        (
            scheme,
            n_value,
            r_value,
            p_value,
            salt_value,
            digest_value,
        ) = stored_hash.split("$", 5)

        if scheme != PASSWORD_SCHEME:
            return False

        expected = _decode_bytes(
            digest_value
        )

        actual = scrypt(
            password.encode("utf-8"),
            salt=_decode_bytes(salt_value),
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            dklen=len(expected),
        )
    except (
        ValueError,
        TypeError,
    ):
        return False

    return compare_digest(
        actual,
        expected,
    )


_DUMMY_PASSWORD_HASH = hash_password(
    "auneron-dummy-password-not-valid"
)


def hash_session_token(token: str) -> str:
    return sha256(
        token.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    session: AuthSession


def authenticate_user(
    db: Session,
    *,
    email: str,
    password: str,
) -> User | None:
    normalized_email = normalize_email(
        email
    )

    user = (
        db.query(User)
        .filter(
            User.email == normalized_email
        )
        .one_or_none()
    )

    password_hash = (
        user.password_hash
        if user is not None
        else _DUMMY_PASSWORD_HASH
    )

    password_valid = verify_password(
        password,
        password_hash,
    )

    if (
        user is None
        or not password_valid
        or not user.active
    ):
        return None

    return user


def create_session(
    db: Session,
    user: User,
) -> tuple[str, AuthSession]:
    now = utc_now()
    raw_token = token_urlsafe(32)

    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(
            raw_token
        ),
        expires_at=(
            now
            + timedelta(
                minutes=(
                    settings.auth_session_ttl_minutes
                )
            )
        ),
    )

    user.last_login_at = now

    db.add(auth_session)
    db.commit()
    db.refresh(auth_session)

    return raw_token, auth_session


def revoke_session(
    db: Session,
    auth_session: AuthSession,
) -> None:
    if auth_session.revoked_at is None:
        auth_session.revoked_at = utc_now()
        auth_session.elevated_until = None
        db.commit()


def elevate_session(
    db: Session,
    auth_session: AuthSession,
) -> datetime:
    now = utc_now()
    requested_expiration = (
        now
        + timedelta(
            minutes=(
                settings.auth_elevation_ttl_minutes
            )
        )
    )

    elevated_until = min(
        requested_expiration,
        auth_session.expires_at,
    )

    auth_session.elevated_until = (
        elevated_until
    )
    db.commit()

    return elevated_until


def revoke_elevation(
    db: Session,
    auth_session: AuthSession,
) -> None:
    if auth_session.elevated_until is not None:
        auth_session.elevated_until = None
        db.commit()


def is_session_elevated(
    auth_session: AuthSession,
) -> bool:
    return (
        auth_session.elevated_until
        is not None
        and auth_session.elevated_until
        > utc_now()
    )


def require_user_session(
    session_token: str | None = Cookie(
        default=None,
        alias=AUTH_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
) -> AuthenticatedSession:
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Sessão de usuário ausente."
            ),
            headers={
                "WWW-Authenticate": "Session",
            },
        )

    token_hash = hash_session_token(
        session_token
    )

    auth_session = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash
            == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at
            > utc_now(),
        )
        .one_or_none()
    )

    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Sessão de usuário inválida "
                "ou expirada."
            ),
            headers={
                "WWW-Authenticate": "Session",
            },
        )

    user = db.get(
        User,
        auth_session.user_id,
    )

    if user is None or not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Usuário inativo ou indisponível."
            ),
            headers={
                "WWW-Authenticate": "Session",
            },
        )

    return AuthenticatedSession(
        user=user,
        session=auth_session,
    )


def require_permission(
    permission: Permission,
):
    def dependency(
        authenticated: AuthenticatedSession = Depends(
            require_user_session
        ),
    ) -> AuthenticatedSession:
        if not has_permission(
            authenticated.user.role,
            permission,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Seu perfil não possui "
                    "permissão para este recurso."
                ),
            )

        return authenticated

    return dependency


def require_elevated_permission(
    permission: Permission,
):
    def dependency(
        authenticated: AuthenticatedSession = Depends(
            require_permission(permission)
        ),
    ) -> AuthenticatedSession:
        if not is_session_elevated(
            authenticated.session
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Este recurso exige "
                    "autenticação elevada."
                ),
            )

        return authenticated

    return dependency
