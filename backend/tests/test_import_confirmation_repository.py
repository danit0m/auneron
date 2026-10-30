"""
Testes offline do adaptador PostgreSQL NÃO REGISTRADO da confirmação de
importação (MVP-FIN-IMPORT-01 / TASK-024).

Standalone com unittest, sem conftest, banco, engine, rede ou disco. A
Session é um fake que compila cada statement com o dialeto PostgreSQL do
SQLAlchemy (sem DBAPI) e registra SQL, parâmetros e chamadas de
transação. Os módulos são carregados pelo caminho do arquivo para não
executar ``app/services/__init__.py``. Nada aqui prova concorrência,
durabilidade ou o schema real: as tabelas são apenas metadata.

    cd backend
    python -B tests/test_import_confirmation_repository.py -v
"""

import ast
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

from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import ResourceClosedError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
SERVICES_DIR = BACKEND_DIR / "app" / "services"
EXECUTION_PATH = SERVICES_DIR / "import_confirmation_execution.py"
REPOSITORY_PATH = SERVICES_DIR / "import_confirmation_repository.py"
ACCOUNT_MODEL_PATH = BACKEND_DIR / "app" / "models" / "account.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Pureza medida num processo NOVO: o sys.modules deste processo pode já
# conter app.* (coleta do pytest, conftest), e isso não diz nada sobre o
# que o módulo sob teste importa. -I ignora PYTHON* e o site do usuário;
# o ambiente mínimo não carrega DATABASE_URL nem rede.
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


execution = _load("import_confirmation_execution_for_repository_test", EXECUTION_PATH)
repository = _load("import_confirmation_repository_under_test", REPOSITORY_PATH)

Repo = repository.PostgresImportConfirmationRepository
RepoError = repository.ImportConfirmationRepositoryError
LOCK_KEY = repository.IMPORT_CONFIRMATION_LOCK_KEY

PII = "Fulana Sigilosa da Silva"
EMAIL = "fulana.sigilosa@example.com"
PHONE = "+55 (11) 98888-7777"
ACTOR = "actor-7f3a9c"
KEY = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
CONTENT = "a" * 63 + "1"
DIALECT = postgresql.dialect()


# ---------------------------------------------------------------- fakes


class FakeResult:
    def __init__(self, rows, returns_rows=True):
        self._rows = rows
        self.returns_rows = returns_rows

    def all(self):
        if not self.returns_rows:
            raise ResourceClosedError()
        return list(self._rows)


class FakeConnection:
    def __init__(self, session):
        self._session = session

    def get_isolation_level(self):
        self._session.log.append(("isolation",))
        return self._session.isolation


class FakeSession:
    """Session fake por instância; nenhum estado global."""

    def __init__(
        self,
        rows=None,
        dialect="postgresql",
        isolation="READ COMMITTED",
        fail_execute_at=None,
        execute_error=None,
        fail_commit=False,
        fail_rollback=False,
    ):
        self.rows = rows or {}
        self.dialect = dialect
        self.isolation = isolation
        self.fail_execute_at = fail_execute_at
        self.execute_error = execute_error
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.log = []
        self.statements = []
        self._next_id = 100

    def get_bind(self):
        if self.dialect is None:
            return None

        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def begin(self):
        self.log.append(("begin",))

    def connection(self):
        return FakeConnection(self)

    def execute(self, statement):
        compiled = statement.compile(
            dialect=DIALECT, compile_kwargs={"render_postcompile": True}
        )
        sql = " ".join(str(compiled).split())
        params = dict(compiled.params)
        kind = _kind(sql)

        self.statements.append((sql, params))
        self.log.append(("execute", kind))

        if self.fail_execute_at == len(self.statements):
            raise self.execute_error

        if kind == "insert_account":
            self._next_id += 1
            return FakeResult([(self._next_id,)])

        return FakeResult(self.rows.get(kind, []), returns_rows=kind not in ('insert_receipt', 'insert_audit'))

    def commit(self):
        self.log.append(("commit",))

        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.log.append(("rollback",))

        if self.fail_rollback:
            raise RuntimeError("rollback failed")

    def close(self):
        self.log.append(("close",))


def _kind(sql):
    prefixes = (
        ("SELECT pg_advisory_xact_lock", "lock"),
        ("SELECT import_confirmation_receipts.actor_reference", "find_receipt"),
        ("SELECT %(param_1)s AS anon_1 FROM import_confirmation_receipts", "content"),
        ("SELECT accounts.cliente", "identities"),
        ("INSERT INTO accounts ", "insert_account"),
        ("INSERT INTO import_confirmation_audit_events ", "insert_audit"),
        ("INSERT INTO import_confirmation_receipts ", "insert_receipt"),
    )

    for prefix, kind in prefixes:
        if sql.startswith(prefix):
            return kind

    raise AssertionError("SQL inesperado no fake")


class Factory:
    def __init__(self, **options):
        self.options = options
        self.sessions = []

    def __call__(self):
        session = FakeSession(**self.options)
        self.sessions.append(session)
        return session

    @property
    def session(self):
        assert len(self.sessions) == 1
        return self.sessions[0]


def _repo(factory=None, **kwargs):
    kwargs.setdefault("writer_protocol_verified", True)
    return Repo(factory or Factory(), execution, **kwargs)


