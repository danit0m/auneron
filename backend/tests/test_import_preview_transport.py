"""
Testes offline do transporte limitado e da serialização privada da
pré-visualização de importação.

Sem banco, rede, disco, rota ou autenticação: o módulo de transporte e o
parser real são carregados diretamente pelo caminho do arquivo, sem
executar ``app/services/__init__.py``. Todos os dados são sintéticos.
"""

import ast
import asyncio
import copy
import importlib.util
import io
import json
import sys
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = BACKEND_DIR / "app" / "services"
TRANSPORT_PATH = SERVICES_DIR / "import_preview_transport.py"
PARSER_PATH = SERVICES_DIR / "import_preview_parser.py"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# O transporte é carregado antes do parser para medir o que ele importa.
_MODULES_BEFORE = set(sys.modules)
transport = _load("import_preview_transport_under_test", TRANSPORT_PATH)
_MODULES_ADDED_BY_TRANSPORT = set(sys.modules) - _MODULES_BEFORE

parser = _load("import_preview_parser_for_transport_test", PARSER_PATH)

ImportTransportError = transport.ImportTransportError
read_bounded_body = transport.read_bounded_body
serialize_preview = transport.serialize_preview
parse = parser.parse_import_preview

PII_SENTINEL = "Fulana Sigilosa da Silva"
PII_EMAIL = "fulana.sigilosa@example.com"
PII_PHONE = "+55 (11) 98888-7777"
HEADER = "cliente;valor;vencimento;status;email;whatsapp"
LIMIT = 16


# ------------------------------------------------------------ utilitários


class RecordingStream:
    """
    Fluxo assíncrono sintético que registra quantos chunks foram pedidos.
    """

    def __init__(self, *chunks, error=None):
        self.chunks = list(chunks)
        self.error = error
        self.consumed = 0

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

        if self.error is not None:
            raise self.error


def _read(stream, content_length=None, max_bytes=LIMIT):
    return asyncio.run(
        read_bounded_body(stream, content_length, max_bytes=max_bytes)
    )


def _transport_error(stream, content_length=None, max_bytes=LIMIT):
    with pytest.raises(ImportTransportError) as caught:
        _read(stream, content_length, max_bytes)
    return caught.value


def _assert_no_leak(error, *secrets):
    rendered = " ".join((str(error), repr(error), repr(error.args)))
    for secret in secrets:
        assert secret not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def _csv(*lines):
    return "\r\n".join(lines).encode("utf-8")


