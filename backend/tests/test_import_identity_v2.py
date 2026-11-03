"""
Testes offline da identidade de importação v2 (AREA2 TI2, CONTRACT-046 §A5).

Standalone com unittest, sem conftest, banco, rede ou disco. Parser,
transporte, plano/coordenação v2, adaptadores v1/v2 e rotas são carregados
pelo caminho do arquivo (sem ``app/services/__init__.py``). A coordenação
roda contra um fake transacional (cópia profunda, commit só na saída
normal); o adaptador v2 contra uma Session fake que só compila SQL. Nada
aqui prova concorrência, unicidade ou o schema num PostgreSQL real.

    cd backend
    python -B tests/test_import_identity_v2.py -v
"""

import ast
import contextlib
import copy
import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unicodedata
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi import Cookie, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SERVICES = BACKEND_DIR / "app" / "services"
V2_PATH = SERVICES / "import_identity_v2.py"
REPO_V2_PATH = SERVICES / "import_confirmation_repository_v2.py"
TI1_PATH = SERVICES / "account_title_reference.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parser = _load("v2_test_parser", SERVICES / "import_preview_parser.py")
transport = _load("v2_test_transport", SERVICES / "import_preview_transport.py")
plan_v1 = _load("v2_test_plan_v1", SERVICES / "import_confirmation_plan.py")
execution_v1 = _load("v2_test_execution_v1", SERVICES / "import_confirmation_execution.py")
repository_v1 = _load("v2_test_repository_v1", SERVICES / "import_confirmation_repository.py")
v2 = _load("v2_test_identity_v2", V2_PATH)
repository_v2 = _load("v2_test_repository_v2", REPO_V2_PATH)
confirm_route = _load("v2_test_confirm_route", BACKEND_DIR / "app/api/routes/import_confirm.py")
preview_route = _load("v2_test_preview_route", BACKEND_DIR / "app/api/routes/import_preview.py")

V2Error = v2.ImportIdentityV2Error
parse = parser.parse_import_preview

PII = "Fulana Sigilosa da Silva"
REF_PII = "CPF-123.456.789-00"
ORIGEM = "erp-omie"
ACTOR = "user:7"
KEY = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
OTHER_KEY = "0d9e8f7a-6b5c-4d3e-9f1a-2b3c4d5e6f70"
HEADER_V1 = "cliente;valor;vencimento;status"
HEADER_V2 = "cliente;valor;vencimento;status;referencia"


def _csv(*lines):
    return "\r\n".join(lines).encode("utf-8")


ROWS_V2 = _csv(
    HEADER_V2,
    f"{PII};10,00;2026-01-05;aberto;{REF_PII}",
    "Bruno Lima;0,20;2026-02-28;;NF2/1",
)


def _preview(data=ROWS_V2, origem=ORIGEM):
    return parse("preview.csv", data, identity_version=2, origem=origem)


def _plan(data=ROWS_V2, origem=ORIGEM, key=KEY):
    preview = _preview(data, origem)
    return v2.plan_import_confirmation_v2(
        parse, data, "csv",
        origem=origem,
        expected_file_sha256=preview.file_sha256,
        expected_content_sha256=preview.content_sha256,
        idempotency_key=key,
    )


# ================================================================ parser