def _kinds(session):
    return [entry[0] if entry[0] != "execute" else entry[1] for entry in session.log]


def _flat(values):
    for value in values:
        if isinstance(value, (list, tuple)):
            yield from _flat(value)
        else:
            yield value


def _plan(rows=None):
    rows = rows or (
        (PII, "1234.56", date(2026, 1, 5), EMAIL, None),
        ("Bruno Lima", "0.10", date(2026, 2, 28), None, PHONE),
    )
    records = tuple(
        SimpleNamespace(
            row_number=index + 2,
            cliente=cliente,
            valor=Decimal(valor),
            vencimento=vencimento,
            status="aberto",
            email=email,
            whatsapp=whatsapp,
        )
        for index, (cliente, valor, vencimento, email, whatsapp) in enumerate(rows)
    )
    return SimpleNamespace(
        file_kind="csv",
        record_count=len(records),
        idempotency_key=KEY,
        content_sha256=CONTENT,
        total=sum((r.valor for r in records), Decimal("0.00")),
        records=records,
    )


def _receipt_row(**overrides):
    row = {
        "actor_reference": ACTOR,
        "idempotency_key": KEY,
        "identity_version": 1,
        "content_sha256": CONTENT,
        "file_kind": "csv",
        "record_count": 2,
        "total": Decimal("1234.66"),
        "account_references": [101, 102],
    }
    row.update(overrides)
    return tuple(row.values())


def _draft(**overrides):
    values = dict(
        row_number=2,
        cliente=PII,
        valor=Decimal("10.00"),
        vencimento=date(2026, 1, 5),
        status="aberto",
        email=EMAIL,
        whatsapp=PHONE,
    )
    values.update(overrides)
    return execution.AccountDraft(**values)


def _locked_unit(factory):
    """Abre a transação manualmente; o chamador fecha com ``cm.__exit__``."""
    cm = _repo(factory).transaction()
    unit = cm.__enter__()
    unit.lock_account_writers()
    return cm, unit


class _UnitCase(unittest.TestCase):
    def open(self, **options):
        self.factory = Factory(**options)
        self.cm, self.unit = _locked_unit(self.factory)
        self.addCleanup(self._close)
        return self.unit

    def _close(self):
        try:
            self.cm.__exit__(None, None, None)
        except Exception:
            pass

    def last(self):
        return self.factory.session.statements[-1]


# ---------------------------------------------------------------- configuração


class ConfigurationTests(unittest.TestCase):
    def test_default_flag_blocks_before_opening_session(self):
        factory = Factory()
        repo = Repo(factory, execution)

        with self.assertRaises(RepoError) as caught:
            repo.transaction()

        self.assertEqual(caught.exception.code, "writer_protocol_unverified")
        self.assertEqual(factory.sessions, [])

    def test_explicit_false_also_blocks(self):
        factory = Factory()
        repo = Repo(factory, execution, writer_protocol_verified=False)

        with self.assertRaises(RepoError):
            repo.transaction()

        with self.assertRaises(RepoError):
            execution.execute_import_confirmation(
                repo, _plan(), actor_reference=ACTOR
            )

        self.assertEqual(factory.sessions, [])

    def test_flag_must_be_strict_bool(self):
        for flag in (1, "true", None, "False", 0):
            with self.subTest(flag=flag):
                with self.assertRaises(RepoError) as caught:
                    Repo(Factory(), execution, writer_protocol_verified=flag)
                self.assertEqual(caught.exception.code, "invalid_configuration")

    def test_flag_is_keyword_only_and_no_fallback_mode(self):
        with self.assertRaises(TypeError):
            Repo(Factory(), execution, True)

        with self.assertRaises(TypeError):
            Repo(Factory(), execution, fallback=True)

    def test_invalid_factory_or_execution_module(self):
        broken = SimpleNamespace(
            **{n: getattr(execution, n) for n in repository._EXECUTION_NAMES}
        )
        del broken.ImportAuditEvent

        cases = {
            "factory": (None, execution),
            "module": (Factory(), broken),
            "version": (Factory(), SimpleNamespace(**{**vars(execution), "IDENTITY_VERSION": 2})),
        }

        for name, (factory, module) in cases.items():
            with self.subTest(name):
                with self.assertRaises(RepoError) as caught:
                    Repo(factory, module, writer_protocol_verified=True)
                self.assertEqual(caught.exception.code, "invalid_configuration")

    def test_injected_accounts_table_must_have_real_fields(self):
        incomplete = Table("accounts", MetaData(), Column("id", Integer, primary_key=True))

        with self.assertRaises(RepoError):
            _repo(accounts=incomplete)

        with self.assertRaises(RepoError):
            _repo(accounts="accounts")

        compatible = Table(
            "accounts",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("cliente", String(150)),
            Column("email", String(150)),
            Column("whatsapp", String(30)),
            Column("valor", Numeric(14, 2)),
            Column("vencimento", Date),
            Column("status", String(30)),
        )
        factory = Factory()
        repo = _repo(factory, accounts=compatible)

        with repo.transaction() as unit:
            unit.lock_account_writers()
            self.assertEqual(unit.create_account(_draft()), 101)

        self.assertEqual(_kinds(factory.session)[-2:], ["commit", "close"])

    def test_repositories_do_not_share_state(self):
        first, second = Factory(), Factory()

        with _repo(first).transaction() as unit:
            unit.lock_account_writers()

        self.assertEqual(second.sessions, [])
        self.assertEqual(len(first.sessions), 1)


