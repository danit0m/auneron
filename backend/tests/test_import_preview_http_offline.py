"""
Testes offline do adaptador HTTP e do middleware no-store da
pré-visualização de importação (MVP-FIN-IMPORT-01 / TASK-017).

Sem conftest, banco, rede, servidor ou httpx: cada teste monta um app
FastAPI DESCARTÁVEL e o chama com scope/receive/send ASGI crus via
``asyncio.run``. O parser e o transporte REAIS são carregados pelo caminho
do arquivo, sem executar ``app/services/__init__.py``.

Isto prova o isolamento do adaptador, NÃO a autenticação/RBAC real: os
status 401/403/503 abaixo são simulados por dependências falsas no app
descartável. A rota não está registrada em ``app/main.py``.
"""

import ast
import asyncio
import builtins
import importlib
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import anyio
import anyio.to_thread
import openpyxl
import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from starlette.requests import ClientDisconnect


BACKEND_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = BACKEND_DIR / "app" / "services"
PARSER_PATH = SERVICES_DIR / "import_preview_parser.py"
TRANSPORT_PATH = SERVICES_DIR / "import_preview_transport.py"
ROUTE_PATH = BACKEND_DIR / "app" / "api" / "routes" / "import_preview.py"
HTTP_PATH = BACKEND_DIR / "app" / "core" / "import_preview_http.py"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Pureza medida num processo NOVO: o sys.modules deste processo pode já
# conter app.main/app.services/sqlalchemy (coleta do pytest, conftest), o
# que faria uma diferença antes/depois aqui passar sem medir nada. -I
# ignora PYTHON* e o site do usuário; o ambiente mínimo não carrega
# DATABASE_URL nem rede.
_FRESH_IMPORT_PROBE = """
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
for name in json.loads(sys.argv[2]):
    importlib.import_module(name)
print(json.dumps(sorted(sys.modules)))
"""


def _modules_after_fresh_import(*names):
    env = {key: os.environ[key] for key in ("SYSTEMROOT",) if key in os.environ}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _FRESH_IMPORT_PROBE,
         str(BACKEND_DIR), json.dumps(list(names))],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return set(json.loads(result.stdout))


route_module = importlib.import_module("app.api.routes.import_preview")
http_module = importlib.import_module("app.core.import_preview_http")

parser = _load("import_preview_parser_for_http_test", PARSER_PATH)
transport = _load("import_preview_transport_for_http_test", TRANSPORT_PATH)

create_import_preview_router = route_module.create_import_preview_router
ImportPreviewHTTPMiddleware = http_module.ImportPreviewHTTPMiddleware

PATH = "/imports/preview"
OCTET = (b"content-type", b"application/octet-stream")
PII_SENTINEL = "Fulana Sigilosa da Silva"
PII_EMAIL = "fulana.sigilosa@example.com"
PII_PHONE = "+55 (11) 98888-7777"
CANARY = "PII-CANARY-Fulana"
HEADER = "cliente;valor;vencimento;status;email;whatsapp"
GENERIC_500 = {
    "detail": {
        "code": "import_preview_internal_error",
        "message": "Erro interno na pré-visualização de importação.",
    }
}


# ------------------------------------------------------------ utilitários


class Receiver:
    """
    ``receive`` ASGI sintético. Conta mensagens ``http.request`` entregues
    e devolve ``http.disconnect`` quando o corpo acaba (ou de início).
    """

    def __init__(self, chunks=(b"",), *, disconnect_first=False, error=None):
        self.messages = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }
            for index, chunk in enumerate(chunks)
        ]
        self.disconnect_first = disconnect_first
        self.error = error
        self.consumed = 0

    async def __call__(self):
        if self.error is not None:
            raise self.error

        if not self.disconnect_first and self.consumed < len(self.messages):
            message = self.messages[self.consumed]
            self.consumed += 1
            return message

        return {"type": "http.disconnect"}


class Result:
    def __init__(self, sent, receiver):
        self.sent = sent
        self.receiver = receiver
        starts = [m for m in sent if m["type"] == "http.response.start"]
        self.starts = starts
        self.status = starts[0]["status"] if starts else None
        self.headers = list(starts[0].get("headers", [])) if starts else []
        self.body = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )

    def header_values(self, name):
        return [
            value for key, value in self.headers
            if key.lower() == name.lower()
        ]

    def json(self):
        return json.loads(self.body)