def _xlsx(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _mixed_csv():
    return _csv(
        HEADER,
        f"{PII_SENTINEL};R$ 1.234,56;05/01/2026;Aberto;{PII_EMAIL};"
        f"{PII_PHONE}",
        f"{PII_SENTINEL} B;-5;2026-12-31;aberto;;",
        f"{PII_SENTINEL} C;10;2026-02-30;pago;;",
        "Beto Lima;10,00;2026-03-01;;;",
    )


def _dumps(payload):
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


# --------------------------------------------------------- corpo: sucesso


def test_multiple_chunks_are_joined_in_order():
    stream = RecordingStream(b"abc", b"", bytearray(b"def"), b"gh")

    body = _read(stream, None)

    assert body == b"abcdefgh"
    assert type(body) is bytes
    assert stream.consumed == 4


def test_exact_limit_is_allowed_with_and_without_content_length():
    chunks = (b"a" * 10, b"b" * 6)

    assert _read(RecordingStream(*chunks), None) == b"a" * 10 + b"b" * 6
    assert _read(RecordingStream(*chunks), "16") == b"a" * 10 + b"b" * 6
    assert _read(RecordingStream(*chunks), "0016") == b"a" * 10 + b"b" * 6


def test_zero_bytes_are_returned_for_parser_to_reject():
    assert _read(RecordingStream(), None) == b""
    assert _read(RecordingStream(b"", b""), "0") == b""

    with pytest.raises(parser.ImportPreviewError) as caught:
        parse("contas.csv", _read(RecordingStream(), "0"))
    assert caught.value.code == "empty_file"


# --------------------------------------------------------- corpo: limite


def test_overflow_stops_at_first_exceeding_chunk_without_content_length():
    stream = RecordingStream(b"a" * 10, b"b" * 7, b"never-read")

    error = _transport_error(stream, None)

    assert error.code == "file_too_large"
    assert error.status_code == 413
    assert stream.consumed == 2


def test_single_oversized_chunk_is_rejected_immediately():
    stream = RecordingStream(b"x" * (LIMIT + 1), b"never-read")

    assert _transport_error(stream, None).code == "file_too_large"
    assert stream.consumed == 1


def test_underdeclared_content_length_is_still_bounded_by_received_bytes():
    stream = RecordingStream(b"a" * 10, b"b" * 10, b"never-read")

    error = _transport_error(stream, "3")

    assert error.code == "file_too_large"
    assert stream.consumed == 2


@pytest.mark.parametrize("declared", ["17", "00017", "999999", "9" * 5000])
def test_declared_oversize_is_rejected_before_consuming(declared):
    stream = RecordingStream(b"a")

    error = _transport_error(stream, declared)

    assert error.code == "file_too_large"
    assert error.status_code == 413
    assert stream.consumed == 0


# ------------------------------------------------- corpo: Content-Length


@pytest.mark.parametrize(
    "declared",
    [
        "",
        " ",
        " 5",
        "5 ",
        "5\n",
        "-1",
        "+5",
        "1.0",
        "1e3",
        "0x10",
        "abc",
        "5,5",
        "١٢",  # dígitos arábico-índicos
        "²",  # sobrescrito
        "５",  # dígito de largura total
        PII_SENTINEL,
    ],
)
def test_invalid_content_length_is_rejected_before_consuming(declared):
    stream = RecordingStream(b"a")

    error = _transport_error(stream, declared)

    assert error.code == "invalid_content_length"
    assert error.status_code == 400
    assert stream.consumed == 0
    if declared.strip():
        _assert_no_leak(error, declared)


@pytest.mark.parametrize("declared", [b"5", 5, 5.0, True, ["5"]])
def test_non_string_content_length_is_rejected(declared):
    stream = RecordingStream(b"a")

    error = _transport_error(stream, declared)

    assert error.code == "invalid_content_length"
    assert stream.consumed == 0


# ------------------------------------------------- corpo: configuração


@pytest.mark.parametrize(
    "max_bytes", [0, -1, True, False, 1.5, 16.0, "16", None, Decimal(16)]
)
def test_invalid_max_bytes_is_rejected_before_consuming(max_bytes):
    stream = RecordingStream(b"a")

    error = _transport_error(stream, None, max_bytes)

    assert error.code == "invalid_configuration"
    assert error.status_code == 500
    assert stream.consumed == 0


def test_max_bytes_is_keyword_only():
    with pytest.raises(TypeError):
        read_bounded_body(RecordingStream(), None, LIMIT)


# --------------------------------------------------- corpo: tipos do fluxo


def test_non_async_iterables_are_rejected():
    def sync_generator():
        yield b"a"

    for stream in ([b"a"], (b"a",), b"abc", sync_generator(), None):
        assert _transport_error(stream).code == "invalid_stream"


@pytest.mark.parametrize(
    "chunk",
    [PII_SENTINEL, 7, None, memoryview(b"abc"), [b"a"], object()],
)
def test_invalid_chunk_types_fail_with_fixed_code(chunk):
    stream = RecordingStream(b"ok", chunk, b"never-read")

    error = _transport_error(stream)

    assert error.code == "invalid_stream"
    assert stream.consumed == 2
    _assert_no_leak(error, PII_SENTINEL, "memoryview", "object at")


# ------------------------------------------ corpo: erro e cancelamento


def test_stream_errors_propagate_unchanged():
    failure = ConnectionResetError("cliente desconectou")
    stream = RecordingStream(b"a", error=failure)

    with pytest.raises(ConnectionResetError) as caught:
        _read(stream)

    assert caught.value is failure


def test_cancelled_error_from_stream_propagates():
    stream = RecordingStream(b"a", error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _read(stream)


def test_task_cancellation_is_not_reported_as_success():
    async def scenario():
        started = asyncio.Event()

        async def blocking_stream():
            yield b"a"
            started.set()
            await asyncio.Event().wait()
            yield b"never"

        task = asyncio.create_task(
            read_bounded_body(blocking_stream(), None, max_bytes=LIMIT)
        )
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.cancelled()

    asyncio.run(scenario())


# ------------------------------------------------------ corpo: erros fixos


def test_transport_errors_carry_only_fixed_fields():
    stream = RecordingStream(PII_SENTINEL.encode("utf-8") * 10)

    error = _transport_error(stream, None)

    assert error.code == "file_too_large"
    assert error.message == transport.TRANSPORT_ERRORS["file_too_large"][1]
    assert error.args == (error.message,)
    assert repr(error) == "ImportTransportError(code='file_too_large')"
    _assert_no_leak(error, PII_SENTINEL, "Fulana")


def test_unknown_error_code_is_refused():
    with pytest.raises(ValueError):
        ImportTransportError(PII_SENTINEL)


def test_error_status_codes():
    statuses = {
        code: status for code, (status, _) in transport.TRANSPORT_ERRORS.items()
    }
    assert statuses["invalid_content_length"] == 400
    assert statuses["file_too_large"] == 413


# ---------------------------------------------- serialização: parser real


EXPECTED_KEYS = {
    "file_kind",
    "file_sha256",
    "content_sha256",
    "column_mapping",
    "ignored_columns",
    "total_rows",
    "valid_rows",
    "invalid_rows",
    "valid_total",
    "returned_rows",
    "rows_truncated",
    "rows",
}


def test_csv_default_serialization_omits_values_and_pii():
    preview = parse(f"{PII_SENTINEL}.csv", _mixed_csv())

    payload = serialize_preview(preview)
    rendered = _dumps(payload)

    assert set(payload) == EXPECTED_KEYS
    assert payload["file_kind"] == "csv"
    assert payload["file_sha256"] == preview.file_sha256
    assert payload["content_sha256"] == preview.content_sha256
    assert (payload["total_rows"], payload["valid_rows"]) == (4, 2)
    assert payload["invalid_rows"] == 2
    assert payload["valid_total"] == "1244.56"
    assert payload["returned_rows"] == 4
    assert payload["rows_truncated"] is False
    for row in payload["rows"]:
        assert set(row) == {"row_number", "valid", "issues"}
    for secret in (PII_SENTINEL, PII_EMAIL, PII_PHONE, "1234.56", "cliente;"):
        assert secret not in rendered
    assert json.loads(rendered) == payload


def test_csv_rows_carry_parser_issues_verbatim():
    preview = parse("contas.csv", _mixed_csv())

    payload = serialize_preview(preview)

    assert [row["row_number"] for row in payload["rows"]] == [2, 3, 4, 5]
    assert [row["valid"] for row in payload["rows"]] == [
        True, False, False, True,
    ]
    for source, row in zip(preview.rows, payload["rows"]):
        assert row["issues"] == [
            {"field": i.field, "code": i.code, "message": i.message}
            for i in source.issues
        ]
    assert payload["rows"][1]["issues"][0]["code"] == "non_positive_money"
    assert {i["code"] for i in payload["rows"][2]["issues"]} == {
        "invalid_date", "status_not_allowed",
    }


def test_xlsx_serialization_with_typed_cells():
    data = _xlsx([
        ["cliente", "valor", "vencimento", "status", "whatsapp"],
        [PII_SENTINEL, 1500, datetime(2026, 1, 5), "aberto", 11988887777],
        ["Beto Lima", "-1", "2026-01-05", "aberto", None],
    ])
    preview = parse("contas.xlsx", data)

    default = serialize_preview(preview)
    opted_in = serialize_preview(preview, include_values=True)

    assert default["file_kind"] == "xlsx"
    assert default["valid_total"] == "1500.00"
    assert PII_SENTINEL not in _dumps(default)
    assert opted_in["rows"][0]["values"] == {
        "cliente": PII_SENTINEL,
        "valor": "1500.00",
        "vencimento": "2026-01-05",
        "status": "aberto",
        "email": None,
        "whatsapp": "11988887777",
    }
    assert opted_in["rows"][1]["values"] is None


def test_manual_mapping_is_serialized_by_indices_only():
    header_secret = "Coluna Secreta Do Cliente"
    data = _csv(
        f"{header_secret};vencimento x;montante;situacao x;apelido;mail",
        f"{PII_SENTINEL};2026-01-05;10,00;;ignorado;{PII_EMAIL}",
    )
    preview = parse(
        "contas.csv",
        data,
        column_mapping={
            "email": 5,
            "status": 3,
            "valor": 2,
            "vencimento": 1,
            "cliente": 0,
        },
        csv_delimiter=";",
    )

    payload = serialize_preview(preview, include_values=True)
    rendered = _dumps(payload)

    assert list(payload["column_mapping"].items()) == [
        ("cliente", 0), ("valor", 2), ("vencimento", 1),
        ("status", 3), ("email", 5),
    ]
    assert payload["ignored_columns"] == [4]
    assert header_secret not in rendered
    assert "montante" not in rendered
    assert "apelido" not in rendered
    assert payload["rows"][0]["values"]["email"] == PII_EMAIL
    assert payload["rows"][0]["values"]["whatsapp"] is None


def test_include_values_opt_in_normalizes_decimal_and_date():
    preview = parse("contas.csv", _mixed_csv())

    payload = serialize_preview(preview, include_values=True)

    first = payload["rows"][0]
    assert set(first) == {"row_number", "valid", "issues", "values"}
    assert first["values"] == {
        "cliente": PII_SENTINEL,
        "valor": "1234.56",
        "vencimento": "2026-01-05",
        "status": "aberto",
        "email": PII_EMAIL,
        "whatsapp": PII_PHONE,
    }
    assert payload["rows"][1]["values"] is None
    assert payload["rows"][2]["values"] is None
    assert payload["rows"][3]["values"]["email"] is None
    assert json.loads(_dumps(payload)) == payload


@pytest.mark.parametrize(
    "option", [1, 0, "true", "True", None, [True], Decimal(1)]
)
def test_include_values_requires_strict_bool(option):
    preview = parse("contas.csv", _mixed_csv())

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview, include_values=option)

    assert caught.value.code == "invalid_option"


def test_include_values_is_keyword_only():
    preview = parse("contas.csv", _mixed_csv())

    with pytest.raises(TypeError):
        serialize_preview(preview, True)


def test_explicit_false_matches_default():
    preview = parse("contas.csv", _mixed_csv())

    assert serialize_preview(preview, include_values=False) == (
        serialize_preview(preview)
    )


def _many_rows_csv(count):
    lines = [HEADER]
    for index in range(count):
        if index % 10 == 9:
            lines.append(f"Cliente {index:04d};-1;2026-01-01;;;")
        else:
            lines.append(f"Cliente {index:04d};{index + 1},25;2026-01-01;;;")
    return _csv(*lines)


def test_more_than_100_rows_keeps_real_totals_and_digest():
    preview = parse("contas.csv", _many_rows_csv(250))
    snapshot = copy.deepcopy(preview)

    payload = serialize_preview(preview, include_values=True)

    assert payload["total_rows"] == 250 == preview.total_rows
    assert payload["valid_rows"] == 225 == preview.valid_rows
    assert payload["invalid_rows"] == 25
    assert payload["valid_total"] == f"{preview.valid_total:f}"
    expected = sum(
        (Decimal(f"{i + 1}.25") for i in range(250) if i % 10 != 9),
        Decimal("0.00"),
    )
    assert payload["valid_total"] == f"{expected:f}"
    assert payload["returned_rows"] == 100
    assert payload["rows_truncated"] is True
    assert len(payload["rows"]) == 100
    assert payload["rows"][-1]["row_number"] == 101
    assert "Cliente 0100" not in _dumps(payload)
    assert payload["content_sha256"] == preview.content_sha256
    assert preview == snapshot
    assert parse("contas.csv", _many_rows_csv(250)).content_sha256 == (
        payload["content_sha256"]
    )


def test_exactly_100_rows_is_not_truncated():
    payload = serialize_preview(parse("contas.csv", _many_rows_csv(100)))

    assert payload["returned_rows"] == 100
    assert payload["rows_truncated"] is False


def test_serialization_does_not_alias_or_mutate_preview():
    preview = parse("contas.csv", _mixed_csv())
    snapshot = copy.deepcopy(preview)

    payload = serialize_preview(preview, include_values=True)
    payload["column_mapping"]["cliente"] = 99
    payload["ignored_columns"].append(7)
    payload["rows"][0]["values"]["cliente"] = "alterado"
    payload["rows"][1]["issues"].clear()

    assert preview == snapshot
    assert preview.column_mapping["cliente"] == 0
    assert preview.rows[0].values["cliente"] == PII_SENTINEL
    assert preview.rows[1].issues


# --------------------------------------- serialização: objetos inesperados


class _Leaky:
    def __str__(self):
        return PII_SENTINEL

    __repr__ = __str__
    __format__ = lambda self, spec: PII_SENTINEL  # noqa: E731


def _fake_preview(**overrides):
    row = SimpleNamespace(
        row_number=2,
        is_valid=True,
        issues=(),
        values={
            "cliente": "Ana Souza",
            "valor": Decimal("10.00"),
            "vencimento": date(2026, 1, 5),
            "status": "aberto",
            "email": None,
            "whatsapp": None,
        },
    )
    fields = dict(
        file_kind="csv",
        file_sha256="a" * 64,
        content_sha256="b" * 64,
        column_mapping={"cliente": 0, "valor": 1, "vencimento": 2, "status": 3},
        ignored_columns=(),
        rows=(row,),
        total_rows=1,
        valid_rows=1,
        invalid_rows=0,
        valid_total=Decimal("10.00"),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields), row


def test_duck_typed_preview_is_accepted():
    preview, _ = _fake_preview()

    payload = serialize_preview(preview, include_values=True)

    assert payload["rows"][0]["values"]["valor"] == "10.00"


def test_extra_value_keys_are_never_emitted():
    preview, row = _fake_preview()
    row.values["cpf"] = PII_SENTINEL
    row.values["header_original"] = PII_SENTINEL

    payload = serialize_preview(preview, include_values=True)

    assert set(payload["rows"][0]["values"]) == set(transport.CANONICAL_FIELDS)
    assert PII_SENTINEL not in _dumps(payload)


def test_invalid_row_values_are_never_emitted():
    preview, row = _fake_preview(valid_rows=0, invalid_rows=1)
    row.is_valid = False
    row.issues = (
        SimpleNamespace(field="valor", code="invalid_money", message="m"),
    )
    row.values["cliente"] = PII_SENTINEL

    payload = serialize_preview(preview, include_values=True)

    assert payload["rows"][0]["values"] is None
    assert PII_SENTINEL not in _dumps(payload)


@pytest.mark.parametrize(
    "field, value",
    [
        ("valor", 10.0),
        ("valor", "10.00"),
        ("valor", Decimal("NaN")),
        ("valor", Decimal("Infinity")),
        ("valor", _Leaky()),
        ("vencimento", datetime(2026, 1, 5)),
        ("vencimento", "2026-01-05"),
        ("cliente", _Leaky()),
        ("cliente", None),
        ("status", None),
        ("email", _Leaky()),
        ("whatsapp", 11988887777),
    ],
)
def test_unexpected_value_types_fail_without_str(field, value):
    preview, row = _fake_preview()
    row.values[field] = value

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview, include_values=True)

    assert caught.value.code == "invalid_preview"
    _assert_no_leak(caught.value, PII_SENTINEL)


