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

from app.core.observability import get_request_id


WORK_PATH_PREFIX = "/work-items"
MAX_WORK_REQUEST_BYTES = 512 * 1024

work_http_logger = logging.getLogger(
    "auneron.work.http"
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
        "invalid_work_request",
        "Requisição de trabalho inválida.",
    ),
    401: (
        "work_unauthenticated",
        "Autenticação necessária para acessar trabalhos.",
    ),
    403: (
        "work_forbidden",
        "Operação de trabalho não autorizada.",
    ),
    404: (
        "work_not_found",
        "Trabalho não encontrado.",
    ),
    409: (
        "work_conflict",
        "Conflito na operação de trabalho.",
    ),
    413: (
        "work_payload_too_large",
        "Payload de trabalho excede o limite permitido.",
    ),
    422: (
        "invalid_work_request",
        "Requisição de trabalho inválida.",
    ),
    429: (
        "work_rate_limited",
        "Limite de requisições de trabalho excedido.",
    ),
    503: (
        "work_unavailable",
        "Serviço de trabalho indisponível.",
    ),
    500: (
        "work_internal_error",
        "Erro interno no serviço de trabalho.",
    ),
}


def is_work_path(path: str) -> bool:
    return (
        path == WORK_PATH_PREFIX
        or path.startswith(
            f"{WORK_PATH_PREFIX}/"
        )
    )


def work_http_exception_response(
    exception: HTTPException,
) -> JSONResponse:
    code, message = _exception_contract(exception)
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


def work_validation_exception_response(
    _: RequestValidationError,
) -> JSONResponse:
    return _error_response(
        status_code=422,
        code="invalid_work_request",
        message="Requisição de trabalho inválida.",
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
            "work_request_failed",
            "Requisição de trabalho não concluída.",
        ),
    )


class _WorkPayloadTooLargeError(Exception):
    pass


class WorkHTTPMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_request_bytes: int = (
            MAX_WORK_REQUEST_BYTES
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
            or not is_work_path(
                scope.get("path", "")
            )
        ):
            await self.app(scope, receive, send)
            return

        response_started = False

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

        content_length = Headers(
            scope=scope
        ).get("content-length")

        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = 0

            if declared_length > self.max_request_bytes:
                response = _error_response(
                    status_code=413,
                    code="work_payload_too_large",
                    message=(
                        "Payload de trabalho excede "
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

                if received_bytes > self.max_request_bytes:
                    raise _WorkPayloadTooLargeError

            return message

        try:
            await self.app(
                scope,
                limited_receive,
                send_with_no_store,
            )
        except _WorkPayloadTooLargeError:
            response = _error_response(
                status_code=413,
                code="work_payload_too_large",
                message=(
                    "Payload de trabalho excede "
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
            code, message = _DEFAULT_ERRORS[status_code]

            work_http_logger.error(
                "work_http_failed",
                extra={
                    "event": "work.internal_error",
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