def _scope(method="POST", path=PATH, query=b"format=csv", headers=None):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": query,
        "headers": [OCTET] if headers is None else list(headers),
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


def _call(app, *, receiver=None, chunks=(b"",), **scope_kwargs):
    receiver = receiver or Receiver(chunks)
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(app(_scope(**scope_kwargs), receiver, send))
    return Result(sent, receiver)


def _build_app(parser_module=None, transport_module=None, dependencies=None):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.include_router(
        create_import_preview_router(
            parser_module or parser,
            transport_module or transport,
        ),
        dependencies=dependencies or [],
    )
    app.add_middleware(ImportPreviewHTTPMiddleware)
    return app


def _module_proxy(module, **overrides):
    """
    Proxy do módulo REAL com atributos substituídos para injeção de falhas
    ou espionagem; o restante delega ao módulo carregado do arquivo.
    """

    names = {
        name: getattr(module, name)
        for name in dir(module)
        if not name.startswith("__")
    }
    names.update(overrides)
    return SimpleNamespace(**names)


def _csv(*lines):
    return "\r\n".join(lines).encode("utf-8")


def _mixed_csv():
    return _csv(
        HEADER,
        f"{PII_SENTINEL};R$ 1.234,56;05/01/2026;Aberto;{PII_EMAIL};"
        f"{PII_PHONE}",
        f"{PII_SENTINEL} B;-5;2026-12-31;aberto;;",
        f"{PII_SENTINEL} C;10;2026-02-30;pago;;",
        "Beto Lima;10,00;2026-03-01;;;",
    )


def _many_rows_csv(count):
    lines = [HEADER]
    for index in range(count):
        if index % 10 == 9:
            lines.append(f"Cliente {index:04d};-1;2026-01-01;;;")
        else:
            lines.append(f"Cliente {index:04d};{index + 1},25;2026-01-01;;;")
    return _csv(*lines)