def test_missing_canonical_value_key_fails():
    preview, row = _fake_preview()
    del row.values["whatsapp"]

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview, include_values=True)

    assert caught.value.code == "invalid_preview"


@pytest.mark.parametrize(
    "overrides",
    [
        {"file_kind": "xlsm"},
        {"file_kind": _Leaky()},
        {"file_sha256": PII_SENTINEL},
        {"content_sha256": "A" * 64},
        {"column_mapping": {"cliente": 0}},
        {"column_mapping": {
            "cliente": True, "valor": 1, "vencimento": 2, "status": 3,
        }},
        {"column_mapping": {
            "cliente": 0, "valor": 1, "vencimento": 2, "status": 3,
            PII_SENTINEL: 4,
        }},
        {"ignored_columns": (-1,)},
        {"ignored_columns": "4"},
        {"total_rows": 2},
        {"total_rows": True},
        {"valid_rows": 0},
        {"valid_total": 10.0},
        {"valid_total": _Leaky()},
        {"rows": iter(())},
    ],
)
def test_unexpected_preview_shape_fails_with_fixed_code(overrides):
    preview, _ = _fake_preview(**overrides)

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview)

    assert caught.value.code == "invalid_preview"
    _assert_no_leak(caught.value, PII_SENTINEL)


@pytest.mark.parametrize(
    "attribute, value",
    [
        ("row_number", True),
        ("row_number", 0),
        ("row_number", "2"),
        ("is_valid", 1),
        ("issues", "x"),
        (
            "issues",
            (SimpleNamespace(field=None, code="extra_columns", message="m"),),
        ),
    ],
)
def test_unexpected_row_shape_fails(attribute, value):
    preview, row = _fake_preview()
    setattr(row, attribute, value)

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview)

    assert caught.value.code == "invalid_preview"