class ParserV1UnchangedTests(unittest.TestCase):
    def test_default_is_v1_and_digest_bytes_are_unchanged(self):
        data = _csv(HEADER_V1 + ";documento", f"{PII};10,00;2026-01-05;aberto;NF1")
        default = parse("x.csv", data)
        explicit = parse("x.csv", data, identity_version=1)
        self.assertEqual(default, explicit)
        self.assertEqual(default.identity_version, 1)
        self.assertIsNone(default.title_source)
        # Em v1 "documento" segue ignorada e não entra nos valores.
        self.assertEqual(default.ignored_columns, (4,))
        self.assertNotIn("referencia", default.rows[0].values)
        self.assertEqual(default.rows[0].warnings, ())
        # Mesmo payload/serialização do digest v1 anterior ao TI2.
        payload = {
            "fields": sorted(default.column_mapping),
            "rows": [{
                "row": 2,
                "values": {f: (None if default.rows[0].values[f] is None
                               else str(default.rows[0].values[f]))
                           for f in parser.CANONICAL_FIELDS},
                "issues": [],
            }],
        }
        expected = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")).hexdigest()
        self.assertEqual(default.content_sha256, expected)

    def test_v1_tuple_duplicate_still_blocks(self):
        data = _csv(HEADER_V1, "Ana;1,00;2026-01-01;", "ana ;1,00;2026-01-01;")
        preview = parse("x.csv", data)
        self.assertEqual(preview.rows[1].issues[0].code, "duplicate_in_file")

    def test_version_and_origin_options_fail_closed(self):
        for kwargs, code in (
            ({"identity_version": 3}, "invalid_identity_version"),
            ({"identity_version": True}, "invalid_identity_version"),
            ({"identity_version": "2"}, "invalid_identity_version"),
            ({"origem": ORIGEM}, "invalid_origin"),
            ({"identity_version": 2}, "invalid_origin"),
            ({"identity_version": 2, "origem": "ERP"}, "invalid_origin"),
            ({"identity_version": 2, "origem": " erp"}, "invalid_origin"),
            ({"identity_version": 2, "origem": "erp\n"}, "invalid_origin"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(parser.ImportPreviewError) as caught:
                    parse("x.csv", ROWS_V2, **kwargs)
                self.assertEqual(caught.exception.code, code)


class ParserV2Tests(unittest.TestCase):
    def test_reference_column_is_required(self):
        with self.assertRaises(parser.ImportPreviewError) as caught:
            parse("x.csv", _csv(HEADER_V1, "Ana;1,00;2026-01-01;"),
                  identity_version=2, origem=ORIGEM)
        self.assertEqual(caught.exception.code, "missing_required_fields")

    def test_aliases(self):
        for header in ("Referência", "Referência do título", "Documento",
                       "Número do documento"):
            with self.subTest(header=header):
                preview = _preview(_csv(f"{HEADER_V1};{header}", "Ana;1,00;2026-01-01;;R1"))
                self.assertEqual(preview.column_mapping["referencia"], 4)
                self.assertEqual(preview.rows[0].values["referencia"], "R1")

    def test_row_reference_issues(self):
        data = _csv(
            HEADER_V2,
            "Ana;1,00;2026-01-01;;",
            "Bia;1,00;2026-01-01;;R​1",
            "Caio;1,00;2026-01-01;;" + "x" * 129,
            "Dani;1,00;2026-01-01;;  R4  ",
            "Edu;1,00;2026-01-01;;R4",
        )
        rows = _preview(data).rows
        self.assertEqual([i.code for i in rows[0].issues], ["reference_required"])
        self.assertEqual([i.code for i in rows[1].issues], ["invalid_reference"])
        self.assertEqual([i.code for i in rows[2].issues], ["invalid_reference"])
        self.assertEqual(rows[3].values["referencia"], "R4")
        # Mesma (origem, ref) duas vezes: a segunda linha bloqueia o arquivo.
        self.assertEqual([(i.field, i.code) for i in rows[4].issues],
                         [("referencia", "duplicate_reference")])

    def test_reference_is_nfc_and_case_preserving(self):
        decomposed = unicodedata.normalize("NFD", "Ação-a")
        rows = _preview(_csv(HEADER_V2, f"Ana;1,00;2026-01-01;;{decomposed}",
                             "Bia;1,00;2026-01-01;;AÇÃO-A")).rows
        self.assertEqual(rows[0].values["referencia"], "Ação-a")
        self.assertTrue(rows[1].is_valid)

    def test_coincident_tuple_with_distinct_refs_is_only_a_warning(self):
        data = _csv(HEADER_V2, "Ana;1,00;2026-01-01;;P1", "ana ;1,00;2026-01-01;;P2",
                    "Bia;2,00;2026-01-01;;P3")
        preview = _preview(data)
        self.assertEqual(preview.invalid_rows, 0)
        self.assertEqual([[w.code for w in r.warnings] for r in preview.rows],
                         [["coincident_tuple_in_file"]] * 2 + [[]])

    def test_digest_domain_is_separate_and_binds_origin_and_reference(self):
        v1_like = _csv(HEADER_V2, f"{PII};10,00;2026-01-05;aberto;R1")
        base = _preview(v1_like).content_sha256
        self.assertNotEqual(base, parse("x.csv", v1_like).content_sha256)
        self.assertNotEqual(base, _preview(v1_like, origem="erp-outro").content_sha256)
        changed = _csv(HEADER_V2, f"{PII};10,00;2026-01-05;aberto;R2")
        self.assertNotEqual(base, _preview(changed).content_sha256)
        reordered = _csv("referencia;cliente;valor;vencimento;status",
                         f"R1;{PII};10,00;2026-01-05;aberto")
        self.assertEqual(base, _preview(reordered).content_sha256)

    def test_rejected_categories_match_ti1_source(self):
        tree = ast.parse(TI1_PATH.read_text(encoding="utf-8"))
        (node,) = [n for n in tree.body if isinstance(n, ast.Assign)
                   and getattr(n.targets[0], "id", None) == "REJECTED_CATEGORIES"]
        ti1 = {elt.value for elt in node.value.args[0].elts}
        self.assertEqual(ti1, set(parser._REJECTED_REFERENCE_CATEGORIES))
        self.assertEqual(ti1, set(v2._REJECTED_REFERENCE_CATEGORIES))


# ================================================================ transporte


class TransportTests(unittest.TestCase):
    V1_KEYS = {"file_kind", "file_sha256", "content_sha256", "column_mapping",
               "ignored_columns", "total_rows", "valid_rows", "invalid_rows",
               "valid_total", "returned_rows", "rows_truncated", "rows"}

    def test_v1_output_keys_unchanged(self):
        payload = transport.serialize_preview(
            parse("x.csv", _csv(HEADER_V1, "Ana;1,00;2026-01-01;")), include_values=True)
        self.assertEqual(set(payload), self.V1_KEYS)
        self.assertEqual(set(payload["rows"][0]), {"row_number", "valid", "issues", "values"})

    def test_v2_output(self):
        data = _csv(HEADER_V2, "Ana;1,00;2026-01-01;;P1", "Ana;1,00;2026-01-01;;P2",
                    "Bia;1,00;2026-01-01;;")
        payload = transport.serialize_preview(_preview(data), include_values=True)
        self.assertEqual(set(payload) - self.V1_KEYS, {"identity_version", "origem", "warning_rows"})
        self.assertEqual((payload["identity_version"], payload["origem"]), (2, ORIGEM))
        self.assertEqual(payload["warning_rows"], 2)
        self.assertEqual(payload["rows"][0]["values"]["referencia"], "P1")
        self.assertEqual(payload["rows"][0]["warnings"][0]["code"], "coincident_tuple_in_file")
        self.assertEqual(payload["rows"][2]["issues"][0]["field"], "referencia")


# ================================================================ plano


class PlanTests(unittest.TestCase):
    def test_happy_plan(self):
        plan = _plan()
        self.assertEqual((plan.identity_version, plan.title_source, plan.record_count),
                         (2, ORIGEM, 2))
        self.assertEqual([r.title_reference for r in plan.records], [REF_PII, "NF2/1"])
        self.assertNotIn(REF_PII, repr(plan))
        self.assertNotIn(PII, repr(plan.records))

    def test_digest_from_other_origin_is_content_mismatch(self):
        preview = _preview()
        with self.assertRaises(V2Error) as caught:
            v2.plan_import_confirmation_v2(
                parse, ROWS_V2, "csv", origem="erp-outro",
                expected_file_sha256=preview.file_sha256,
                expected_content_sha256=preview.content_sha256,
                idempotency_key=KEY)
        self.assertEqual(caught.exception.code, "content_mismatch")

    def test_v1_digest_is_never_accepted_by_v2(self):
        v1 = parse("preview.csv", ROWS_V2)
        with self.assertRaises(V2Error) as caught:
            v2.plan_import_confirmation_v2(
                parse, ROWS_V2, "csv", origem=ORIGEM,
                expected_file_sha256=v1.file_sha256,
                expected_content_sha256=v1.content_sha256,
                idempotency_key=KEY)
        self.assertEqual(caught.exception.code, "content_mismatch")

    def test_invalid_rows_and_inputs(self):
        bad = _csv(HEADER_V2, "Ana;1,00;2026-01-01;;R1", "Bia;1,00;2026-01-01;;R1")
        with self.assertRaises(V2Error) as caught:
            _plan(bad)
        self.assertEqual(caught.exception.code, "invalid_rows")
        for kwargs, code in (({"origem": "ERP"}, "invalid_origin"),
                             ({"key": KEY.upper()}, "invalid_idempotency_key")):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(V2Error) as caught:
                    v2.plan_import_confirmation_v2(
                        parse, ROWS_V2, "csv",
                        origem=kwargs.get("origem", ORIGEM),
                        expected_file_sha256="0" * 64,
                        expected_content_sha256="0" * 64,
                        idempotency_key=kwargs.get("key", KEY))
                self.assertEqual(caught.exception.code, code)


# ================================================================ coordenação


class InjectedFailure(RuntimeError):
    pass


class FakeStore:
    """Repositório v2 em memória; commit só na saída normal."""

    def __init__(self):
        self.state = {"accounts": {}, "refs": {}, "receipts": [], "audit": [], "next": 1}
        self.log = []
        self.fail_at = None
        self.commits = 0
        self.rollbacks = 0

    @contextlib.contextmanager
    def transaction(self):
        working = copy.deepcopy(self.state)
        try:
            yield FakeUnit(self, working)
        except BaseException:
            self.rollbacks += 1
            raise
        self.state = working
        self.commits += 1

    def add_account(self, cliente, valor, vencimento, ref=None):
        number = self.state["next"]
        self.state["next"] += 1
        self.state["accounts"][number] = (cliente, Decimal(valor), vencimento)
        if ref is not None:
            self.state["refs"][(ORIGEM, ref)] = number
        return number


class FakeUnit:
    def __init__(self, store, working):
        self.store, self.w, self.locked = store, working, False

    def _step(self, name):
        self.store.log.append(name)
        if name != "lock_account_writers" and not self.locked:
            raise AssertionError("acesso antes do lock")
        if self.store.fail_at == name:
            raise InjectedFailure("injected")

    def lock_account_writers(self):
        self._step("lock_account_writers")
        self.locked = True

    def find_receipt(self, actor, key):
        self._step("find_receipt")
        return next((r for r in self.w["receipts"]
                     if (r.actor_reference, r.idempotency_key) == (actor, key)), None)

    def content_already_confirmed(self, sha, version):
        self._step("content_already_confirmed")
        return any((r.content_sha256, r.identity_version) == (sha, version)
                   for r in self.w["receipts"])

    def find_title_references(self, source, refs):
        self._step("find_title_references")
        return [(ref, account, *self.w["accounts"][account])
                for (src, ref), account in self.w["refs"].items()
                if src == source and ref in refs]

    def find_account_tuples(self, candidates):
        self._step("find_account_tuples")
        referenced = set(self.w["refs"].values())
        return [(number, c, v, d, number in referenced)
                for number, (c, v, d) in self.w["accounts"].items()
                if (v, d) in candidates]

    def create_account(self, draft):
        self._step("create_account")
        number = self.w["next"]
        self.w["next"] += 1
        self.w["accounts"][number] = (draft.cliente, draft.valor, draft.vencimento)
        return number

    def create_title_reference(self, assignment):
        self._step("create_title_reference")
        key = (assignment.title_source, assignment.title_reference)
        if key in self.w["refs"] or assignment.account_reference in self.w["refs"].values():
            raise InjectedFailure("unique violation")
        self.w["refs"][key] = assignment.account_reference

    def append_audit_event(self, event):
        self._step("append_audit_event")
        self.w["audit"].append(event)

    def insert_receipt(self, receipt):
        self._step("insert_receipt")
        self.w["receipts"].append(receipt)


def _execute(store, plan=None):
    return v2.execute_import_confirmation_v2(store, plan or _plan(), actor_reference=ACTOR)


class ExecutionTests(unittest.TestCase):
    def assertUntouched(self, store, before):
        self.assertEqual(store.state, before)
        self.assertEqual(store.commits, 0)

    def test_creates_accounts_references_audit_and_v2_receipt(self):
        store = FakeStore()
        result = _execute(store)
        self.assertEqual((result.replayed, result.record_count), (False, 2))
        self.assertEqual(store.state["refs"], {(ORIGEM, REF_PII): 1, (ORIGEM, "NF2/1"): 2})
        receipt = store.state["receipts"][0]
        self.assertEqual((receipt.identity_version, receipt.account_references, receipt.total),
                         (2, (1, 2), Decimal("10.20")))
        self.assertEqual([e.action for e in store.state["audit"]],
                         ["account.created_by_import"] * 2 + ["import.confirmed"])
        self.assertEqual(store.log[:3], ["lock_account_writers", "find_receipt",
                                         "content_already_confirmed"])

    def test_existing_same_is_matched_not_inserted(self):
        store = FakeStore()
        existing = store.add_account(" fulana  SIGILOSA da silva", "10.00", date(2026, 1, 5), REF_PII)
        result = _execute(store)
        self.assertEqual(result.record_count, 1)
        self.assertEqual([(m.row_number, m.account_reference) for m in result.matched],
                         [(2, existing)])
        receipt = store.state["receipts"][0]
        self.assertEqual((receipt.record_count, receipt.total), (1, Decimal("0.20")))
        self.assertEqual(len(store.state["accounts"]), 2)
        matched = [e for e in store.state["audit"] if e.action == "title_reference_matched"]
        self.assertEqual([(e.account_reference, e.row_number) for e in matched], [(existing, 2)])

    def test_all_existing_same_is_nothing_to_import_without_receipt(self):
        store = FakeStore()
        store.add_account(PII, "10.00", date(2026, 1, 5), REF_PII)
        store.add_account("Bruno Lima", "0.20", date(2026, 2, 28), "NF2/1")
        before = copy.deepcopy(store.state)
        with self.assertRaises(V2Error) as caught:
            _execute(store)
        self.assertEqual(caught.exception.code, "nothing_to_import")
        self.assertUntouched(store, before)

    def test_reference_with_other_payload_is_conflict_for_whole_file(self):
        store = FakeStore()
        owner = store.add_account(PII, "99.00", date(2026, 1, 5), REF_PII)
        before = copy.deepcopy(store.state)
        with self.assertRaises(V2Error) as caught:
            _execute(store)
        error = caught.exception
        self.assertEqual((error.code, error.status_code), ("reference_conflict", 409))
        self.assertEqual((error.row_numbers, error.account_ids), ((2,), (owner,)))
        self.assertUntouched(store, before)
        for text in (str(error), repr(error), repr(error.args)):
            self.assertNotIn(REF_PII, text)
            self.assertNotIn(PII, text)

    def test_unreferenced_tuple_is_legacy_match_review(self):
        store = FakeStore()
        legacy = store.add_account("Bruno  lima", "0.20", date(2026, 2, 28))
        before = copy.deepcopy(store.state)
        with self.assertRaises(V2Error) as caught:
            _execute(store)
        self.assertEqual(caught.exception.code, "legacy_match_review")
        self.assertEqual((caught.exception.row_numbers, caught.exception.account_ids),
                         ((3,), (legacy,)))
        self.assertUntouched(store, before)

    def test_referenced_coincident_tuple_is_created_with_warning(self):
        store = FakeStore()
        other = store.add_account("Bruno Lima", "0.20", date(2026, 2, 28), "NF2/2")
        result = _execute(store)
        self.assertEqual(result.record_count, 2)
        self.assertEqual([(w.row_number, w.code, w.account_references) for w in result.warnings],
                         [(3, "coincident_referenced_tuple", (other,))])

    def test_replay_and_version_conflicts(self):
        store = FakeStore()
        first = _execute(store)
        state = copy.deepcopy(store.state)
        replay = _execute(store)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, first.receipt)
        self.assertEqual(store.state, state)

        # Mesma chave com outro conteúdo v2.
        other = _csv(HEADER_V2, "Caio;3,00;2026-03-01;;R9")
        with self.assertRaises(V2Error) as caught:
            _execute(store, _plan(other))
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        # Mesmo conteúdo com chave nova.
        with self.assertRaises(V2Error) as caught:
            _execute(store, _plan(key=OTHER_KEY))
        self.assertEqual(caught.exception.code, "content_already_imported")

    def test_v1_receipt_with_same_key_is_idempotency_conflict(self):
        store = FakeStore()
        store.state["receipts"].append(execution_v1.ImportConfirmationReceipt(
            file_kind="csv", record_count=1, identity_version=1, actor_reference=ACTOR,
            idempotency_key=KEY, content_sha256="a" * 64, total=Decimal("1.00"),
            account_references=(1,)))
        with self.assertRaises(V2Error) as caught:
            _execute(store)
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_v1_execution_rejects_v2_receipt_with_same_key(self):
        # O adaptador v1 devolve recibos de qualquer versão como o DTO v1;
        # mesmo com o MESMO digest, a versão 2 vira idempotency_conflict.
        store = FakeStore()
        _execute(store)
        stored = execution_v1.ImportConfirmationReceipt(
            **dataclasses.asdict(store.state["receipts"][0]))
        writes = []
        unit = SimpleNamespace(
            lock_account_writers=lambda: None,
            find_receipt=lambda actor, key: stored,
            content_already_confirmed=lambda *args: False,
            find_account_identities=lambda candidates: (),
            create_account=lambda draft: writes.append(draft) or 1,
            append_audit_event=writes.append,
            insert_receipt=writes.append,
        )
        repo = SimpleNamespace(transaction=lambda: contextlib.nullcontext(unit))
        v1_plan = SimpleNamespace(
            file_kind="csv", record_count=1, idempotency_key=KEY,
            content_sha256=stored.content_sha256, total=Decimal("1.00"),
            records=(SimpleNamespace(row_number=2, cliente="Zeca", valor=Decimal("1.00"),
                                     vencimento=date(2026, 1, 1), status="aberto",
                                     email=None, whatsapp=None),))
        with self.assertRaises(execution_v1.ImportConfirmationExecutionError) as caught:
            execution_v1.execute_import_confirmation(repo, v1_plan, actor_reference=ACTOR)
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(writes, [])

    def test_failure_after_reference_insert_rolls_back_everything(self):
        for step in ("create_title_reference", "insert_receipt", "append_audit_event"):
            with self.subTest(step=step):
                store = FakeStore()
                store.fail_at = step
                before = copy.deepcopy(store.state)
                with self.assertRaises(InjectedFailure):
                    _execute(store)
                self.assertUntouched(store, before)
                self.assertEqual(store.rollbacks, 1)

    def test_invalid_plan_and_repository_fail_closed(self):
        plan = _plan()
        for changes in ({"identity_version": 1}, {"title_source": "ERP"},
                        {"records": ()}, {"total": Decimal("1.00")}):
            with self.subTest(changes=changes):
                with self.assertRaises(V2Error) as caught:
                    _execute(FakeStore(), dataclasses.replace(plan, **changes))
                self.assertEqual(caught.exception.code, "invalid_plan")
        with self.assertRaises(V2Error) as caught:
            v2.execute_import_confirmation_v2(SimpleNamespace(), plan, actor_reference=ACTOR)
        self.assertEqual(caught.exception.code, "invalid_repository")


# ================================================================ adaptador v2


DIALECT = postgresql.dialect()


class FakeResult:
    def __init__(self, rows, returns_rows=True):
        self._rows, self.returns_rows = rows, returns_rows

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.statements = []
        self.log = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def begin(self):
        self.log.append("begin")

    def connection(self):
        return SimpleNamespace(get_isolation_level=lambda: "READ COMMITTED")

    def execute(self, statement):
        compiled = statement.compile(dialect=DIALECT, compile_kwargs={"render_postcompile": True})
        sql = " ".join(str(compiled).split())
        self.statements.append((sql, dict(compiled.params)))
        if sql.startswith("INSERT INTO accounts "):
            return FakeResult([(500 + len(self.statements),)])
        if sql.startswith("INSERT"):
            return FakeResult([], returns_rows=False)
        for prefix, rows in self.rows.items():
            if sql.startswith(prefix):
                return FakeResult(rows)
        return FakeResult([])

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        self.log.append("rollback")

    def close(self):
        self.log.append("close")


def _v2_repo(session, verified=True):
    base = repository_v1.PostgresImportConfirmationRepository(
        lambda: session, execution_v1, writer_protocol_verified=verified)
    return repository_v2.PostgresImportIdentityV2Repository(base, repository_v1, v2)


class RepositoryV2Tests(unittest.TestCase):
    def test_unverified_protocol_blocks_before_session(self):
        opened = []
        base = repository_v1.PostgresImportConfirmationRepository(
            lambda: opened.append(1), execution_v1)
        repo = repository_v2.PostgresImportIdentityV2Repository(base, repository_v1, v2)
        with self.assertRaises(repository_v1.ImportConfirmationRepositoryError) as caught:
            repo.transaction()
        self.assertEqual(caught.exception.code, "writer_protocol_unverified")
        self.assertEqual(opened, [])

    def test_full_flow_sql_is_parametrized_and_in_one_transaction(self):
        session = FakeSession()
        result = _execute(_v2_repo(session))
        self.assertEqual(result.record_count, 2)
        self.assertEqual(session.log, ["begin", "commit", "close"])
        sqls = [sql for sql, _ in session.statements]
        self.assertTrue(sqls[0].startswith("SELECT pg_advisory_xact_lock"))
        refs = [p for s, p in session.statements
                if s.startswith("INSERT INTO account_title_references ")]
        self.assertEqual([p["assigned_via"] for p in refs], ["import_v2"] * 2)
        self.assertEqual([p["actor_user_id"] for p in refs], [7, 7])
        self.assertEqual({p["import_idempotency_key"] for p in refs}, {KEY})
        receipt = [p for s, p in session.statements
                   if s.startswith("INSERT INTO import_confirmation_receipts ")]
        self.assertEqual(receipt[0]["identity_version"], 2)
        for sql in sqls:
            self.assertNotIn(REF_PII, sql)
            self.assertNotIn(PII, sql)
            self.assertNotIn("lower(", sql.lower())

    def test_existing_reference_rows_drive_conflict_with_rollback(self):
        prefix = "SELECT account_title_references.title_reference"
        session = FakeSession({prefix: [(REF_PII, 41, PII, Decimal("99.00"), date(2026, 1, 5))]})
        with self.assertRaises(V2Error) as caught:
            _execute(_v2_repo(session))
        self.assertEqual(caught.exception.code, "reference_conflict")
        self.assertEqual(session.log, ["begin", "rollback", "close"])
        self.assertFalse(any(s.startswith("INSERT") for s, _ in session.statements))

    def test_unit_validates_actor_and_dtos(self):
        session = FakeSession()
        with _v2_repo(session).transaction() as unit:
            unit.lock_account_writers()
            assignment = v2.TitleReferenceAssignment(
                account_reference=1, title_source=ORIGEM, title_reference="R1",
                actor_reference="user:7", idempotency_key=KEY, content_sha256="a" * 64)
            for bad in (dataclasses.replace(assignment, actor_reference="actor-x"),
                        dataclasses.replace(assignment, actor_reference="user:0"),
                        dataclasses.replace(assignment, actor_reference="user:99999999999"),
                        dataclasses.replace(assignment, account_reference=0),
                        dataclasses.replace(assignment, title_source="ERP"),
                        dataclasses.replace(assignment, title_reference=" R1"),
                        dataclasses.replace(assignment, content_sha256="A" * 64),
                        SimpleNamespace(**dataclasses.asdict(assignment))):
                with self.subTest(bad=bad):
                    with self.assertRaises(repository_v1.ImportConfirmationRepositoryError):
                        unit.create_title_reference(bad)
            self.assertFalse(any(s.startswith("INSERT") for s, _ in session.statements))
            unit.create_title_reference(assignment)
            sql, params = session.statements[-1]
            self.assertTrue(sql.startswith("INSERT INTO account_title_references "))
            self.assertEqual((params["assigned_via"], params["actor_user_id"]), ("import_v2", 7))
            with self.assertRaises(repository_v1.ImportConfirmationRepositoryError):
                unit.content_already_confirmed("a" * 64, 1)

    def test_v1_adapter_source_still_never_touches_side_table(self):
        source = (SERVICES / "import_confirmation_repository.py").read_text(encoding="utf-8")
        self.assertNotIn("account_title_references", source)
        self.assertNotIn("title_references_table", source)


# ================================================================ rotas


def _require_api_key(x_api_key: str | None = Header(default=None)):
    if x_api_key != "k":
        raise HTTPException(401, "API key inválida.")


def _require_permission(permission):
    def dependency(session: str | None = Cookie(default=None)):
        if session != "ok":
            raise HTTPException(401, "Sessão ausente.")
        return SimpleNamespace(user=SimpleNamespace(id=7))
    return dependency


class _V1Repo:
    @contextlib.contextmanager
    def transaction(self):
        raise AssertionError("v1 não deve ser usado em v2")
        yield


class RouteTests(unittest.TestCase):
    def _client(self, wired=True, store=None):
        app = FastAPI()
        app.include_router(confirm_route.create_import_confirm_router(
            parser_module=parser, transport_module=transport, plan_module=plan_v1,
            execution_module=execution_v1, repository_module=repository_v1,
            repository=_V1Repo(), require_api_key=_require_api_key,
            require_permission=_require_permission,
            v2_module=v2 if wired else None,
            v2_repository=(store or FakeStore()) if wired else None,
        ))
        app.include_router(preview_route.create_import_preview_router(parser, transport))
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("session", "ok")
        return client

    def _confirm(self, client, data=ROWS_V2, **params):
        preview = _preview(data)
        query = {"format": "csv", "expected_file_sha256": preview.file_sha256,
                 "expected_content_sha256": preview.content_sha256,
                 "identity_version": "2", "origem": ORIGEM}
        query.update(params)
        query = {k: v for k, v in query.items() if v is not None}
        return client.post(confirm_route.IMPORT_CONFIRM_PATH, params=query, content=data,
                           headers={"content-type": "application/octet-stream",
                                    "x-api-key": "k", "idempotency-key": KEY})

    def test_v2_create_then_replay(self):
        store = FakeStore()
        client = self._client(store=store)
        first = self._confirm(client)
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertEqual((body["identity_version"], body["record_count"]), (2, 2))
        self.assertEqual(body["matched_existing"], [])
        replay = self._confirm(client)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])

    def test_v2_conflict_body_has_rows_and_ids_only(self):
        store = FakeStore()
        owner = store.add_account(PII, "99.00", date(2026, 1, 5), REF_PII)
        response = self._confirm(self._client(store=store))
        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual((detail["code"], detail["rows"], detail["account_ids"]),
                         ("reference_conflict", [2], [owner]))
        self.assertNotIn(REF_PII, response.text)
        self.assertNotIn(PII, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_request_shape(self):
        client = self._client()
        self.assertEqual(self._confirm(client, identity_version="3").status_code, 422)
        self.assertEqual(self._confirm(client, identity_version="1").status_code, 422)
        missing_origin = self._confirm(client, origem=None)
        self.assertEqual(missing_origin.json()["detail"]["code"], "invalid_origin")
        self.assertEqual(self._confirm(self._client(wired=False)).status_code, 503)

    def test_preview_route_v2_and_v1_default(self):
        client = self._client()
        headers = {"content-type": "application/octet-stream"}
        v2_response = client.post("/imports/preview", content=ROWS_V2, headers=headers,
                                  params={"format": "csv", "identity_version": "2",
                                          "origem": ORIGEM})
        self.assertEqual(v2_response.status_code, 200, v2_response.text)
        self.assertEqual(v2_response.json()["content_sha256"], _preview().content_sha256)
        v1_response = client.post("/imports/preview", content=ROWS_V2, headers=headers,
                                  params={"format": "csv"})
        self.assertNotIn("identity_version", v1_response.json())
        bad = client.post("/imports/preview", content=ROWS_V2, headers=headers,
                          params={"format": "csv", "origem": ORIGEM})
        self.assertEqual(bad.status_code, 422)


# ================================================================ pureza


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
        [sys.executable, "-I", "-B", "-c", _FRESH_LOAD_PROBE, str(BACKEND_DIR),
         json.dumps([[name, str(path)] for name, path in targets])],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"fresh load failed: {result.stderr}")
    return set(json.loads(result.stdout))