def _xlsx(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _assert_no_store(result):
    assert result.header_values(b"cache-control") == [b"no-store"]


def _assert_fixed_error(result, status, code):
    assert result.status == status
    _assert_no_store(result)
    body = result.json()
    assert set(body) == {"detail"}
    assert set(body["detail"]) == {"code", "message"}
    assert body["detail"]["code"] == code
    return body


# ----------------------------------------------------------- 200: sucesso


def test_csv_preview_default_omits_values_and_pii():
    result = _call(_build_app(), chunks=(_mixed_csv(),))

    assert result.status == 200
    _assert_no_store(result)
    assert result.header_values(b"content-type") == [b"application/json"]
    body = result.json()
    assert body["file_kind"] == "csv"
    assert (body["total_rows"], body["valid_rows"], body["invalid_rows"]) == (
        4, 2, 2,
    )
    assert body["valid_total"] == "1244.56"
    assert body["returned_rows"] == 4
    assert body["rows_truncated"] is False
    assert all("values" not in row for row in body["rows"])
    text = result.body.decode("utf-8")
    for secret in (PII_SENTINEL, PII_EMAIL, PII_PHONE, "preview.csv", "cliente;"):
        assert secret not in text


def test_csv_preview_matches_real_transport_serialization():
    data = _mixed_csv()
    result = _call(_build_app(), chunks=(data[:7], data[7:40], data[40:]))

    expected = transport.serialize_preview(
        parser.parse_import_preview("preview.csv", data)
    )
    assert result.status == 200
    assert result.json() == json.loads(json.dumps(expected))
    assert result.receiver.consumed == 3


def test_xlsx_preview_with_values_opt_in():
    data = _xlsx([
        ["cliente", "valor", "vencimento", "status", "email"],
        [PII_SENTINEL, "10,00", "2026-12-31", "aberto", PII_EMAIL],
        ["X", "10,00", "2026-12-31", "aberto", None],
    ])
    result = _call(
        _build_app(),
        query=b"format=xlsx&include_values=true",
        chunks=(data,),
    )

    assert result.status == 200
    _assert_no_store(result)
    body = result.json()
    assert body["file_kind"] == "xlsx"
    valid, invalid = body["rows"]
    assert valid["values"] == {
        "cliente": PII_SENTINEL,
        "valor": "10.00",
        "vencimento": "2026-12-31",
        "status": "aberto",
        "email": PII_EMAIL,
        "whatsapp": None,
    }
    assert invalid["valid"] is False
    assert invalid["values"] is None


def test_xlsx_preview_default_has_no_values():
    data = _xlsx([
        ["cliente", "valor", "vencimento", "status"],
        [PII_SENTINEL, "10,00", "2026-12-31", "aberto"],
    ])
    result = _call(_build_app(), query=b"format=xlsx", chunks=(data,))

    assert result.status == 200
    assert "values" not in result.json()["rows"][0]
    assert PII_SENTINEL not in result.body.decode("utf-8")


def test_include_values_false_equals_default():
    default = _call(_build_app(), chunks=(_mixed_csv(),))
    explicit = _call(
        _build_app(),
        query=b"format=csv&include_values=false",
        chunks=(_mixed_csv(),),
    )

    assert explicit.status == default.status == 200
    assert explicit.json() == default.json()


def test_include_values_true_emits_whitelisted_values_only_for_valid_rows():
    result = _call(
        _build_app(),
        query=b"format=csv&include_values=true",
        chunks=(_mixed_csv(),),
    )

    rows = result.json()["rows"]
    assert rows[0]["values"]["cliente"] == PII_SENTINEL
    assert rows[0]["values"]["valor"] == "1234.56"
    assert set(rows[0]["values"]) == {
        "cliente", "valor", "vencimento", "status", "email", "whatsapp",
    }
    assert rows[1]["values"] is None
    assert rows[2]["values"] is None


def test_more_than_100_rows_returns_sample_with_full_totals():
    result = _call(_build_app(), chunks=(_many_rows_csv(250),))

    body = result.json()
    assert result.status == 200
    assert body["total_rows"] == 250
    assert body["valid_rows"] == 225
    assert body["invalid_rows"] == 25
    assert body["returned_rows"] == 100
    assert body["rows_truncated"] is True
    assert len(body["rows"]) == 100
    assert body["rows"][-1]["row_number"] == 101


@pytest.mark.parametrize(
    "content_type",
    [
        b"application/octet-stream; charset=binary",
        b"Application/Octet-Stream",
    ],
)
def test_octet_stream_with_parameters_or_case_is_accepted(content_type):
    result = _call(
        _build_app(),
        headers=[(b"content-type", content_type)],
        chunks=(_mixed_csv(),),
    )

    assert result.status == 200


# ------------------------------------------------- 422: query inválida


@pytest.mark.parametrize(
    "query",
    [
        b"",
        b"format=",
        b"format=xlsm",
        b"format=CSV",
        b"format=csv%20",
        b"format=" + PII_SENTINEL.replace(" ", "+").encode("ascii"),
        b"format=csv&format=csv",
        b"format=csv&format=xlsx",
        b"format=csv&include_values=",
        b"format=csv&include_values=1",
        b"format=csv&include_values=True",
        b"format=csv&include_values=yes",
        b"format=csv&include_values=" + CANARY.encode("ascii"),
        b"format=csv&include_values=true&include_values=true",
        b"format=csv&include_values=true&include_values=false",
        b"include_values=true",
    ],
)
def test_invalid_query_is_fixed_422_without_echo_or_body_read(query):
    result = _call(_build_app(), query=query, chunks=(_mixed_csv(),))

    body = _assert_fixed_error(result, 422, "invalid_preview_request")
    assert body["detail"]["message"] == (
        "Parâmetros da pré-visualização inválidos."
    )
    text = result.body.decode("utf-8")
    for echo in ("xlsm", "CSV", "Fulana", CANARY, "yes", "True"):
        assert echo not in text
    assert result.receiver.consumed == 0


def test_unknown_query_parameters_are_ignored():
    result = _call(
        _build_app(),
        query=b"format=csv&cache_buster=123",
        chunks=(_mixed_csv(),),
    )

    assert result.status == 200


# ---------------------------------------------------- 415: content-type


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-type", b"text/csv")],
        [(b"content-type", b"application/json")],
        [(b"content-type", b"multipart/form-data; boundary=x")],
        [(b"content-type", b"application/x-www-form-urlencoded")],
        [(b"content-type", b"application/octet-streamx")],
        [OCTET, OCTET],
    ],
)
def test_wrong_content_type_is_fixed_415_without_body_read(headers):
    result = _call(_build_app(), headers=headers, chunks=(_mixed_csv(),))

    body = _assert_fixed_error(result, 415, "unsupported_media_type")
    assert body["detail"]["message"] == (
        "Envie o arquivo como application/octet-stream."
    )
    assert result.receiver.consumed == 0


