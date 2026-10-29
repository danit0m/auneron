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


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import import_confirmation_schema as schema  # noqa: E402
from app.core import account_identity_lock as lock  # noqa: E402


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
        self.assertEqual(modules, {"__future__", "typing", "sqlalchemy", "app.import_confirmation_schema"})

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


if __name__ == "__main__":
    unittest.main()
