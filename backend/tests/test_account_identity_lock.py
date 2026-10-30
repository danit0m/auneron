"""
Testes offline do protocolo comum de escritores de identidade de contas
(MVP-FIN-IMPORT-01 / TASK-024).

Standalone com unittest: sessões SQLAlchemy falsas, sem conftest, banco ou
conexão. NÃO prova a exclusão num PostgreSQL real; só fixa chave, SQL,
dialeto e a ordem lock-antes-de-mutação nos três caminhos.

    cd backend
    python -B tests/test_account_identity_lock.py -v
"""

import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, OperationalError, ResourceClosedError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import import_confirmation_schema as schema  # noqa: E402
from app.core import account_identity_lock as lock  # noqa: E402
from app.core import account_writers_busy as busy  # noqa: E402


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BACKEND_DIR / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


execution = _load("lock_test_execution", "app/services/import_confirmation_execution.py")
repository = _load("lock_test_repository", "app/services/import_confirmation_repository.py")


class FakeSession:
    def __init__(self, dialect):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.calls, self.statements = [], []

    def get_bind(self):
        return self.bind

    def connection(self):
        return SimpleNamespace(get_isolation_level=lambda: 'READ COMMITTED')

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(returns_rows=True, all=lambda: [(None,)])

    def __getattr__(self, name):  # begin/commit/flush/... seriam registrados
        return lambda *a, **k: self.calls.append(name)


def _compiled(statement):
    compiled = statement.compile(dialect=postgresql.psycopg2.dialect())
    return str(compiled), compiled.params


def _first_line(function, predicate):
    lines = [n.lineno for n in ast.walk(function) if isinstance(n, ast.Call) and predicate(n)]
    return min(lines) if lines else None


