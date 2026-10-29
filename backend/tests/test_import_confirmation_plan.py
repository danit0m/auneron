"""
Testes offline do planejamento puro da confirmação de importação
(MVP-FIN-IMPORT-01 / TASK-024).

Standalone com unittest, sem conftest, banco, rede ou disco: arquivos
sintéticos montados em memória. Parser e plano são carregados pelo
caminho do arquivo para não executar ``app/services/__init__.py``.

    cd backend
    python -B tests/test_import_confirmation_plan.py -v
"""

import ast
import builtins
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import openpyxl


BACKEND_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = BACKEND_DIR / "app" / "services"
PARSER_PATH = SERVICES_DIR / "import_preview_parser.py"
PLAN_PATH = SERVICES_DIR / "import_confirmation_plan.py"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Pureza medida num processo NOVO: o sys.modules deste processo pode já
# conter sqlalchemy/app.* (coleta do pytest, conftest), e isso não diz
# nada sobre o que o módulo sob teste importa. -I ignora PYTHON* e o
# site do usuário; o ambiente mínimo não carrega DATABASE_URL nem rede.
_FRESH_LOAD_PROBE = """
import importlib.util, json, sys
sys.path.insert(0, sys.argv[1])
for name, path in json.loads(sys.argv[2]):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
print(json.dumps(sorted(sys.modules)))
"""


def _modules_after_fresh_load(*targets):
    env = {key: os.environ[key] for key in ("SYSTEMROOT",) if key in os.environ}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _FRESH_LOAD_PROBE,
         str(BACKEND_DIR),
         json.dumps([[name, str(path)] for name, path in targets])],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"fresh load failed: {result.stderr}")
    return set(json.loads(result.stdout))


parser = _load("import_preview_parser_for_plan_test", PARSER_PATH)
plan_module = _load("import_confirmation_plan_under_test", PLAN_PATH)

parse = parser.parse_import_preview
ImportPreviewError = parser.ImportPreviewError
plan = plan_module.plan_import_confirmation
ImportConfirmationError = plan_module.ImportConfirmationError

PII_SENTINEL = "Fulana Sigilosa da Silva"
EMAIL_SENTINEL = "fulana.sigilosa@example.com"
KEY = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
OTHER_KEY = "0d9e8f7a-6b5c-4d3e-9f1a-2b3c4d5e6f70"
HEADER = "cliente;valor;vencimento;status;email;whatsapp"


