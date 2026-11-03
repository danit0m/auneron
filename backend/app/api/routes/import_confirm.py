"""
Rota de confirmação de importação CSV/XLSX (MVP-FIN-IMPORT-01 / TASK-024).

``create_import_confirm_router(...)`` devolve um ``APIRouter`` com
``POST /imports/confirm``. Registrada em ``app/main.py`` somente sob o guard do ambiente de teste
local explicitamente habilitado; indisponível em produção e por padrão. Tudo é injetado (parser, transporte,
plano, execução, módulo e instância do repositório, dependência de
X-API-Key e fábrica ``require_permission``); este módulo não importa
``app.services``, banco nem ``app.main``.

Auth antes de qualquer leitura do corpo: X-API-Key, depois sessão com
``imports.execute`` E ``clients.manage`` (permissões existentes). O ator é
sempre ``user:<id>`` da sessão autenticada; corpo e headers nunca o
definem. Corpo ``application/octet-stream`` limitado a
``parser.MAX_FILE_BYTES`` (5 MiB); query ``format``,
``expected_file_sha256``, ``expected_content_sha256``; header
``Idempotency-Key`` único, validado estritamente pelo plano.

201 novo, 200 replay, 409 conflitos, 413/415/422 entrada, 503 adaptador
bloqueado (protocolo de escritores não verificado ou isolamento não
suportado), 503 ``account_writers_busy`` com ``Retry-After: 5`` (lock ou
execute 55P03/57014 desfeito antes do commit), 500 genérico sem dados. Toda resposta desta rota (inclusive 401/403/422/500) sai com
``Cache-Control: no-store`` via ``route_class``; outras rotas não são
afetadas.

Identidade v2 (AREA2 TI2): query opcional ``identity_version`` (``1`` ou
``2``; ausente = 1) e ``origem`` (só v2). v2 usa ``v2_module`` e
``v2_repository`` injetados (503 se ausentes); 409 v2 carregam só números
de linha e ids de conta. A resposta v1 não muda.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.core.account_writers_busy import BUSY_CODE
from app.core.account_writers_busy import busy_detail
from app.core.account_writers_busy import busy_headers


IMPORT_CONFIRM_PATH = "/imports/confirm"
PERMISSIONS = ("imports.execute", "clients.manage")
ACCEPTED_CONTENT_TYPE = "application/octet-stream"
QUERY_FIELDS = ("format", "expected_file_sha256", "expected_content_sha256")
NO_STORE = "no-store"

# Identidade v2 (AREA2 TI2): ``identity_version`` ausente = 1; ``origem``
# só em v2. v1 inalterado (mesmo plano, execução, adaptador e resposta).
IDENTITY_VERSION_OPTIONS = {"1": 1, "2": 2}

CONFLICT_CODES = frozenset({
    'file_mismatch', 'content_mismatch',
    "existing_identity_conflict",
    "idempotency_conflict",
    "content_already_imported",
    "conflict",
})
UNAVAILABLE_CODES = frozenset({"writer_protocol_unverified", "unsupported_isolation"})
INVALID_REQUEST = {
    "code": "invalid_confirmation_request",
    "message": "Parâmetros da confirmação inválidos.",
}
UNSUPPORTED_MEDIA = {
    "code": "unsupported_media_type",
    "message": "Envie o arquivo como application/octet-stream.",
}
UNAVAILABLE = {
    "code": "import_confirmation_unavailable",
    "message": "Confirmação de importação indisponível.",
}
INTERNAL_ERROR = {
    "code": "import_confirmation_internal_error",
    "message": "Erro interno na confirmação de importação.",
}

import_confirm_logger = logging.getLogger("auneron.imports.confirm")


def _error(status_code: int, detail: dict, headers: dict | None = None):
    response = JSONResponse(
        status_code=status_code, content={"detail": dict(detail)}, headers=headers
    )
    response.headers["Cache-Control"] = NO_STORE
    return response


class NoStoreRoute(APIRoute):
    """Garante no-store em toda saída da rota, inclusive falhas de deps."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError:
                # Sem eco da entrada (o padrão do FastAPI devolveria valores).
                return _error(422, INVALID_REQUEST)
            except HTTPException as error:
                if error.status_code >= 500:
                    return _error(503, UNAVAILABLE) if error.status_code == 503 else _error(500, INTERNAL_ERROR)
                response = JSONResponse(
                    status_code=error.status_code,
                    content={"detail": error.detail},
                    headers=getattr(error, "headers", None),
                )
            except Exception as error:
                import_confirm_logger.error(
                    "import_confirm_failed",
                    extra={"error_type": type(error).__name__},
                )
                return _error(500, INTERNAL_ERROR)

            response.headers["Cache-Control"] = NO_STORE
            return response

        return guarded