class AccountIdentityLockTests(unittest.TestCase):
    def test_postgres_takes_xact_lock_without_extra_transaction_control(self):
        session = FakeSession("postgresql")
        self.assertIs(lock.lock_account_identity(session), True)
        self.assertEqual(len(session.statements), 1)
        sql, params = _compiled(session.statements[0])
        self.assertTrue(sql.startswith("SELECT pg_advisory_xact_lock(%(lock_key)s)"))
        self.assertEqual(params, {"lock_key": schema.ACCOUNT_IDENTITY_LOCK_KEY})
        self.assertEqual(session.calls, [])

    def test_postgres_rejects_stale_snapshot_isolation_before_lock(self):
        session = FakeSession('postgresql')
        session.connection = lambda: SimpleNamespace(get_isolation_level=lambda: 'REPEATABLE READ')
        with self.assertRaises(RuntimeError):
            lock.lock_account_identity(session)
        self.assertEqual(session.statements, [])

    def test_identity_check_uses_casefold_and_excludes_same_account(self):
        session = FakeSession('postgresql')
        names = ['  STRASSE   GMBH  ']
        def execute(statement):
            session.statements.append(statement)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: names))
        session.execute = execute
        model = SimpleNamespace(**dict(schema.accounts_table.c.items()))
        from decimal import Decimal
        from datetime import date
        self.assertTrue(lock.account_identity_exists(
            session, model, 'Straße GMBH', Decimal('1.00'), date(2026, 1, 1), exclude_id=7,
        ))
        sql, params = _compiled(session.statements[0])
        self.assertIn('accounts.id !=', sql)
        self.assertNotIn('lower(', sql.lower())
        self.assertIn(7, params.values())
        names[:] = ['Outro cliente']
        self.assertFalse(lock.account_identity_exists(
            session, model, 'Straße GMBH', Decimal('1.00'), date(2026, 1, 1),
        ))

    def test_duplicate_check_is_present_before_manual_mutations(self):
        source = (BACKEND_DIR / 'app/api/routes/accounts.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ('create_account', 'update_account'):
                locked = _first_line(node, lambda c: getattr(c.func, 'id', None) == 'lock_account_identity')
                checked = _first_line(node, lambda c: getattr(c.func, 'id', None) == 'account_identity_exists')
                committed = _first_line(node, lambda c: isinstance(c.func, ast.Attribute) and c.func.attr == 'commit')
                self.assertLess(locked, checked)
                self.assertLess(checked, committed)

    def test_sqlite_legacy_is_untouched(self):
        session = FakeSession("sqlite")
        self.assertIs(lock.lock_account_identity(session), False)
        self.assertEqual((session.statements, session.calls), ([], []))

    def test_same_key_and_sql_as_confirmation_repository(self):
        self.assertIs(repository.IMPORT_CONFIRMATION_LOCK_KEY, schema.ACCOUNT_IDENTITY_LOCK_KEY)
        session = FakeSession("postgresql")
        repository._UnitOfWork(session, execution, schema.accounts_table).lock_account_writers()
        self.assertEqual(_compiled(session.statements[0]), _compiled(lock.account_identity_lock_statement()))

    def test_key_value_lives_only_in_pure_schema(self):
        literal = schema.ACCOUNT_IDENTITY_LOCK_KEY
        for relative in ("app/core/account_identity_lock.py", "app/services/import_confirmation_repository.py"):
            tree = ast.parse((BACKEND_DIR / relative).read_text(encoding="utf-8"))
            constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
            self.assertNotIn(literal, constants, relative)

    def test_helper_imports_only_sqlalchemy_and_schema(self):
        tree = ast.parse((BACKEND_DIR / "app/core/account_identity_lock.py").read_text(encoding="utf-8"))
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(modules, {"__future__", "typing", "sqlalchemy", "sqlalchemy.exc",
                                   "app.core.account_writers_busy", "app.import_confirmation_schema"})

    def test_busy_helper_is_pure(self):
        tree = ast.parse((BACKEND_DIR / "app/core/account_writers_busy.py").read_text(encoding="utf-8"))
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(modules, {"__future__", "typing"})

    def test_lock_precedes_db_access_in_the_three_writer_paths(self):
        targets = {
            "app/api/routes/accounts.py": ("create_account", "update_account", "delete_account"),
            "app/services/import_service.py": ("import_clients",),
        }
        for relative, names in targets.items():
            tree = ast.parse((BACKEND_DIR / relative).read_text(encoding="utf-8"))
            functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
            for name in names:
                function = functions[name]
                locked = _first_line(function, lambda c: getattr(c.func, "id", None) == "lock_account_identity")
                db_use = _first_line(function, lambda c: isinstance(c.func, ast.Attribute)
                                     and getattr(c.func.value, "id", None) == "db")
                self.assertIsNotNone(locked, name)
                self.assertLess(locked, db_use, name)


PII = "Fulana Sigilosa da Silva"


class _Orig(Exception):
    pass


def _db_error(sqlstate=None, pgcode=None, cls=OperationalError):
    orig = _Orig(PII)
    if sqlstate is not None:
        orig.sqlstate = sqlstate
    if pgcode is not None:
        orig.pgcode = pgcode
    return cls("SELECT pg_advisory_xact_lock(%(lock_key)s)", {"lock_key": PII}, orig)


class _FailingLockSession(FakeSession):
    def __init__(self, error):
        super().__init__("postgresql")
        self.error = error

    def execute(self, statement):
        self.statements.append(statement)
        raise self.error


class WritersBusyLockTests(unittest.TestCase):
    def test_lock_timeout_and_statement_timeout_become_sanitized_busy(self):
        for error in (_db_error("55P03"), _db_error("57014"), _db_error(pgcode="55P03"),
                      _db_error("57014", cls=DBAPIError)):
            session = _FailingLockSession(error)
            with self.assertRaises(busy.AccountWritersBusy) as caught:
                lock.lock_account_identity(session)
            exc = caught.exception
            self.assertEqual(exc.code, "account_writers_busy")
            self.assertIsNone(exc.__context__)
            self.assertIsNone(exc.__cause__)
            for text in (str(exc), repr(exc)):
                self.assertNotIn(PII, text)
                self.assertNotIn("pg_advisory", text)
            self.assertEqual(len(session.statements), 1)
            self.assertEqual(session.calls, [])  # sem commit/rollback/retry no helper

    def test_other_failures_propagate_unchanged(self):
        others = (_db_error("40P01"), _db_error("23505"), _db_error(), _db_error("55P03 "))
        for error in others:
            with self.assertRaises(OperationalError) as caught:
                lock.lock_account_identity(_FailingLockSession(error))
            self.assertIs(caught.exception, error)
        not_dbapi = RuntimeError("x")
        not_dbapi.orig = SimpleNamespace(sqlstate="55P03")
        with self.assertRaises(RuntimeError) as caught:
            lock.lock_account_identity(_FailingLockSession(not_dbapi))
        self.assertIs(caught.exception, not_dbapi)
        with self.assertRaises(ResourceClosedError):
            lock.lock_account_identity(_FailingLockSession(ResourceClosedError()))

    def test_sqlite_and_isolation_guard_are_not_classified(self):
        session = _FailingLockSession(_db_error("55P03"))
        session.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        self.assertIs(lock.lock_account_identity(session), False)
        self.assertEqual(session.statements, [])
        session = _FailingLockSession(_db_error("55P03"))
        session.connection = lambda: SimpleNamespace(get_isolation_level=lambda: "REPEATABLE READ")
        with self.assertRaises(RuntimeError) as caught:
            lock.lock_account_identity(session)
        self.assertNotIsInstance(caught.exception, busy.AccountWritersBusy)
        self.assertEqual(session.statements, [])

    def test_busy_contract_values(self):
        self.assertEqual(busy.RETRYABLE_SQLSTATES, frozenset({"55P03", "57014"}))
        self.assertEqual(busy.busy_headers(), {"Retry-After": "5"})
        self.assertEqual(busy.busy_detail()["code"], "account_writers_busy")
        self.assertIsNot(busy.busy_detail(), busy.busy_detail())
        self.assertFalse(busy.is_retryable_sqlstate(SimpleNamespace(orig=SimpleNamespace(sqlstate=55))))


def _functions(relative):
    tree = ast.parse((BACKEND_DIR / relative).read_text(encoding="utf-8"))
    return tree, {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _catches_busy(handler):
    return isinstance(handler.type, ast.Name) and handler.type.id == "AccountWritersBusy"


class AccountsRouteBusyMappingTests(unittest.TestCase):
    def test_busy_is_caught_only_around_the_lock_call(self):
        _, functions = _functions("app/api/routes/accounts.py")
        for name in ("create_account", "update_account", "delete_account"):
            function = functions[name]
            tries = [n for n in ast.walk(function) if isinstance(n, ast.Try)
                     and any(_catches_busy(h) for h in n.handlers)]
            self.assertEqual(len(tries), 1, name)
            node = tries[0]
            self.assertEqual(len(node.body), 1, name)
            call = node.body[0].value
            self.assertEqual(getattr(call.func, "id", None), "lock_account_identity", name)
            self.assertEqual([a.id for a in call.args], ["db"], name)
            self.assertEqual(len(node.handlers), 1, name)
            self.assertEqual((node.orelse, node.finalbody), ([], []), name)
            handler_body = node.handlers[0].body
            self.assertEqual(len(handler_body), 1, name)
            raised = handler_body[0]
            self.assertIsInstance(raised, ast.Raise)
            self.assertEqual(raised.exc.func.id, "_writers_busy")
            self.assertIsInstance(raised.cause, ast.Constant)
            self.assertIsNone(raised.cause.value)
            commits = [n.lineno for n in ast.walk(function) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == "commit"]
            self.assertTrue(all(line > node.end_lineno for line in commits), name)

    def test_writers_busy_is_503_with_retry_after_and_no_sql(self):
        from fastapi import HTTPException, status
        tree, functions = _functions("app/api/routes/accounts.py")
        source = (BACKEND_DIR / "app/api/routes/accounts.py").read_text(encoding="utf-8")
        namespace = {"HTTPException": HTTPException, "status": status,
                     "busy_detail": busy.busy_detail, "busy_headers": busy.busy_headers}
        exec(compile(ast.get_source_segment(source, functions["_writers_busy"]), "accounts_busy", "exec"),
             namespace)
        exc = namespace["_writers_busy"]()
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.headers, {"Retry-After": "5"})
        self.assertEqual(exc.detail, busy.busy_detail())

    def test_legacy_import_rolls_back_before_busy_propagates(self):
        _, functions = _functions("app/services/import_service.py")
        outer = next(n for n in functions["import_clients"].body if isinstance(n, ast.Try))
        first = outer.body[0].value
        self.assertEqual(first.func.id, "lock_account_identity")
        handler = outer.handlers[0]
        self.assertEqual(handler.type.id, "Exception")
        self.assertEqual(handler.body[0].value.func.attr, "rollback")
        self.assertIsInstance(handler.body[-1], ast.Raise)
        self.assertIsNone(handler.body[-1].exc)


if __name__ == "__main__":
    unittest.main()