# --------------------------------------------- 400/413: content-length


@pytest.mark.parametrize(
    "values",
    [
        [b"abc"],
        [b"-1"],
        [b"+5"],
        [b"1.0"],
        [b""],
        [b" 5"],
        [CANARY.encode("ascii")],
        [b"5", b"5"],
        [b"5", b"6"],
    ],
)
def test_invalid_or_duplicate_content_length_is_400_before_reading(values):
    headers = [OCTET] + [(b"content-length", value) for value in values]
    result = _call(_build_app(), headers=headers, chunks=(_mixed_csv(),))

    _assert_fixed_error(result, 400, "invalid_content_length")
    assert CANARY not in result.body.decode("utf-8")
    assert result.receiver.consumed == 0


@pytest.mark.parametrize(
    "declared",
    [str(parser.MAX_FILE_BYTES + 1).encode("ascii"), b"9" * 400],
)
def test_declared_oversize_is_413_before_reading(declared):
    headers = [OCTET, (b"content-length", declared)]
    result = _call(_build_app(), headers=headers, chunks=(b"x",))

    _assert_fixed_error(result, 413, "file_too_large")
    assert result.receiver.consumed == 0


def test_chunked_overflow_stops_at_first_chunk_over_real_limit():
    first = b"a" * parser.MAX_FILE_BYTES
    receiver = Receiver((first, b"b", b"never-consumed"))
    result = _call(_build_app(), receiver=receiver)

    _assert_fixed_error(result, 413, "file_too_large")
    assert receiver.consumed == 2


def test_underdeclared_content_length_is_still_capped_by_received_bytes():
    first = b"a" * parser.MAX_FILE_BYTES
    headers = [OCTET, (b"content-length", b"3")]
    receiver = Receiver((first, b"b", b"never-consumed"))
    result = _call(_build_app(), headers=headers, receiver=receiver)

    _assert_fixed_error(result, 413, "file_too_large")
    assert receiver.consumed == 2


# --------------------------------------------------- 422: parser real


@pytest.mark.parametrize(
    ("query", "data", "code"),
    [
        (b"format=csv", b"", "empty_file"),
        (b"format=csv", b"PK\x03\x04" + PII_SENTINEL.encode(), "content_mismatch"),
        (b"format=xlsx", PII_SENTINEL.encode(), "content_mismatch"),
        (b"format=xlsx", b"PK\x03\x04" + PII_SENTINEL.encode(), "malformed_zip"),
        (b"format=csv", _csv("nome;valor", f"{PII_SENTINEL};10"), "missing_required_fields"),
        (b"format=csv", b"\xff\xfe" + PII_SENTINEL.encode("utf-16-le"), "unsupported_encoding"),
    ],
)
def test_parser_rejection_is_fixed_422_without_content_echo(query, data, code):
    result = _call(_build_app(), query=query, chunks=(data,))

    body = _assert_fixed_error(result, 422, code)
    assert body["detail"]["message"] == parser.FILE_ERROR_MESSAGES[code]
    assert PII_SENTINEL not in result.body.decode("utf-8")


# ------------------------------------------------------ threadpool


def test_parser_runs_in_worker_thread_not_event_loop():
    threads = []

    def spy(filename, data):
        threads.append(threading.get_ident())
        return parser.parse_import_preview(filename, data)

    app = _build_app(_module_proxy(parser, parse_import_preview=spy))
    result = _call(app, chunks=(_mixed_csv(),))

    assert result.status == 200
    assert len(threads) == 1
    assert threads[0] != threading.get_ident()


