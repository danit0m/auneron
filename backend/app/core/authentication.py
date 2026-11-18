import asyncio
import logging
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
from app.database.database import SessionLocal
from app.database.database import get_db
from app.models.auth_session import AuthSession
from app.models.user import User


identity_guard_logger = logging.getLogger(
    "auneron.security"
)


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


PASSWORD_CHANGE_REQUIRED_CODE = "password_change_required"
TEMP_PASSWORD_EXPIRED_CODE = "temp_password_expired"
TEMP_PASSWORD_EXPIRED_MESSAGE = (
    "A senha temporária venceu. Peça ao responsável "
    "uma nova redefinição de senha."
)
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def is_temp_password_expired(user: User) -> bool:
    """
    Senha temporaria vencida: troca obrigatoria pendente e prazo
    (temp_password_expires_at) ja passado. Sem prazo gravado nao ha
    vencimento.
    """

    expires_at = user.temp_password_expires_at

    return bool(
        user.must_change_password
        and expires_at is not None
        and expires_at < utc_now()
    )


def revoke_user_sessions(
    db: Session,
    user_id: int,
    *,
    except_session_id: int | None = None,
) -> int:
    """
    Revoga as sessoes ainda nao revogadas do usuario SEM commit: o
    chamador decide a transacao (o script de redefinicao e a troca de
    senha gravam hash e revogacao juntos). Devolve quantas revogou.
    """

    query = db.query(AuthSession).filter(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    )

    if except_session_id is not None:
        query = query.filter(
            AuthSession.id != except_session_id
        )

    now = utc_now()
    count = 0

    for auth_session in query.all():
        auth_session.revoked_at = now
        auth_session.elevated_until = None
        count += 1

    return count


class NonInteractivePrincipalError(Exception):
    """
    role=system is a non-interactive principal. It cannot authenticate
    with a password, cannot receive an AuthSession, and an AuthSession
    belonging to it (legacy or otherwise) is never a valid mechanism
    for the app to treat it as a logged-in user.
    """


class ProductionRoleNotAllowedError(Exception):
    """
    role=developer is interactive and fully valid in development/test.
    In production it is a different invariant from role=system: not an
    intrinsically non-interactive principal, but a role this
    environment does not permit for a live session. Existing
    role=developer rows are never mutated/removed by this check -- see
    check_production_developer_roles() for the read-only startup
    counterpart.
    """


def _production_role_blocked(user: User) -> bool:
    return (
        settings.environment == "production"
        and user.role == "developer"
    )


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
        or user.role == "system"
        or _production_role_blocked(user)
    ):
        return None

    return user


def create_session(
    db: Session,
    user: User,
) -> tuple[str, AuthSession]:
    if _production_role_blocked(user):
        raise ProductionRoleNotAllowedError(
            "role=developer cannot receive an AuthSession "
            "while environment=production."
        )

    if user.role == "system":
        raise NonInteractivePrincipalError(
            "role=system cannot receive an AuthSession."
        )

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


def _resolve_user_session(
    session_token: str | None,
    db: Session,
    *,
    allow_password_change: bool,
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

    if (
        user is None
        or not user.active
        or user.role == "system"
        or _production_role_blocked(user)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Usuário inativo ou indisponível."
            ),
            headers={
                "WWW-Authenticate": "Session",
            },
        )

    if user.must_change_password:
        if is_temp_password_expired(user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=TEMP_PASSWORD_EXPIRED_MESSAGE,
                headers={
                    "WWW-Authenticate": "Session",
                },
            )

        if not allow_password_change:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": PASSWORD_CHANGE_REQUIRED_CODE,
                    "message": (
                        "É obrigatório trocar a senha "
                        "temporária antes de continuar."
                    ),
                },
            )

    return AuthenticatedSession(
        user=user,
        session=auth_session,
    )


def require_user_session(
    session_token: str | None = Cookie(
        default=None,
        alias=AUTH_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
) -> AuthenticatedSession:
    return _resolve_user_session(
        session_token,
        db,
        allow_password_change=False,
    )


def require_user_session_allow_password_change(
    session_token: str | None = Cookie(
        default=None,
        alias=AUTH_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
) -> AuthenticatedSession:
    """
    Unica dependencia que aceita sessao com must_change_password=true:
    so para GET /auth/me, POST /auth/logout e POST /auth/change-password.
    """

    return _resolve_user_session(
        session_token,
        db,
        allow_password_change=True,
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


def check_production_developer_roles() -> int:
    """
    Read-only startup evidence (PR-2 DEFENSE-IN-DEPTH): in production,
    counts persisted role=developer users and emits a structured
    warning if any exist. Never mutates, deletes, disables or
    reassigns a role -- create_session()/require_user_session() are
    the enforcement boundary; this is observability only. No-op
    outside production. Never logs email/name, only a count.
    """

    if settings.environment != "production":
        return 0

    db = SessionLocal()

    try:
        count = (
            db.query(User)
            .filter(User.role == "developer")
            .count()
        )

        if count > 0:
            identity_guard_logger.warning(
                "production_developer_role_detected",
                extra={
                    "event": (
                        "production.developer_role_detected"
                    ),
                    "count": count,
                },
            )

        return count
    finally:
        db.close()


async def check_production_developer_roles_async() -> int:
    return await asyncio.to_thread(
        check_production_developer_roles
    )


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