# ---------------------------------------------------------------- transação


class TransactionTests(unittest.TestCase):
    def test_non_postgresql_dialect_rejected_before_begin(self):
        for dialect in ("sqlite", "mysql", "PostgreSQL", None):
            with self.subTest(dialect=dialect):
                factory = Factory(dialect=dialect)

                with self.assertRaises(RepoError) as caught:
                    with _repo(factory).transaction():
                        self.fail("não deveria entrar")

                self.assertEqual(caught.exception.code, "unsupported_dialect")
                self.assertEqual(_kinds(factory.session), ["close"])

    def test_isolation_other_than_read_committed_rejected(self):
        for level in ("SERIALIZABLE", "REPEATABLE READ", "read committed", "AUTOCOMMIT", None):
            with self.subTest(level=level):
                factory = Factory(isolation=level)

                with self.assertRaises(RepoError) as caught:
                    with _repo(factory).transaction():
                        self.fail("não deveria entrar")

                self.assertEqual(caught.exception.code, "unsupported_isolation")
                self.assertEqual(
                    _kinds(factory.session),
                    ["begin", "isolation", "rollback", "close"],
                )

    def test_invalid_session_closed_and_rejected(self):
        closed = []
        partial = SimpleNamespace(close=lambda: closed.append(True))

        for session in (partial, object()):
            with self.subTest(session=type(session).__name__):
                with self.assertRaises(RepoError) as caught:
                    with _repo(lambda s=session: s).transaction():
                        self.fail("não deveria entrar")
                self.assertEqual(caught.exception.code, "invalid_session")

        self.assertEqual(closed, [True])

    def test_success_commits_once_then_closes(self):
        factory = Factory()

        with _repo(factory).transaction() as unit:
            unit.lock_account_writers()

        self.assertEqual(
            _kinds(factory.session),
            ["begin", "isolation", "lock", "commit", "close"],
        )

    def test_body_exception_rolls_back_closes_and_propagates(self):
        factory = Factory()

        with self.assertRaises(KeyError):
            with _repo(factory).transaction() as unit:
                unit.lock_account_writers()
                raise KeyError("x")

        self.assertEqual(
            _kinds(factory.session),
            ["begin", "isolation", "lock", "rollback", "close"],
        )

    def test_sqlalchemy_commit_error_is_private_and_still_rolls_back(self):
        session = FakeSession()
        def fail_commit():
            session.log.append(('commit',))
            raise OperationalError('INSERT', {'private': PII}, Exception(PII))
        session.commit = fail_commit
        with self.assertRaises(RepoError) as caught:
            with _repo(lambda: session).transaction() as unit:
                unit.lock_account_writers()
        self.assertEqual(caught.exception.code, 'database_error')
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(PII, str(caught.exception))
        self.assertEqual(_kinds(session)[-3:], ['commit', 'rollback', 'close'])

    def test_commit_failure_rolls_back_and_closes(self):
        factory = Factory(fail_commit=True)

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            with _repo(factory).transaction() as unit:
                unit.lock_account_writers()

        self.assertEqual(
            _kinds(factory.session),
            ["begin", "isolation", "lock", "commit", "rollback", "close"],
        )

    def test_rollback_failure_still_closes(self):
        factory = Factory(fail_rollback=True)

        with self.assertRaises(RuntimeError) as caught:
            with _repo(factory).transaction() as unit:
                unit.lock_account_writers()
                raise KeyError("body")

        self.assertIsInstance(caught.exception.__context__, KeyError)
        self.assertEqual(_kinds(factory.session)[-1], "close")

    def test_unit_unusable_after_exit(self):
        factory = Factory()

        with _repo(factory).transaction() as unit:
            unit.lock_account_writers()

        with self.assertRaises(RepoError) as caught:
            unit.find_receipt(ACTOR, KEY)

        self.assertEqual(caught.exception.code, "invalid_session")
        self.assertEqual(len(factory.session.statements), 1)

    def test_each_transaction_uses_new_session(self):
        factory = Factory()
        repo = _repo(factory)

        for _ in range(2):
            with repo.transaction() as unit:
                unit.lock_account_writers()

        self.assertEqual(len(factory.sessions), 2)
        self.assertIsNot(factory.sessions[0], factory.sessions[1])


# ---------------------------------------------------------------- lock