def test_parser_waits_for_anyio_default_thread_limiter():
    calls = []
    gate = threading.Event()

    def spy(filename, data):
        calls.append(filename)
        return parser.parse_import_preview(filename, data)

    app = _build_app(_module_proxy(parser, parse_import_preview=spy))
    sent = []

    async def send(message):
        sent.append(message)

    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 1
        blocker = asyncio.ensure_future(anyio.to_thread.run_sync(gate.wait))

        try:
            for _ in range(100):
                if limiter.borrowed_tokens == 1:
                    break
                await asyncio.sleep(0.01)
            assert limiter.borrowed_tokens == 1

            request = asyncio.ensure_future(
                app(_scope(), Receiver((_mixed_csv(),)), send)
            )
            await asyncio.sleep(0.2)
            assert calls == []
            assert sent == []
        finally:
            gate.set()

        await blocker
        await asyncio.wait_for(request, timeout=10)

    try:
        asyncio.run(scenario())
    finally:
        gate.set()

    assert calls == ["preview.csv"]
    assert Result(sent, None).status == 200


# --------------------------------------- 500 genérico / falhas internas


def test_unexpected_parser_error_is_generic_500_without_leak(caplog):
    def explode(filename, data):
        raise RuntimeError(f"{CANARY} {PII_SENTINEL}")

    app = _build_app(_module_proxy(parser, parse_import_preview=explode))

    with caplog.at_level(logging.DEBUG):
        result = _call(app, chunks=(_mixed_csv(),))

    assert result.status == 500
    _assert_no_store(result)
    assert result.json() == GENERIC_500
    assert len(result.starts) == 1
    assert CANARY not in result.body.decode("utf-8")

    records = [r for r in caplog.records if r.name == "auneron.imports.http"]
    assert len(records) == 1
    assert records[0].error_type == "RuntimeError"
    assert records[0].exc_info is None
    for record in caplog.records:
        rendered = record.getMessage() + repr(record.__dict__)
        assert CANARY not in rendered
        assert PII_SENTINEL not in rendered


def test_internal_transport_error_is_generic_500():
    def broken_serializer(preview, *, include_values=False):
        raise transport.ImportTransportError("invalid_preview")

    app = _build_app(
        transport_module=_module_proxy(
            transport,
            serialize_preview=broken_serializer,
        )
    )
    result = _call(app, chunks=(_mixed_csv(),))

    assert result.status == 500
    _assert_no_store(result)
    assert result.json() == GENERIC_500


def test_middleware_converts_error_before_start_into_generic_500():
    async def inner(scope, receive, send):
        raise ValueError(CANARY)

    result = _call(ImportPreviewHTTPMiddleware(inner))

    assert result.status == 500
    _assert_no_store(result)
    assert result.header_values(b"content-type") == [b"application/json"]
    assert result.json() == GENERIC_500
    assert len(result.starts) == 1


@pytest.mark.parametrize("send_body", [False, True])
def test_error_after_response_started_propagates_without_second_response(send_body):
    async def inner(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"cache-control", b"public")],
        })
        if send_body:
            await send({"type": "http.response.body", "body": b"partial"})
        raise RuntimeError(CANARY)

    sent = []

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError):
        asyncio.run(
            ImportPreviewHTTPMiddleware(inner)(_scope(), Receiver(), send)
        )

    result = Result(sent, None)
    assert len(result.starts) == 1
    assert result.status == 200
    _assert_no_store(result)
    assert CANARY.encode() not in result.body


# ------------------------------------------ desconexão / cancelamento


def test_client_disconnect_propagates_from_route_without_response():
    # Pilha do app descartável sem o ServerErrorMiddleware externo do
    # framework: middleware deste adaptador -> exceções -> rota.
    app = _build_app()
    stack = app.build_middleware_stack()
    assert type(stack).__name__ == "ServerErrorMiddleware"
    inner = stack.app
    assert isinstance(inner, ImportPreviewHTTPMiddleware)

    scope = _scope()
    scope["app"] = app
    sent = []

    async def send(message):
        sent.append(message)

    with pytest.raises(ClientDisconnect):
        asyncio.run(inner(scope, Receiver(disconnect_first=True), send))

    assert sent == []


def test_client_disconnect_in_disposable_app_is_never_200_nor_generic_json():
    sent = []

    async def send(message):
        sent.append(message)

    with pytest.raises(ClientDisconnect):
        asyncio.run(
            _build_app()(_scope(), Receiver(disconnect_first=True), send)
        )

    # O ServerErrorMiddleware do framework (fora deste adaptador) pode
    # tentar um 500 em texto puro para o cliente já desconectado.
    result = Result(sent, None)
    assert result.status != 200
    if result.starts:
        assert result.status == 500
        assert b"import_preview_internal_error" not in result.body