class _Invalid(Exception):
    pass


class _V2NotWired(Exception):
    """Placeholder de classe de erro quando v2 não foi injetado."""


def create_import_confirm_router(
    *,
    parser_module: Any,
    transport_module: Any,
    plan_module: Any,
    execution_module: Any,
    repository_module: Any,
    repository: Any,
    require_api_key: Any,
    require_permission: Any,
    v2_module: Any = None,
    v2_repository: Any = None,
) -> APIRouter:
    parse = parser_module.parse_import_preview
    preview_error = parser_module.ImportPreviewError
    max_bytes = parser_module.MAX_FILE_BYTES
    read_body = transport_module.read_bounded_body
    transport_error = transport_module.ImportTransportError
    plan_error = plan_module.ImportConfirmationError
    execution_error = execution_module.ImportConfirmationExecutionError
    repository_error = repository_module.ImportConfirmationRepositoryError
    v2_available = v2_module is not None and v2_repository is not None
    v2_error = v2_module.ImportIdentityV2Error if v2_available else _V2NotWired
    require_execute, require_manage = (require_permission(p) for p in PERMISSIONS)

    def confirm(data: bytes, query: dict, key: str, actor: str):
        plan = plan_module.plan_import_confirmation(
            parse,
            data,
            query["format"],
            expected_file_sha256=query["expected_file_sha256"],
            expected_content_sha256=query["expected_content_sha256"],
            idempotency_key=key,
        )
        return execution_module.execute_import_confirmation(
            repository, plan, actor_reference=actor
        )

    def confirm_v2(data: bytes, query: dict, key: str, actor: str, origem):
        plan = v2_module.plan_import_confirmation_v2(
            parse,
            data,
            query["format"],
            origem=origem,
            expected_file_sha256=query["expected_file_sha256"],
            expected_content_sha256=query["expected_content_sha256"],
            idempotency_key=key,
        )
        return v2_module.execute_import_confirmation_v2(
            v2_repository, plan, actor_reference=actor
        )

    router = APIRouter(route_class=NoStoreRoute)

    @router.post(IMPORT_CONFIRM_PATH + '/', dependencies=[Depends(require_api_key)], include_in_schema=False)
    @router.post(IMPORT_CONFIRM_PATH, dependencies=[Depends(require_api_key)])
    async def confirm_import(
        request: Request,
        execute_session: Any = Depends(require_execute),
        manage_session: Any = Depends(require_manage),
    ) -> JSONResponse:
        actor = _actor(execute_session, manage_session)

        try:
            query, key = _read_request(request)
            version, origem = _read_identity(request)
        except _Invalid:
            return _error(422, INVALID_REQUEST)

        if not _single_header(request, "content-type", ACCEPTED_CONTENT_TYPE):
            return _error(415, UNSUPPORTED_MEDIA)

        if version == 2 and not v2_available:
            return _error(503, UNAVAILABLE)

        try:
            lengths = request.headers.getlist("content-length")

            if len(lengths) > 1:
                raise transport_error("invalid_content_length")

            data = await read_body(
                request.stream(),
                lengths[0] if lengths else None,
                max_bytes=max_bytes,
            )
            if version == 2:
                result = await run_in_threadpool(
                    confirm_v2, data, query, key, actor, origem
                )
            else:
                result = await run_in_threadpool(confirm, data, query, key, actor)
        except v2_error as error:
            if error.status_code >= 500:
                return _error(500, INTERNAL_ERROR)
            detail = {"code": error.code, "message": error.message}
            # Só números de linha e ids de conta; nunca valores do arquivo.
            if error.row_numbers:
                detail["rows"] = list(error.row_numbers)
            if error.account_ids:
                detail["account_ids"] = list(error.account_ids)
            return _error(error.status_code, detail)
        except preview_error as error:
            code = 413 if error.code == "file_too_large" else 422
            return _error(code, {"code": error.code, "message": error.message})
        except transport_error as error:
            if error.status_code >= 500:
                return _error(500, INTERNAL_ERROR)
            return _error(
                error.status_code, {"code": error.code, "message": error.message}
            )
        except (plan_error, execution_error, repository_error) as error:
            if error.code in UNAVAILABLE_CODES:
                return _error(503, UNAVAILABLE)
            if isinstance(error, repository_error) and error.code == BUSY_CODE:
                # Rollback antes do commit já ocorreu; sem retry automático.
                return _error(503, busy_detail(), headers=busy_headers())
            if error.code in CONFLICT_CODES:
                return _error(409, {"code": error.code, "message": error.message})
            if isinstance(error, plan_error) and error.code != "invalid_parser":
                return _error(422, {"code": error.code, "message": error.message})
            return _error(500, INTERNAL_ERROR)

        receipt = result.receipt
        content = {
            "replayed": result.replayed,
            "file_kind": receipt.file_kind,
            "record_count": receipt.record_count,
            "total": str(receipt.total),
            "account_references": list(receipt.account_references),
        }

        if version == 2:
            # Linhas já existentes e avisos só na criação; o replay devolve
            # apenas o recibo (que cobre as contas criadas).
            content["identity_version"] = 2
            content["matched_existing"] = [
                {"row_number": m.row_number, "account_id": m.account_reference}
                for m in result.matched
            ]
            content["warnings"] = [
                {
                    "row_number": w.row_number,
                    "code": w.code,
                    "account_ids": list(w.account_references),
                }
                for w in result.warnings
            ]

        return JSONResponse(
            status_code=200 if result.replayed else 201,
            content=content,
        )

    return router


