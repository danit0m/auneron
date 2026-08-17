import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime
from datetime import timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from app.core.config import settings


REQUEST_ID_HEADER_NAME = "X-Request-ID"

_REQUEST_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,128}$"
)

_request_id_context: ContextVar[
    str | None
] = ContextVar(
    "auneron_request_id",
    default=None,
)

_STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "database_url",
    "idempotency",
    "password",
    "secret",
    "token",
)


def get_request_id() -> str | None:
    return _request_id_context.get()


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()

    return any(
        part in normalized
        for part in _SENSITIVE_KEY_PARTS
    )


def _sanitize_value(
    key: str,
    value: Any,
) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"

    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_value(
                str(child_key),
                child_value,
            )
            for child_key, child_value
            in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _sanitize_value(
                key,
                item,
            )
            for item in value
        ]

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
            type(None),
        ),
    ):
        return value

    return str(value)


class JsonLogFormatter(logging.Formatter):
    def format(
        self,
        record: logging.LogRecord,
    ) -> str:
        timestamp = datetime.fromtimestamp(
            record.created,
            tz=timezone.utc,
        ).isoformat(
            timespec="milliseconds"
        )

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS:
                continue

            payload[key] = _sanitize_value(
                key,
                value,
            )

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def configure_logging() -> None:
    application_logger = logging.getLogger(
        "auneron"
    )
    application_logger.setLevel(
        settings.log_level
    )
    application_logger.propagate = False

    existing_handler = next(
        (
            handler
            for handler
            in application_logger.handlers
            if getattr(
                handler,
                "_auneron_handler",
                False,
            )
        ),
        None,
    )

    if existing_handler is not None:
        existing_handler.setLevel(
            settings.log_level
        )
        return

    handler = logging.StreamHandler(
        sys.stdout
    )
    handler.setLevel(
        settings.log_level
    )
    handler.setFormatter(
        JsonLogFormatter()
    )
    handler._auneron_handler = True

    application_logger.addHandler(
        handler
    )


http_logger = logging.getLogger(
    "auneron.http"
)


def _resolve_request_id(
    scope: Scope,
) -> str:
    provided_request_id = Headers(
        scope=scope
    ).get(
        REQUEST_ID_HEADER_NAME
    )

    if (
        provided_request_id
        and _REQUEST_ID_PATTERN.fullmatch(
            provided_request_id
        )
    ):
        return provided_request_id

    return str(uuid4())


def _request_log_fields(
    *,
    scope: Scope,
    request_id: str,
    status_code: int,
    duration_ms: float,
) -> dict[str, Any]:
    return {
        "event": "http_request",
        "request_id": request_id,
        "method": scope.get(
            "method",
            "UNKNOWN",
        ),
        "path": scope.get(
            "path",
            "",
        ),
        "status_code": status_code,
        "duration_ms": round(
            duration_ms,
            3,
        ),
        "environment": settings.environment,
    }


class RequestObservabilityMiddleware:
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
        if scope["type"] != "http":
            await self.app(
                scope,
                receive,
                send,
            )
            return

        request_id = _resolve_request_id(
            scope
        )
        token = _request_id_context.set(
            request_id
        )
        started_at = perf_counter()
        status_code = 500

        async def send_with_request_id(
            message: Message,
        ) -> None:
            nonlocal status_code

            if (
                message["type"]
                == "http.response.start"
            ):
                status_code = message[
                    "status"
                ]

                response_headers = MutableHeaders(
                    scope=message
                )
                response_headers[
                    REQUEST_ID_HEADER_NAME
                ] = request_id

            await send(message)

        try:
            await self.app(
                scope,
                receive,
                send_with_request_id,
            )
        except Exception as error:
            duration_ms = (
                perf_counter()
                - started_at
            ) * 1000

            fields = _request_log_fields(
                scope=scope,
                request_id=request_id,
                status_code=500,
                duration_ms=duration_ms,
            )
            fields["error_type"] = (
                type(error).__name__
            )

            http_logger.error(
                "http_request_failed",
                extra=fields,
            )
            raise
        else:
            duration_ms = (
                perf_counter()
                - started_at
            ) * 1000

            fields = _request_log_fields(
                scope=scope,
                request_id=request_id,
                status_code=status_code,
                duration_ms=duration_ms,
            )

            log_level = (
                logging.WARNING
                if status_code >= 400
                else logging.INFO
            )

            http_logger.log(
                log_level,
                "http_request_completed",
                extra=fields,
            )
        finally:
            _request_id_context.reset(
                token
            )
