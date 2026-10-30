"""
Testes offline da coordenação da confirmação de importação
(MVP-FIN-IMPORT-01 / TASK-024).

Standalone com unittest, sem conftest, banco, rede ou disco. O repositório
é um fake transacional: cada transação trabalha numa cópia profunda do
estado, que só substitui o original no commit; qualquer exceção descarta
a cópia (rollback real das estruturas). Os módulos são carregados pelo
caminho do arquivo para não executar ``app/services/__init__.py``.

    cd backend
    python -B tests/test_import_confirmation_execution.py -v
"""

import ast
import contextlib
import copy
import dataclasses
import importlib.util
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


BACKEND_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = BACKEND_DIR / "app" / "services"
PARSER_PATH = SERVICES_DIR / "import_preview_parser.py"
PLAN_PATH = SERVICES_DIR / "import_confirmation_plan.py"
EXECUTION_PATH = SERVICES_DIR / "import_confirmation_execution.py"

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


plan_module = _load("import_confirmation_plan_for_execution_test", PLAN_PATH)
execution = _load("import_confirmation_execution_under_test", EXECUTION_PATH)

execute = execution.execute_import_confirmation
ExecutionError = execution.ImportConfirmationExecutionError
Receipt = execution.ImportConfirmationReceipt
AuditEvent = execution.ImportAuditEvent
AccountDraft = execution.AccountDraft

PII_SENTINEL = "Fulana Sigilosa da Silva"
EMAIL_SENTINEL = "fulana.sigilosa@example.com"
PHONE_SENTINEL = "+55 (11) 98888-7777"
ACTOR = "actor-7f3a9c"
OTHER_ACTOR = "actor-1b2c3d"
KEY = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
OTHER_KEY = "0d9e8f7a-6b5c-4d3e-9f1a-2b3c4d5e6f70"
CONTENT_A = "a" * 63 + "1"
CONTENT_B = "b" * 63 + "2"
FILE_HASH = "c" * 63 + "3"
TOTAL_TEXT = "1234.66"

ROWS = (
    (PII_SENTINEL, "1234.56", date(2026, 1, 5), EMAIL_SENTINEL, None),
    ("Bruno Lima", "0.10", date(2026, 2, 28), None, PHONE_SENTINEL),
)


def _plan(key=KEY, content=CONTENT_A, rows=ROWS, file_kind="csv"):
    records = tuple(
        plan_module.PlannedAccount(
            row_number=index + 2,
            cliente=cliente,
            valor=Decimal(valor),
            vencimento=vencimento,
            status="aberto",
            email=email,
            whatsapp=whatsapp,
        )
        for index, (cliente, valor, vencimento, email, whatsapp)
        in enumerate(rows)
    )
    return plan_module.ImportConfirmationPlan(
        file_kind=file_kind,
        record_count=len(records),
        idempotency_key=key,
        file_sha256=FILE_HASH,
        content_sha256=content,
        total=sum((r.valor for r in records), Decimal("0.00")),
        records=records,
    )