class LockTests(unittest.TestCase):
    def test_lock_is_first_statement_and_parametrized(self):
        factory = Factory()

        with _repo(factory).transaction() as unit:
            unit.lock_account_writers()

        sql, params = factory.session.statements[0]
        self.assertTrue(sql.startswith("SELECT pg_advisory_xact_lock(%(lock_key)s)"))
        self.assertEqual(params, {"lock_key": LOCK_KEY})
        self.assertNotIn(str(LOCK_KEY), sql)
        self.assertLess(LOCK_KEY, 2**63)

    def test_operations_before_lock_rejected_without_sql(self):
        calls = {
            "find_receipt": (ACTOR, KEY),
            "content_already_confirmed": (CONTENT, 1),
            "find_account_identities": (frozenset(),),
            "create_account": (_draft(),),
            "append_audit_event": (None,),
            "insert_receipt": (None,),
        }

        for name, args in calls.items():
            with self.subTest(name):
                factory = Factory()

                with self.assertRaises(RepoError) as caught:
                    with _repo(factory).transaction() as unit:
                        getattr(unit, name)(*args)

                self.assertEqual(caught.exception.code, "lock_required")
                self.assertEqual(factory.session.statements, [])
                self.assertIn("rollback", _kinds(factory.session))

    def test_second_lock_rejected(self):
        factory = Factory()

        with self.assertRaises(RepoError):
            with _repo(factory).transaction() as unit:
                unit.lock_account_writers()
                unit.lock_account_writers()

        self.assertEqual(len(factory.session.statements), 1)


# ---------------------------------------------------------------- leituras


class ReceiptReadTests(_UnitCase):
    def test_select_bound_to_actor_and_key(self):
        unit = self.open()

        self.assertIsNone(unit.find_receipt(ACTOR, KEY))

        sql, params = self.last()
        self.assertIn(
            "WHERE import_confirmation_receipts.actor_reference = %(actor_reference_1)s "
            "AND import_confirmation_receipts.idempotency_key = %(idempotency_key_1)s",
            sql,
        )
        self.assertIn("LIMIT %(param_1)s", sql)
        self.assertEqual(
            params,
            {"actor_reference_1": ACTOR, "idempotency_key_1": KEY, "param_1": 2},
        )
        self.assertNotIn(ACTOR, sql)
        self.assertNotIn(KEY, sql)

    def test_row_mapped_to_execution_receipt(self):
        unit = self.open(rows={"find_receipt": [_receipt_row()]})

        receipt = unit.find_receipt(ACTOR, KEY)

        self.assertIs(type(receipt), execution.ImportConfirmationReceipt)
        self.assertEqual(receipt.account_references, (101, 102))
        self.assertEqual(receipt.total, Decimal("1234.66"))
        self.assertNotIn(ACTOR, repr(receipt))

    def test_malformed_receipt_rows_rejected(self):
        cases = {
            "other_actor": [_receipt_row(actor_reference="actor-other")],
            "other_key": [_receipt_row(idempotency_key="x")],
            "float_total": [_receipt_row(total=1234.66)],
            "scale": [_receipt_row(total=Decimal("1234.660"))],
            "refs_tuple": [_receipt_row(account_references=(101, 102))],
            "refs_bool": [_receipt_row(account_references=[True, 102])],
            "refs_count": [_receipt_row(account_references=[101])],
            "kind": [_receipt_row(file_kind="pdf")],
            "version_str": [_receipt_row(identity_version="1")],
            "short_row": [_receipt_row()[:7]],
            "two_rows": [_receipt_row(), _receipt_row()],
        }

        for name, rows in cases.items():
            with self.subTest(name):
                unit = self.open(rows={"find_receipt": rows})

                with self.assertRaises(RepoError) as caught:
                    unit.find_receipt(ACTOR, KEY)

                self.assertEqual(caught.exception.code, "invalid_row")

    def test_content_check_parametrized_and_bool(self):
        unit = self.open()
        self.assertIs(unit.content_already_confirmed(CONTENT, 1), False)

        sql, params = self.last()
        self.assertIn("content_sha256 = %(content_sha256_1)s", sql)
        self.assertIn("identity_version = %(identity_version_1)s", sql)
        self.assertEqual(params["content_sha256_1"], CONTENT)
        self.assertEqual(params["identity_version_1"], 1)
        self.assertNotIn("actor_reference", sql)

        unit = self.open(rows={"content": [(1,)]})
        self.assertIs(unit.content_already_confirmed(CONTENT, 1), True)

    def test_invalid_arguments(self):
        unit = self.open()

        for args in ((None, KEY), (ACTOR, ""), (ACTOR, "k" * 129), (b"a", KEY)):
            with self.subTest(args=args):
                with self.assertRaises(RepoError):
                    unit.find_receipt(*args)

        with self.assertRaises(RepoError):
            unit.content_already_confirmed(CONTENT, "1")

        self.assertEqual(len(self.factory.session.statements), 1)


