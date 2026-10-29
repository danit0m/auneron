"""
Fronteira HTTP da pré-visualização de importação (MVP-FIN-IMPORT-01 /
TASK-017).

Middleware ASGI puro, restrito ao path exato ``/imports/preview`` e à sua
barra final. Outros paths e escopos passam sem alteração. No path
protegido:

- todo ``http.response.start`` (2xx/3xx/4xx/5xx) sai com um único
  ``Cache-Control: no-store``, substituindo qualquer valor conflitante;
- uma exceção inesperada antes do início da resposta vira 500 JSON fixo,
  sem detalhes e com no-store;
- depois de a resposta ter começado, nenhuma segunda resposta é enviada e
  a exceção é propagada;
- cancelamento (``BaseException``) e desconexão do cliente propagam sem
  resposta deste middleware.

Logs registram apenas o tipo da exceção: nunca payload, query, headers,
nome de arquivo ou mensagem da exceção.

Este módulo NÃO está registrado em ``app/main.py``. Integração futura
(não aplicada): ``app.add_middleware(ImportPreviewHTTPMiddleware)`` como
última chamada ``add_middleware`` (camada de usuário mais externa, ainda
dentro do ``ServerErrorMiddleware`` do Starlette), para que nenhum
middleware externo a ela reescreva o Cache-Control do path protegido.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from starlette.requests import ClientDisconnect


IMPORT_PREVIEW_PATH = "/imports/preview"

NO_STORE = "no-store"

INTERNAL_ERROR_DETAIL = MappingProxyType({
    "code": "import_preview_internal_error",
    "message": "Erro interno na pré-visualização de importação.",
})

_PROTECTED_PATHS = frozenset({
    IMPORT_PREVIEW_PATH,
    f"{IMPORT_PREVIEW_PATH}/",
})

_CACHE_CONTROL = b"cache-control"

import_preview_http_logger = logging.getLogger(
    "auneron.imports.http"
)

Message = dict[str, Any]
Scope = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def is_import_preview_path(path: Any) -> bool:
    return type(path) is str and path in _PROTECTED_PATHS


def internal_error_body() -> dict[str, Any]:
    return {"detail": dict(INTERNAL_ERROR_DETAIL)}


def with_no_store(message: Message) -> Message:
    """
    Devolve cópia de ``http.response.start`` com exatamente um
    ``Cache-Control: no-store``. Outras mensagens não são alteradas.
    """

    if message.get("type") != "http.response.start":
        return message

    headers = [
        (name, value)
        for name, value in message.get("headers", ())
        if bytes(name).lower() != _CACHE_CONTROL
    ]
    headers.append((_CACHE_CONTROL, NO_STORE.encode("ascii")))

    return {**message, "headers": headers}


class ImportPreviewHTTPMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope.get("type") != "http"
            or not is_import_preview_path(scope.get("path"))
        ):
            await self.app(scope, receive, send)
            return

        response_started = False
        disconnected = False

        async def tracking_receive() -> Message:
            nonlocal disconnected

            message = await receive()

            if message.get("type") == "http.disconnect":
                disconnected = True

            return message

        async def send_with_no_store(message: Message) -> None:
            nonlocal response_started

            if message.get("type") == "http.response.start":
                response_started = True

            await send(with_no_store(message))

        try:
            await self.app(scope, tracking_receive, send_with_no_store)
        except Exception as error:
            if (
                response_started
                or disconnected
                or isinstance(error, ClientDisconnect)
            ):
                raise

            import_preview_http_logger.error(
                "import_preview_http_failed",
                extra={
                    "event": "imports.preview.internal_error",
                    "status_code": 500,
                    "error_type": type(error).__name__,
                },
            )

            await _send_internal_error(send_with_no_store)


async def _send_internal_error(send: Send) -> None:
    body = json.dumps(
        internal_error_body(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    await send({
        "type": "http.response.start",
        "status": 500,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })
