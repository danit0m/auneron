import logging
from collections.abc import Awaitable
from collections.abc import Callable

from fastapi import Request
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler as default_validation_handler,
)
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

from app.core.observability import get_request_id


MEMORY_PATH_PREFIX = "/memories"
MAX_MEMORY_REQUEST_BYTES = 512 * 1024

memory_http_logger = logging.getLogger(
    "auneron.memory.http"
)

ASGIApp = Callable[
    [Scope, Receive, Send],
    Awaitable[None],
]

_DEFAULT_ERRORS: dict[
    int,
    tuple[str, str],
] = {
    400: (
        "invalid_memory_request",
        "Requisicao de memoria invalida.",
    ),
    401: (
        "memory_unauthenticated",
        "Autenticacao necessaria para acessar memoria.",
    ),
    403: (
        "memory_forbidden",
        "Operacao de memoria nao autorizada.",
    ),
    404: (
        "memory_not_found",
        "Memoria nao encontrada.",
    ),
    409: (
        "memory_conflict",
        "Conflito na operacao de memoria.",
    ),
    413: (
        "memory_payload_too_large",
        "Payload de memoria excede o limite permitido.",
    ),
    422: (
        "invalid_memory_request",
        "Requisicao de memoria invalida.",
    ),
    429: (
        "memory_rate_limited",
        "Limite de requisicoes de memoria excedido.",
    ),
    503: (
        "memory_unavailable",
        "Servico de memoria indisponivel.",
    ),
    500: (
        "memory_internal_error",
        "Erro interno no servico de memoria.",
    ),
}


def is_memory_path(path: str) -> bool:
    return (
        path == MEMORY_PATH_PREFIX
        or path.startswith(
            f"{MEMORY_PATH_PREFIX}/"
        )
    )


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    response_headers["Cache-Control"] = "no-store"

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": get_request_id(),
            }
        },
        headers=response_headers,
    )


def _exception_contract(
    exception: HTTPException,
) -> tuple[str, str]:
    detail = exception.detail

    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")

        if isinstance(code, str) and isinstance(
            message,
            str,
        ):
            return code, message

    return _DEFAULT_ERRORS.get(
        exception.status_code,
        (
            "memory_request_failed",
            "Requisicao de memoria nao concluida.",
        ),
    )


async def memory_http_exception_handler(
    request: Request,
    exception: HTTPException,
) -> Response:
    if not is_memory_path(
        request.url.path
    ):
        return await default_http_exception_handler(
            request,
            exception,
        )

    code, message = _exception_contract(
        exception
    )

    return _error_response(
        status_code=exception.status_code,
        code=code,
        message=message,
        headers=(
            dict(exception.headers)
            if exception.headers is not None
            else None
        ),
    )


async def memory_validation_exception_handler(
    request: Request,
    exception: RequestValidationError,
) -> Response:
    if not is_memory_path(
        request.url.path
    ):
        return await default_validation_handler(
            request,
            exception,
        )

    return _error_response(
        status_code=422,
        code="invalid_memory_request",
        message="Requisicao de memoria invalida.",
    )


class _MemoryPayloadTooLargeError(Exception):
    pass


class MemoryHTTPMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_request_bytes: int = (
            MAX_MEMORY_REQUEST_BYTES
        ),
    ) -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or not is_memory_path(
                scope.get("path", "")
            )
        ):
            await self.app(
                scope,
                receive,
                send,
            )
            return

        async def send_with_no_store(
            message: Message,
        ) -> None:
            nonlocal response_started

            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(
                    scope=message
                )
                headers["Cache-Control"] = "no-store"

            await send(message)

        response_started = False
        content_length = Headers(
            scope=scope
        ).get("content-length")

        if content_length is not None:
            try:
                declared_length = int(
                    content_length
                )
            except ValueError:
                declared_length = 0

            if declared_length > self.max_request_bytes:
                response = _error_response(
                    status_code=413,
                    code="memory_payload_too_large",
                    message=(
                        "Payload de memoria excede "
                        "o limite permitido."
                    ),
                )
                await response(
                    scope,
                    receive,
                    send_with_no_store,
                )
                return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes

            message = await receive()

            if message["type"] == "http.request":
                received_bytes += len(
                    message.get("body", b"")
                )

                if (
                    received_bytes
                    > self.max_request_bytes
                ):
                    raise _MemoryPayloadTooLargeError

            return message

        try:
            await self.app(
                scope,
                limited_receive,
                send_with_no_store,
            )
        except _MemoryPayloadTooLargeError:
            response = _error_response(
                status_code=413,
                code="memory_payload_too_large",
                message=(
                    "Payload de memoria excede "
                    "o limite permitido."
                ),
            )
            await response(
                scope,
                receive,
                send_with_no_store,
            )
        except Exception as error:
            if response_started:
                raise

            unavailable = isinstance(
                error,
                OperationalError,
            )
            status_code = 503 if unavailable else 500
            code, message = _DEFAULT_ERRORS[
                status_code
            ]

            memory_http_logger.error(
                "memory_http_failed",
                extra={
                    "event": "memory.internal_error",
                    "request_id": get_request_id(),
                    "status_code": status_code,
                    "error_type": type(error).__name__,
                },
            )

            response = _error_response(
                status_code=status_code,
                code=code,
                message=message,
            )
            await response(
                scope,
                receive,
                send_with_no_store,
            )