def _csv(*lines, bom=False):
    data = "\r\n".join(lines).encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def _xlsx(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _valid_csv(**kwargs):
    return _csv(
        HEADER,
        f"{PII_SENTINEL};R$ 1.234,56;05/01/2026;Aberto;{EMAIL_SENTINEL};",
        "Bruno Lima;0,10;2026-02-28;;;+55 (11) 99999-0000",
        "Carla Dias;0.20;2026-03-01;aberto;;",
        **kwargs,
    )


def _valid_xlsx():
    return _xlsx([
        ["cliente", "valor", "vencimento", "status", "email", "whatsapp"],
        [PII_SENTINEL, "1.234,56", datetime(2026, 1, 5), "aberto",
         EMAIL_SENTINEL, None],
        ["Bruno Lima", "0,10", "2026-02-28", None, None,
         "+55 (11) 99999-0000"],
        ["Carla Dias", "0.20", "01/03/2026", "Aberto", None, None],
    ])


def _plan_for(data, file_format="csv", *, key=KEY, **kwargs):
    preview = parse(f"preview.{file_format}", data)
    return plan(
        parse,
        data,
        file_format,
        expected_file_sha256=preview.file_sha256,
        expected_content_sha256=preview.content_sha256,
        idempotency_key=key,
        **kwargs,
    )


class _Spy:
    def __init__(self, target=parse):
        self.target = target
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.target(*args, **kwargs)


def _fake_parser(preview):
    return lambda *args, **kwargs: preview


def _namespace_from(preview, **changes):
    fields = {
        "file_kind": preview.file_kind,
        "file_sha256": preview.file_sha256,
        "content_sha256": preview.content_sha256,
        "rows": preview.rows,
        "total_rows": preview.total_rows,
        "valid_rows": preview.valid_rows,
        "invalid_rows": preview.invalid_rows,
        "valid_total": preview.valid_total,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


class ConfirmationTestCase(unittest.TestCase):
    def assertCode(self, code, call, *args, **kwargs):
        with self.assertRaises(ImportConfirmationError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def plan_with(self, parser_fn, data, *, file_format="csv", **overrides):
        preview = parse(f"preview.{file_format}", data)
        options = {
            "expected_file_sha256": preview.file_sha256,
            "expected_content_sha256": preview.content_sha256,
            "idempotency_key": KEY,
        }
        options.update(overrides)
        return plan(parser_fn, data, file_format, **options)


# ------------------------------------------------------------ planos válidos


class ValidPlanTests(ConfirmationTestCase):
    def test_csv_plan_has_normalized_records_and_exact_total(self):
        data = _valid_csv(bom=True)
        preview = parse("preview.csv", data)
        result = _plan_for(data)

        self.assertEqual(result.file_kind, "csv")
        self.assertEqual(result.record_count, 3)
        self.assertEqual(result.idempotency_key, KEY)
        self.assertEqual(result.file_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(result.content_sha256, preview.content_sha256)
        self.assertIs(type(result.total), Decimal)
        self.assertEqual(str(result.total), "1234.86")
        self.assertEqual(result.total, preview.valid_total)

        first, second, third = result.records
        self.assertEqual(first.row_number, 2)
        self.assertEqual(first.cliente, PII_SENTINEL)
        self.assertEqual(str(first.valor), "1234.56")
        self.assertEqual(first.vencimento, date(2026, 1, 5))
        self.assertIs(type(first.vencimento), date)
        self.assertEqual(first.email, EMAIL_SENTINEL)
        self.assertIsNone(first.whatsapp)
        self.assertEqual(str(second.valor), "0.10")
        self.assertEqual(second.whatsapp, "+55 (11) 99999-0000")
        self.assertEqual(str(third.valor), "0.20")
        self.assertTrue(all(r.status == "aberto" for r in result.records))

    def test_records_mirror_parser_values_without_reconversion(self):
        data = _valid_csv()
        preview = parse("preview.csv", data)
        result = _plan_for(data)

        for row, record in zip(preview.rows, result.records):
            self.assertEqual(record.row_number, row.row_number)
            for name in plan_module.CANONICAL_FIELDS:
                self.assertEqual(getattr(record, name), row.values[name])
                self.assertIs(type(getattr(record, name)),
                              type(row.values[name]))

    def test_xlsx_plan(self):
        data = _valid_xlsx()
        result = _plan_for(data, "xlsx")

        self.assertEqual(result.file_kind, "xlsx")
        self.assertEqual(result.record_count, 3)
        self.assertEqual(str(result.total), "1234.86")
        self.assertEqual(result.records[0].vencimento, date(2026, 1, 5))
        self.assertEqual(result.records[2].vencimento, date(2026, 3, 1))

    def test_csv_and_xlsx_with_same_content_plan_same_records(self):
        from_csv = _plan_for(_valid_csv())
        from_xlsx = _plan_for(_valid_xlsx(), "xlsx")

        self.assertEqual(from_csv.content_sha256, from_xlsx.content_sha256)
        self.assertNotEqual(from_csv.file_sha256, from_xlsx.file_sha256)
        self.assertEqual(from_csv.records, from_xlsx.records)
        self.assertEqual(from_csv.total, from_xlsx.total)

    def test_bytearray_is_accepted(self):
        data = _valid_csv()
        self.assertEqual(
            self.plan_with(parse, bytearray(data)).record_count, 3
        )

    def test_reparse_uses_http_default_options(self):
        data = _valid_csv()
        spy = _Spy()
        self.plan_with(spy, data)

        self.assertEqual(spy.calls, [(("preview.csv", data), {})])

        spy_xlsx = _Spy()
        xlsx = _valid_xlsx()
        self.plan_with(spy_xlsx, xlsx, file_format="xlsx")
        self.assertEqual(spy_xlsx.calls, [(("preview.xlsx", xlsx), {})])

    def test_decimal_amounts_are_exact(self):
        rows = [f"Cliente {i:02d};0,10;2026-01-01;;;" for i in range(30)]
        result = _plan_for(_csv(HEADER, *rows))

        self.assertEqual(result.total, Decimal("3.00"))
        self.assertEqual(str(result.total), "3.00")

    def test_max_money_is_preserved(self):
        result = _plan_for(
            _csv(HEADER, "Ana Souza;999999999999.99;2026-01-01;;;")
        )
        self.assertEqual(result.total, Decimal("999999999999.99"))


# ------------------------------------------------- determinismo / imutável


class DeterminismTests(ConfirmationTestCase):
    def test_replay_planning_is_deterministic(self):
        data = _valid_csv()
        first = _plan_for(data)
        second = _plan_for(data)

        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(repr(first), repr(second))

    def test_different_key_changes_only_the_key(self):
        data = _valid_csv()
        first = _plan_for(data)
        second = _plan_for(data, key=OTHER_KEY)

        self.assertNotEqual(first, second)
        self.assertEqual(
            dataclasses.replace(second, idempotency_key=KEY), first
        )

    def test_plan_and_records_are_immutable(self):
        result = _plan_for(_valid_csv())

        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.total = Decimal("0.00")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.records[0].valor = Decimal("1.00")
        self.assertIs(type(result.records), tuple)
        with self.assertRaises(TypeError):
            result.records[0] = None

    def test_plan_does_not_alias_parser_rows(self):
        data = _valid_csv()
        preview = parse("preview.csv", data)
        result = plan(
            _fake_parser(preview),
            data,
            "csv",
            expected_file_sha256=preview.file_sha256,
            expected_content_sha256=preview.content_sha256,
            idempotency_key=KEY,
        )

        preview.rows[0].values["cliente"] = "Outro Nome"
        self.assertEqual(result.records[0].cliente, PII_SENTINEL)


# ------------------------------------------------------- pré-visualização


class PreviewMismatchTests(ConfirmationTestCase):
    def test_changed_file_is_rejected_before_parsing(self):
        original = _valid_csv()
        changed = original.replace(b"0,10", b"0,11")
        preview = parse("preview.csv", original)
        spy = _Spy()

        self.assertCode(
            "file_mismatch", plan, spy, changed, "csv",
            expected_file_sha256=preview.file_sha256,
            expected_content_sha256=preview.content_sha256,
            idempotency_key=KEY,
        )
        self.assertEqual(spy.calls, [])

    def test_same_content_with_different_bytes_is_rejected(self):
        with_bom = _valid_csv(bom=True)
        without_bom = _valid_csv()
        preview = parse("preview.csv", with_bom)

        self.assertEqual(
            preview.content_sha256,
            parse("preview.csv", without_bom).content_sha256,
        )
        self.assertCode(
            "file_mismatch", plan, parse, without_bom, "csv",
            expected_file_sha256=preview.file_sha256,
            expected_content_sha256=preview.content_sha256,
            idempotency_key=KEY,
        )

    def test_swapped_hashes_are_rejected(self):
        data = _valid_csv()
        preview = parse("preview.csv", data)

        self.assertCode(
            "file_mismatch", plan, parse, data, "csv",
            expected_file_sha256=preview.content_sha256,
            expected_content_sha256=preview.file_sha256,
            idempotency_key=KEY,
        )

    def test_wrong_content_hash_is_rejected(self):
        data = _valid_csv()
        other = parse("preview.csv", _csv(HEADER, "Ana Souza;1;2026-01-01;;;"))

        self.assertCode(
            "content_mismatch", self.plan_with, parse, data,
            expected_content_sha256=other.content_sha256,
        )

    def test_format_mismatch_is_rejected_by_parser(self):
        data = _valid_xlsx()
        preview = parse("preview.xlsx", data)

        with self.assertRaises(ImportPreviewError) as caught:
            plan(
                parse, data, "csv",
                expected_file_sha256=preview.file_sha256,
                expected_content_sha256=preview.content_sha256,
                idempotency_key=KEY,
            )
        self.assertEqual(caught.exception.code, "content_mismatch")


# ------------------------------------------------------- entradas inválidas


class InputValidationTests(ConfirmationTestCase):
    def test_parser_must_be_callable(self):
        self.assertCode("invalid_parser", self.plan_with, None, _valid_csv())

    def test_invalid_formats(self):
        data = _valid_csv()
        preview = parse("preview.csv", data)
        for value in ("CSV", "xls", " csv", "", None, b"csv", ("csv",)):
            with self.subTest(value=value):
                self.assertCode(
                    "invalid_format", plan, parse, data, value,
                    expected_file_sha256=preview.file_sha256,
                    expected_content_sha256=preview.content_sha256,
                    idempotency_key=KEY,
                )

    def test_non_bytes_data(self):
        data = _valid_csv()
        preview = parse("preview.csv", data)
        for value in (data.decode("utf-8"), memoryview(data), None, [1]):
            with self.subTest(kind=type(value).__name__):
                self.assertCode(
                    "invalid_data", plan, parse, value, "csv",
                    expected_file_sha256=preview.file_sha256,
                    expected_content_sha256=preview.content_sha256,
                    idempotency_key=KEY,
                )

    def test_empty_file_is_rejected_without_parsing(self):
        spy = _Spy()
        empty_hash = hashlib.sha256(b"").hexdigest()
        self.assertCode(
            "empty_file", plan, spy, b"", "csv",
            expected_file_sha256=empty_hash,
            expected_content_sha256=empty_hash,
            idempotency_key=KEY,
        )
        self.assertEqual(spy.calls, [])

    def test_invalid_expected_hashes(self):
        data = _valid_csv()
        good = parse("preview.csv", data).file_sha256
        bad_values = (
            good.upper(),
            good[:-1],
            good + "0",
            " " + good[1:],
            "g" * 64,
            None,
            good.encode("ascii"),
            0,
        )
        for name in ("expected_file_sha256", "expected_content_sha256"):
            for value in bad_values:
                with self.subTest(name=name, value=value):
                    self.assertCode(
                        "invalid_expected_hash", self.plan_with, parse, data,
                        **{name: value},
                    )

    def test_invalid_idempotency_keys(self):
        data = _valid_csv()
        bad_keys = (
            KEY.upper(),
            KEY.replace("-", ""),
            "{" + KEY + "}",
            KEY + "\n",
            " " + KEY,
            "3f2b8c1e-9a4d-1e6f-8b2a-1c3d5e7f9a0b",  # versão 1
            "3f2b8c1e-9a4d-4e6f-7b2a-1c3d5e7f9a0b",  # variante inválida
            "x" * 10_000,
            "",
            None,
            KEY.encode("ascii"),
            12345,
        )
        for value in bad_keys:
            with self.subTest(index=bad_keys.index(value)):
                self.assertCode(
                    "invalid_idempotency_key", self.plan_with, parse, data,
                    idempotency_key=value,
                )

    def test_parser_rejection_propagates_with_fixed_code(self):
        data = _csv("nome;quantia", f"{PII_SENTINEL};10")
        file_hash = hashlib.sha256(data).hexdigest()

        with self.assertRaises(ImportPreviewError) as caught:
            plan(
                parse, data, "csv",
                expected_file_sha256=file_hash,
                expected_content_sha256=file_hash,
                idempotency_key=KEY,
            )
        self.assertEqual(caught.exception.code, "missing_required_fields")
        self.assertNotIn(PII_SENTINEL, str(caught.exception))


# --------------------------------------------------------- tudo ou nada


class AllOrNothingTests(ConfirmationTestCase):
    def test_single_invalid_row_blocks_entire_plan(self):
        data = _csv(
            HEADER,
            "Ana Souza;10,00;2026-01-01;;;",
            f"{PII_SENTINEL};abc;2026-01-01;;;",
            "Bruno Lima;20,00;2026-01-02;;;",
        )
        error = self.assertCode("invalid_rows", _plan_for, data)
        self.assertNotIn(PII_SENTINEL, str(error) + repr(error))

    def test_status_other_than_open_blocks_plan(self):
        for status in ("pago", "atrasado", "cancelado"):
            with self.subTest(status=status):
                data = _csv(
                    HEADER,
                    "Ana Souza;10,00;2026-01-01;;;",
                    f"Bruno Lima;20,00;2026-01-02;{status};;",
                )
                self.assertCode("invalid_rows", _plan_for, data)

    def test_duplicate_rows_with_unicode_spacing_and_case_block_plan(self):
        data = _csv(
            HEADER,
            "Ana Souza;10,00;2026-01-01;;;",
            "  ANA  souza ;10.00;01/01/2026;;;",
        )
        preview = parse("preview.csv", data)
        self.assertEqual(
            [i.code for i in preview.rows[1].issues], ["duplicate_in_file"]
        )
        self.assertCode("invalid_rows", _plan_for, data)

    def test_same_client_with_different_amount_or_date_is_allowed(self):
        data = _csv(
            HEADER,
            "Ana Souza;10,00;2026-01-01;;;",
            "ana souza;10,01;2026-01-01;;;",
            "ANA SOUZA;10,00;2026-01-02;;;",
        )
        self.assertEqual(_plan_for(data).record_count, 3)


# ------------------------------------------------------ identidade existente


class ExistingIdentityTests(ConfirmationTestCase):
    def test_identity_key_matches_parser_duplicate_rule(self):
        pairs = (
            ("Ana Souza", "  ana SOUZA "),
            ("Straße Ltda", "STRASSE LTDA"),
            ("João  da Silva", "joão da silva"),
        )
        for left, right in pairs:
            with self.subTest(left=left):
                data = _csv(
                    HEADER,
                    f"{left};10,00;2026-01-01;;;",
                    f"{right};10,00;2026-01-01;;;",
                )
                preview = parse("preview.csv", data)
                flagged = any(
                    i.code == "duplicate_in_file"
                    for i in preview.rows[1].issues
                )
                same_key = plan_module.identity_key(
                    left, Decimal("10.00"), date(2026, 1, 1)
                ) == plan_module.identity_key(
                    right, Decimal("10.00"), date(2026, 1, 1)
                )
                self.assertTrue(flagged)
                self.assertTrue(same_key)

    def test_collision_with_existing_identity_blocks_plan(self):
        data = _valid_csv()
        existing = [
            ("Outra Pessoa", Decimal("5.00"), date(2026, 1, 1)),
            ("bruno  LIMA", Decimal("0.10"), date(2026, 2, 28)),
        ]
        error = self.assertCode(
            "existing_identity_conflict", _plan_for, data,
            existing_identities=existing,
        )
        self.assertNotIn("bruno", (str(error) + repr(error)).casefold())

    def test_non_colliding_existing_identities_are_allowed(self):
        existing = (
            ("Bruno Lima", Decimal("0.11"), date(2026, 2, 28)),
            ("Bruno Lima", Decimal("0.10"), date(2026, 2, 27)),
            ("Bruna Lima", Decimal("0.10"), date(2026, 2, 28)),
        )
        result = _plan_for(_valid_csv(), existing_identities=existing)
        self.assertEqual(result.record_count, 3)

    def test_existing_identities_accept_generator(self):
        existing = (item for item in [
            ("Carla Dias", Decimal("0.20"), date(2026, 3, 1)),
        ])
        self.assertCode(
            "existing_identity_conflict", _plan_for, _valid_csv(),
            existing_identities=existing,
        )

    def test_invalid_existing_identities(self):
        bad_inputs = (
            "Ana Souza",
            b"x",
            {"cliente": "Ana"},
            None,
            [["Ana Souza", Decimal("1.00"), date(2026, 1, 1)]],
            [("Ana Souza", Decimal("1.00"))],
            [("", Decimal("1.00"), date(2026, 1, 1))],
            [("Ana Souza", 1.0, date(2026, 1, 1))],
            [("Ana Souza", Decimal("1"), date(2026, 1, 1))],
            [("Ana Souza", Decimal("NaN"), date(2026, 1, 1))],
            [("Ana Souza", Decimal("1.00"), datetime(2026, 1, 1))],
            [("Ana Souza", Decimal("1.00"), "2026-01-01")],
        )
        for index, value in enumerate(bad_inputs):
            with self.subTest(index=index):
                self.assertCode(
                    "invalid_existing_identity", _plan_for, _valid_csv(),
                    existing_identities=value,
                )

    def test_repeated_existing_identities_are_also_bounded(self):
        item = ('Cliente repetido', Decimal('1.00'), date(2026, 1, 1))
        with mock.patch.object(plan_module, 'MAX_EXISTING_IDENTITIES', 2):
            self.assertCode(
                'invalid_existing_identity', _plan_for, _valid_csv(),
                existing_identities=(item for _ in range(3)),
            )

    def test_existing_identity_count_is_bounded(self):
        limit = plan_module.MAX_EXISTING_IDENTITIES
        existing = (
            (f"Cliente {i}", Decimal("1.00"), date(2026, 1, 1))
            for i in range(limit + 1)
        )
        self.assertCode(
            "invalid_existing_identity", _plan_for, _valid_csv(),
            existing_identities=existing,
        )


# --------------------------------------------- parser malformado (fail closed)


class MalformedParserTests(ConfirmationTestCase):
    def setUp(self):
        self.data = _valid_csv()
        self.preview = parse("preview.csv", self.data)

    def run_fake(self, fake_preview):
        return plan(
            _fake_parser(fake_preview),
            self.data,
            "csv",
            expected_file_sha256=self.preview.file_sha256,
            expected_content_sha256=self.preview.content_sha256,
            idempotency_key=KEY,
        )

    def replace_row(self, index, **changes):
        rows = list(self.preview.rows)
        rows[index] = dataclasses.replace(rows[index], **changes)
        return tuple(rows)

    def replace_value(self, index, name, value):
        values = dict(self.preview.rows[index].values)
        values[name] = value
        return self.replace_row(index, values=values)

    def test_real_preview_through_fake_is_accepted(self):
        self.assertEqual(self.run_fake(self.preview).record_count, 3)

    def test_malformed_previews_fail_closed(self):
        valid_total = self.preview.valid_total
        cases = {
            "none": None,
            "missing_rows": SimpleNamespace(
                file_kind="csv",
                file_sha256=self.preview.file_sha256,
                content_sha256=self.preview.content_sha256,
            ),
            "wrong_kind": _namespace_from(self.preview, file_kind="xlsx"),
            "wrong_file_hash": _namespace_from(
                self.preview, file_sha256="0" * 64
            ),
            "content_hash_not_hex": _namespace_from(
                self.preview, content_sha256=None
            ),
            "rows_list": _namespace_from(
                self.preview, rows=list(self.preview.rows)
            ),
            "rows_empty": _namespace_from(
                self.preview, rows=(), total_rows=0, valid_rows=0,
                valid_total=Decimal("0.00"),
            ),
            "issues_list": _namespace_from(
                self.preview, rows=self.replace_row(0, issues=[]),
            ),
            "values_none": _namespace_from(
                self.preview, rows=self.replace_row(0, values=None),
            ),
            "values_missing_field": _namespace_from(
                self.preview,
                rows=self.replace_row(0, values={"cliente": "Ana"}),
            ),
            "row_number_bool": _namespace_from(
                self.preview, rows=self.replace_row(0, row_number=True),
            ),
            "row_number_header": _namespace_from(
                self.preview, rows=self.replace_row(0, row_number=1),
            ),
            "row_order": _namespace_from(
                self.preview, rows=self.replace_row(1, row_number=2),
            ),
            "valor_float": _namespace_from(
                self.preview, rows=self.replace_value(0, "valor", 1234.56),
            ),
            "valor_unquantized": _namespace_from(
                self.preview,
                rows=self.replace_value(0, "valor", Decimal("1234.5")),
            ),
            "valor_zero": _namespace_from(
                self.preview,
                rows=self.replace_value(0, "valor", Decimal("0.00")),
            ),
            "valor_infinite": _namespace_from(
                self.preview,
                rows=self.replace_value(0, "valor", Decimal("Infinity")),
            ),
            "vencimento_datetime": _namespace_from(
                self.preview,
                rows=self.replace_value(
                    0, "vencimento", datetime(2026, 1, 5)
                ),
            ),
            "vencimento_text": _namespace_from(
                self.preview,
                rows=self.replace_value(0, "vencimento", "2026-01-05"),
            ),
            "cliente_blank": _namespace_from(
                self.preview, rows=self.replace_value(0, "cliente", "  "),
            ),
            "email_int": _namespace_from(
                self.preview, rows=self.replace_value(0, "email", 1),
            ),
            "total_rows_wrong": _namespace_from(self.preview, total_rows=4),
            "invalid_rows_nonzero": _namespace_from(
                self.preview, invalid_rows=1
            ),
            "valid_rows_bool": _namespace_from(self.preview, valid_rows=True),
            "valid_total_wrong": _namespace_from(
                self.preview, valid_total=valid_total + Decimal("0.01")
            ),
            "valid_total_float": _namespace_from(
                self.preview, valid_total=float(valid_total)
            ),
        }
        for name, fake in cases.items():
            with self.subTest(case=name):
                self.assertCode("invalid_preview", self.run_fake, fake)

    def test_status_not_open_without_issue_is_rejected(self):
        for status in ("pago", "Aberto", "aberto "):
            with self.subTest(status=status):
                fake = _namespace_from(
                    self.preview,
                    rows=self.replace_value(1, "status", status),
                )
                self.assertCode("status_not_open", self.run_fake, fake)

    def test_duplicate_identity_without_issue_is_rejected(self):
        values = dict(self.preview.rows[0].values)
        values["cliente"] = "  " + PII_SENTINEL.upper()
        rows = self.preview.rows + (
            dataclasses.replace(
                self.preview.rows[0], row_number=9, values=values
            ),
        )
        fake = _namespace_from(
            self.preview,
            rows=rows,
            total_rows=4,
            valid_rows=4,
            valid_total=self.preview.valid_total + values["valor"],
        )
        error = self.assertCode("duplicate_identity", self.run_fake, fake)
        self.assertNotIn(PII_SENTINEL.casefold(),
                         (str(error) + repr(error)).casefold())

    def test_unexpected_parser_exception_propagates(self):
        def broken(*args, **kwargs):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            plan(
                broken, self.data, "csv",
                expected_file_sha256=self.preview.file_sha256,
                expected_content_sha256=self.preview.content_sha256,
                idempotency_key=KEY,
            )


# --------------------------------------------------------------- privacidade


class PrivacyTests(ConfirmationTestCase):
    def test_plan_and_record_repr_hide_pii_key_hashes_and_total(self):
        result = _plan_for(_valid_csv())
        rendered = repr(result) + str(result) + "".join(
            repr(record) for record in result.records
        )

        for secret in (
            PII_SENTINEL,
            EMAIL_SENTINEL,
            "99999-0000",
            KEY,
            result.file_sha256,
            result.content_sha256,
            "1234",
            "2026",
        ):
            self.assertNotIn(secret, rendered)

        self.assertEqual(
            repr(result),
            "ImportConfirmationPlan(file_kind='csv', record_count=3)",
        )
        self.assertEqual(
            repr(result.records[0]), "PlannedAccount(row_number=2)"
        )

    def test_errors_do_not_echo_inputs(self):
        data = _valid_csv()
        preview = parse("preview.csv", data)
        secret_key = PII_SENTINEL
        errors = []

        for overrides in (
            {"idempotency_key": secret_key},
            {"expected_file_sha256": secret_key},
            {"expected_content_sha256": "f" * 64},
        ):
            options = {
                "expected_file_sha256": preview.file_sha256,
                "expected_content_sha256": preview.content_sha256,
                "idempotency_key": KEY,
            }
            options.update(overrides)
            with self.assertRaises(ImportConfirmationError) as caught:
                plan(parse, data, "csv", **options)
            errors.append(caught.exception)

        for error in errors:
            rendered = " ".join(
                [str(error), repr(error), error.message]
                + [str(arg) for arg in error.args]
            )
            for secret in (
                PII_SENTINEL,
                KEY,
                preview.file_sha256,
                preview.content_sha256,
                "f" * 64,
            ):
                self.assertNotIn(secret, rendered)
            self.assertIsNone(error.__cause__)

    def test_error_repr_is_code_only(self):
        error = ImportConfirmationError("file_mismatch")
        self.assertEqual(
            repr(error), "ImportConfirmationError(code='file_mismatch')"
        )

    def test_unknown_error_code_is_refused(self):
        with self.assertRaises(ValueError):
            ImportConfirmationError(PII_SENTINEL)

    def test_all_codes_have_fixed_messages(self):
        for code, message in plan_module.CONFIRMATION_ERROR_MESSAGES.items():
            self.assertRegex(code, r"^[a-z][a-z_]+$")
            self.assertEqual(ImportConfirmationError(code).message, message)


# ------------------------------------------------- pureza / sem efeitos


ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "decimal",
    "hashlib",
    "re",
    "typing",
}
FORBIDDEN_CALLS = {"open", "print", "eval", "exec", "compile", "__import__",
                   "input", "breakpoint"}


def _plan_tree():
    return ast.parse(PLAN_PATH.read_text(encoding="utf-8"))


class PurityTests(unittest.TestCase):
    def test_module_imports_only_stdlib_pure_dependencies(self):
        imported = set()
        for node in ast.walk(_plan_tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)

        self.assertLessEqual(imported, ALLOWED_IMPORTS,
                             imported - ALLOWED_IMPORTS)

    def test_module_has_no_io_or_dynamic_calls(self):
        for node in ast.walk(_plan_tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, FORBIDDEN_CALLS)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"system", "popen", "connect",
                                             "execute", "commit", "write"})

    def test_module_documents_non_guarantees(self):
        doc = ast.get_docstring(_plan_tree())
        self.assertIn("NÃO grava nada", doc)
        self.assertIn("NÃO assegura", doc)

    def test_planning_never_opens_files(self):
        csv_data = _valid_csv()
        xlsx_data = _valid_xlsx()

        def forbidden(*args, **kwargs):
            raise AssertionError("planning must not open files")

        with mock.patch.object(builtins, "open", forbidden), \
                mock.patch.object(io, "open", forbidden):
            self.assertEqual(_plan_for(csv_data).record_count, 3)
            self.assertEqual(_plan_for(xlsx_data, "xlsx").record_count, 3)

    def test_no_app_services_main_or_database_modules_loaded(self):
        loaded = _modules_after_fresh_load(
            ("import_preview_parser_for_plan_test", PARSER_PATH),
            ("import_confirmation_plan_under_test", PLAN_PATH),
        )
        self.assertIn("import_confirmation_plan_under_test", loaded)
        for name in ("app.services", "app.main", "app.database",
                     "app.models", "sqlalchemy", "psycopg", "psycopg2",
                     "fastapi"):
            self.assertNotIn(name, loaded, name)


if __name__ == "__main__":
    unittest.main()
