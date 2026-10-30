"""
Rota de pré-visualização de importação CSV/XLSX (MVP-FIN-IMPORT-01 /
TASK-017).

``create_import_preview_router(parser_module, transport_module)`` devolve
um ``APIRouter`` com ``POST /imports/preview``. O parser e o transporte
reais são injetados na criação; este módulo não importa ``app.services``
(cujo ``__init__`` acopla banco) nem ``app.main``.

Somente leitura: não acessa banco, não grava arquivo, não registra log.
Corpo bruto ``application/octet-stream`` lido em memória com limite de
bytes; sem multipart e sem nome de arquivo do cliente.

NÃO registrado no app real. Integração futura (não aplicada) em
``app/main.py``::

    from app.api.routes.import_preview import create_import_preview_router
    from app.services import import_preview_parser
    from app.services import import_preview_transport

    app.include_router(
        create_import_preview_router(
            import_preview_parser,
            import_preview_transport,
        ),
        dependencies=business_dependencies("imports.execute"),
    )

junto com ``ImportPreviewHTTPMiddleware`` (ver app/core/import_preview_http).
A autenticação e o RBAC vêm desse include, não deste módulo.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.import_preview_http import IMPORT_PREVIEW_PATH
from app.core.import_preview_http import NO_STORE
from app.core.import_preview_http import internal_error_body


ACCEPTED_CONTENT_TYPE = "application/octet-stream"
FORMATS = ("csv", "xlsx")
INCLUDE_VALUES_OPTIONS = {"true": True, "false": False}

INVALID_REQUEST_DETAIL = {
    "code": "invalid_preview_request",
    "message": "Parâmetros da pré-visualização inválidos.",
}
UNSUPPORTED_MEDIA_DETAIL = {
    "code": "unsupported_media_type",
    "message": "Envie o arquivo como application/octet-stream.",
}


class _InvalidPreviewRequest(Exception):
    pass


def create_import_preview_router(
    parser_module: Any,
    transport_module: Any,
) -> APIRouter:
    parse_import_preview = parser_module.parse_import_preview
    import_preview_error = parser_module.ImportPreviewError
    max_file_bytes = parser_module.MAX_FILE_BYTES

    read_bounded_body = transport_module.read_bounded_body
    serialize_preview = transport_module.serialize_preview
    import_transport_error = transport_module.ImportTransportError
    response_headers = dict(transport_module.PREVIEW_RESPONSE_HEADERS)

    router = APIRouter()

    @router.post(IMPORT_PREVIEW_PATH, response_class=JSONResponse)
    async def preview_import(request: Request) -> JSONResponse:
        try:
            file_format, include_values = _read_query(request)
        except _InvalidPreviewRequest:
            return _error(422, INVALID_REQUEST_DETAIL)

        if not _accepts_content_type(request):
            return _error(415, UNSUPPORTED_MEDIA_DETAIL)

        try:
            content_length = _single_content_length(
                request,
                import_transport_error,
            )
            data = await read_bounded_body(
                request.stream(),
                content_length,
                max_bytes=max_file_bytes,
            )
            preview = await run_in_threadpool(
                parse_import_preview,
                f"preview.{file_format}",
                data,
            )
            content = serialize_preview(
                preview,
                include_values=include_values,
            )
        except import_preview_error as error:
            status_code = 413 if error.code == "file_too_large" else 422
            return _error(
                status_code,
                {"code": error.code, "message": error.message},
            )
        except import_transport_error as error:
            if error.status_code >= 500:
                return JSONResponse(
                    status_code=500,
                    content=internal_error_body(),
                    headers={"Cache-Control": NO_STORE},
                )

            return _error(
                error.status_code,
                {"code": error.code, "message": error.message},
            )

        return JSONResponse(
            status_code=200,
            content=content,
            headers=response_headers,
        )

    return router


def _read_query(request: Request) -> tuple[str, bool]:
    params = request.query_params
    formats = params.getlist("format")

    if len(formats) != 1 or formats[0] not in FORMATS:
        raise _InvalidPreviewRequest

    include = params.getlist("include_values")

    if not include:
        return formats[0], False

    if len(include) != 1 or include[0] not in INCLUDE_VALUES_OPTIONS:
        raise _InvalidPreviewRequest

    return formats[0], INCLUDE_VALUES_OPTIONS[include[0]]


def _accepts_content_type(request: Request) -> bool:
    values = request.headers.getlist("content-type")

    if len(values) != 1:
        return False

    media_type = values[0].split(";", 1)[0].strip().lower()

    return media_type == ACCEPTED_CONTENT_TYPE


def _single_content_length(
    request: Request,
    import_transport_error: type[Exception],
) -> str | None:
    values = request.headers.getlist("content-length")

    if len(values) > 1:
        raise import_transport_error("invalid_content_length")

    return values[0] if values else None


def _error(status_code: int, detail: dict[str, str]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": dict(detail)},
        headers={"Cache-Control": NO_STORE},
    )