class IdentityReadTests(_UnitCase):
    def test_pairs_parametrized_and_raw_names_returned(self):
        raw = ("  FULANA Sigilosa ", Decimal("10.00"), date(2026, 1, 5))
        unit = self.open(rows={"identities": [raw]})
        candidates = frozenset({(Decimal("10.00"), date(2026, 1, 5))})

        self.assertEqual(unit.find_account_identities(candidates), (raw,))

        sql, params = self.last()
        self.assertIn("(accounts.valor, accounts.vencimento) IN (", sql)
        self.assertNotIn("lower(", sql.lower())
        self.assertNotIn("10.00", sql)
        self.assertNotIn("2026", sql)
        self.assertIn(Decimal("10.00"), list(_flat(params.values())))
        self.assertIn(date(2026, 1, 5), list(_flat(params.values())))
        self.assertIn("ORDER BY accounts.id LIMIT", sql)

    def test_candidates_chunked_and_sorted(self):
        unit = self.open()
        base = date(2026, 1, 1)
        candidates = frozenset(
            (Decimal(f"{n}.00"), date.fromordinal(base.toordinal() + n % 7))
            for n in range(1, 1202)
        )

        self.assertEqual(unit.find_account_identities(candidates), ())

        selects = self.factory.session.statements[1:]
        self.assertEqual(len(selects), 3)
        seen = []

        for sql, params in selects:
            values = [v for v in _flat(params.values()) if isinstance(v, (Decimal, date))]
            self.assertLessEqual(len(values), 2 * repository.IDENTITY_CHUNK_SIZE)
            seen.extend(values)

        expected = list(
            _flat(sorted(candidates, key=lambda pair: (pair[1], pair[0])))
        )
        self.assertEqual(seen, expected)

    def test_empty_candidates_issue_no_sql(self):
        unit = self.open()
        self.assertEqual(unit.find_account_identities(frozenset()), ())
        self.assertEqual(len(self.factory.session.statements), 1)

    def test_invalid_candidates_rejected(self):
        unit = self.open()
        cases = (
            {(Decimal("1.00"), date(2026, 1, 1))},
            frozenset({(1.0, date(2026, 1, 1))}),
            frozenset({(Decimal("1.0"), date(2026, 1, 1))}),
            frozenset({(Decimal("1.00"), datetime(2026, 1, 1))}),
            frozenset({(Decimal("1.00"),)}),
            frozenset({"x"}),
        )

        for candidates in cases:
            with self.subTest(candidates=candidates):
                with self.assertRaises(RepoError) as caught:
                    unit.find_account_identities(candidates)
                self.assertEqual(caught.exception.code, "invalid_argument")

        self.assertEqual(len(self.factory.session.statements), 1)

    def test_invalid_identity_rows_rejected(self):
        pair = frozenset({(Decimal("1.00"), date(2026, 1, 1))})
        cases = (
            [("A", 1.0, date(2026, 1, 1))],
            [("A", Decimal("1.00"), datetime(2026, 1, 1))],
            [(None, Decimal("1.00"), date(2026, 1, 1))],
            [("A", Decimal("1.00"))],
        )

        for rows in cases:
            with self.subTest(rows=rows):
                unit = self.open(rows={"identities": rows})
                with self.assertRaises(RepoError) as caught:
                    unit.find_account_identities(pair)
                self.assertEqual(caught.exception.code, "invalid_row")

    def test_result_bounded(self):
        small = SimpleNamespace(**{**vars(execution), "MAX_EXISTING_IDENTITIES": 2})
        factory = Factory(
            rows={"identities": [("A", Decimal("1.00"), date(2026, 1, 1))] * 3}
        )
        repo = Repo(factory, small, writer_protocol_verified=True)
        pair = frozenset({(Decimal("1.00"), date(2026, 1, 1))})

        with self.assertRaises(RepoError) as caught:
            with repo.transaction() as unit:
                unit.lock_account_writers()
                unit.find_account_identities(pair)

        self.assertEqual(caught.exception.code, "invalid_row")
        sql, params = factory.session.statements[1]
        self.assertIn("LIMIT %(", sql)
        self.assertEqual(
            [v for v in _flat(params.values()) if type(v) is int], [3]
        )

        with self.assertRaises(RepoError):
            with repo.transaction() as unit:
                unit.lock_account_writers()
                unit.find_account_identities(
                    frozenset(
                        (Decimal(f"{n}.00"), date(2026, 1, 1)) for n in range(1, 4)
                    )
                )


# ---------------------------------------------------------------- escritas