class PurityTests(unittest.TestCase):
    def test_v2_module_is_stdlib_only(self):
        tree = ast.parse(V2_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"lower", "upper", "execute", "commit"})
        self.assertLessEqual(imported, {
            "__future__", "hashlib", "re", "unicodedata", "collections.abc",
            "contextlib", "dataclasses", "datetime", "decimal", "typing"})
        loaded = _modules_after_fresh_load(("v2_fresh", V2_PATH))
        for name in ("sqlalchemy", "fastapi", "app.services", "app.database", "app.models"):
            self.assertNotIn(name, loaded)

    def test_repository_v2_loads_no_app_services_or_driver(self):
        loaded = _modules_after_fresh_load(("repo_v2_fresh", REPO_V2_PATH))
        for name in ("app.services", "app.database", "app.models", "app.main",
                     "psycopg", "psycopg2"):
            self.assertNotIn(name, loaded)


class PreviewPrivacyRegressionTests(unittest.TestCase):
    def test_v2_row_and_preview_repr_hide_personal_values(self):
        preview = _preview()
        self.assertEqual(preview.valid_rows, 2)
        self.assertEqual(preview.rows[0].values["referencia"], REF_PII)
        for obj in (preview.rows[0], preview):
            for marker in (PII, REF_PII, "Bruno Lima", "NF2/1"):
                self.assertNotIn(marker, repr(obj))
                self.assertNotIn(marker, str(obj))

    def test_v1_row_and_preview_repr_hide_personal_values(self):
        data = _csv(HEADER_V1 + ";email;whatsapp",
                    f"{PII};10,00;2026-01-05;aberto;private-marker@example.com;11987654321")
        preview = parse("preview.csv", data)
        self.assertEqual(preview.valid_rows, 1)
        self.assertEqual(preview.rows[0].values["cliente"], PII)
        for obj in (preview.rows[0], preview):
            for marker in (PII, "private-marker@example.com", "11987654321"):
                self.assertNotIn(marker, repr(obj))
                self.assertNotIn(marker, str(obj))

    def test_hiding_repr_preserves_values_equality_and_serialization(self):
        row = _preview().rows[0]
        self.assertEqual(dataclasses.asdict(row)["values"], row.values)
        self.assertEqual(dataclasses.replace(row), row)
        self.assertNotIn("values=", repr(row))


if __name__ == "__main__":
    unittest.main()