def _namespace_plan(**changes):
    source = _plan()
    fields = {
        "file_kind": source.file_kind,
        "record_count": source.record_count,
        "idempotency_key": source.idempotency_key,
        "content_sha256": source.content_sha256,
        "total": source.total,
        "records": tuple(
            SimpleNamespace(**dataclasses.asdict(record))
            for record in source.records
        ),
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def _record(**changes):
    fields = dataclasses.asdict(_plan().records[0])
    fields.update(changes)
    return SimpleNamespace(**fields)


class InjectedFailure(RuntimeError):
    pass


class CommitFailure(RuntimeError):
    pass


class FakeStore:
    """
    Repositório transacional em memória, apenas para testes. Registra a
    ordem das chamadas e recusa leitura/escrita antes do lock.
    """

    def __init__(self):
        self.state = {"accounts": {}, "receipts": [], "audit": [], "next": 1}
        self.log = []
        self.fail_at = {}
        self.fail_commit = False
        self.commits = 0
        self.rollbacks = 0
        self.candidates = []
        self.drafts = []

    @contextlib.contextmanager
    def transaction(self):
        working = copy.deepcopy(self.state)
        unit = FakeUnit(self, working)
        self.log.append("begin")

        try:
            yield unit
        except BaseException:
            self.rollbacks += 1
            self.log.append("rollback")
            raise

        if self.fail_commit:
            self.rollbacks += 1
            self.log.append("rollback")
            raise CommitFailure("commit failed")

        self.state = working
        self.commits += 1
        self.log.append("commit")

    def add_account(self, cliente, valor, vencimento, status="aberto"):
        number = self.state["next"]
        self.state["next"] += 1
        self.state["accounts"][number] = {
            "cliente": cliente,
            "valor": Decimal(valor),
            "vencimento": vencimento,
            "status": status,
            "email": None,
            "whatsapp": None,
        }

    def snapshot(self):
        return copy.deepcopy(self.state)


class FakeUnit:
    def __init__(self, store, working):
        self.store = store
        self.working = working
        self.locked = False
        self.calls = {}

    def _step(self, name):
        self.store.log.append(name)

        if name != "lock_account_writers" and not self.locked:
            raise AssertionError("acesso antes do lock")

        self.calls[name] = self.calls.get(name, 0) + 1

        if self.store.fail_at.get(name) == self.calls[name]:
            raise InjectedFailure("injected")

    def lock_account_writers(self):
        self._step("lock_account_writers")
        self.locked = True

    def find_receipt(self, actor_reference, idempotency_key):
        self._step("find_receipt")
        for receipt in self.working["receipts"]:
            if (
                receipt.actor_reference == actor_reference
                and receipt.idempotency_key == idempotency_key
            ):
                return receipt
        return None

    def content_already_confirmed(self, content_sha256, identity_version):
        self._step("content_already_confirmed")
        return any(
            receipt.content_sha256 == content_sha256
            and receipt.identity_version == identity_version
            for receipt in self.working["receipts"]
        )

    def find_account_identities(self, candidates):
        self._step("find_account_identities")
        self.store.candidates.append(candidates)
        return [
            (account["cliente"], account["valor"], account["vencimento"])
            for account in self.working["accounts"].values()
            if (account["valor"], account["vencimento"]) in candidates
        ]

    def create_account(self, draft):
        self._step("create_account")
        self.store.drafts.append(draft)
        number = self.working["next"]
        self.working["next"] += 1
        self.working["accounts"][number] = {
            "cliente": draft.cliente,
            "valor": draft.valor,
            "vencimento": draft.vencimento,
            "status": draft.status,
            "email": draft.email,
            "whatsapp": draft.whatsapp,
        }
        return number

    def append_audit_event(self, event):
        self._step("append_audit_event")
        self.working["audit"].append(event)

    def insert_receipt(self, receipt):
        self._step("insert_receipt")
        for stored in self.working["receipts"]:
            if (
                stored.actor_reference == receipt.actor_reference
                and stored.idempotency_key == receipt.idempotency_key
            ):
                raise InjectedFailure("unique violation")
        self.working["receipts"].append(receipt)


CREATE_LOG = [
    "begin",
    "lock_account_writers",
    "find_receipt",
    "content_already_confirmed",
    "find_account_identities",
    "create_account",
    "create_account",
    "append_audit_event",
    "append_audit_event",
    "insert_receipt",
    "append_audit_event",
    "commit",
]


class SuccessTests(unittest.TestCase):
    def test_creates_open_accounts_audit_and_receipt_atomically(self):
        store = FakeStore()
        result = execute(store, _plan(), actor_reference=ACTOR)

        self.assertFalse(result.replayed)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(store.commits, 1)
        self.assertEqual(store.rollbacks, 0)

        accounts = store.state["accounts"]
        self.assertEqual(sorted(accounts), [1, 2])
        self.assertEqual(accounts[1]["cliente"], PII_SENTINEL)
        self.assertEqual(accounts[1]["valor"], Decimal("1234.56"))
        self.assertEqual(accounts[1]["vencimento"], date(2026, 1, 5))
        self.assertEqual(accounts[1]["email"], EMAIL_SENTINEL)
        self.assertEqual(accounts[2]["whatsapp"], PHONE_SENTINEL)
        self.assertTrue(all(a["status"] == "aberto" for a in accounts.values()))

        receipt = result.receipt
        self.assertEqual(store.state["receipts"], [receipt])
        self.assertEqual(receipt.account_references, (1, 2))
        self.assertEqual(receipt.actor_reference, ACTOR)
        self.assertEqual(receipt.idempotency_key, KEY)
        self.assertEqual(receipt.content_sha256, CONTENT_A)
        self.assertEqual(receipt.identity_version, 1)
        self.assertEqual(receipt.file_kind, "csv")
        self.assertEqual(receipt.total, Decimal(TOTAL_TEXT))

        audit = store.state["audit"]
        self.assertEqual(
            [(e.action, e.account_reference, e.row_number, e.record_count)
             for e in audit],
            [
                ("account.created_by_import", 1, 2, None),
                ("account.created_by_import", 2, 3, None),
                ("import.confirmed", None, None, 2),
            ],
        )
        self.assertTrue(all(e.actor_reference == ACTOR for e in audit))
        self.assertTrue(all(e.idempotency_key == KEY for e in audit))

    def test_lock_precedes_every_read_and_write(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        self.assertEqual(store.log, CREATE_LOG)

    def test_drafts_are_frozen_and_open(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)

        for draft in store.drafts:
            self.assertIs(type(draft), AccountDraft)
            self.assertEqual(draft.status, "aberto")
            with self.assertRaises(dataclasses.FrozenInstanceError):
                draft.status = "pago"

    def test_candidates_are_exact_value_and_due_date_pairs(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        self.assertEqual(
            store.candidates,
            [frozenset({
                (Decimal("1234.56"), date(2026, 1, 5)),
                (Decimal("0.10"), date(2026, 2, 28)),
            })],
        )

    def test_duck_typed_plan_is_accepted(self):
        store = FakeStore()
        result = execute(store, _namespace_plan(), actor_reference=ACTOR)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(store.commits, 1)

    def test_result_is_immutable(self):
        result = execute(FakeStore(), _plan(), actor_reference=ACTOR)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.replayed = True
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.receipt.record_count = 9
        self.assertIsInstance(result.receipt.account_references, tuple)

    def test_real_parser_plan_and_execution(self):
        parser = _load("import_preview_parser_for_execution_test", PARSER_PATH)
        data = "\r\n".join([
            "cliente;valor;vencimento;status;email;whatsapp",
            f"{PII_SENTINEL};R$ 1.234,56;05/01/2026;Aberto;{EMAIL_SENTINEL};",
            "Bruno Lima;0,10;2026-02-28;;;",
        ]).encode("utf-8")
        preview = parser.parse_import_preview("preview.csv", data)
        confirmed = plan_module.plan_import_confirmation(
            parser.parse_import_preview,
            data,
            "csv",
            expected_file_sha256=preview.file_sha256,
            expected_content_sha256=preview.content_sha256,
            idempotency_key=KEY,
        )

        store = FakeStore()
        result = execute(store, confirmed, actor_reference=ACTOR)

        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.receipt.content_sha256, preview.content_sha256)
        self.assertEqual(result.receipt.total, Decimal(TOTAL_TEXT))
        self.assertEqual(
            [a["valor"] for a in store.state["accounts"].values()],
            [Decimal("1234.56"), Decimal("0.10")],
        )


class IdempotencyTests(unittest.TestCase):
    def test_corrupt_receipt_is_rejected_without_recreating_accounts(self):
        for changes in (
            {'record_count': 99}, {'record_count': True},
            {'total': Decimal('0.00')}, {'total': Decimal('NaN')},
            {'account_references': (1,)}, {'account_references': (1, 1)},
            {'account_references': (1, False)}, {'identity_version': True},
        ):
            with self.subTest(changes=changes):
                store = FakeStore()
                execute(store, _plan(), actor_reference=ACTOR)
                store.state['receipts'][0] = dataclasses.replace(store.state['receipts'][0], **changes)
                store.log.clear()
                with self.assertRaises(ExecutionError) as caught:
                    execute(store, _plan(), actor_reference=ACTOR)
                self.assertEqual(caught.exception.code, 'invalid_repository')
                self.assertNotIn('create_account', store.log)
                self.assertEqual(store.log[-1], 'rollback')

    def test_lost_response_after_commit_is_resolved_by_same_key_replay(self):
        store = FakeStore()
        class LostResponse:
            @contextlib.contextmanager
            def transaction(self):
                with store.transaction() as unit:
                    yield unit
                raise CommitFailure('response lost after actual commit')
        with self.assertRaises(CommitFailure):
            execute(LostResponse(), _plan(), actor_reference=ACTOR)
        committed = store.snapshot()
        self.assertEqual(len(committed['accounts']), 2)
        result = execute(store, _plan(), actor_reference=ACTOR)
        self.assertTrue(result.replayed)
        self.assertEqual(store.state, committed)

    def test_replay_returns_original_receipt_without_new_effects(self):
        store = FakeStore()
        first = execute(store, _plan(), actor_reference=ACTOR)
        before = store.snapshot()
        store.log.clear()

        second = execute(store, _plan(), actor_reference=ACTOR)

        self.assertTrue(second.replayed)
        self.assertEqual(second.receipt, first.receipt)
        self.assertEqual(second.record_count, 2)
        self.assertEqual(store.state, before)
        self.assertEqual(
            store.log,
            ["begin", "lock_account_writers", "find_receipt", "commit"],
        )

    def test_replay_after_accounts_deleted_does_not_recreate(self):
        store = FakeStore()
        first = execute(store, _plan(), actor_reference=ACTOR)
        store.state["accounts"].clear()
        store.log.clear()

        second = execute(store, _plan(), actor_reference=ACTOR)

        self.assertTrue(second.replayed)
        self.assertEqual(second.receipt.account_references, (1, 2))
        self.assertEqual(second.receipt, first.receipt)
        self.assertEqual(store.state["accounts"], {})
        self.assertNotIn("create_account", store.log)
        self.assertNotIn("find_account_identities", store.log)

    def test_same_key_other_content_conflicts(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        before = store.snapshot()

        other_rows = (("Outra Pessoa", "5.00", date(2026, 5, 1), None, None),)

        with self.assertRaises(ExecutionError) as caught:
            execute(
                store,
                _plan(content=CONTENT_B, rows=other_rows),
                actor_reference=ACTOR,
            )

        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(store.state, before)
        self.assertEqual(store.rollbacks, 1)

    def test_other_actor_key_never_returns_foreign_receipt(self):
        store = FakeStore()
        first = execute(store, _plan(), actor_reference=ACTOR)

        other_rows = (("Outra Pessoa", "5.00", date(2026, 5, 1), None, None),)
        second = execute(
            store,
            _plan(content=CONTENT_B, rows=other_rows),
            actor_reference=OTHER_ACTOR,
        )

        self.assertFalse(second.replayed)
        self.assertNotEqual(second.receipt, first.receipt)
        self.assertEqual(second.receipt.actor_reference, OTHER_ACTOR)
        self.assertEqual(second.receipt.account_references, (3,))
        self.assertEqual(len(store.state["receipts"]), 2)

    def test_other_actor_same_key_same_content_is_blocked_without_leak(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        before = store.snapshot()

        with self.assertRaises(ExecutionError) as caught:
            execute(store, _plan(), actor_reference=OTHER_ACTOR)

        error = caught.exception
        self.assertEqual(error.code, "content_already_imported")
        self.assertEqual(store.state, before)
        self.assertFalse(hasattr(error, "receipt"))
        self.assertEqual(error.args, (error.message,))

    def test_same_content_new_key_is_blocked(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        before = store.snapshot()

        with self.assertRaises(ExecutionError) as caught:
            execute(store, _plan(key=OTHER_KEY), actor_reference=ACTOR)

        self.assertEqual(caught.exception.code, "content_already_imported")
        self.assertEqual(store.state, before)

    def test_same_content_new_key_blocked_even_after_deletion(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        store.state["accounts"].clear()
        before = store.snapshot()

        with self.assertRaises(ExecutionError) as caught:
            execute(store, _plan(key=OTHER_KEY), actor_reference=ACTOR)

        self.assertEqual(caught.exception.code, "content_already_imported")
        self.assertEqual(store.state, before)
        self.assertEqual(store.state["accounts"], {})


class IdentityTests(unittest.TestCase):
    def test_existing_account_blocks_whole_batch(self):
        variants = (
            PII_SENTINEL,
            "  fulana   SIGILOSA da silva ",
            "Fulana Sigilosa da Silva",
        )

        for cliente in variants:
            with self.subTest(cliente=repr(cliente)):
                store = FakeStore()
                store.add_account(cliente, "1234.56", date(2026, 1, 5), "pago")
                before = store.snapshot()

                with self.assertRaises(ExecutionError) as caught:
                    execute(store, _plan(), actor_reference=ACTOR)

                self.assertEqual(
                    caught.exception.code, "existing_identity_conflict"
                )
                self.assertEqual(store.state, before)
                self.assertNotIn("create_account", store.log)

    def test_identity_is_revisited_after_plan_was_built(self):
        # Plano montado sem conflito; conta concorrente surge antes da
        # transação e só a revisão pós-lock a detecta.
        confirmed = _plan()
        store = FakeStore()
        store.add_account("BRUNO LIMA", "0.10", date(2026, 2, 28))

        with self.assertRaises(ExecutionError) as caught:
            execute(store, confirmed, actor_reference=ACTOR)

        self.assertEqual(caught.exception.code, "existing_identity_conflict")
        self.assertEqual(
            store.log[:5],
            [
                "begin",
                "lock_account_writers",
                "find_receipt",
                "content_already_confirmed",
                "find_account_identities",
            ],
        )

    def test_same_client_other_value_or_date_is_allowed(self):
        store = FakeStore()
        store.add_account(PII_SENTINEL, "1234.57", date(2026, 1, 5))
        store.add_account(PII_SENTINEL, "1234.56", date(2026, 1, 6))

        result = execute(store, _plan(), actor_reference=ACTOR)

        self.assertEqual(result.receipt.account_references, (3, 4))

    def test_identity_matches_plan_identity_key(self):
        samples = (
            (PII_SENTINEL, Decimal("1.00"), date(2026, 1, 1)),
            ("  Ana  Maria ", Decimal("2.50"), date(2026, 2, 2)),
            ("STRASSE", Decimal("3.00"), date(2026, 3, 3)),
            ("straße", Decimal("3.00"), date(2026, 3, 3)),
        )

        for sample in samples:
            with self.subTest(sample=repr(sample[0])):
                self.assertEqual(
                    execution._identity(*sample),
                    plan_module.identity_key(*sample),
                )


class AtomicityTests(unittest.TestCase):
    def _assert_rolled_back(self, store, before, error_type):
        with self.assertRaises(error_type):
            execute(store, _plan(), actor_reference=ACTOR)

        self.assertEqual(store.state, before)
        self.assertEqual(store.commits, 0)
        self.assertEqual(store.rollbacks, 1)
        self.assertEqual(store.log[-1], "rollback")

    def test_failure_at_any_write_leaves_no_partial_commit(self):
        points = (
            ("create_account", 1),
            ("create_account", 2),
            ("append_audit_event", 1),
            ("append_audit_event", 2),
            ("insert_receipt", 1),
            ("append_audit_event", 3),
        )

        for name, call in points:
            with self.subTest(point=f"{name}#{call}"):
                store = FakeStore()
                store.fail_at = {name: call}
                self._assert_rolled_back(store, store.snapshot(), InjectedFailure)

                # Chave não consumida: nova tentativa conclui normalmente.
                store.fail_at = {}
                result = execute(store, _plan(), actor_reference=ACTOR)
                self.assertFalse(result.replayed)
                self.assertEqual(store.commits, 1)
                self.assertEqual(len(store.state["receipts"]), 1)
                self.assertEqual(len(store.state["audit"]), 3)

    def test_failure_on_commit_returns_nothing_and_keeps_state(self):
        store = FakeStore()
        store.fail_commit = True
        self._assert_rolled_back(store, store.snapshot(), CommitFailure)

        store.fail_commit = False
        result = execute(store, _plan(), actor_reference=ACTOR)
        self.assertFalse(result.replayed)

    def test_failure_on_lock_reads_nothing(self):
        store = FakeStore()
        store.fail_at = {"lock_account_writers": 1}
        self._assert_rolled_back(store, store.snapshot(), InjectedFailure)
        self.assertEqual(
            store.log, ["begin", "lock_account_writers", "rollback"]
        )

    def test_failure_on_reads_rolls_back(self):
        for name in (
            "find_receipt",
            "content_already_confirmed",
            "find_account_identities",
        ):
            with self.subTest(read=name):
                store = FakeStore()
                store.fail_at = {name: 1}
                self._assert_rolled_back(
                    store, store.snapshot(), InjectedFailure
                )
                self.assertNotIn("create_account", store.log)

    def test_suppressing_context_manager_fails_closed(self):
        store = FakeStore()
        store.fail_at = {"create_account": 1}

        class Swallowing:
            @contextlib.contextmanager
            def transaction(self):
                try:
                    with store.transaction() as unit:
                        yield unit
                except InjectedFailure:
                    pass

        with self.assertRaises(ExecutionError) as caught:
            execute(Swallowing(), _plan(), actor_reference=ACTOR)

        self.assertEqual(caught.exception.code, "invalid_repository")
        self.assertEqual(store.state["accounts"], {})


class InputTests(unittest.TestCase):
    def test_invalid_actor_rejected_before_repository(self):
        for actor in (
            None, "", " ", " actor", "actor ", "a" * 129, "actor\n",
            "actor\x00", b"actor", 7, True, ["actor"],
        ):
            with self.subTest(actor=repr(actor)):
                store = FakeStore()
                with self.assertRaises(ExecutionError) as caught:
                    execute(store, _plan(), actor_reference=actor)
                self.assertEqual(caught.exception.code, "invalid_actor")
                self.assertEqual(store.log, [])

    def test_actor_is_keyword_only(self):
        with self.assertRaises(TypeError):
            execute(FakeStore(), _plan(), ACTOR)

    def test_invalid_plan_rejected_before_repository(self):
        good = _record()
        later = _record(row_number=3, cliente="Bruno Lima")
        cases = {
            "none": None,
            "format": _namespace_plan(file_kind="pdf"),
            "format_type": _namespace_plan(file_kind=b"csv"),
            "key_upper": _namespace_plan(idempotency_key=KEY.upper()),
            "key_v1": _namespace_plan(
                idempotency_key="3f2b8c1e-9a4d-1e6f-8b2a-1c3d5e7f9a0b"
            ),
            "key_none": _namespace_plan(idempotency_key=None),
            "content_short": _namespace_plan(content_sha256="a" * 63),
            "content_upper": _namespace_plan(content_sha256="A" * 64),
            "records_list": _namespace_plan(records=[good]),
            "records_empty": _namespace_plan(records=(), record_count=0),
            "count_mismatch": _namespace_plan(record_count=3),
            "count_bool": _namespace_plan(
                records=(good,), record_count=True,
                total=Decimal("1234.56"),
            ),
            "total_wrong": _namespace_plan(total=Decimal("1.00")),
            "total_float": _namespace_plan(total=1234.66),
            "status_paid": _namespace_plan(
                records=(_record(status="pago"),), record_count=1,
                total=Decimal("1234.56"),
            ),
            "value_float": _namespace_plan(
                records=(_record(valor=1234.56),), record_count=1,
                total=Decimal("1234.56"),
            ),
            "value_scale": _namespace_plan(
                records=(_record(valor=Decimal("1234.560")),), record_count=1,
                total=Decimal("1234.56"),
            ),
            "value_zero": _namespace_plan(
                records=(_record(valor=Decimal("0.00")),), record_count=1,
                total=Decimal("0.00"),
            ),
            "datetime": _namespace_plan(
                records=(_record(vencimento=datetime(2026, 1, 5)),),
                record_count=1, total=Decimal("1234.56"),
            ),
            "blank_client": _namespace_plan(
                records=(_record(cliente="  "),), record_count=1,
                total=Decimal("1234.56"),
            ),
            "row_number": _namespace_plan(
                records=(_record(row_number=1),), record_count=1,
                total=Decimal("1234.56"),
            ),
            "row_order": _namespace_plan(records=(later, good)),
            "duplicate_identity": _namespace_plan(
                records=(good, _record(row_number=3, cliente="FULANA "
                                       "sigilosa  da silva")),
                total=Decimal("2469.12"),
            ),
            "email_type": _namespace_plan(
                records=(_record(email=1),), record_count=1,
                total=Decimal("1234.56"),
            ),
            "missing_field": SimpleNamespace(
                file_kind="csv", record_count=1, idempotency_key=KEY,
                content_sha256=CONTENT_A, total=Decimal("1234.56"),
            ),
            "record_missing_field": _namespace_plan(
                records=(SimpleNamespace(row_number=2),), record_count=1,
                total=Decimal("1234.56"),
            ),
        }

        for name, candidate in cases.items():
            with self.subTest(case=name):
                store = FakeStore()
                with self.assertRaises(ExecutionError) as caught:
                    execute(store, candidate, actor_reference=ACTOR)
                self.assertEqual(caught.exception.code, "invalid_plan")
                self.assertEqual(store.log, [])

    def test_invalid_repository_shape(self):
        for repository in (None, object(), SimpleNamespace(transaction=1)):
            with self.subTest(repository=type(repository).__name__):
                with self.assertRaises(ExecutionError) as caught:
                    execute(repository, _plan(), actor_reference=ACTOR)
                self.assertEqual(caught.exception.code, "invalid_repository")

    def test_unit_missing_method_rolls_back(self):
        store = FakeStore()

        class Partial:
            @contextlib.contextmanager
            def transaction(self):
                with store.transaction() as unit:
                    yield SimpleNamespace(
                        lock_account_writers=unit.lock_account_writers
                    )

        with self.assertRaises(ExecutionError) as caught:
            execute(Partial(), _plan(), actor_reference=ACTOR)

        self.assertEqual(caught.exception.code, "invalid_repository")
        self.assertEqual(store.log, ["begin", "rollback"])

    def test_malformed_adapter_answers_fail_closed(self):
        foreign = Receipt(
            file_kind="csv",
            record_count=2,
            identity_version=1,
            actor_reference=OTHER_ACTOR,
            idempotency_key=KEY,
            content_sha256=CONTENT_A,
            total=Decimal(TOTAL_TEXT),
            account_references=(1, 2),
        )
        overrides = {
            "receipt_dict": ("find_receipt", lambda *a: {"key": KEY}),
            "receipt_false": ("find_receipt", lambda *a: False),
            "receipt_foreign_actor": ("find_receipt", lambda *a: foreign),
            "content_str": ("content_already_confirmed", lambda *a: "no"),
            "content_none": ("content_already_confirmed", lambda *a: None),
            "content_int": ("content_already_confirmed", lambda *a: 0),
            "identities_none": ("find_account_identities", lambda *a: None),
            "identities_str": ("find_account_identities", lambda *a: "abc"),
            "identities_list_item": (
                "find_account_identities",
                lambda *a: [[PII_SENTINEL, Decimal("1.00"), date(2026, 1, 1)]],
            ),
            "identities_float": (
                "find_account_identities",
                lambda *a: [(PII_SENTINEL, 1.0, date(2026, 1, 1))],
            ),
            "identities_datetime": (
                "find_account_identities",
                lambda *a: [(PII_SENTINEL, Decimal("1.00"),
                             datetime(2026, 1, 1))],
            ),
            "identities_too_many": (
                "find_account_identities",
                lambda *a: (
                    ("x", Decimal("1.00"), date(2026, 1, 1))
                    for _ in range(execution.MAX_EXISTING_IDENTITIES + 1)
                ),
            ),
            "account_none": ("create_account", lambda *a: None),
            "account_bool": ("create_account", lambda *a: True),
            "account_zero": ("create_account", lambda *a: 0),
            "account_empty": ("create_account", lambda *a: ""),
            "account_duplicate": ("create_account", lambda *a: 7),
        }

        for name, (method, replacement) in overrides.items():
            with self.subTest(case=name):
                store = FakeStore()

                class Patched:
                    @contextlib.contextmanager
                    def transaction(self, method=method, fn=replacement):
                        with store.transaction() as unit:
                            original = getattr(unit, method)

                            def patched(*args):
                                original(*args)
                                return fn(*args)

                            setattr(unit, method, patched)
                            yield unit

                with self.assertRaises(ExecutionError) as caught:
                    execute(Patched(), _plan(), actor_reference=ACTOR)

                self.assertEqual(caught.exception.code, "invalid_repository")
                self.assertEqual(store.state["receipts"], [])
                self.assertEqual(store.state["accounts"], {})
                self.assertEqual(store.log[-1], "rollback")


class PrivacyTests(unittest.TestCase):
    FORBIDDEN = (
        PII_SENTINEL, "Fulana", "Bruno", EMAIL_SENTINEL, PHONE_SENTINEL,
        ACTOR, KEY, CONTENT_A, FILE_HASH, TOTAL_TEXT, "1234.56", "2026",
    )

    def _assert_clean(self, text):
        for forbidden in self.FORBIDDEN:
            self.assertNotIn(forbidden, text)

    def test_internal_plan_snapshot_repr_is_private(self):
        self._assert_clean(repr(execution._snapshot_plan(_plan())))

    def test_reprs_hide_personal_and_secret_data(self):
        store = FakeStore()
        result = execute(store, _plan(), actor_reference=ACTOR)

        self.assertEqual(
            repr(result),
            "ImportConfirmationResult(replayed=False, record_count=2)",
        )
        self.assertEqual(
            repr(result.receipt),
            "ImportConfirmationReceipt(file_kind='csv', record_count=2, "
            "identity_version=1)",
        )

        for value in (
            result, result.receipt, *store.drafts, *store.state["audit"],
        ):
            self._assert_clean(repr(value))
            self._assert_clean(str(value))

    def test_audit_events_carry_no_file_personal_data(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)

        for event in store.state["audit"]:
            values = repr(dataclasses.astuple(event))
            for forbidden in (PII_SENTINEL, "Bruno", EMAIL_SENTINEL,
                              PHONE_SENTINEL):
                self.assertNotIn(forbidden, values)

    def test_errors_carry_only_fixed_codes(self):
        store = FakeStore()
        execute(store, _plan(), actor_reference=ACTOR)
        errors = []

        for call in (
            lambda: execute(store, _plan(), actor_reference="bad\n"),
            lambda: execute(store, _plan(key=OTHER_KEY),
                            actor_reference=ACTOR),
            lambda: execute(store, _plan(content=CONTENT_B),
                            actor_reference=ACTOR),
            lambda: execute(FakeStore(), _namespace_plan(total=None),
                            actor_reference=ACTOR),
        ):
            with self.assertRaises(ExecutionError) as caught:
                call()
            errors.append(caught.exception)

        fresh = FakeStore()
        fresh.add_account(PII_SENTINEL, "1234.56", date(2026, 1, 5))
        with self.assertRaises(ExecutionError) as caught:
            execute(fresh, _plan(), actor_reference=ACTOR)
        errors.append(caught.exception)

        self.assertEqual(
            [e.code for e in errors],
            [
                "invalid_actor",
                "content_already_imported",
                "idempotency_conflict",
                "invalid_plan",
                "existing_identity_conflict",
            ],
        )

        for error in errors:
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertEqual(repr(error),
                             f"ImportConfirmationExecutionError(code="
                             f"{error.code!r})")
            self.assertEqual(
                str(error), execution.EXECUTION_ERROR_MESSAGES[error.code]
            )
            self._assert_clean(repr(error) + str(error))

    def test_unknown_error_code_is_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionError("database said: Fulana")


class PurityTests(unittest.TestCase):
    ALLOWED_IMPORTS = {
        "__future__", "re", "collections.abc", "contextlib", "dataclasses",
        "datetime", "decimal", "typing",
    }
    FORBIDDEN_NAMES = {
        "open", "print", "eval", "exec", "compile", "__import__", "input",
        "globals", "setattr",
    }

    def setUp(self):
        self.source = EXECUTION_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_imports_are_standard_library_only(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, self.ALLOWED_IMPORTS)

    def test_no_io_dynamic_code_or_sql_lower(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, self.FORBIDDEN_NAMES)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"lower", "upper"})
        self.assertNotIn("lower(", self.source.lower().replace(
            "nenhuma ``lower()``", ""))

    def test_no_module_level_mutable_store(self):
        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                self.assertNotIsInstance(
                    value, (ast.List, ast.Set, ast.ListComp, ast.SetComp)
                )
                if isinstance(value, ast.Call):
                    self.assertNotIn(
                        getattr(value.func, "id", None),
                        {"list", "set", "dict", "defaultdict"},
                    )

    def test_docstring_disclaims_real_database_guarantee(self):
        doc = ast.get_docstring(self.tree)
        self.assertIn("NÃO implementa as garantias", doc)
        self.assertIn("Nenhum adaptador real", doc)
        self.assertIn("single-company", doc)

    def test_no_heavy_modules_loaded(self):
        loaded = _modules_after_fresh_load(
            ("import_confirmation_plan_for_execution_test", PLAN_PATH),
            ("import_confirmation_execution_under_test", EXECUTION_PATH),
        )
        self.assertIn("import_confirmation_execution_under_test", loaded)
        for name in ("sqlalchemy", "psycopg2", "psycopg", "fastapi",
                     "app.database", "app.models", "app.main",
                     "app.services"):
            self.assertNotIn(name, loaded)


if __name__ == "__main__":
    unittest.main()