def test_middleware_propagates_errors_after_observed_disconnect():
    async def inner(scope, receive, send):
        await receive()
        raise RuntimeError(CANARY)

    sent = []

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError):
        asyncio.run(
            ImportPreviewHTTPMiddleware(inner)(
                _scope(),
                Receiver(disconnect_first=True),
                send,
            )
        )

    assert sent == []


def test_cancelled_receive_propagates_without_response():
    sent = []

    async def send(message):
        sent.append(message)

    receiver = Receiver(error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_build_app()(_scope(), receiver, send))

    assert sent == []


def test_real_task_cancellation_while_waiting_for_body_propagates():
    sent = []
    waiting = []

    async def send(message):
        sent.append(message)

    async def scenario():
        forever = asyncio.Event()

        async def receive():
            waiting.append(True)
            await forever.wait()

        task = asyncio.ensure_future(_build_app()(_scope(), receive, send))

        for _ in range(100):
            if waiting:
                break
            await asyncio.sleep(0.01)

        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert waiting == [True]
    assert sent == []


# ---------------------------- no-store em status simulados e roteamento


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (401, {"WWW-Authenticate": "ApiKey"}),
        (401, {"WWW-Authenticate": "Session"}),
        (403, None),
        (503, None),
    ],
)
def test_simulated_auth_denials_get_no_store_without_body_read(status, headers):
    def deny():
        raise HTTPException(status_code=status, detail="negado", headers=headers)

    app = _build_app(dependencies=[Depends(deny)])
    result = _call(app, chunks=(_mixed_csv(),))

    assert result.status == status
    _assert_no_store(result)
    assert result.receiver.consumed == 0
    assert PII_SENTINEL not in result.body.decode("utf-8")


def test_wrong_method_405_gets_no_store():
    result = _call(_build_app(), method="GET")

    assert result.status == 405
    _assert_no_store(result)


def test_protected_path_404_gets_no_store_when_route_absent():
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(ImportPreviewHTTPMiddleware)
    result = _call(app)

    assert result.status == 404
    _assert_no_store(result)


def test_trailing_slash_redirect_gets_no_store():
    result = _call(_build_app(), path=f"{PATH}/")

    assert 300 <= result.status < 400
    _assert_no_store(result)
    assert result.receiver.consumed == 0


@pytest.mark.parametrize(
    "status",
    [200, 204, 302, 400, 401, 403, 404, 405, 413, 415, 422, 500, 503],
)
@pytest.mark.parametrize("path", [PATH, f"{PATH}/"])
def test_conflicting_cache_headers_are_replaced_by_single_no_store(status, path):
    async def inner(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"cache-control", b"public, max-age=600"),
                (b"x-other", b"kept"),
                (b"Cache-Control", b"private"),
                (b"CACHE-CONTROL", b"no-cache"),
            ],
        })
        await send({"type": "http.response.body", "body": b"{}"})

    result = _call(ImportPreviewHTTPMiddleware(inner), path=path)

    assert result.status == status
    _assert_no_store(result)
    assert result.header_values(b"x-other") == [b"kept"]
    assert len(result.starts) == 1


@pytest.mark.parametrize(
    "path",
    ["/imports", "/imports/previews", "/imports/preview/x", "/imports/preview//",
     "/IMPORTS/PREVIEW", "/memories", "/"],
)
def test_other_paths_pass_through_untouched(path):
    original_headers = [(b"cache-control", b"public, max-age=600")]
    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope
        seen["receive"] = receive
        seen["send"] = send
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": original_headers,
        })
        await send({"type": "http.response.body", "body": b"ok"})

    sent = []

    async def send(message):
        sent.append(message)

    scope = _scope(path=path)
    receiver = Receiver()
    asyncio.run(ImportPreviewHTTPMiddleware(inner)(scope, receiver, send))

    assert seen == {"scope": scope, "receive": receiver, "send": send}
    assert sent[0]["headers"] is original_headers
    assert sent[0]["headers"] == [(b"cache-control", b"public, max-age=600")]