@pytest.mark.parametrize(
    "issue",
    [
        SimpleNamespace(field=PII_SENTINEL, code="required", message="m"),
        SimpleNamespace(field="cliente", code=PII_SENTINEL, message="m"),
        SimpleNamespace(field="cliente", code="required", message=_Leaky()),
        SimpleNamespace(field="cliente", code="required"),
    ],
)
def test_unexpected_issue_shape_fails(issue):
    preview, row = _fake_preview(valid_rows=0, invalid_rows=1)
    row.is_valid = False
    row.issues = (issue,)

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview)

    assert caught.value.code == "invalid_preview"
    _assert_no_leak(caught.value, PII_SENTINEL)


def test_missing_preview_attribute_fails():
    preview, _ = _fake_preview()
    del preview.content_sha256

    with pytest.raises(ImportTransportError) as caught:
        serialize_preview(preview)

    assert caught.value.code == "invalid_preview"


# --------------------------------------------------- cabeçalhos de contrato


def test_response_headers_contract_is_static_and_immutable():
    assert dict(transport.PREVIEW_RESPONSE_HEADERS) == {
        "Cache-Control": "no-store"
    }

    with pytest.raises(TypeError):
        transport.PREVIEW_RESPONSE_HEADERS["Cache-Control"] = "public"


# ------------------------------------------------- pureza / sem efeitos


ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "datetime",
    "decimal",
    "re",
    "types",
    "typing",
}

FORBIDDEN_CALLS = {
    "open",
    "print",
    "eval",
    "exec",
    "compile",
    "__import__",
    "str",
    "repr",
    "setattr",
    "delattr",
}


def _module_tree():
    return ast.parse(TRANSPORT_PATH.read_text(encoding="utf-8"))


def test_module_imports_only_stdlib_allowlist():
    imported = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)

    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS


def test_module_has_no_io_dynamic_or_generic_str_calls():
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, node.func.id


def test_loading_transport_does_not_import_app_packages():
    assert not any(
        name == "app" or name.startswith("app.")
        for name in _MODULES_ADDED_BY_TRANSPORT
    )
    assert "app.services" not in sys.modules