def _actor(*sessions: Any) -> str:
    ids = {getattr(getattr(s, "user", None), "id", None) for s in sessions}
    user_id = ids.pop() if len(ids) == 1 else None

    if type(user_id) is not int or user_id <= 0:
        # Sessão inconsistente: falha fechada, vira 500 genérico.
        raise RuntimeError("invalid authenticated actor")

    return f"user:{user_id}"


def _read_request(request: Request) -> tuple[dict, str]:
    query = {}

    for name in QUERY_FIELDS:
        values = request.query_params.getlist(name)
        if len(values) != 1:
            raise _Invalid
        query[name] = values[0]

    keys = request.headers.getlist("idempotency-key")
    if len(keys) != 1:
        raise _Invalid

    return query, keys[0]


def _read_identity(request: Request) -> tuple[int, Any]:
    versions = request.query_params.getlist("identity_version")
    origins = request.query_params.getlist("origem")

    if len(versions) > 1 or len(origins) > 1:
        raise _Invalid

    if versions and versions[0] not in IDENTITY_VERSION_OPTIONS:
        raise _Invalid

    version = IDENTITY_VERSION_OPTIONS[versions[0]] if versions else 1

    if version == 1 and origins:
        raise _Invalid

    # ``origem`` ausente em v2 vira ``invalid_origin`` (422) no plano.
    return version, origins[0] if origins else None


def _single_header(request: Request, name: str, expected: str) -> bool:
    values = request.headers.getlist(name)
    return (
        len(values) == 1
        and values[0].split(";", 1)[0].strip().lower() == expected
    )


def confirmation_test_is_available(settings: Any, database_url: Any) -> bool:
    # Hard boundary: this release never enables confirmation outside the
    # explicitly selected, local, disposable test database.
    return (
        getattr(settings, 'environment', None) == 'test'
        and getattr(settings, 'import_confirmation_test_enabled', False) is True
        and getattr(database_url, 'database', None) == 'auneron_test'
        and getattr(database_url, 'host', None) in ('localhost', '127.0.0.1', '::1')
        and str(getattr(database_url, 'drivername', '')).split('+', 1)[0] == 'postgresql'
    )


def create_import_capabilities_router(*, enabled: bool, require_api_key: Any, require_permission: Any) -> APIRouter:
    router = APIRouter(route_class=NoStoreRoute)
    dependencies = [Depends(require_api_key), *(Depends(require_permission(p)) for p in PERMISSIONS)]
    @router.get('/imports/capabilities', dependencies=dependencies)
    async def capabilities():
        return {'confirmation_available': enabled is True}
    return router
