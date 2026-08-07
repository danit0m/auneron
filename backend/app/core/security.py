import logging
from secrets import compare_digest

from fastapi import HTTPException
from fastapi import Security
from fastapi import status
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.core.observability import get_request_id


API_KEY_HEADER_NAME = "X-API-Key"

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    scheme_name="AuneronApiKey",
    description=(
        "Chave de acesso à API do Auneron."
    ),
    auto_error=False,
)

security_logger = logging.getLogger(
    "auneron.security"
)


def require_api_key(
    provided_api_key: str | None = Security(
        api_key_header
    ),
) -> None:
    configured_api_key = settings.api_key

    if configured_api_key is None:
        security_logger.error(
            "api_auth_unavailable",
            extra={
                "event": "api_auth",
                "request_id": get_request_id(),
                "reason": "not_configured",
            },
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Autenticação da API não configurada."
            ),
        )

    expected_api_key = (
        configured_api_key.get_secret_value()
    )

    if (
        provided_api_key is None
        or not compare_digest(
            provided_api_key,
            expected_api_key,
        )
    ):
        security_logger.warning(
            "api_auth_rejected",
            extra={
                "event": "api_auth",
                "request_id": get_request_id(),
                "reason": (
                    "missing"
                    if provided_api_key is None
                    else "invalid"
                ),
            },
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Credencial de API inválida ou ausente."
            ),
            headers={
                "WWW-Authenticate": "ApiKey",
            },
        )