class WriteTests(_UnitCase):
    def test_create_account_insert_returning_id(self):
        unit = self.open()

        self.assertEqual(unit.create_account(_draft()), 101)

        sql, params = self.last()
        self.assertTrue(sql.startswith("INSERT INTO accounts (cliente, email, whatsapp, valor, vencimento, status) VALUES ("))
        self.assertTrue(sql.endswith("RETURNING accounts.id"))
        self.assertEqual(
            sorted(params.values(), key=repr),
            sorted([PII, EMAIL, PHONE, Decimal("10.00"), date(2026, 1, 5), "aberto"], key=repr),
        )
        for secret in (PII, EMAIL, PHONE, "10.00"):
            self.assertNotIn(secret, sql)

    def test_create_account_rejects_non_drafts(self):
        unit = self.open()
        cases = (
            SimpleNamespace(**vars(_draft())),
            _draft(status="pago"),
            _draft(valor=Decimal("10")),
            _draft(vencimento=datetime(2026, 1, 5)),
        )

        for draft in cases:
            with self.subTest(draft=draft):
                with self.assertRaises(RepoError) as caught:
                    unit.create_account(draft)
                self.assertEqual(caught.exception.code, "invalid_argument")

    def test_create_account_rejects_bad_returning(self):
        for returned in ([], [(0,)], [(True,)], [("1",)], [(1,), (2,)], [(1, 2)]):
            with self.subTest(returned=returned):
                unit = self.open()
                self.factory.session.execute = (
                    lambda statement, rows=returned: FakeResult(rows)
                )
                with self.assertRaises(RepoError) as caught:
                    unit.create_account(_draft())
                self.assertEqual(caught.exception.code, "invalid_row")

    def test_audit_insert_has_no_file_pii(self):
        unit = self.open()
        event = execution.ImportAuditEvent(
            action=execution.AUDIT_ACCOUNT_CREATED,
            actor_reference=ACTOR,
            idempotency_key=KEY,
            content_sha256=CONTENT,
            account_reference=101,
            row_number=2,
            record_count=None,
        )

        unit.append_audit_event(event)

        sql, params = self.last()
        self.assertTrue(sql.startswith("INSERT INTO import_confirmation_audit_events "))
        for column in ("cliente", "email", "whatsapp", "valor", "vencimento"):
            self.assertNotIn(column, sql)
        self.assertEqual(
            params,
            {
                "action": execution.AUDIT_ACCOUNT_CREATED,
                "actor_reference": ACTOR,
                "idempotency_key": KEY,
                "content_sha256": CONTENT,
                "account_reference": 101,
                "row_number": 2,
                "record_count": None,
            },
        )

    def test_audit_rejects_unknown_action_or_reference(self):
        unit = self.open()
        base = dict(
            action=execution.AUDIT_IMPORT_CONFIRMED,
            actor_reference=ACTOR,
            idempotency_key=KEY,
            content_sha256=CONTENT,
            account_reference=None,
            row_number=None,
            record_count=2,
        )

        for override in (
            {"action": "account.status_changed"},
            {"account_reference": "101"},
            {"record_count": True},
            {"actor_reference": ""},
        ):
            with self.subTest(override=override):
                with self.assertRaises(RepoError):
                    unit.append_audit_event(
                        execution.ImportAuditEvent(**{**base, **override})
                    )

        with self.assertRaises(RepoError):
            unit.append_audit_event(SimpleNamespace(**base))

        self.assertEqual(len(self.factory.session.statements), 1)

    def test_receipt_insert_parametrized(self):
        unit = self.open()
        receipt = execution.ImportConfirmationReceipt(
            file_kind="xlsx",
            record_count=2,
            identity_version=1,
            actor_reference=ACTOR,
            idempotency_key=KEY,
            content_sha256=CONTENT,
            total=Decimal("1234.66"),
            account_references=(101, 102),
        )

        unit.insert_receipt(receipt)

        sql, params = self.last()
        self.assertTrue(sql.startswith("INSERT INTO import_confirmation_receipts "))
        self.assertEqual(params["account_references"], [101, 102])
        self.assertEqual(params["total"], Decimal("1234.66"))
        self.assertEqual(params["actor_reference"], ACTOR)
        self.assertNotIn(ACTOR, sql)

        for override in (
            {"account_references": ("101", 102)},
            {"account_references": (101,)},
            {"total": Decimal("0.00")},
            {"identity_version": 2},
            {"file_kind": "pdf"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(RepoError):
                    unit.insert_receipt(
                        execution.ImportConfirmationReceipt(
                            **{**vars(receipt), **override}
                        )
                    )


# ---------------------------------------------------------------- integração


class ExecutionIntegrationTests(unittest.TestCase):
    def test_accounts_receipt_and_audit_in_one_transaction(self):
        factory = Factory()

        result = execution.execute_import_confirmation(
            _repo(factory), _plan(), actor_reference=ACTOR
        )

        self.assertFalse(result.replayed)
        self.assertEqual(result.receipt.account_references, (101, 102))
        self.assertEqual(len(factory.sessions), 1)
        self.assertEqual(
            _kinds(factory.session),
            [
                "begin",
                "isolation",
                "lock",
                "find_receipt",
                "content",
                "identities",
                "insert_account",
                "insert_account",
                "insert_audit",
                "insert_audit",
                "insert_receipt",
                "insert_audit",
                "commit",
                "close",
            ],
        )
        receipt_params = factory.session.statements[-2][1]
        self.assertEqual(receipt_params["account_references"], [101, 102])
        self.assertEqual(receipt_params["total"], Decimal("1234.66"))

        for sql, params in factory.session.statements:
            if "audit" in sql:
                for secret in (PII, EMAIL, PHONE):
                    self.assertNotIn(secret, repr(params))

    def test_replay_returns_stored_receipt_without_writes(self):
        factory = Factory(rows={"find_receipt": [_receipt_row()]})

        result = execution.execute_import_confirmation(
            _repo(factory), _plan(), actor_reference=ACTOR
        )

        self.assertTrue(result.replayed)
        self.assertEqual(
            _kinds(factory.session),
            ["begin", "isolation", "lock", "find_receipt", "commit", "close"],
        )

    def test_existing_identity_found_by_python_normalization(self):
        raw = ("  fulana SIGILOSA   da silva", Decimal("1234.56"), date(2026, 1, 5))
        factory = Factory(rows={"identities": [raw]})

        with self.assertRaises(execution.ImportConfirmationExecutionError) as caught:
            execution.execute_import_confirmation(
                _repo(factory), _plan(), actor_reference=ACTOR
            )

        self.assertEqual(caught.exception.code, "existing_identity_conflict")
        kinds = _kinds(factory.session)
        self.assertNotIn("insert_account", kinds)
        self.assertEqual(kinds[-2:], ["rollback", "close"])

    def test_content_already_confirmed_blocks(self):
        factory = Factory(rows={"content": [(1,)]})

        with self.assertRaises(execution.ImportConfirmationExecutionError) as caught:
            execution.execute_import_confirmation(
                _repo(factory), _plan(), actor_reference=ACTOR
            )

        self.assertEqual(caught.exception.code, "content_already_imported")
        self.assertNotIn("identities", _kinds(factory.session))

    def test_failure_at_every_statement_rolls_back_and_closes(self):
        for position in range(1, 11):
            with self.subTest(position=position):
                factory = Factory(
                    fail_execute_at=position,
                    execute_error=OperationalError("SELECT", {}, Exception(PII)),
                )

                with self.assertRaises(RepoError) as caught:
                    execution.execute_import_confirmation(
                        _repo(factory), _plan(), actor_reference=ACTOR
                    )

                self.assertEqual(caught.exception.code, "database_error")
                kinds = _kinds(factory.session)
                self.assertNotIn("commit", kinds)
                self.assertEqual(kinds[-2:], ["rollback", "close"])

    def test_unique_violation_mapped_without_values(self):
        orig = Exception(f"Key (actor_reference, idempotency_key)=({ACTOR}, {KEY})")
        orig.sqlstate = "23505"
        factory = Factory(
            fail_execute_at=9,
            execute_error=IntegrityError("INSERT", {"actor_reference": ACTOR}, orig),
        )

        with self.assertRaises(RepoError) as caught:
            execution.execute_import_confirmation(
                _repo(factory), _plan(), actor_reference=ACTOR
            )

        error = caught.exception
        self.assertEqual(error.code, "conflict")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for secret in (ACTOR, KEY, PII):
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
        self.assertEqual(_kinds(factory.session)[-2:], ["rollback", "close"])

    def test_non_database_exception_propagates_unchanged(self):
        factory = Factory(fail_execute_at=2, execute_error=LookupError())

        with self.assertRaises(LookupError):
            execution.execute_import_confirmation(
                _repo(factory), _plan(), actor_reference=ACTOR
            )

        self.assertEqual(_kinds(factory.session)[-2:], ["rollback", "close"])

    def test_commit_failure_returns_no_result(self):
        factory = Factory(fail_commit=True)

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            execution.execute_import_confirmation(
                _repo(factory), _plan(), actor_reference=ACTOR
            )

        self.assertEqual(_kinds(factory.session)[-3:], ["commit", "rollback", "close"])


# ---------------------------------------------------------------- privacidade


class PrivacyTests(unittest.TestCase):
    def test_reprs_hide_session_actor_and_key(self):
        factory = Factory()
        repo = _repo(factory)
        self.assertEqual(
            repr(repo), "PostgresImportConfirmationRepository(writer_protocol_verified=True)"
        )

        with repo.transaction() as unit:
            unit.lock_account_writers()
            unit.find_receipt(ACTOR, KEY)
            text = repr(unit)

        for secret in (ACTOR, KEY, "FakeSession"):
            self.assertNotIn(secret, text)

    def test_error_codes_fixed(self):
        self.assertEqual(repr(RepoError("conflict")), "ImportConfirmationRepositoryError(code='conflict')")

        with self.assertRaises(ValueError):
            RepoError("anything")


# ---------------------------------------------------------------- schema


class SchemaMetadataTests(unittest.TestCase):
    def test_only_contract_tables_in_independent_metadata(self):
        self.assertEqual(
            set(repository.metadata.tables),
            {"accounts", "import_confirmation_receipts", "import_confirmation_audit_events"},
        )

    def test_receipt_constraints(self):
        table = repository.receipts_table
        uniques = {
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(
            uniques,
            {("actor_reference", "idempotency_key"), ("content_sha256", "identity_version")},
        )
        self.assertEqual((table.c.total.type.precision, table.c.total.type.scale), (18, 2))
        self.assertIsInstance(table.c.account_references.type, postgresql.JSONB)

    def test_no_foreign_keys_and_no_pii_columns_in_history(self):
        for table in (repository.receipts_table, repository.audit_events_table):
            with self.subTest(table=table.name):
                self.assertEqual(table.foreign_keys, set())
                for column in ("cliente", "email", "whatsapp", "valor", "vencimento"):
                    self.assertNotIn(column, table.c)

    def test_accounts_mirror_matches_real_model_source(self):
        tree = ast.parse(ACCOUNT_MODEL_PATH.read_text(encoding="utf-8"))
        model_columns = {}

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "Column"
            ):
                type_node = node.value.args[0]
                type_name = getattr(type_node, "id", None) or type_node.func.id
                length = (
                    type_node.args[0].value
                    if isinstance(type_node, ast.Call) and type_node.args
                    else None
                )
                model_columns[node.targets[0].id] = (type_name, length)

        mirror = repository.accounts_table
        expected = set(model_columns) - {"created_at"}
        self.assertEqual(set(mirror.c.keys()), expected)

        for name in expected:
            with self.subTest(column=name):
                type_name, length = model_columns[name]
                self.assertEqual(type(mirror.c[name].type).__name__, type_name)
                if type_name == "String":
                    self.assertEqual(mirror.c[name].type.length, length)

        self.assertEqual(
            (mirror.c.valor.type.precision, mirror.c.valor.type.scale), (14, 2)
        )


# ---------------------------------------------------------------- pureza


class PurityTests(unittest.TestCase):
    source = REPOSITORY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    def test_imports_allowlist(self):
        allowed = {
            "__future__",
            "contextlib",
            "datetime",
            "decimal",
            "types",
            "typing",
            "sqlalchemy",
            "sqlalchemy.dialects.postgresql",
            "sqlalchemy.exc",
            'app.import_confirmation_schema',
            "app.core.account_writers_busy",
        }

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name, allowed)
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, allowed)

    def test_no_engine_session_schema_or_logging_names(self):
        names = {
            node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
        }

        for forbidden in (
            "create_engine",
            "sessionmaker",
            "Session",
            "connect",
            "create_all",
            "drop_all",
            "text",
            "lower",
            "upper",
            "logging",
            "logger",
            "print",
            "open",
            "eval",
            "exec",
        ):
            self.assertNotIn(forbidden, names)

        self.assertNotIn("ForeignKey", self.source)
        self.assertNotIn("ondelete", self.source)

    def test_no_module_level_mutable_store(self):
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and isinstance(
                node.value, (ast.List, ast.Set, ast.Dict)
            ):
                self.assertEqual(
                    [t.id for t in node.targets], ["REPOSITORY_ERROR_MESSAGES"]
                )

    def test_docstring_states_limits(self):
        doc = ast.get_docstring(self.tree)
        for phrase in (
            "NÃO REGISTRADO e NÃO PRONTO PARA PRODUÇÃO",
            "apenas METADATA",
            "NÃO PROVADO",
            "NÃO uma prova",
        ):
            self.assertIn(phrase, doc)

    def test_no_app_or_driver_modules_loaded(self):
        loaded = _modules_after_fresh_load(
            ("import_confirmation_execution_for_repository_test", EXECUTION_PATH),
            ("import_confirmation_repository_under_test", REPOSITORY_PATH),
        )
        self.assertIn("import_confirmation_repository_under_test", loaded)
        for name in ("app.database", "app.models", "app.main", "app.services", "psycopg", "psycopg2"):
            self.assertNotIn(name, loaded)


