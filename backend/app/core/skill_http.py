import logging
from collections.abc import Awaitable
from collections.abc import Callable

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.datastructures import Headers
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from app.core.memory_http import (
    memory_http_exception_handler,
)
from app.core.memory_http import (
    memory_validation_exception_handler,
)
from app.core.observability import get_request_id


SKILL_PATH_PREFIX = "/agent-skills"
MAX_SKILL_REQUEST_BYTES = 128 * 1024

skill_http_logger = logging.getLogger(
    "auneron.skill.http"
)

ASGIApp = Callable[
    [Scope, Receive, Send],
    Awaitable[None],
]

_SKILL_DETAIL_CODES = frozenset({
    "skill_not_found",
    "skill_forbidden",
    "skill_conflict",
    "skill_idempotency_conflict",
    "skill_invocation_in_progress",
    "invalid_skill_state",
    "invalid_skill_request",
    "skill_runtime_busy",
    "skill_rate_limited",
    "skill_timeout",
    "skill_runtime_unavailable",
    "skill_execution_failed",
})

_DEFAULT_ERRORS: dict[
    int,
    tuple[str, str],
] = {
    400: (
        "invalid_skill_request",
        "Requisição de skill inválida.",
    ),
    401: (
        "skill_unauthenticated",
        "Autenticação necessária para executar skills.",
    ),
    403: (
        "skill_forbidden",
        "Execução de skill não autorizada.",
    ),
    404: (
        "skill_not_found",
        "Skill não encontrada.",
    ),
    409: (
        "skill_conflict",
        "Conflito na execução de skill.",
    ),
    413: (
        "skill_payload_too_large",
        "Payload de skill excede o limite permitido.",
    ),
    422: (
        "invalid_skill_request",
        "Requisição de skill inválida.",
    ),
    429: (
        "skill_rate_limited",
        "Limite de requisições de skill excedido.",
    ),
    500: (
        "skill_internal_error",
        "Erro interno no serviço de skills.",
    ),
    502: (
        "skill_execution_failed",
        "A execução da skill falhou.",
    ),
    503: (
        "skill_runtime_unavailable",
        "Runtime de skills indisponível.",
    ),
    504: (
        "skill_timeout",
        "A execução da skill excedeu o tempo permitido.",
    ),
}


class _SkillPayloadTooLarge(Exception):
    pass


def is_skill_path(path: str) -> bool:
    return (
        path == SKILL_PATH_PREFIX
        or path.startswith(
            SKILL_PATH_PREFIX + "/"
        )
    )


def _safe_exception_headers(
    exception: HTTPException,
) -> dict[str, str]:
    headers = exception.headers or {}
    allowed = {
        "www-authenticate",
        "retry-after",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in allowed
    }


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": get_request_id(),
            }
        },
        headers=headers,
    )
    response.headers[
        "Cache-Control"
    ] = "no-store"
    return response


def skill_http_exception_response(
    exception: HTTPException,
) -> Response:
    default_code, default_message = (
        _DEFAULT_ERRORS.get(
            exception.status_code,
            (
                "skill_request_failed",
                "A requisição de skill não pôde ser concluída.",
            ),
        )
    )

    detail = exception.detail
    if (
        isinstance(detail, dict)
        and detail.get("code")
        in _SKILL_DETAIL_CODES
        and isinstance(
            detail.get("message"),
            str,
        )
    ):
        code = detail["code"]
        message = detail["message"]
    else:
        code = default_code
        message = default_message

    return _error_response(
        status_code=exception.status_code,
        code=code,
        message=message,
        headers=_safe_exception_headers(
            exception
        ),
    )


def skill_validation_exception_response(
    _: RequestValidationError,
) -> Response:
    return _error_response(
        status_code=422,
        code="invalid_skill_request",
        message="Requisição de skill inválida.",
    )


async def application_http_exception_handler(
    request: Request,
    exception: HTTPException,
) -> Response:
    if is_skill_path(
        request.url.path
    ):
        return skill_http_exception_response(
            exception
        )

    return await memory_http_exception_handler(
        request,
        exception,
    )


async def application_validation_exception_handler(
    request: Request,
    exception: RequestValidationError,
) -> Response:
    if is_skill_path(
        request.url.path
    ):
        return skill_validation_exception_response(
            exception
        )

    return await memory_validation_exception_handler(
        request,
        exception,
    )


class SkillHTTPMiddleware:
    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or not is_skill_path(
                scope.get("path", "")
            )
        ):
            await self.app(
                scope,
                receive,
                send,
            )
            return

        headers = Headers(
            scope=scope
        )
        content_length = headers.get(
            "content-length"
        )

        if content_length is not None:
            try:
                declared_size = int(
                    content_length
                )
            except ValueError:
                await _error_response(
                    status_code=400,
                    code="invalid_skill_request",
                    message="Requisição de skill inválida.",
                )(
                    scope,
                    receive,
                    send,
                )
                return

            if (
                declared_size < 0
                or declared_size
                > MAX_SKILL_REQUEST_BYTES
            ):
                await _error_response(
                    status_code=413,
                    code="skill_payload_too_large",
                    message=(
                        "Payload de skill excede "
                        "o limite permitido."
                    ),
                )(
                    scope,
                    receive,
                    send,
                )
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes

            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(
                    message.get(
                        "body",
                        b"",
                    )
                )
                if (
                    received_bytes
                    > MAX_SKILL_REQUEST_BYTES
                ):
                    raise _SkillPayloadTooLarge

            return message

        async def no_store_send(
            message: Message,
        ) -> None:
            nonlocal response_started

            if (
                message["type"]
                == "http.response.start"
            ):
                response_started = True
                mutable = MutableHeaders(
                    scope=message
                )
                mutable[
                    "Cache-Control"
                ] = "no-store"

            await send(message)

        try:
            await self.app(
                scope,
                limited_receive,
                no_store_send,
            )
        except _SkillPayloadTooLarge:
            if response_started:
                raise
            await _error_response(
                status_code=413,
                code="skill_payload_too_large",
                message=(
                    "Payload de skill excede "
                    "o limite permitido."
                ),
            )(
                scope,
                receive,
                send,
            )
        except OperationalError:
            if response_started:
                raise
            skill_http_logger.error(
                "skill.http_database_unavailable",
                extra={
                    "event": (
                        "skill.http_database_unavailable"
                    ),
                    "request_id": get_request_id(),
                },
            )
            await _error_response(
                status_code=503,
                code="skill_runtime_unavailable",
                message="Runtime de skills indisponível.",
            )(
                scope,
                receive,
                send,
            )
        except Exception:
            if response_started:
                raise
            skill_http_logger.error(
                "skill.http_internal_error",
                extra={
                    "event": (
                        "skill.http_internal_error"
                    ),
                    "request_id": get_request_id(),
                },
            )
            await _error_response(
                status_code=500,
                code="skill_internal_error",
                message=(
                    "Erro interno no serviço de skills."
                ),
            )(
                scope,
                receive,
                send,
            )