def test_errors_on_other_paths_are_not_converted():
    async def inner(scope, receive, send):
        raise RuntimeError(CANARY)

    sent = []

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError):
        asyncio.run(
            ImportPreviewHTTPMiddleware(inner)(_scope(path="/other"), Receiver(), send)
        )

    assert sent == []


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
def test_non_http_scopes_pass_through(scope_type):
    seen = []

    async def inner(scope, receive, send):
        seen.append((scope, receive, send))

    scope = {"type": scope_type, "path": PATH}

    async def receive():
        return {}

    async def send(message):
        return None

    asyncio.run(ImportPreviewHTTPMiddleware(inner)(scope, receive, send))

    assert seen == [(scope, receive, send)]


# ----------------------------------------------- persistência e isolamento


def test_no_file_or_directory_io_during_preview(monkeypatch):
    csv_data = _mixed_csv()
    xlsx_data = _xlsx([
        ["cliente", "valor", "vencimento", "status"],
        [PII_SENTINEL, "10,00", "2026-12-31", "aberto"],
    ])
    app = _build_app()
    attempts = []

    def forbidden(name):
        def guard(*args, **kwargs):
            attempts.append(name)
            raise AssertionError("I/O proibido durante o preview")
        return guard

    for target, name in [
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "mkdir"),
        (os, "makedirs"),
        (os, "remove"),
        (os, "replace"),
        (tempfile, "mkstemp"),
        (tempfile, "mkdtemp"),
        (tempfile, "NamedTemporaryFile"),
        (tempfile, "SpooledTemporaryFile"),
        (tempfile, "TemporaryFile"),
    ]:
        monkeypatch.setattr(target, name, forbidden(name))

    csv_result = _call(app, chunks=(csv_data,))
    xlsx_result = _call(app, query=b"format=xlsx", chunks=(xlsx_data,))

    monkeypatch.undo()
    assert attempts == []
    assert csv_result.status == 200
    assert xlsx_result.status == 200


def test_adapter_import_does_not_load_services_main_db_or_upload():
    forbidden_prefixes = (
        "app.services",
        "app.main",
        "app.database",
        "app.models",
        "app.api.routes.upload",
        "sqlalchemy",
        "psycopg",
        "httpx",
    )
    loaded = _modules_after_fresh_import(
        "app.api.routes.import_preview", "app.core.import_preview_http"
    )
    assert {"app.api.routes.import_preview",
            "app.core.import_preview_http"} <= loaded
    for name in loaded:
        assert not name.startswith(forbidden_prefixes), name

    for name in ("app.main", "app.services", "app.database"):
        assert name not in loaded


def test_factory_returns_router_with_single_post_route():
    router = create_import_preview_router(parser, transport)
    routes = [
        (route.path, sorted(route.methods))
        for route in router.routes
    ]

    assert routes == [(PATH, ["POST"])]


def _imports(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    return names


def _referenced_names(tree):
    return {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _handlers(tree):
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
    ]


def test_route_module_ast_has_no_db_io_logging_or_startup_imports():
    tree = ast.parse(ROUTE_PATH.read_text(encoding="utf-8"))

    assert _imports(tree) <= {
        "__future__",
        "typing",
        "fastapi",
        "fastapi.concurrency",
        "fastapi.responses",
        "app.core.import_preview_http",
    }
    forbidden = {
        "get_db", "Session", "SessionLocal", "open", "UploadFile", "File",
        "Form", "logger", "logging", "print", "tempfile", "NamedTemporaryFile",
        "import_clients", "upload_router", "os", "Path", "eval", "exec",
        "BaseException", "HTTPException",
    }
    assert not (_referenced_names(tree) & forbidden)
    for handler in _handlers(tree):
        assert handler.type is not None


def test_http_module_ast_is_pure_asgi_without_db_or_io():
    tree = ast.parse(HTTP_PATH.read_text(encoding="utf-8"))

    assert _imports(tree) <= {
        "__future__",
        "json",
        "logging",
        "collections.abc",
        "types",
        "typing",
        "starlette.requests",
    }
    forbidden = {
        "get_db", "Session", "open", "print", "tempfile", "os", "Path",
        "BaseException", "exc_info", "exception", "query_string",
        "query_params", "stream",
    }
    names = _referenced_names(tree)
    assert not (names & forbidden)
    for handler in _handlers(tree):
        assert handler.type is not None
        assert not (
            isinstance(handler.type, ast.Name)
            and handler.type.id == "BaseException"
        )
