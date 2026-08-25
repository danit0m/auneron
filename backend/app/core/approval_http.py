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


APPROVAL_PATH_PREFIX = "/approvals"
MAX_APPROVAL_REQUEST_BYTES = 128 * 1024

approval_http_logger = logging.getLogger(
    "auneron.approval.http"
)

ASGIApp = Callable[
    [Scope, Receive, Send],
    Awaitable[None],
]

_APPROVAL_DETAIL_CODES = frozenset({
    "approval_not_found",
    "approval_forbidden",
    "approval_conflict",
    "approval_idempotency_conflict",
    "approval_expired",
    "approval_elevation_required",
    "invalid_approval_state",
    "invalid_approval_request",
})

_DEFAULT_ERRORS: dict[
    int,
    tuple[str, str],
] = {
    400: (
        "invalid_approval_request",
        "Requisição de aprovação inválida.",
    ),
    401: (
        "approval_unauthenticated",
        "Autenticação necessária para aprovações.",
    ),
    403: (
        "approval_forbidden",
        "Operação de aprovação não autorizada.",
    ),
    404: (
        "approval_not_found",
        "Solicitação de aprovação não encontrada.",
    ),
    409: (
        "approval_conflict",
        "Conflito na operação de aprovação.",
    ),
    413: (
        "approval_payload_too_large",
        "Payload de aprovação excede o limite permitido.",
    ),
    422: (
        "invalid_approval_request",
        "Requisição de aprovação inválida.",
    ),
    500: (
        "approval_internal_error",
        "Erro interno no serviço de aprovação.",
    ),
    503: (
        "approval_unavailable",
        "Serviço de aprovação indisponível.",
    ),
}


class _ApprovalPayloadTooLarge(Exception):
    pass


def is_approval_path(
    path: str,
) -> bool:
    return (
        path == APPROVAL_PATH_PREFIX
        or path.startswith(
            APPROVAL_PATH_PREFIX + "/"
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


def approval_http_exception_response(
    exception: HTTPException,
) -> Response:
    default_code, default_message = (
        _DEFAULT_ERRORS.get(
            exception.status_code,
            (
                "approval_request_failed",
                "A requisição de aprovação não pôde "
                "ser concluída.",
            ),
        )
    )

    detail = exception.detail
    if (
        isinstance(detail, dict)
        and detail.get("code")
        in _APPROVAL_DETAIL_CODES
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


def approval_validation_exception_response(
    _: RequestValidationError,
) -> Response:
    return _error_response(
        status_code=422,
        code="invalid_approval_request",
        message="Requisição de aprovação inválida.",
    )


async def application_http_exception_handler(
    request: Request,
    exception: HTTPException,
) -> Response:
    if is_approval_path(
        request.url.path
    ):
        return approval_http_exception_response(
            exception
        )

    from app.core.skill_http import (
        application_http_exception_handler
        as next_http_exception_handler,
    )

    return await next_http_exception_handler(
        request,
        exception,
    )


async def application_validation_exception_handler(
    request: Request,
    exception: RequestValidationError,
) -> Response:
    if is_approval_path(
        request.url.path
    ):
        return approval_validation_exception_response(
            exception
        )

    from app.core.skill_http import (
        application_validation_exception_handler
        as next_validation_exception_handler,
    )

    return await next_validation_exception_handler(
        request,
        exception,
    )


class ApprovalHTTPMiddleware:
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
            or not is_approval_path(
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
                declared_size = -1

            if (
                declared_size < 0
                or declared_size
                > MAX_APPROVAL_REQUEST_BYTES
            ):
                await _error_response(
                    status_code=(
                        400
                        if declared_size < 0
                        else 413
                    ),
                    code=(
                        "invalid_approval_request"
                        if declared_size < 0
                        else "approval_payload_too_large"
                    ),
                    message=(
                        "Requisição de aprovação inválida."
                        if declared_size < 0
                        else (
                            "Payload de aprovação excede "
                            "o limite permitido."
                        )
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
                    > MAX_APPROVAL_REQUEST_BYTES
                ):
                    raise _ApprovalPayloadTooLarge

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
        except _ApprovalPayloadTooLarge:
            if response_started:
                raise
            await _error_response(
                status_code=413,
                code="approval_payload_too_large",
                message=(
                    "Payload de aprovação excede "
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

            approval_http_logger.error(
                "approval.http_database_unavailable",
                extra={
                    "event": (
                        "approval.http_database_unavailable"
                    ),
                    "request_id": get_request_id(),
                },
            )
            await _error_response(
                status_code=503,
                code="approval_unavailable",
                message=(
                    "Serviço de aprovação indisponível."
                ),
            )(
                scope,
                receive,
                send,
            )
        except Exception:
            if response_started:
                raise

            approval_http_logger.error(
                "approval.http_internal_error",
                extra={
                    "event": (
                        "approval.http_internal_error"
                    ),
                    "request_id": get_request_id(),
                },
            )
            await _error_response(
                status_code=500,
                code="approval_internal_error",
                message=(
                    "Erro interno no serviço de aprovação."
                ),
            )(
                scope,
                receive,
                send,
            )