# ---------------------------------------------------------------- busy (2A)


def _state_error(sqlstate):
    orig = Exception(PII)
    orig.sqlstate = sqlstate
    return OperationalError("SELECT pg_advisory_xact_lock", {"private": PII}, orig)


class WritersBusyTests(unittest.TestCase):
    def _assert_sanitized(self, error, code):
        self.assertEqual(error.code, code)
        self.assertIsNone(error.__context__)
        self.assertNotIn(PII, str(error) + repr(error))

    def test_lock_timeouts_are_busy_and_roll_back_without_commit(self):
        for sqlstate in ("55P03", "57014"):
            factory = Factory(fail_execute_at=1, execute_error=_state_error(sqlstate))
            with self.assertRaises(RepoError) as caught:
                with _repo(factory).transaction() as unit:
                    unit.lock_account_writers()
            self._assert_sanitized(caught.exception, "account_writers_busy")
            self.assertEqual(_kinds(factory.session), ["begin", "isolation", "lock", "rollback", "close"])

    def test_statement_timeout_after_lock_is_busy_before_commit(self):
        factory = Factory(fail_execute_at=2, execute_error=_state_error("57014"))
        with self.assertRaises(RepoError) as caught:
            with _repo(factory).transaction() as unit:
                unit.lock_account_writers()
                unit.find_receipt(ACTOR, KEY)
        self._assert_sanitized(caught.exception, "account_writers_busy")
        self.assertNotIn("commit", _kinds(factory.session))
        self.assertEqual(_kinds(factory.session)[-2:], ["rollback", "close"])

    def test_other_sqlstates_remain_database_error_or_conflict(self):
        for sqlstate, code in (("40P01", "database_error"), (None, "database_error"),
                               ("23505", "conflict")):
            factory = Factory(fail_execute_at=1, execute_error=_state_error(sqlstate))
            with self.assertRaises(RepoError) as caught:
                with _repo(factory).transaction() as unit:
                    unit.lock_account_writers()
            self._assert_sanitized(caught.exception, code)

    def test_commit_timeouts_are_never_busy(self):
        for sqlstate in ("55P03", "57014"):
            session = FakeSession()
            def fail_commit(session=session, sqlstate=sqlstate):
                session.log.append(("commit",))
                raise _state_error(sqlstate)
            session.commit = fail_commit
            with self.assertRaises(RepoError) as caught:
                with _repo(lambda: session).transaction() as unit:
                    unit.lock_account_writers()
            self._assert_sanitized(caught.exception, "database_error")
            self.assertEqual(_kinds(session)[-3:], ["commit", "rollback", "close"])

    def test_unsupported_isolation_is_unchanged_and_never_locks(self):
        factory = Factory(isolation="REPEATABLE READ")
        with self.assertRaises(RepoError) as caught:
            with _repo(factory).transaction():
                pass
        self.assertEqual(caught.exception.code, "unsupported_isolation")
        self.assertNotIn("lock", _kinds(factory.session))


if __name__ == "__main__":
    unittest.main()
