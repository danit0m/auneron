"""Offline verification of the prepared migration; never loads Alembic env or a DB."""
import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateColumn

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from app import import_confirmation_schema as schema

VERSION = BACKEND / 'migrations/versions/b428d1a6c903_add_import_confirmation_receipts.py'
MODEL = BACKEND / 'app/models/import_confirmation.py'

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def ddl(table):
    dialect = postgresql.dialect()
    compiler = dialect.ddl_compiler(dialect, CreateTable(table))
    columns = tuple(' '.join(str(CreateColumn(c).compile(dialect=dialect)).split()) for c in table.columns)
    constraints = tuple(sorted(' '.join(compiler.process(c).split()) for c in table.constraints))
    return columns, constraints

class Recorder:
    def __init__(self):
        self.metadata = MetaData()
        self.dropped = []
    def create_table(self, name, *columns, **options):
        return Table(name, self.metadata, *columns, **options)
    def drop_table(self, name):
        self.dropped.append(name)

class PreparedSchemaTests(unittest.TestCase):
    def test_upgrade_matches_adapter_metadata_exactly(self):
        migration = load('candidate_import_migration', VERSION)
        recorder = Recorder()
        migration.op = recorder
        migration.upgrade()
        self.assertEqual(set(recorder.metadata.tables), {
            'import_confirmation_receipts', 'import_confirmation_audit_events',
        })
        for name, actual in recorder.metadata.tables.items():
            self.assertEqual(ddl(actual), ddl(schema.metadata.tables[name]))

    def test_revision_extends_current_parent(self):
        migration = load('candidate_revision', VERSION)
        self.assertEqual(migration.revision, 'b428d1a6c903')
        self.assertEqual(migration.down_revision, 'a7d3e91c4b26')

    def test_downgrade_only_drops_new_tables_in_reverse_order(self):
        migration = load('candidate_downgrade', VERSION)
        recorder = Recorder()
        migration.op = recorder
        migration.downgrade()
        self.assertEqual(recorder.dropped, [
            'import_confirmation_audit_events', 'import_confirmation_receipts',
        ])

    def test_model_registers_only_import_tables_without_database_import(self):
        metadata = MetaData()
        fake_database = types.ModuleType('app.database.database')
        fake_database.Base = types.SimpleNamespace(metadata=metadata)
        with mock.patch.dict(sys.modules, {'app.database.database': fake_database}):
            load('candidate_import_model', MODEL)
        self.assertEqual(set(metadata.tables), {
            'import_confirmation_receipts', 'import_confirmation_audit_events',
        })
        for name, actual in metadata.tables.items():
            self.assertEqual(ddl(actual), ddl(schema.metadata.tables[name]))

    def test_historical_receipts_and_audit_have_no_cascading_foreign_keys(self):
        for table in (schema.receipts_table, schema.audit_events_table):
            self.assertFalse(table.foreign_keys)
        self.assertEqual(schema.receipts_table.c.total.type.precision, 18)
        self.assertEqual(schema.receipts_table.c.total.type.scale, 2)

    def test_schema_has_no_connection_or_ddl_execution(self):
        source = (BACKEND / 'app/import_confirmation_schema.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(names & {'create_engine', 'Session', 'connect', 'create_all', 'drop_all', 'execute'})

    def test_models_registry_import_is_present(self):
        source = (BACKEND / 'app/models/__init__.py').read_text(encoding='utf-8-sig')
        self.assertIn('from app.models import import_confirmation', source)

if __name__ == '__main__':
    unittest.main()
