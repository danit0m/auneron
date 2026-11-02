"""Offline tests for audit_recovery_delta.py: stdlib + fakes only; no child process, DB, Docker or network."""

import ast
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_recovery_delta as rd  # noqa: E402

CID = "c" * 64
PASSWORD = "ci-secret-password-xyz"
DSN = f"postgresql+psycopg://auneron:{PASSWORD}@localhost:5432/auneron_test"
GOOD_ENV = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": rd.REPOSITORY, "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main", "APP_ENV": "test", "DATABASE_URL": DSN,
            "CI_POSTGRES_CONTAINER_ID": CID, "GITHUB_RUN_ID": "41203778900", "GITHUB_RUN_ATTEMPT": "1"}
TARGET = "auneron_audit_recovery_41203778900_1"
FUNCTION_SRC = " BEGIN RAISE EXCEPTION 'account_data_entry_audit_events is append-only'; END "
REF_FUNCTION_SRC = " BEGIN RAISE EXCEPTION 'account_title_references is append-only'; END "
REF_KEY = ("account_title_references", "account_id", 1)


# Verbatim from the CI run 41257863930 report (area2-audit-recovery.json, schema_comparison.details.checks);
# embedded so the tests never read an external artifact.
_CK = "ck_account_data_entry_audit_"
_HEX = "CHECK ((({0} IS NULL) OR (({0})::text ~ '^[0-9a-f]{{64}}$'::text)))"
ARTIFACT_SOURCE_CHECKS = [
    (_CK + "account_reference", "CHECK ((account_reference > 0))"),
    (_CK + "actor_reference", "CHECK (((actor_reference)::text = ('user:'::text || (actor_user_id)::text)))"),
    (_CK + "actor_type", "CHECK (((actor_type)::text = 'user'::text))"),
    (_CK + "actor_user_id", "CHECK ((actor_user_id > 0))"),
    (_CK + "after_cliente_sha256_hex", _HEX.format("after_cliente_sha256")),
    (_CK + "after_email_sha256_hex", _HEX.format("after_email_sha256")),
    (_CK + "after_whatsapp_sha256_hex", _HEX.format("after_whatsapp_sha256")),
    (_CK + "auth_session_id", "CHECK (((auth_session_id IS NULL) OR (auth_session_id > 0)))"),
    (_CK + "before_cliente_sha256_hex", _HEX.format("before_cliente_sha256")),
    (_CK + "before_email_sha256_hex", _HEX.format("before_email_sha256")),
    (_CK + "before_whatsapp_sha256_hex", _HEX.format("before_whatsapp_sha256")),
    (_CK + "changed_fields_array", "CHECK ((jsonb_typeof(changed_fields) = 'array'::text))"),
    (_CK + "channel", "CHECK (((channel)::text = ANY ((ARRAY['manual_api'::character varying, "
                      "'legacy_csv_upload'::character varying])::text[])))"),
    (_CK + "create_shape", "CHECK ((((operation)::text <> 'create'::text) OR ((before_valor IS NULL) AND "
                           "(before_vencimento IS NULL) AND (before_cliente_sha256 IS NULL) AND (before_email_sha256 "
                           "IS NULL) AND (before_whatsapp_sha256 IS NULL) AND (after_valor IS NOT NULL) AND "
                           "(after_vencimento IS NOT NULL) AND (after_cliente_sha256 IS NOT NULL))))"),
    (_CK + "delete_shape", "CHECK ((((operation)::text <> 'delete'::text) OR ((after_valor IS NULL) AND "
                           "(after_vencimento IS NULL) AND (after_cliente_sha256 IS NULL) AND (after_email_sha256 IS "
                           "NULL) AND (after_whatsapp_sha256 IS NULL) AND (before_valor IS NOT NULL) AND "
                           "(before_vencimento IS NOT NULL) AND (before_cliente_sha256 IS NOT NULL) AND "
                           "(changed_fields = '[]'::jsonb))))"),
    (_CK + "legacy_create_only", "CHECK ((((channel)::text = 'manual_api'::text) OR ((operation)::text = "
                                 "'create'::text)))"),
    (_CK + "operation", "CHECK (((operation)::text = ANY ((ARRAY['create'::character varying, 'update'::character "
                        "varying, 'delete'::character varying])::text[])))"),
    (_CK + "source_by_channel", "CHECK ((((channel)::text = 'legacy_csv_upload'::text) = ((source_sha256 IS NOT "
                                "NULL) AND (source_row IS NOT NULL))))"),
    (_CK + "source_row", "CHECK (((source_row IS NULL) OR (source_row >= 2)))"),
    (_CK + "source_sha256_hex", _HEX.format("source_sha256")),
    (_CK + "status", "CHECK (((status_at_operation)::text = ANY ((ARRAY['aberto'::character varying, "
                     "'atrasado'::character varying, 'pago'::character varying])::text[])))"),
    (_CK + "update_shape", "CHECK ((((operation)::text <> 'update'::text) OR ((before_valor IS NOT NULL) AND "
                           "(after_valor IS NOT NULL) AND (before_vencimento IS NOT NULL) AND (after_vencimento IS "
                           "NOT NULL) AND (before_cliente_sha256 IS NOT NULL) AND (after_cliente_sha256 IS NOT "
                           "NULL) AND (jsonb_array_length(changed_fields) > 0))))"),
]
ARTIFACT_TARGET_ONLY = {
    _CK + "channel": "CHECK (((channel)::text = ANY (ARRAY[('manual_api'::character varying)::text, "
                     "('legacy_csv_upload'::character varying)::text])))",
    _CK + "operation": "CHECK (((operation)::text = ANY (ARRAY[('create'::character varying)::text, "
                       "('update'::character varying)::text, ('delete'::character varying)::text])))",
    _CK + "status": "CHECK (((status_at_operation)::text = ANY (ARRAY[('aberto'::character varying)::text, "
                    "('atrasado'::character varying)::text, ('pago'::character varying)::text])))",
}
ARTIFACT_LITERALS = {_CK + "channel": ("manual_api", "legacy_csv_upload"),
                     _CK + "operation": ("create", "update", "delete"),
                     _CK + "status": ("aberto", "atrasado", "pago")}
KNOWN = sorted(ARTIFACT_TARGET_ONLY)


def artifact_target_checks(names=KNOWN):
    return [(n, ARTIFACT_TARGET_ONLY[n] if n in names else d) for n, d in ARTIFACT_SOURCE_CHECKS]


def artifact_facts(checks=None):
    facts = good_facts()
    facts["checks"] = list(ARTIFACT_SOURCE_CHECKS if checks is None else checks)
    return facts


def ref_index_rows():
    return [(name, f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON public.{rd.REF_TABLE} {suffix}")
            for name, (unique, suffix) in sorted(rd.REF_INDEXES.items())]


def catalog(checks=None):
    """Facts in the exact shape schema_facts() returns (lists of lists), for facts_match unit tests."""
    return {"columns": list(rd.COLUMNS),
            "checks": [list(c) for c in (ARTIFACT_SOURCE_CHECKS if checks is None else checks)],
            "index": [f"CREATE INDEX {rd.INDEX} ON public.{rd.TABLE} {rd.INDEX_SUFFIX}"],
            "function": [["plpgsql", "trigger", rd.sha256_text(FUNCTION_SRC), True]],
            "trigger": [["O", rd.TRIGGER_TYPE, rd.FUNCTION]],
            "ref_columns": list(rd.REF_COLUMNS),
            "ref_checks": [[name, f"CHECK ({name})"] for name in sorted(rd.REF_CHECKS)],
            "ref_keys": [[n, t, d] for n, (t, d) in sorted(rd.REF_KEYS.items())],
            "ref_indexes": [list(r) for r in ref_index_rows()],
            "ref_function": [["plpgsql", "trigger", rd.sha256_text(REF_FUNCTION_SRC), True]],
            "ref_trigger": [["O", rd.TRIGGER_TYPE, rd.REF_FUNCTION]]}


def boom(*args, **kwargs):
    raise AssertionError("no child process or connection may be started")


def good_facts():
    return {"columns": [(c,) for c in rd.COLUMNS],
            "checks": [(name, f"CHECK ({name})") for name in sorted(rd.CHECKS)],
            "index": [(f"CREATE INDEX {rd.INDEX} ON public.{rd.TABLE} {rd.INDEX_SUFFIX}",)],
            "function": [("plpgsql", "trigger", FUNCTION_SRC)],
            "trigger": [("O", rd.TRIGGER_TYPE, rd.FUNCTION)],
            "ref_columns": [(c,) for c in rd.REF_COLUMNS],
            "ref_checks": [(name, f"CHECK ({name})") for name in sorted(rd.REF_CHECKS)],
            "ref_keys": [(n, t, d) for n, (t, d) in sorted(rd.REF_KEYS.items())],
            "ref_indexes": ref_index_rows(),
            "ref_function": [("plpgsql", "trigger", REF_FUNCTION_SRC)],
            "ref_trigger": [("O", rd.TRIGGER_TYPE, rd.REF_FUNCTION)]}


class FakePgError(Exception):
    def __init__(self, sqlstate, message):
        super().__init__(message)
        self.sqlstate = sqlstate


class Cursor:
    def __init__(self, result):
        self._result = result

    def fetchall(self):
        return list(self._result)


TRANSACTIONAL = ("rows", "accounts", "refs")


class FakeServer:
    """In-memory stand-in for the service PostgreSQL. Writes apply immediately (no MVCC); a non-autocommit
    connection that wrote and then rolls back or closes without commit restores rows/accounts/refs."""

    def __init__(self):
        self.system_id, self.docker_system_id = "7400000000000000001", "7400000000000000001"
        self.dbs = {rd.SOURCE_DB: self.new_db(16384)}
        self.counts = {"users": 0, "import_confirmation_receipts": 0}
        self.calls, self.dropped, self.truncated, self.next_oid, self.next_id = [], [], 0, 30000, 1
        self.ref_truncated, self.deleted_accounts, self.commits = 0, [], []
        self.child_keys = [REF_KEY, ("account_events", "account_id", 1), ("knowledge", "account_id", 1)]
        self.child_rows = {}  # (table, column) -> set of referenced account ids
        self.hooks, self.fail = {}, set()
        self.timeouts, self.connections = [], 0  # (connection no., db, sql, statement_timeout ms in effect)
        self.probe = lambda name, value, truth: (truth, truth)  # PostgreSQL agreeing with plain membership

    def new_db(self, oid, comment=None, facts=None):
        return {"oid": oid, "comment": comment, "rows": [], "accounts": [], "refs": [],
                "facts": facts or good_facts(), "revision": rd.HEAD_REVISION, "tables": rd.TABLE_COUNT,
                "update_rejected": True, "delete_rejected": True, "ref_update_rejected": True,
                "ref_delete_rejected": True, "fk_restricts": True}

    def connect(self, **kw):
        assert kw["hostaddr"] == "127.0.0.1" and kw["port"] == 5432 and kw["password"] == PASSWORD
        assert kw["options"] == rd.CONNECT_OPTIONS and kw["dbname"] in self.dbs
        self.connections += 1
        return FakeConn(self, kw["dbname"], self.connections, kw["autocommit"])

    def sql(self, kind):
        return [sql for _, sql in self.calls if sql.startswith(kind) or sql == kind]


class FakeConn:
    def __init__(self, server, db, number=0, autocommit=False):
        self.server, self.db, self.number, self.autocommit = server, db, number, autocommit
        self.statement_timeout = 30000  # what CONNECT_OPTIONS sets for every session
        self.snapshot = None

    def _write(self):
        if not self.autocommit and self.snapshot is None:
            d = self.server.dbs[self.db]
            self.snapshot = copy.deepcopy({k: d[k] for k in TRANSACTIONAL})

    def execute(self, sql, params=None):
        s, d = self.server, self.server.dbs[self.db]
        s.calls.append((self.db, sql))
        s.timeouts.append((self.number, self.db, sql, self.statement_timeout))
        result = self._reference_sql(s, d, sql, params)
        if result is not None:
            return result
        if sql == rd.SQL_DROP_BUDGET:
            if "set_budget" in s.fail:
                raise FakePgError("42501", "permission denied to set parameter \"statement_timeout\"")
            if "budget_ignored" not in s.fail:
                self.statement_timeout = rd.CLEANUP_DROP_STATEMENT_TIMEOUT_MS
            return Cursor([])
        if sql == rd.SQL_STATEMENT_TIMEOUT:
            return Cursor([(str(self.statement_timeout),)])
        facts = d["facts"]
        name = sql.split('"')[1] if '"' in sql else None
        if sql == rd.SQL_IDENTITY:
            return Cursor([(self.db, d["revision"], d["tables"])])
        if sql == rd.SQL_SYSTEM_ID:
            return Cursor([(s.system_id,)])
        if sql == rd.SQL_COUNTS:
            return Cursor([(len(d["rows"]), s.counts["users"], len(d["accounts"]),
                            s.counts["import_confirmation_receipts"], len(d["refs"]))])
        if sql == rd.SQL_DB_BY_NAME:
            found = s.dbs.get(params[0])
            return Cursor([(found["oid"], found["comment"])] if found else [])
        if sql == rd.SQL_DB_BY_OID:
            return Cursor([(sum(1 for x in s.dbs.values() if x["oid"] == params[0]),)])
        if sql == rd.SQL_COLUMNS:
            return Cursor(facts["columns" if params[0] == rd.TABLE else "ref_columns"])
        if sql == rd.SQL_FUNCTION:
            return Cursor(facts["function" if params[0] == rd.FUNCTION else "ref_function"])
        for key, query in (("checks", rd.SQL_CHECKS), ("index", rd.SQL_INDEX), ("trigger", rd.SQL_TRIGGER),
                           ("ref_checks", rd.SQL_REF_CHECKS), ("ref_keys", rd.SQL_REF_KEYS),
                           ("ref_indexes", rd.SQL_INDEXES), ("ref_trigger", rd.SQL_REF_TRIGGER)):
            if sql == query:
                return Cursor(facts[key])
        if sql == rd.SQL_ROWS:
            return Cursor([(r["id"], r["request_id"], r["json"]) for r in d["rows"]])
        if sql == rd.SQL_SEED:
            assert self.db == rd.SOURCE_DB
            self._write()
            row = {"id": s.next_id, "request_id": params[3],
                   "json": json.dumps({"id": s.next_id, "request_id": params[3], "valor": str(params[4])})}
            s.next_id += 1
            d["rows"].append(row)
            s.hooks.get("after_seed", lambda: None)()
            return Cursor([(row["id"],)])
        if sql in (rd.SQL_UPDATE_PROBE, rd.SQL_DELETE_PROBE):
            if d["update_rejected" if sql == rd.SQL_UPDATE_PROBE else "delete_rejected"]:
                raise FakePgError("P0001", rd.APPEND_ONLY_MESSAGE)
            return Cursor([])
        if sql == rd.SQL_LOCK or sql == rd.SQL_READ_ONLY:
            return Cursor([])
        for check, query in rd.CHECK_PROBE_SQL.items():
            if sql == query:
                assert params[0] == params[1] and (params[0] is None or isinstance(params[0], str))
                truth = None if params[0] is None else params[0] in ARTIFACT_LITERALS[check]
                return Cursor([s.probe(check, params[0], truth)])
        if sql == rd.SQL_TRUNCATE:
            assert self.db == rd.SOURCE_DB
            self._write()
            s.truncated += 1
            d["rows"].clear()
            return Cursor([])
        if sql.startswith("CREATE DATABASE "):
            assert name not in s.dbs and sql.endswith("TEMPLATE template0")
            s.next_oid += 1
            s.dbs[name] = s.new_db(s.next_oid, facts={k: [] for k in good_facts()})
            return Cursor([])
        if sql.startswith("COMMENT ON DATABASE "):
            s.dbs[name]["comment"] = sql.split("'")[1]
            return Cursor([])
        if sql.startswith("DROP DATABASE "):
            assert name != rd.SOURCE_DB
            assert self.autocommit  # DROP DATABASE cannot run inside a transaction block
            if "drop" in s.fail:
                raise FakePgError("55006", "database is being accessed by other users")
            if "drop_timeout" in s.fail or ("slow_checkpoint" in s.fail and self.statement_timeout < 180000):
                raise FakePgError("57014", "canceling statement due to statement timeout")
            if "drop_noop" in s.fail:  # reports success but the database is still there
                return Cursor([])
            s.dropped.append(name)
            del s.dbs[name]
            return Cursor([])
        if sql == rd.SQL_FINAL_COUNTS:
            return Cursor([(len(d["rows"]), len(d["accounts"]), len(d["refs"]))])
        raise AssertionError(f"unexpected SQL: {sql}")

    def _reference_sql(self, s, d, sql, params):
        """TASK054 statements: owned account/reference seed, rows, probes and the guarded cleanup."""
        if sql == rd.SQL_SEED_ACCOUNT:
            assert self.db == rd.SOURCE_DB
            self._write()
            row = {"id": s.next_id, "cliente": params[0],
                   "json": json.dumps({"id": s.next_id, "cliente": params[0], "valor": str(params[1])})}
            s.next_id += 1
            d["accounts"].append(row)
            return Cursor([(row["id"],)])
        if sql == rd.SQL_SEED_REFERENCE:
            assert self.db == rd.SOURCE_DB
            self._write()
            if "ref_seed_fk" in s.fail or not any(a["id"] == params[0] for a in d["accounts"]):
                raise FakePgError("23503", "insert violates foreign key constraint")
            row = {"id": s.next_id, "account_id": params[0], "request_id": params[4],
                   "json": json.dumps({"id": s.next_id, "account_id": params[0], "title_reference": params[2]})}
            s.next_id += 1
            d["refs"].append(row)
            return Cursor([(row["id"],)])
        if sql == rd.SQL_ACCOUNT_ROWS:
            return Cursor([(a["id"], a["cliente"], a["json"]) for a in d["accounts"]])
        if sql == rd.SQL_REF_ROWS:
            return Cursor([(r["id"], r["request_id"], r["json"]) for r in d["refs"]])
        if sql in (rd.SQL_REF_UPDATE_PROBE, rd.SQL_REF_DELETE_PROBE):
            if d["ref_update_rejected" if sql == rd.SQL_REF_UPDATE_PROBE else "ref_delete_rejected"]:
                raise FakePgError("P0001", rd.REF_APPEND_ONLY_MESSAGE)
            return Cursor([])
        if sql == rd.SQL_ACCOUNT_FK_PROBE:
            assert self.db != rd.SOURCE_DB  # the FK probe never runs on the source
            if d["fk_restricts"] and any(r["account_id"] == params[0] for r in d["refs"]):
                raise FakePgError("23503", "update or delete violates foreign key constraint")
            return Cursor([])  # would delete; the caller always rolls back
        if sql in (rd.SQL_LOCK_ACCOUNTS, rd.SQL_LOCK_REFERENCES):
            return Cursor([])
        if sql == rd.SQL_LOCK_OWN_ACCOUNT:
            return Cursor([(a["id"],) for a in d["accounts"] if a["id"] == params[0]])
        if sql == rd.SQL_ACCOUNT_CHILD_KEYS:
            return Cursor(list(s.child_keys))
        children = {f"SELECT count(*) FROM {t} WHERE {c} = %s": (t, c) for t, c, _ in s.child_keys}
        if sql in children:
            assert self.db == rd.SOURCE_DB
            return Cursor([(len({params[0]} & s.child_rows.get(children[sql], set())),)])
        if sql == rd.SQL_TRUNCATE_REFERENCES:
            assert self.db == rd.SOURCE_DB
            self._write()
            s.ref_truncated += 1
            d["refs"].clear()
            return Cursor([])
        if sql == rd.SQL_DELETE_OWN_ACCOUNT:
            assert self.db == rd.SOURCE_DB
            self._write()
            if any(r["account_id"] == params[0] for r in d["refs"]):
                raise FakePgError("23503", "update or delete violates foreign key constraint")
            if "delete_noop" in s.fail:
                return Cursor([])
            gone = [a for a in d["accounts"] if a["id"] == params[0] and a["cliente"] == params[1]]
            d["accounts"][:] = [a for a in d["accounts"] if a not in gone]
            s.deleted_accounts.extend(a["id"] for a in gone)
            return Cursor([(a["id"],) for a in gone])
        return None

    def commit(self):
        self.server.commits.append((self.number, self.db))
        self.snapshot = None

    def rollback(self):
        if self.snapshot is not None:
            self.server.dbs[self.db].update(copy.deepcopy(self.snapshot))
            self.snapshot = None

    def close(self):
        if self.db in self.server.dbs:
            self.rollback()  # psycopg: closing without commit discards the transaction


class FakeRun:
    """Stand-in for Runner: enforces the real argv filter and emulates docker inspect/psql/pg_dump/pg_restore."""

    def __init__(self, server, image=rd.PG_IMAGE, inspect_id=CID):
        self.server, self.image, self.inspect_id, self.calls, self.envs = server, image, inspect_id, [], []
        self.abort, self.dump_bytes = set(), None

    def __call__(self, name, argv, *, env, timeout, stdin_path=None, stdout_path=None, cap=rd.STREAM_CAP,
                 check=True):
        rd.check_argv(argv, (PASSWORD,))
        self.calls.append(argv)
        self.envs.append(env)
        if name in self.abort:
            raise rd.StepAborted(f"{name}:timeout")
        s = self.server
        if argv[1] == "inspect":
            return rd.Result(0, f"{self.inspect_id}|{self.image}\n", "")
        tool = argv[argv.index(CID) + 1]
        assert env.get("PGPASSWORD") == PASSWORD
        if tool == "psql":
            return rd.Result(0, s.docker_system_id + "\n", "")
        if tool == "pg_dump":
            source = s.dbs[rd.SOURCE_DB]
            payload = self.dump_bytes or b"PGDMP" + json.dumps(
                {"rows": source["rows"], "accounts": source["accounts"], "refs": source["refs"],
                 "facts": source["facts"]}).encode()
            Path(stdout_path).write_bytes(payload)
            s.hooks.get("after_dump", lambda: None)()
            return rd.Result(0, "", "")
        if tool == "pg_restore":
            data = Path(stdin_path).read_bytes()
            assert data.startswith(b"PGDMP")
            restored = json.loads(data[5:])
            target = s.dbs[argv[argv.index("-d") + 1]]
            target["rows"] = [dict(r) for r in restored["rows"]]
            target["accounts"] = [dict(r) for r in restored["accounts"]]
            target["refs"] = [dict(r) for r in restored["refs"]]
            target["facts"] = {k: [tuple(x) for x in v] for k, v in restored["facts"].items()}
            s.hooks.get("after_restore", lambda: None)()
            return rd.Result(0, "", "")
        raise AssertionError(argv)


def run_main(env=None, server=None, fake_run=None, report=True, argv=None):
    server = server or FakeServer()
    fake_run = fake_run or FakeRun(server)
    with tempfile.TemporaryDirectory() as temp:
        environ = dict(GOOD_ENV if env is None else env)
        environ.setdefault("RUNNER_TEMP", temp)
        path = os.path.join(temp, "area2-audit-recovery.json")
        args = argv if argv is not None else ["--execute"] + (["--report", path] if report else [])
        out = io.StringIO()
        with mock.patch.object(rd, "Runner", lambda *a, **k: fake_run), contextlib.redirect_stdout(out):
            code = rd.main(args, environ=environ, popen=boom, connect=server.connect)
        written = Path(path).read_text(encoding="utf-8") if os.path.exists(path) else ""
        leftovers = sorted(p.name for p in Path(temp).iterdir() if p.name != "area2-audit-recovery.json")
    return code, out.getvalue(), (json.loads(written) if written else None), server, fake_run, leftovers


class DryPlanTests(unittest.TestCase):
    def test_default_is_dry_plan_without_child_or_db(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rd.main([], environ={}, popen=boom, connect=boom), 0)
        plan = json.loads(out.getvalue())
        self.assertEqual((plan["mode"], plan["executed"]), ("DRY_PLAN", False))
        self.assertEqual((plan["head_revision"], plan["tables"]), ("e9a4c7d2b135", 39))
        self.assertEqual(plan["reference_table"]["table"], "account_title_references")
        self.assertEqual(plan["cleanup_steps"], ["target_database", "source_audit_row",
                                                 "source_reference_and_account", "temp_files", "final_state"])


class GateTests(unittest.TestCase):
    CASES = [
        ({"GITHUB_ACTIONS": "false"}, "not_github_actions"),
        ({"GITHUB_REPOSITORY": "someone/auneron"}, "wrong_repository"),
        ({"GITHUB_EVENT_NAME": "workflow_dispatch"}, "event_not_allowed"),
        ({"GITHUB_EVENT_NAME": "schedule"}, "event_not_allowed"),
        ({"GITHUB_REF": "refs/heads/review/x"}, "push_not_main"),
        ({"GITHUB_EVENT_NAME": "pull_request", "GITHUB_BASE_REF": "develop"}, "pull_request_base_not_main"),
        ({"APP_ENV": "development"}, "app_env_not_test"),
        ({"PGHOSTADDR": "10.0.0.5"}, "libpq_env_override_present"),
        ({"PGSERVICE": "prod"}, "libpq_env_override_present"),
        ({"DATABASE_URL": "sqlite:///auneron_test"}, "database_url_not_postgresql"),
        ({"DATABASE_URL": DSN.replace("localhost", "db.example.com")}, "database_url_not_loopback_5432"),
        ({"DATABASE_URL": DSN.replace("5432", "5433")}, "database_url_not_loopback_5432"),
        ({"DATABASE_URL": DSN.replace(":5432", ":abc")}, "database_url_unparsable"),
        ({"DATABASE_URL": DSN.replace("/auneron_test", "/auneron")}, "database_url_not_auneron_test"),
        ({"DATABASE_URL": DSN + "?host=10.0.0.5"}, "database_url_not_auneron_test"),
        ({"DATABASE_URL": DSN.replace(f":{PASSWORD}", "")}, "database_url_password_missing"),
        ({"CI_POSTGRES_CONTAINER_ID": "c" * 12}, "service_container_id_invalid"),
        ({"CI_POSTGRES_CONTAINER_ID": "C" * 64}, "service_container_id_invalid"),
        ({"GITHUB_RUN_ID": "12;drop"}, "run_id_invalid"),
        ({"GITHUB_RUN_ATTEMPT": "0"}, "run_id_invalid"),
        ({"RUNNER_TEMP": "relative/tmp"}, "runner_temp_invalid"),
    ]

    def test_each_wrong_context_blocks_before_any_child_or_db(self):
        for change, code in self.CASES:
            with self.subTest(code=code, change=change):
                env = dict(GOOD_ENV, **change)
                result, out, report, server, fake_run, _ = run_main(env)
                self.assertEqual(result, 2)
                self.assertEqual(json.loads(out)["gate"], code)
                self.assertIsNone(report)
                self.assertEqual((server.calls, fake_run.calls), ([], []))
                self.assertNotIn(PASSWORD, out)

    def test_missing_variable_blocks(self):
        for key in ("CI_POSTGRES_CONTAINER_ID", "DATABASE_URL", "GITHUB_RUN_ID", "APP_ENV"):
            with self.subTest(key=key):
                env = {k: v for k, v in GOOD_ENV.items() if k != key}
                self.assertEqual(run_main(env)[0], 2)

    def test_report_path_must_be_runner_temp_json(self):
        for argv in (["--execute"], ["--execute", "--report", "/elsewhere/area2-audit-recovery.json"]):
            with self.subTest(argv=argv):
                result, out, _, server, fake_run, _ = run_main(argv=argv)
                self.assertEqual((result, json.loads(out)["gate"]), (2, "report_path_not_in_runner_temp"))
                self.assertEqual(server.calls, [])

    def test_target_name_is_derived_only_from_numeric_run_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            ctx = rd.check_gate(dict(GOOD_ENV, RUNNER_TEMP=temp))
        self.assertEqual(ctx.target, TARGET)
        self.assertNotEqual(ctx.target, rd.SOURCE_DB)


class FlowTests(unittest.TestCase):
    def assert_cleaned(self, report, server, leftovers):
        self.assertEqual(report["cleanup"], {"target_database": "DROPPED_VERIFIED", "source_audit_row": "TRUNCATED",
                                             "source_reference_and_account": "REMOVED",
                                             "temp_files": "REMOVED", "final_state": "VERIFIED"})
        self.assertEqual((server.dropped, server.truncated, server.ref_truncated), ([TARGET], 1, 1))
        self.assertEqual(server.deleted_accounts, [report["ledger"]["account_id"]])
        source = server.dbs[rd.SOURCE_DB]
        self.assertEqual((source["rows"], source["accounts"], source["refs"]), ([], [], []))
        self.assertEqual(list(server.dbs), [rd.SOURCE_DB])
        self.assertEqual(leftovers, [])

    def test_pass_proves_delta_and_cleans_only_owned_state(self):
        result, out, report, server, fake_run, leftovers = run_main()
        self.assertEqual((result, report["outcome"]), (0, "PASS"), report)
        delta = report["delta"]
        self.assertEqual((delta["tables"], delta["revision"], delta["columns"]), (39, "e9a4c7d2b135", 24))
        self.assertEqual(len(delta["check_constraints"]), 22)
        self.assertTrue(delta["update_rejected"] and delta["delete_rejected"])
        self.assertEqual(report["target"]["database"], TARGET)
        self.assert_cleaned(report, server, leftovers)
        self.assertNotIn(PASSWORD, out)
        self.assertNotIn(PASSWORD, json.dumps(report))
        self.assertNotIn("RAISE EXCEPTION", json.dumps(report))
        for argv in fake_run.calls:
            self.assertFalse(any("PGPASSWORD=" in a for a in argv))
        self.assertEqual(fake_run.calls[0][:2], ["docker", "inspect"])
        self.assertEqual([argv[argv.index(CID) + 1] for argv in fake_run.calls[1:]], ["psql", "pg_dump", "pg_restore"])
        self.assertEqual(len(server.sql(rd.SQL_SEED)), 1)
        self.assertEqual((len(server.sql(rd.SQL_SEED_ACCOUNT)), len(server.sql(rd.SQL_SEED_REFERENCE))), (1, 1))
        self.assertNotIn(PASSWORD, json.dumps(fake_run.envs[0]))

    def test_pass_on_pull_request_into_main(self):
        env = dict(GOOD_ENV, GITHUB_EVENT_NAME="pull_request", GITHUB_BASE_REF="main",
                   GITHUB_REF="refs/pull/13/merge")
        self.assertEqual(run_main(env)[0], 0)

    def blocked_before_seed(self, server=None, fake_run=None, expect=None):
        result, _, report, server, fake_run, leftovers = run_main(server=server, fake_run=fake_run)
        self.assertEqual((result, report["outcome"]), (2, "BLOCKED"), report)
        if expect:
            self.assertEqual(report["error"], expect)
        self.assertEqual(server.sql(rd.SQL_SEED), [])
        self.assertEqual(server.sql(rd.SQL_SEED_ACCOUNT), [])
        self.assertEqual(server.sql("CREATE DATABASE "), [])
        self.assertEqual((server.dropped, server.truncated, server.ref_truncated), ([], 0, 0))
        self.assertEqual(server.deleted_accounts, [])
        self.assertEqual(report["cleanup"]["source_reference_and_account"], "NOT_SEEDED")
        self.assertEqual(leftovers, [])
        return report, server

    def test_container_image_or_id_mismatch_blocks(self):
        server = FakeServer()
        self.blocked_before_seed(server, FakeRun(server, image="postgres:16"), "service_container_mismatch")
        server = FakeServer()
        self.blocked_before_seed(server, FakeRun(server, inspect_id="d" * 64), "service_container_mismatch")

    def test_different_server_blocks(self):
        server = FakeServer()
        server.docker_system_id = "7400000000000000002"
        self.blocked_before_seed(server, expect="server_identity_mismatch")

    def test_wrong_head_or_table_count_blocks(self):
        for key, value, code in (("revision", "c3a8f1e6d204", "source_head_not_e9a4c7d2b135"),
                                 ("tables", 38, "source_table_count_not_39")):
            with self.subTest(code=code):
                server = FakeServer()
                server.dbs[rd.SOURCE_DB][key] = value
                self.blocked_before_seed(server, expect=code)

    def test_non_baseline_source_blocks_and_foreign_rows_are_untouched(self):
        server = FakeServer()
        server.counts["users"] = 1
        self.blocked_before_seed(server, expect="source_not_empty_ci_baseline")
        server = FakeServer()
        server.dbs[rd.SOURCE_DB]["rows"].append({"id": 77, "request_id": "foreign", "json": "{}"})
        report, server = self.blocked_before_seed(server, expect="source_not_empty_ci_baseline")
        self.assertEqual(len(server.dbs[rd.SOURCE_DB]["rows"]), 1)
        self.assertEqual(report["cleanup"]["source_audit_row"], "NOT_SEEDED")

    def test_preexisting_target_blocks_and_is_never_dropped(self):
        server = FakeServer()
        server.dbs[TARGET] = server.new_db(555, comment="someone else")
        report, server = self.blocked_before_seed(server, expect="target_preexists")
        self.assertIn(TARGET, server.dbs)
        self.assertEqual(report["cleanup"]["target_database"], "NOT_CREATED")

    def test_source_schema_defect_fails_before_seed(self):
        server = FakeServer()
        server.dbs[rd.SOURCE_DB]["facts"]["trigger"] = [("D", rd.TRIGGER_TYPE, rd.FUNCTION)]
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["error"]), (1, "StepFailed: source_trigger_not_enabled"))
        self.assertEqual(server.sql(rd.SQL_SEED), [])

    def test_target_defects_fail_and_still_clean_up(self):
        def drop_check(f):
            f["checks"] = f["checks"][1:]

        def disable(f):
            f["trigger"] = [("D", rd.TRIGGER_TYPE, rd.FUNCTION)]

        def no_index(f):
            f["index"] = []

        def no_function(f):
            f["function"] = []

        def fewer_columns(f):
            f["columns"] = f["columns"][:-1]

        cases = [(drop_check, "target_check_constraints"), (disable, "target_trigger_not_enabled"),
                 (no_index, "target_index"), (no_function, "target_function"),
                 (fewer_columns, "target_columns")]
        for mutate, code in cases:
            with self.subTest(code=code):
                server = FakeServer()
                server.hooks["after_restore"] = lambda s=server, m=mutate: m(s.dbs[TARGET]["facts"])
                result, _, report, server, _, leftovers = run_main(server=server)
                self.assertEqual((result, report["outcome"], report["error"]), (1, "FAIL", f"StepFailed: {code}"))
                self.assert_cleaned(report, server, leftovers)

    def test_row_hash_and_append_only_are_required_for_pass(self):
        def alter_row(s):
            s.dbs[TARGET]["rows"][0]["json"] += " "

        cases = [(alter_row, "target_row_not_restored_exactly"),
                 (lambda s: s.dbs[TARGET].update(update_rejected=False), "target_update_not_rejected"),
                 (lambda s: s.dbs[TARGET].update(delete_rejected=False), "target_delete_not_rejected")]
        for hook, code in cases:
            with self.subTest(code=code):
                server = FakeServer()
                server.hooks["after_restore"] = lambda s=server, h=hook: h(s)
                result, _, report, server, _, leftovers = run_main(server=server)
                self.assertEqual((result, report["error"]), (1, f"StepFailed: {code}"))
                self.assert_cleaned(report, server, leftovers)

    def schema_differ(self, mutate):
        server = FakeServer()
        server.hooks["after_restore"] = lambda: mutate(server.dbs[TARGET]["facts"])
        result, out, report, server, _, leftovers = run_main(server=server)
        self.assertEqual((result, report["outcome"], report["error"]),
                         (1, "FAIL", "StepFailed: schema_source_target_differ"), report)
        self.assert_cleaned(report, server, leftovers)
        self.assertNotIn("cleanup_status", report)
        comparison = report["schema_comparison"]
        self.assertFalse(comparison["equal"])
        self.assertEqual((comparison["decision"], comparison["accepted_known_check_renderings"]), ("DIFFERENT", []))
        text = json.dumps(comparison)
        self.assertNotIn(PASSWORD, out)
        self.assertNotIn(report["seed"]["marker"], text)
        self.assertNotIn('"valor"', text)
        return comparison

    def test_equal_schema_reports_equal_and_still_passes(self):
        result, _, report, server, _, leftovers = run_main()
        self.assertEqual((result, report["outcome"]), (0, "PASS"))
        self.assertEqual(report["schema_comparison"], {"equal": True, "differing_keys": [],
                                                       "accepted_known_check_renderings": [], "decision": "EQUAL"})
        self.assertEqual(report["check_rendering_probes"],
                         {"count": 48, "expected_count": 48, "passed": 48, "mismatches": []})
        self.assert_cleaned(report, server, leftovers)

    def test_check_definition_difference_is_reported_verbatim(self):
        name = sorted(rd.CHECKS)[0]

        def redefine(f):
            f["checks"] = [(n, d + " NOT VALID") if n == name else (n, d) for n, d in f["checks"]]
        comparison = self.schema_differ(redefine)
        self.assertEqual(comparison["differing_keys"], ["checks"])
        detail = comparison["details"]["checks"]
        self.assertEqual((detail["source_count"], detail["target_count"]), (22, 22))
        self.assertEqual(detail["source_only"], [[name, f"CHECK ({name})"]])
        self.assertEqual(detail["target_only"], [[name, f"CHECK ({name}) NOT VALID"]])
        self.assertEqual(len(detail["source"]), 22)
        self.assertNotIn("function_bodies", comparison)

    def test_function_hash_difference_includes_only_bounded_function_bodies(self):
        changed = FUNCTION_SRC.replace("BEGIN", "BEGIN NULL;")
        comparison = self.schema_differ(lambda f: f.update(function=[("plpgsql", "trigger", changed)]))
        self.assertEqual(comparison["differing_keys"], ["function"])
        bodies = comparison["function_bodies"]
        self.assertEqual((bodies["name"], bodies["limit_chars"]), (rd.FUNCTION, 4096))
        self.assertEqual(bodies["source"], [{"text": FUNCTION_SRC, "chars": len(FUNCTION_SRC), "truncated": False}])
        self.assertEqual(bodies["target"], [{"text": changed, "chars": len(changed), "truncated": False}])
        self.assertNotEqual(comparison["details"]["function"]["source"][0][2],
                            comparison["details"]["function"]["target"][0][2])

    def test_long_function_body_is_truncated_with_indicator(self):
        long_src = FUNCTION_SRC + "-- " + "x" * 5000
        comparison = self.schema_differ(lambda f: f.update(function=[("plpgsql", "trigger", long_src)]))
        target = comparison["function_bodies"]["target"][0]
        self.assertEqual((len(target["text"]), target["chars"], target["truncated"]), (4096, len(long_src), True))
        self.assertEqual(target["text"], long_src[:4096])

    def test_multiple_differing_keys_are_all_listed(self):
        def two(f):
            f["index"] = [(f["index"][0][0] + " WHERE true",)]
            f["trigger"] = [("O", rd.TRIGGER_TYPE, rd.FUNCTION), ("O", rd.TRIGGER_TYPE, rd.FUNCTION)]
        server = FakeServer()
        server.hooks["after_restore"] = lambda: two(server.dbs[TARGET]["facts"])
        result, _, report, server, _, leftovers = run_main(server=server)
        # check_schema still runs first after the diagnostic is recorded: target_index fails the run
        self.assertEqual((result, report["error"]), (1, "StepFailed: target_index"))
        self.assertEqual(report["schema_comparison"]["differing_keys"], ["index", "trigger"])
        self.assert_cleaned(report, server, leftovers)

    def test_compare_schema_bounds_large_facts(self):
        source = {"checks": [["c", "x" * 20000]]}
        detail = rd.compare_schema(source, {"checks": []}, [], [])["details"]["checks"]
        self.assertTrue(detail["source"]["truncated"])
        self.assertEqual(len(detail["source"]["json_prefix"]), rd.FACT_LIMIT)
        self.assertEqual(detail["target"], [])

    def test_unexpected_source_row_refuses_truncate(self):
        server = FakeServer()
        server.hooks["after_dump"] = lambda: server.dbs[rd.SOURCE_DB]["rows"].append(
            {"id": 99, "request_id": "foreign", "json": "{}"})
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["outcome"], report["cleanup_status"]), (1, "FAIL", "BLOCKED"))
        self.assertEqual(report["cleanup"]["source_audit_row"], "REFUSED:unexpected_source_rows")
        self.assertEqual(server.truncated, 0)
        self.assertEqual(len(server.dbs[rd.SOURCE_DB]["rows"]), 2)
        self.assertEqual(server.dropped, [TARGET])

    def test_own_row_with_changed_fingerprint_refuses_truncate(self):
        server = FakeServer()

        def tamper():
            server.dbs[rd.SOURCE_DB]["rows"][0]["json"] += "x"
        server.hooks["after_restore"] = tamper
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["error"]), (1, "StepFailed: source_row_changed"))
        self.assertEqual(report["cleanup"]["source_audit_row"], "REFUSED:unexpected_source_rows")
        self.assertEqual(server.truncated, 0)

    def test_target_not_owned_at_cleanup_is_not_dropped_and_blocks_pass(self):
        def comment(s):
            s.dbs[TARGET]["comment"] = "replaced"

        def oid(s):
            s.dbs[TARGET]["oid"] = 1

        for hook in (comment, oid):
            with self.subTest(hook=hook.__name__):
                server = FakeServer()
                server.hooks["after_restore"] = lambda s=server, h=hook: h(s)
                result, _, report, server, _, _ = run_main(server=server)
                self.assertEqual((result, report["outcome"]), (2, "BLOCKED"))
                self.assertEqual(report["cleanup"]["target_database"], "REFUSED:target_not_owned")
                self.assertEqual(server.dropped, [])
                self.assertIn(TARGET, server.dbs)

    def test_drop_failure_turns_pass_into_blocked(self):
        server = FakeServer()
        server.fail.add("drop")
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["outcome"], report["cleanup_status"]), (2, "BLOCKED", "BLOCKED"))
        self.assertTrue(report["cleanup"]["target_database"].startswith("FAILED:"))

    def test_timeout_aborts_and_cleanup_runs(self):
        for step in ("pg_dump", "pg_restore"):
            with self.subTest(step=step):
                server = FakeServer()
                fake_run = FakeRun(server)
                fake_run.abort.add(step)
                result, _, report, server, _, leftovers = run_main(server=server, fake_run=fake_run)
                self.assertEqual((result, report["outcome"]), (3, "ABORTED"))
                self.assertEqual((server.truncated, server.ref_truncated), (1, 1))
                source = server.dbs[rd.SOURCE_DB]
                self.assertEqual((source["rows"], source["accounts"], source["refs"]), ([], [], []))
                self.assertEqual(report["cleanup"]["source_reference_and_account"], "REMOVED")
                self.assertEqual(leftovers, [])

    def test_non_custom_dump_fails(self):
        server = FakeServer()
        fake_run = FakeRun(server)
        fake_run.dump_bytes = b"-- plain SQL"
        result, _, report, _, _, _ = run_main(server=server, fake_run=fake_run)
        self.assertEqual((result, report["error"]), (1, "StepFailed: dump_not_custom_format"))


class CleanupDropBudgetTests(unittest.TestCase):
    """TASK052: a longer, bounded statement_timeout only on the session that drops the owned target."""

    @staticmethod
    def stmts(server, prefix):
        return [t for t in server.timeouts if t[2].startswith(prefix)]

    def assert_no_budget_and_no_drop(self, server):
        self.assertEqual(self.stmts(server, rd.SQL_DROP_BUDGET), [])
        self.assertEqual(self.stmts(server, "DROP DATABASE "), [])
        self.assertEqual(server.dropped, [])
        self.assertEqual({t[3] for t in server.timeouts}, {30000})

    def assert_blocked_target_left(self, result, report, server):
        self.assertEqual((result, report["outcome"], report["cleanup_status"]), (2, "BLOCKED", "BLOCKED"), report)
        self.assertIn(TARGET, server.dbs)
        self.assertEqual(report["cleanup"]["final_state"], "FAILED:StepFailed:target_present")
        self.assertEqual((report["cleanup"]["source_audit_row"], report["cleanup"]["source_reference_and_account"],
                          report["cleanup"]["temp_files"]), ("TRUNCATED", "REMOVED", "REMOVED"))

    def test_budget_and_defaults_are_declared(self):
        self.assertEqual(rd.CLEANUP_DROP_STATEMENT_TIMEOUT_MS, 180000)
        self.assertEqual(rd.CONNECT_OPTIONS, "-c statement_timeout=30000 -c lock_timeout=5000 -c TimeZone=UTC")
        self.assertEqual(rd.SQL_DROP_BUDGET, "SET statement_timeout = 180000")
        self.assertEqual(rd.CLEANUP_DROP_BUDGET["statement_timeout_ms"], 180000)
        self.assertEqual(rd.CLEANUP_DROP_BUDGET["default_connect_options"], rd.CONNECT_OPTIONS)
        self.assertEqual(rd.plan_summary()["cleanup_drop_budget"], rd.CLEANUP_DROP_BUDGET)
        self.assertEqual(rd.TIMEOUTS, {"inspect": 30, "psql": 30, "pg_dump": 180, "pg_restore": 180})
        report = run_main()[2]
        self.assertEqual(report["cleanup_drop_budget"], json.loads(json.dumps(rd.CLEANUP_DROP_BUDGET)))

    def test_only_the_owned_target_drop_session_gets_the_longer_budget(self):
        result, _, report, server, _, leftovers = run_main()
        self.assertEqual((result, report["outcome"]), (0, "PASS"), report)
        FlowTests.assert_cleaned(self, report, server, leftovers)
        (budget,), (drop,) = self.stmts(server, rd.SQL_DROP_BUDGET), self.stmts(server, "DROP DATABASE ")
        self.assertEqual(drop, (budget[0], rd.SOURCE_DB, f'DROP DATABASE "{TARGET}"', 180000))
        # a fresh session: ownership lookup first, then the budget, its read-back and the single DROP
        session = [(sql, ms) for n, _, sql, ms in server.timeouts if n == budget[0]]
        self.assertEqual(session, [(rd.SQL_DB_BY_NAME, 30000), (rd.SQL_DROP_BUDGET, 30000),
                                   (rd.SQL_STATEMENT_TIMEOUT, 180000), (drop[2], 180000)])
        self.assertEqual({ms for n, _, _, ms in server.timeouts if n != budget[0]}, {30000})
        verify = [t for t in server.timeouts if t[2] == rd.SQL_DB_BY_OID]
        self.assertEqual(len(verify), 1)
        self.assertGreater(verify[0][0], budget[0])  # absence checked on a later session with the defaults
        self.assertEqual(verify[0][3], 30000)

    def test_slow_checkpoint_drop_passes_only_with_the_budget(self):
        server = FakeServer()
        server.fail.add("slow_checkpoint")  # DROP cancelled under the 30 s default, completes under 180 s
        result, _, report, server, _, leftovers = run_main(server=server)
        self.assertEqual((result, report["outcome"]), (0, "PASS"), report)
        FlowTests.assert_cleaned(self, report, server, leftovers)
        server = FakeServer()
        server.fail.update({"slow_checkpoint", "budget_ignored"})
        result, _, report, server, _, _ = run_main(server=server)
        self.assert_blocked_target_left(result, report, server)
        self.assertEqual(report["cleanup"]["target_database"], "FAILED:StepFailed:drop_budget_not_applied")
        self.assertEqual(self.stmts(server, "DROP DATABASE "), [])

    def test_unowned_absent_or_uncreated_target_gets_no_budget_and_no_drop(self):
        def comment(s):
            s.dbs[TARGET]["comment"] = "replaced"

        def oid(s):
            s.dbs[TARGET]["oid"] = 1

        def vanish(s):
            del s.dbs[TARGET]
        for hook, status in ((comment, "REFUSED:target_not_owned"), (oid, "REFUSED:target_not_owned"),
                             (vanish, "ABSENT")):
            with self.subTest(hook=hook.__name__):
                server = FakeServer()
                server.hooks["after_restore"] = lambda s=server, h=hook: h(s)
                result, _, report, server, _, _ = run_main(server=server)
                self.assertNotEqual(result, 0)
                self.assertEqual(report["cleanup"]["target_database"], status)
                self.assert_no_budget_and_no_drop(server)
        server = FakeServer()
        server.dbs[TARGET] = server.new_db(555, comment="someone else")
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["cleanup"]["target_database"]), (2, "NOT_CREATED"))
        self.assert_no_budget_and_no_drop(server)
        self.assertIn(TARGET, server.dbs)

    def test_budget_setting_failure_blocks_without_drop(self):
        for fail, status in (("set_budget", "FAILED:FakePgError:permission denied to set parameter "
                                            "\"statement_timeout\""),
                             ("budget_ignored", "FAILED:StepFailed:drop_budget_not_applied")):
            with self.subTest(fail=fail):
                server = FakeServer()
                server.fail.add(fail)
                result, _, report, server, _, _ = run_main(server=server)
                self.assert_blocked_target_left(result, report, server)
                self.assertEqual(report["cleanup"]["target_database"], status)
                self.assertEqual(len(self.stmts(server, rd.SQL_DROP_BUDGET)), 1)
                self.assertEqual(self.stmts(server, "DROP DATABASE "), [])

    def test_drop_failure_under_the_budget_never_passes_and_is_not_retried(self):
        for fail, status in (("drop", "FAILED:FakePgError:database is being accessed by other users"),
                             ("drop_timeout", "FAILED:FakePgError:canceling statement due to statement timeout")):
            with self.subTest(fail=fail):
                server = FakeServer()
                server.fail.add(fail)
                result, _, report, server, _, _ = run_main(server=server)
                self.assert_blocked_target_left(result, report, server)
                self.assertEqual(report["cleanup"]["target_database"], status)
                drops = self.stmts(server, "DROP DATABASE ")
                self.assertEqual([ms for *_, ms in drops], [180000])
                self.assertEqual(len(self.stmts(server, rd.SQL_DROP_BUDGET)), 1)

    def test_drop_failure_keeps_a_failing_drill_failed(self):
        server = FakeServer()
        server.fail.add("drop_timeout")
        server.hooks["after_restore"] = lambda: server.dbs[TARGET].update(update_rejected=False)
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["outcome"], report["cleanup_status"]), (1, "FAIL", "BLOCKED"))

    def test_absence_is_still_verified_after_a_reported_drop(self):
        server = FakeServer()
        server.fail.add("drop_noop")
        result, _, report, server, _, _ = run_main(server=server)
        self.assert_blocked_target_left(result, report, server)
        self.assertEqual(report["cleanup"]["target_database"], "FAILED:StepFailed:target_still_present")
        (drop,) = self.stmts(server, "DROP DATABASE ")
        lookups = [t for t in server.timeouts if t[2] == rd.SQL_DB_BY_NAME and t[0] > drop[0]]
        self.assertTrue(lookups and all(ms == 30000 for *_, ms in lookups))


class KnownCheckRenderingTests(unittest.TestCase):
    """TASK051: only the three exact pairs observed in CI run 41257863930 are tolerated."""

    def assert_cleaned(self, report, server, leftovers):
        FlowTests.assert_cleaned(self, report, server, leftovers)

    def flow(self, target_checks, source_checks=None, extra=None, probe=None):
        server = FakeServer()
        server.dbs[rd.SOURCE_DB]["facts"] = artifact_facts(source_checks)
        if probe:
            server.probe = probe

        def after_restore():
            server.dbs[TARGET]["facts"]["checks"] = list(target_checks)
            if extra:
                extra(server)
        server.hooks["after_restore"] = after_restore
        result, out, report, server, _, leftovers = run_main(server=server)
        self.assertNotIn(PASSWORD, out)
        self.assert_cleaned(report, server, leftovers)
        return result, report, server

    def assert_differ(self, target_checks, source_checks=None):
        result, report, _ = self.flow(target_checks, source_checks)
        self.assertEqual((result, report["outcome"], report["error"]),
                         (1, "FAIL", "StepFailed: schema_source_target_differ"), report)
        comparison = report["schema_comparison"]
        self.assertEqual((comparison["equal"], comparison["decision"]), (False, "DIFFERENT"))
        self.assertEqual(comparison["accepted_known_check_renderings"], [])
        self.assertNotIn("delta", report)
        return comparison

    # --- frozen allowlist ---------------------------------------------------------------------------------
    def test_allowlist_is_exactly_the_three_observed_pairs(self):
        source = dict(ARTIFACT_SOURCE_CHECKS)
        self.assertEqual(rd.KNOWN_CHECK_RENDERINGS, {n: (source[n], ARTIFACT_TARGET_ONLY[n]) for n in KNOWN})
        self.assertEqual(sorted(rd.KNOWN_CHECK_LITERALS), KNOWN)
        self.assertEqual({n: v[1] for n, v in rd.KNOWN_CHECK_LITERALS.items()}, ARTIFACT_LITERALS)
        self.assertTrue(set(KNOWN) <= rd.CHECKS)
        self.assertEqual(sorted(n for n, _ in ARTIFACT_SOURCE_CHECKS), sorted(rd.CHECKS))

    def test_artifact_forms_pass_with_raw_difference_preserved(self):
        result, report, server = self.flow(artifact_target_checks())
        self.assertEqual((result, report["outcome"]), (0, "PASS"), report)
        comparison = report["schema_comparison"]
        self.assertFalse(comparison["equal"])  # raw comparison is never relabelled
        self.assertEqual(comparison["differing_keys"], ["checks"])
        self.assertEqual(comparison["decision"], "EQUAL_EXCEPT_KNOWN_CHECK_RENDERINGS")
        self.assertEqual(comparison["accepted_known_check_renderings"], KNOWN)
        detail = comparison["details"]["checks"]
        source = dict(ARTIFACT_SOURCE_CHECKS)
        self.assertEqual(detail["source_only"], [[n, source[n]] for n in KNOWN])
        self.assertEqual(detail["target_only"], [[n, ARTIFACT_TARGET_ONLY[n]] for n in KNOWN])
        self.assertEqual((detail["source_count"], detail["target_count"]), (22, 22))
        self.assertEqual(report["check_rendering_probes"],
                         {"count": 48, "expected_count": 48, "passed": 48, "mismatches": []})
        self.assertEqual(report["delta"]["audit_rows"], 1)

    def test_subset_of_known_renderings_passes_and_lists_only_that_subset(self):
        result, report, _ = self.flow(artifact_target_checks([_CK + "status"]))
        self.assertEqual(result, 0, report)
        self.assertEqual(report["schema_comparison"]["accepted_known_check_renderings"], [_CK + "status"])

    def test_identical_artifact_checks_are_equal(self):
        result, report, _ = self.flow(ARTIFACT_SOURCE_CHECKS)
        self.assertEqual(result, 0, report)
        self.assertEqual((report["schema_comparison"]["equal"], report["schema_comparison"]["decision"]),
                         (True, "EQUAL"))

    # --- every other check difference is rejected ---------------------------------------------------------
    def target_with(self, name, definition):
        return [(n, definition if n == name else d) for n, d in artifact_target_checks()]

    def variants(self):
        ch, op, st = (ARTIFACT_TARGET_ONLY[_CK + k] for k in ("channel", "operation", "status"))
        src = dict(ARTIFACT_SOURCE_CHECKS)
        return {
            "changed_literal": (_CK + "status", st.replace("'pago'", "'pagos'")),
            "case_literal": (_CK + "status", st.replace("'pago'", "'PAGO'")),
            "removed_literal": (_CK + "channel", ch.replace(", ('legacy_csv_upload'::character varying)::text", "")),
            "added_literal": (_CK + "operation", op.replace("])))", ", ('archive'::character varying)::text])))")),
            "element_type": (_CK + "status", st.replace("'aberto'::character varying", "'aberto'::text")),
            "column_cast_type": (_CK + "status", st.replace("(status_at_operation)::text", "(status_at_operation)::name")),
            "operator_not_all": (_CK + "channel", ch.replace("= ANY", "<> ALL")),
            "operator_all": (_CK + "operation", op.replace("= ANY", "= ALL")),
            "column": (_CK + "channel", ch.replace("(channel)::text", "(operation)::text")),
            "whitespace": (_CK + "channel", ch.replace("ANY (ARRAY", "ANY  (ARRAY")),
            "lowercase_array": (_CK + "operation", op.replace("ARRAY[", "array[")),
            "not_valid": (_CK + "status", st + " NOT VALID"),
            "pair_under_other_name": (_CK + "status", ch),
            "source_form_reversed_into_target": (_CK + "channel", src[_CK + "channel"].replace("'manual_api'", "'x'")),
            "unlisted_check_same_cast_style": (_CK + "actor_type",
                                               "CHECK (((actor_type)::text = ANY (ARRAY[('user'::character varying)::text])))"),
        }

    def test_changed_known_target_forms_are_rejected(self):
        for label, (name, definition) in self.variants().items():
            with self.subTest(variant=label):
                target = self.target_with(name, definition)
                self.assertEqual(rd.facts_match(catalog(), catalog(target)), (False, []))
        label, (name, definition) = "changed_literal", self.variants()["changed_literal"]
        comparison = self.assert_differ(self.target_with(name, definition))
        self.assertIn([name, definition], comparison["details"]["checks"]["target_only"])

    def test_flow_rejects_operator_type_and_unknown_forms(self):
        for label in ("operator_not_all", "element_type", "whitespace", "pair_under_other_name"):
            with self.subTest(variant=label):
                self.assert_differ(self.target_with(*self.variants()[label]))

    def test_reversed_pair_and_changed_source_are_rejected(self):
        src = dict(ARTIFACT_SOURCE_CHECKS)
        reversed_source = [(n, ARTIFACT_TARGET_ONLY.get(n, d)) for n, d in ARTIFACT_SOURCE_CHECKS]
        reversed_target = list(ARTIFACT_SOURCE_CHECKS)
        self.assertEqual(rd.facts_match(catalog(reversed_source), catalog(reversed_target)), (False, []))
        self.assert_differ(reversed_target, reversed_source)
        changed_source = [(n, d.replace("'pago'", "'pagos'") if n == _CK + "status" else d)
                          for n, d in ARTIFACT_SOURCE_CHECKS]
        self.assertNotEqual(changed_source, ARTIFACT_SOURCE_CHECKS)
        self.assertEqual(rd.facts_match(catalog(changed_source), catalog(artifact_target_checks())), (False, []))
        self.assertIn("'pago'", src[_CK + "status"])

    def test_constraint_presence_changes_are_rejected(self):
        target = artifact_target_checks()
        renamed = [(n + "_v2" if n == _CK + "channel" else n, d) for n, d in target]
        cases = {
            "removed": [c for c in target if c[0] != _CK + "status"],
            "added": target + [(_CK + "zz_extra", "CHECK (true)")],
            "duplicated": target + [target[12]],
            "renamed": renamed,
            "empty": [],
        }
        for label, checks in cases.items():
            with self.subTest(case=label):
                self.assertEqual(rd.facts_match(catalog(), catalog(checks)), (False, []))
        for label, code in (("removed", "target_check_constraints"), ("added", "target_check_constraints"),
                            ("renamed", "target_check_constraints")):
            with self.subTest(flow=label):
                result, report, _ = self.flow(cases[label])
                self.assertEqual((result, report["error"]), (1, f"StepFailed: {code}"))
                self.assertEqual(report["schema_comparison"]["decision"], "DIFFERENT")

    def test_malformed_check_rows_are_rejected(self):
        good = catalog()
        for label, checks in (("not_list", "x"), ("short_row", [["only_name"]] + good["checks"][1:])):
            with self.subTest(case=label):
                self.assertEqual(rd.facts_match(good, dict(good, checks=checks)), (False, []))
        self.assertEqual(rd.facts_match(good, catalog()), (True, []))

    # --- other schema facts stay strictly equal -----------------------------------------------------------
    def test_other_fact_mutations_are_rejected_even_with_known_renderings(self):
        known = catalog(artifact_target_checks())
        self.assertEqual(rd.facts_match(catalog(), known), (True, KNOWN))
        mutations = {
            "columns": lambda f: f.update(columns=f["columns"][::-1]),
            "index": lambda f: f.update(index=["CREATE UNIQUE INDEX x " + rd.INDEX_SUFFIX]),
            "function_hash": lambda f: f.update(function=[["plpgsql", "trigger", "0" * 64, True]]),
            "trigger": lambda f: f.update(trigger=[["D", rd.TRIGGER_TYPE, rd.FUNCTION]]),
            "extra_key": lambda f: f.update(extra=[]),
            "missing_key": lambda f: f.pop("index"),
        }
        for label, mutate in mutations.items():
            with self.subTest(fact=label):
                target = catalog(artifact_target_checks())
                mutate(target)
                self.assertEqual(rd.facts_match(catalog(), target), (False, []))

    def test_flow_function_or_index_change_fails_despite_known_renderings(self):
        changed = FUNCTION_SRC.replace("BEGIN", "BEGIN NULL;")
        index = f"CREATE UNIQUE INDEX {rd.INDEX} ON public.{rd.TABLE} {rd.INDEX_SUFFIX}"
        for label, hook in (("function", lambda s: s.dbs[TARGET]["facts"].update(
                                function=[("plpgsql", "trigger", changed)])),
                            ("index", lambda s: s.dbs[TARGET]["facts"].update(index=[(index,)]))):
            with self.subTest(fact=label):
                result, report, _ = self.flow(artifact_target_checks(), extra=hook)
                self.assertEqual((result, report["error"]), (1, "StepFailed: schema_source_target_differ"))
                comparison = report["schema_comparison"]
                self.assertEqual(comparison["differing_keys"], sorted(["checks", label]))
                self.assertEqual(comparison["decision"], "DIFFERENT")

    def test_row_hash_and_append_only_still_required_with_known_renderings(self):
        cases = [(lambda s: s.dbs[TARGET]["rows"][0].update(json=s.dbs[TARGET]["rows"][0]["json"] + " "),
                  "target_row_not_restored_exactly"),
                 (lambda s: s.dbs[TARGET].update(update_rejected=False), "target_update_not_rejected"),
                 (lambda s: s.dbs[TARGET].update(delete_rejected=False), "target_delete_not_rejected")]
        for hook, code in cases:
            with self.subTest(code=code):
                result, report, _ = self.flow(artifact_target_checks(), extra=hook)
                self.assertEqual((result, report["error"]), (1, f"StepFailed: {code}"))

    # --- read-only truth-value probes ---------------------------------------------------------------------
    def test_probe_sql_is_exactly_the_two_allowed_expressions_on_a_parameter(self):
        def inner(definition, column):
            self.assertTrue(definition.startswith("CHECK (") and definition.endswith(")"))
            return definition[len("CHECK ("):-1].replace(f"(({column})::text", f"(({rd.PROBE_REF})::text", 1)
        source = dict(ARTIFACT_SOURCE_CHECKS)
        for name in KNOWN:
            column = rd.KNOWN_CHECK_LITERALS[name][0]
            expected = f"SELECT {inner(source[name], column)}, {inner(ARTIFACT_TARGET_ONLY[name], column)}"
            self.assertEqual(rd.CHECK_PROBE_SQL[name], expected)
            sql = rd.CHECK_PROBE_SQL[name].upper()
            self.assertEqual(sql.count("%S"), 2)
            for word in ("FROM", "INSERT", "UPDATE ", "DELETE ", "DROP", "TRUNCATE", "CREATE", "ALTER", ";", "\""):
                self.assertNotIn(word, sql.replace("'UPDATE'", "").replace("'DELETE'", "").replace("'CREATE'", ""))

    def test_probe_values_are_fixed_bounded_and_cover_edges(self):
        self.assertEqual(rd.PROBE_COUNT, 48)
        for name, literals in ARTIFACT_LITERALS.items():
            values = rd.probe_values(literals)
            self.assertEqual(len(values), 3 * len(literals) + 8)
            self.assertTrue(set(literals) <= set(values))
            for edge in (None, "", " ", "pagó", "аberto", literals[0].upper(), literals[0] + " "):
                self.assertIn(edge, values)
            self.assertTrue(all(v is None or isinstance(v, str) for v in values))
            self.assertEqual(sum(v in literals for v in values if v is not None), len(literals))

    def test_probes_run_read_only_on_target_only(self):
        result, report, server = self.flow(artifact_target_checks())
        self.assertEqual(result, 0, report)
        probe_sqls = set(rd.CHECK_PROBE_SQL.values())
        target_calls = [sql for db, sql in server.calls if db == TARGET]
        first = next(i for i, sql in enumerate(target_calls) if sql in probe_sqls)
        self.assertEqual(target_calls[first - 1], rd.SQL_READ_ONLY)
        self.assertEqual(sum(sql in probe_sqls for sql in target_calls), 48)
        self.assertEqual([sql for db, sql in server.calls if db != TARGET and sql in probe_sqls], [])
        self.assertEqual(target_calls[first:first + 48], [sql for sql in target_calls if sql in probe_sqls])

    def test_probe_mismatch_fails_and_cleanup_is_still_verified(self):
        cases = {
            "target_disagrees_on_literal": lambda n, v, t: (t, False if v == "pago" else t),
            "source_disagrees_on_null": lambda n, v, t: (False if v is None else t, t),
            "both_accept_outside_value": lambda n, v, t: (True, True) if v == "unknown" else (t, t),
            "both_accept_unicode_variant": lambda n, v, t: (True, True) if v == "pagó" else (t, t),
            "integer_instead_of_bool": lambda n, v, t: (1, 1) if v == "create" else (t, t),
        }
        for label, probe in cases.items():
            with self.subTest(case=label):
                result, report, _ = self.flow(artifact_target_checks(), probe=probe)
                self.assertEqual((result, report["outcome"], report["error"]),
                                 (1, "FAIL", "StepFailed: check_rendering_probe_mismatch"), report)
                probes = report["check_rendering_probes"]
                self.assertEqual((probes["count"], len(probes["mismatches"])), (48, probes["count"] - probes["passed"]))
                self.assertGreaterEqual(len(probes["mismatches"]), 1)
                self.assertNotIn("delta", report)
        result, report, _ = self.flow(ARTIFACT_SOURCE_CHECKS, probe=cases["target_disagrees_on_literal"])
        self.assertEqual(report["error"], "StepFailed: check_rendering_probe_mismatch")
        self.assertEqual(report["check_rendering_probes"]["mismatches"],
                         [{"check": _CK + "status", "value": "pago", "expected": True, "source_form": True,
                           "target_form": False}])


class ReferenceTableTests(unittest.TestCase):
    """TASK054 (TI1): side-table catalog, owned account+reference survival and the fail-closed cleanup."""

    def assert_cleaned(self, report, server, leftovers):
        FlowTests.assert_cleaned(self, report, server, leftovers)

    def run_with(self, hook=None, when="after_restore", server=None):
        server = server or FakeServer()
        if hook:
            server.hooks[when] = lambda: hook(server)
        return run_main(server=server)

    # --- pass ---------------------------------------------------------------------------------------------
    def test_pass_restores_both_owned_rows_exactly_and_proves_probes(self):
        result, out, report, server, _, leftovers = run_main()
        self.assertEqual((result, report["outcome"]), (0, "PASS"), report)
        ref = report["delta"]["reference_table"]
        self.assertEqual((ref["table"], ref["columns"], len(ref["check_constraints"])),
                         ("account_title_references", 11, 7))
        self.assertEqual(ref["keys"], sorted(rd.REF_KEYS))
        self.assertEqual(ref["indexes"], sorted(rd.REF_INDEXES))
        self.assertEqual((ref["account_rows"], ref["reference_rows"]), (1, 1))
        self.assertTrue(ref["update_rejected"] and ref["delete_rejected"] and ref["account_delete_restricted"])
        seed = report["seed_reference"]
        self.assertEqual(seed["initial_counts"], {"accounts": 0, "account_title_references": 0})
        self.assertEqual(seed["marker"], report["seed"]["marker"])
        ledger = report["ledger"]
        self.assertEqual((ledger["initial_account_count"], ledger["initial_reference_count"]), (0, 0))
        self.assertEqual((ledger["account_id"], ledger["reference_id"]), (seed["account_id"], seed["reference_id"]))
        self.assertEqual(len(ledger["account_fingerprint"]), 64)
        self.assertEqual(len(ledger["reference_fingerprint"]), 64)
        self.assertEqual(report["cleanup_steps"], list(rd.CLEANUP_STEPS))
        self.assert_cleaned(report, server, leftovers)
        self.assertNotIn(PASSWORD, out)
        # probes: reference UPDATE/DELETE and account DELETE ran on the target only
        for sql in (rd.SQL_REF_UPDATE_PROBE, rd.SQL_REF_DELETE_PROBE, rd.SQL_ACCOUNT_FK_PROBE):
            self.assertEqual({db for db, s in server.calls if s == sql}, {TARGET})

    def test_seed_and_cleanup_are_single_transactions_with_fixed_lock_order(self):
        result, _, report, server, _, _ = run_main()
        self.assertEqual(result, 0, report)
        source = [(n, sql) for n, db, sql, _ in server.timeouts if db == rd.SOURCE_DB]
        seed_conn = {n for n, sql in source if sql in (rd.SQL_SEED_ACCOUNT, rd.SQL_SEED_REFERENCE)}
        self.assertEqual(len(seed_conn), 1)
        self.assertIn((seed_conn.pop(), rd.SOURCE_DB), server.commits)
        order = [rd.SQL_LOCK_ACCOUNTS, rd.SQL_LOCK_REFERENCES, rd.SQL_ACCOUNT_ROWS, rd.SQL_REF_ROWS,
                 rd.SQL_ACCOUNT_CHILD_KEYS, rd.SQL_LOCK_OWN_ACCOUNT, rd.SQL_TRUNCATE_REFERENCES,
                 rd.SQL_DELETE_OWN_ACCOUNT]
        (conn,) = {n for n, sql in source if sql == rd.SQL_TRUNCATE_REFERENCES}
        session = [sql for n, sql in source if n == conn]
        self.assertEqual([s for s in session if s in order], order + [rd.SQL_ACCOUNT_ROWS, rd.SQL_REF_ROWS])
        self.assertEqual(sum(c == (conn, rd.SOURCE_DB) for c in server.commits), 1)
        self.assertEqual(server.deleted_accounts, [report["ledger"]["account_id"]])

    # --- baseline and source schema -------------------------------------------------------------------------
    def test_foreign_account_or_reference_at_start_blocks_and_is_never_touched(self):
        for key, row in (("accounts", {"id": 501, "cliente": "foreign", "json": "{}"}),
                         ("refs", {"id": 502, "account_id": 501, "request_id": "foreign", "json": "{}"})):
            with self.subTest(table=key):
                server = FakeServer()
                server.dbs[rd.SOURCE_DB][key].append(row)
                report, server = FlowTests.blocked_before_seed(self, server, expect="source_not_empty_ci_baseline")
                self.assertEqual(server.dbs[rd.SOURCE_DB][key], [row])
                self.assertTrue(report["cleanup"]["final_state"].startswith("FAILED:StepFailed:source_"))
                self.assertEqual(report["cleanup_status"], "BLOCKED")

    def test_source_reference_schema_defects_fail_before_any_seed(self):
        cases = [("ref_trigger", [("D", rd.TRIGGER_TYPE, rd.REF_FUNCTION)], "source_reference_trigger_not_enabled"),
                 ("ref_keys", [], "source_reference_keys"),
                 ("ref_function", [], "source_reference_function")]
        for key, value, code in cases:
            with self.subTest(code=code):
                server = FakeServer()
                server.dbs[rd.SOURCE_DB]["facts"][key] = value
                result, _, report, server, _, _ = run_main(server=server)
                self.assertEqual((result, report["error"]), (1, f"StepFailed: {code}"))
                self.assertEqual((server.sql(rd.SQL_SEED), server.sql(rd.SQL_SEED_ACCOUNT)), ([], []))

    # --- target catalog -------------------------------------------------------------------------------------
    def test_target_reference_catalog_defects_fail_and_still_clean_up(self):
        fk = "fk_account_title_references_account_id"

        def keys(f, definition):
            f["ref_keys"] = [(n, t, definition if n == fk else d) for n, t, d in f["ref_keys"]]
        cases = [
            (lambda f: f.update(ref_columns=f["ref_columns"][:-1]), "target_reference_columns"),
            (lambda f: f.update(ref_checks=f["ref_checks"][1:]), "target_reference_check_constraints"),
            (lambda f: keys(f, "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE"),
             "target_reference_keys"),
            (lambda f: keys(f, "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT NOT VALID"),
             "target_reference_keys"),
            (lambda f: f.update(ref_keys=[k for k in f["ref_keys"] if k[0] != fk]), "target_reference_keys"),
            (lambda f: f.update(ref_indexes=[(n, d.replace("UNIQUE ", "")) for n, d in f["ref_indexes"]]),
             "target_reference_indexes"),
            (lambda f: f.update(ref_indexes=f["ref_indexes"][:-1]), "target_reference_indexes"),
            (lambda f: f.update(ref_function=[]), "target_reference_function"),
            (lambda f: f.update(ref_trigger=[("D", rd.TRIGGER_TYPE, rd.REF_FUNCTION)]),
             "target_reference_trigger_not_enabled"),
        ]
        for mutate, code in cases:
            with self.subTest(code=code):
                server = FakeServer()
                server.hooks["after_restore"] = lambda s=server, m=mutate: m(s.dbs[TARGET]["facts"])
                result, _, report, server, _, leftovers = run_main(server=server)
                self.assertEqual((result, report["outcome"], report["error"]), (1, "FAIL", f"StepFailed: {code}"))
                self.assert_cleaned(report, server, leftovers)

    def test_reference_check_definitions_are_strict_with_no_allowlist(self):
        name = sorted(rd.REF_CHECKS)[0]
        in_list = "CHECK (((assigned_via)::text = ANY ((ARRAY['import_v2'::character varying])::text[])))"

        def redefine(f):
            f["ref_checks"] = [(n, in_list if n == name else d) for n, d in f["ref_checks"]]
        server = FakeServer()
        server.hooks["after_restore"] = lambda: redefine(server.dbs[TARGET]["facts"])
        result, _, report, server, _, leftovers = run_main(server=server)
        self.assertEqual((result, report["error"]), (1, "StepFailed: schema_source_target_differ"))
        comparison = report["schema_comparison"]
        self.assertEqual((comparison["differing_keys"], comparison["decision"]), (["ref_checks"], "DIFFERENT"))
        self.assertEqual(comparison["accepted_known_check_renderings"], [])
        self.assertFalse(set(rd.KNOWN_CHECK_RENDERINGS) & rd.REF_CHECKS)
        self.assert_cleaned(report, server, leftovers)

    def test_reference_function_difference_reports_only_its_bounded_body(self):
        changed = REF_FUNCTION_SRC.replace("BEGIN", "BEGIN NULL;")
        server = FakeServer()
        server.hooks["after_restore"] = lambda: server.dbs[TARGET]["facts"].update(
            ref_function=[("plpgsql", "trigger", changed)])
        result, _, report, _, _, _ = run_main(server=server)
        self.assertEqual(report["error"], "StepFailed: schema_source_target_differ")
        comparison = report["schema_comparison"]
        self.assertEqual(comparison["differing_keys"], ["ref_function"])
        self.assertNotIn("function_bodies", comparison)
        self.assertEqual(comparison["ref_function_bodies"]["name"], rd.REF_FUNCTION)
        self.assertEqual(comparison["ref_function_bodies"]["target"][0]["text"], changed)

    def test_facts_match_is_strict_for_every_reference_fact(self):
        self.assertEqual(rd.facts_match(catalog(), catalog()), (True, []))
        for key in ("ref_columns", "ref_checks", "ref_keys", "ref_indexes", "ref_function", "ref_trigger"):
            with self.subTest(fact=key):
                target = catalog()
                target[key] = list(reversed(target[key])) if len(target[key]) > 1 else []
                self.assertEqual(rd.facts_match(catalog(), target), (False, []))

    # --- row survival and probes ----------------------------------------------------------------------------
    def test_restored_rows_and_probes_are_required_for_pass(self):
        def tamper(table):
            def go(s):
                s.dbs[TARGET][table][0]["json"] += " "
            return go
        cases = [
            (tamper("accounts"), "target_reference_rows_not_restored_exactly"),
            (tamper("refs"), "target_reference_rows_not_restored_exactly"),
            (lambda s: s.dbs[TARGET]["refs"].clear(), "target_reference_rows_not_restored_exactly"),
            (lambda s: s.dbs[TARGET]["accounts"].append({"id": 900, "cliente": "x", "json": "{}"}),
             "target_reference_rows_not_restored_exactly"),
            (lambda s: s.dbs[TARGET].update(ref_update_rejected=False), "target_reference_update_not_rejected"),
            (lambda s: s.dbs[TARGET].update(ref_delete_rejected=False), "target_reference_delete_not_rejected"),
            (lambda s: s.dbs[TARGET].update(fk_restricts=False), "target_account_delete_not_restricted"),
        ]
        for hook, code in cases:
            with self.subTest(code=code):
                result, _, report, server, _, leftovers = self.run_with(hook)
                self.assertEqual((result, report["error"]), (1, f"StepFailed: {code}"))
                self.assert_cleaned(report, server, leftovers)
                self.assertNotIn("delta", report)

    def test_reference_seed_fk_failure_fails_and_leaves_nothing(self):
        server = FakeServer()
        server.fail.add("ref_seed_fk")
        result, _, report, server, _, leftovers = run_main(server=server)
        self.assertEqual((result, report["outcome"]), (1, "FAIL"))
        self.assertEqual(report["error"], "FakePgError: insert violates foreign key constraint")
        self.assertNotIn("seed_reference", report)
        self.assertEqual(report["cleanup"]["source_reference_and_account"], "ABSENT")  # rolled back with the seed
        self.assertEqual(report["cleanup"]["final_state"], "VERIFIED")
        self.assertEqual(server.sql("CREATE DATABASE "), [])
        source = server.dbs[rd.SOURCE_DB]
        self.assertEqual((source["accounts"], source["refs"], server.deleted_accounts), ([], [], []))
        self.assertEqual(leftovers, [])

    # --- cleanup refusals (never PASS, never delete unknown rows) --------------------------------------------
    def refused(self, hook, status, error=None, when="after_restore", server=None):
        result, _, report, server, _, _ = self.run_with(hook, when, server)
        self.assertNotEqual(result, 0)
        self.assertEqual(report["cleanup_status"], "BLOCKED")
        self.assertEqual(report["cleanup"]["source_reference_and_account"], status)
        if error:
            self.assertEqual(report["error"], error)
        self.assertEqual((server.ref_truncated, server.deleted_accounts), (0, []))
        self.assertTrue(report["cleanup"]["final_state"].startswith("FAILED:StepFailed:source_"))
        self.assertEqual(report["cleanup"]["target_database"], "DROPPED_VERIFIED")
        return report, server

    def test_foreign_account_added_during_the_run_refuses_cleanup(self):
        foreign = {"id": 777, "cliente": "foreign", "json": "{}"}
        report, server = self.refused(lambda s: s.dbs[rd.SOURCE_DB]["accounts"].append(foreign),
                                      "REFUSED:unexpected_source_rows", "StepFailed: source_reference_rows_changed")
        self.assertIn(foreign, server.dbs[rd.SOURCE_DB]["accounts"])
        self.assertEqual(len(server.dbs[rd.SOURCE_DB]["refs"]), 1)

    def test_foreign_reference_added_during_the_run_refuses_cleanup(self):
        def add(s):
            own = s.dbs[rd.SOURCE_DB]["accounts"][0]["id"]
            s.dbs[rd.SOURCE_DB]["refs"].append({"id": 778, "account_id": own, "request_id": "x", "json": "{}"})
        report, server = self.refused(add, "REFUSED:unexpected_source_rows", "StepFailed: source_reference_rows_changed")
        self.assertEqual(len(server.dbs[rd.SOURCE_DB]["refs"]), 2)

    def test_modified_own_rows_refuse_cleanup(self):
        for table in ("accounts", "refs"):
            with self.subTest(table=table):
                def tamper(s, t=table):
                    s.dbs[rd.SOURCE_DB][t][0]["json"] += "x"
                self.refused(tamper, "REFUSED:unexpected_source_rows", "StepFailed: source_reference_rows_changed")

    def test_own_account_referenced_elsewhere_is_never_deleted(self):
        def reference(s):
            s.child_rows[("account_events", "account_id")] = {s.dbs[rd.SOURCE_DB]["accounts"][0]["id"]}
        report, server = self.refused(reference, "REFUSED:account_still_referenced")
        self.assertEqual(report["outcome"], "BLOCKED")  # the drill passed; cleanup uncertainty blocks PASS
        source = server.dbs[rd.SOURCE_DB]
        self.assertEqual((len(source["accounts"]), len(source["refs"])), (1, 1))

    def test_unexpected_foreign_key_shape_refuses_cleanup(self):
        for keys in ([REF_KEY, ('bad"name', "account_id", 1)], [REF_KEY, ("account_events", "account_id", 2)],
                     [REF_KEY, ("account_events", "Account_Id", 1)]):
            with self.subTest(keys=keys):
                server = FakeServer()
                server.child_keys = keys
                self.refused(None, "REFUSED:unexpected_account_references", server=server)

    def test_inexact_delete_rolls_back_the_truncate(self):
        server = FakeServer()
        server.fail.add("delete_noop")
        result, _, report, server, _, _ = run_main(server=server)
        self.assertEqual((result, report["outcome"]), (2, "BLOCKED"))
        self.assertEqual(report["cleanup"]["source_reference_and_account"], "REFUSED:cleanup_not_exact")
        source = server.dbs[rd.SOURCE_DB]
        self.assertEqual((len(source["accounts"]), len(source["refs"])), (1, 1))  # TRUNCATE undone by rollback
        self.assertEqual(server.deleted_accounts, [])

    def test_cleanup_is_absent_when_rows_already_gone_and_refuses_non_zero_baseline(self):
        result, _, report, _, _, _ = self.run_with(
            lambda s: s.dbs[rd.SOURCE_DB].update(accounts=[], refs=[]))
        self.assertEqual(report["cleanup"]["source_reference_and_account"], "ABSENT")
        self.assertEqual(report["error"], "StepFailed: source_reference_rows_changed")
        ledger = rd.Ledger(account_seed_attempted=True, initial_account_count=1, initial_reference_count=0,
                           account_id=1, reference_id=2, account_fingerprint="a" * 64, reference_fingerprint="b" * 64,
                           marker="m")
        server = FakeServer()
        server.dbs[rd.SOURCE_DB]["accounts"].append({"id": 1, "cliente": "m", "json": "{}"})
        ctx = rd.check_gate(dict(GOOD_ENV, RUNNER_TEMP=tempfile.gettempdir()))
        status = rd.remove_own_reference_and_account(rd.Postgres(ctx, server.connect), ledger)
        self.assertEqual(status, "REFUSED:unexpected_source_rows")
        self.assertEqual(server.deleted_accounts, [])


class FakeProc:
    def __init__(self, argv, stdout, stderr, polls_until_exit, returncode=0, write_out=b"", write_err=b""):
        self.argv, self.pid, self.returncode, self._polls, self.killed = argv, 4242, None, polls_until_exit, False
        self._code = returncode
        stdout.write(write_out)
        stdout.flush()
        stderr.write(write_err)
        stderr.flush()

    def poll(self):
        if self._polls is not None:
            self._polls -= 1
            if self._polls <= 0:
                self.returncode = self._code
        return self.returncode

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.returncode


class RunnerTests(unittest.TestCase):
    ARGV = rd.exec_argv(rd.Context("localhost", "auneron", PASSWORD, CID, "1", "1", "/tmp"), "psql", "auneron_test",
                        "-tAc", "SELECT 1")

    def make(self, **proc_kwargs):
        procs, steps, now = [], [], [0.0]

        def popen(argv, **kw):
            procs.append(FakeProc(argv, kw["stdout"], kw["stderr"], **proc_kwargs))
            return procs[-1]

        def sleep(seconds):
            now[0] += seconds

        runner = rd.Runner(rd.Redactor((PASSWORD,)), steps, popen=popen, clock=lambda: now[0], sleep=sleep,
                           forbidden=(PASSWORD,))
        return runner, procs, steps

    def call(self, runner, **kwargs):
        with mock.patch.object(rd.os, "killpg", create=True) as killpg:
            try:
                return runner("psql", self.ARGV, env={}, **kwargs)
            finally:
                self.killpg_calls = killpg.call_count

    def test_ok_returns_capped_output(self):
        runner, _, steps = self.make(polls_until_exit=1, write_out=b"1\n")
        self.assertEqual(self.call(runner, timeout=5).out, "1\n")
        self.assertEqual(steps[0]["status"], "OK")

    def test_timeout_kills_and_aborts(self):
        runner, procs, steps = self.make(polls_until_exit=None)
        with self.assertRaises(rd.StepAborted):
            self.call(runner, timeout=1)
        self.assertEqual(steps[0]["status"], "TIMEOUT")
        self.assertTrue(self.killpg_calls == 1 or procs[0].killed)

    def test_output_caps_abort(self):
        runner, _, steps = self.make(polls_until_exit=None, write_err=b"x" * (rd.STREAM_CAP + 1))
        with self.assertRaises(rd.StepAborted):
            self.call(runner, timeout=5)
        self.assertEqual(steps[0]["status"], "OUTPUT_CAP")
        with tempfile.TemporaryDirectory() as temp:
            dump = Path(temp) / "d"
            runner, _, steps = self.make(polls_until_exit=None, write_out=b"PGDMP" + b"0" * 64)
            with self.assertRaises(rd.StepAborted):
                self.call(runner, timeout=5, stdout_path=dump, cap=32)
            self.assertEqual(steps[0]["status"], "OUTPUT_CAP")

    def test_nonzero_fails_with_redacted_tail(self):
        runner, _, steps = self.make(polls_until_exit=1, returncode=2, write_err=f"bad {PASSWORD} {DSN}".encode())
        with self.assertRaises(rd.StepFailed):
            self.call(runner, timeout=5)
        self.assertNotIn(PASSWORD, json.dumps(steps))

    def test_secret_in_argv_is_refused_before_popen(self):
        runner, procs, _ = self.make(polls_until_exit=1)
        with self.assertRaises(ValueError):
            runner("psql", self.ARGV[:-1] + [f"SELECT '{PASSWORD}'"], env={}, timeout=5)
        self.assertEqual(procs, [])


class ArgvAndRedactionTests(unittest.TestCase):
    def test_check_argv_allowlist(self):
        ctx = rd.Context("localhost", "auneron", PASSWORD, CID, "1", "1", "/tmp")
        rd.check_argv(rd.exec_argv(ctx, "pg_restore", TARGET, "--exit-on-error", stdin=True))
        rd.check_argv(["docker", "inspect", "--type", "container", "--format", rd.FMT_CONTAINER, CID])
        good = rd.exec_argv(ctx, "psql", "auneron_test")
        bad = [
            ["docker", "inspect", "--format", "{{json .Config.Env}}", CID],
            ["docker", "inspect", "--type", "container", "--format", "{{json .}}", CID],
            ["docker", "rm", "-f", CID], ["docker", "run", "postgres:17"], ["bash", "-c", "x"],
            ["docker", "exec", "-e", f"PGPASSWORD={PASSWORD}"] + good[4:],
            good[:5] + ["sh"] + good[6:],
            good[:6] + ["-h", "localhost"] + good[8:],
            good + ["-e", "X=1"], good + ["--env-file", "f"],
        ]
        for argv in bad:
            with self.subTest(argv=argv), self.assertRaises((ValueError, TypeError)):
                rd.check_argv(argv)

    def test_redactor(self):
        redact = rd.Redactor((PASSWORD,))
        self.assertNotIn(PASSWORD, redact(f"x {PASSWORD} postgresql://u:other@h/d"))
        self.assertNotIn("other", redact("postgresql://u:other@h/d"))
        self.assertTrue(redact("y" * 5000).startswith("[truncated]"))


class StaticTests(unittest.TestCase):
    SOURCE = (HERE / "audit_recovery_delta.py").read_text(encoding="utf-8")
    TREE = ast.parse(SOURCE)

    def code_strings(self):
        docstrings = {id(n.value) for n in ast.walk(self.TREE) if isinstance(n, ast.Expr)
                      and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        return [n.value for n in ast.walk(self.TREE)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]

    def test_top_level_imports_are_stdlib_and_psycopg_is_lazy(self):
        top = {a.name.split(".")[0] for n in self.TREE.body if isinstance(n, ast.Import) for a in n.names}
        top |= {n.module.split(".")[0] for n in self.TREE.body if isinstance(n, ast.ImportFrom)}
        self.assertTrue(top <= set(sys.stdlib_module_names) | {"__future__"}, top)
        self.assertNotIn("psycopg", sys.modules.get("audit_recovery_delta").__dict__)
        lazy = [f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                for n in ast.walk(f) if isinstance(n, ast.Import) and n.names[0].name == "psycopg"]
        self.assertEqual(lazy, ["open"])

    def test_no_forbidden_operations_in_code(self):
        text = " ".join(self.code_strings()).upper()
        for word in ("DISABLE TRIGGER", "CASCADE", "PRUNE", "ALTER TABLE", ".ENV", "DOCKER RM", "VOLUME"):
            self.assertNotIn(word, text)
        self.assertEqual(sum("TRUNCATE TABLE" in s.upper() for s in self.code_strings()), 2)
        self.assertEqual(rd.SQL_TRUNCATE, "TRUNCATE TABLE account_data_entry_audit_events")
        self.assertEqual(rd.SQL_TRUNCATE_REFERENCES, "TRUNCATE TABLE account_title_references")
        for word in ("TRUNCATE TABLE ACCOUNTS", "TRUNCATE TABLE USERS", "TRUNCATE ACCOUNTS", "TRUNCATE USERS",
                     "DISABLE", "SESSION_REPLICATION_ROLE", "DROP CONSTRAINT", "DROP TRIGGER", "ONLY ACCOUNTS"):
            self.assertNotIn(word, text)
        # exactly two DELETE FROM accounts statements: the restored-target FK probe and the owned-id cleanup
        deletes = sorted(s for s in self.code_strings() if "DELETE FROM ACCOUNTS" in s.upper())
        self.assertEqual(deletes, sorted([rd.SQL_ACCOUNT_FK_PROBE, rd.SQL_DELETE_OWN_ACCOUNT]))
        self.assertEqual(rd.SQL_DELETE_OWN_ACCOUNT, "DELETE FROM accounts WHERE id = %s AND cliente = %s RETURNING id")

    def test_destructive_statements_live_only_in_their_guards(self):
        def users(name):
            return sorted({f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                           for n in ast.walk(f) if isinstance(n, ast.Name) and n.id == name})
        self.assertEqual(users("SQL_TRUNCATE"), ["truncate_own_row"])
        self.assertEqual(users("SQL_TRUNCATE_REFERENCES"), ["remove_own_reference_and_account"])
        self.assertEqual(users("SQL_DELETE_OWN_ACCOUNT"), ["remove_own_reference_and_account"])
        self.assertEqual(users("SQL_ACCOUNT_FK_PROBE"), ["drill"])
        self.assertEqual(users("SQL_SEED_ACCOUNT"), ["drill"])
        self.assertEqual(users("SQL_SEED_REFERENCE"), ["drill"])
        dynamic = sorted({f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                          for n in ast.walk(f) if isinstance(n, ast.JoinedStr)
                          and any(isinstance(v, ast.Constant) and "count(*) FROM" in str(v.value) for v in n.values)})
        self.assertEqual(dynamic, ["remove_own_reference_and_account"])
        drops = sorted({f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                        for n in ast.walk(f) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and "DROP DATABASE" in n.value})
        self.assertEqual(drops, ["drop_target"])

    def test_drop_budget_is_scoped_and_nothing_escalates(self):
        users = sorted({f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                        for n in ast.walk(f) if isinstance(n, ast.Name) and n.id == "SQL_DROP_BUDGET"})
        self.assertEqual(users, ["drop_target"])
        strings = [s.upper() for s in self.code_strings()]
        text = " ".join(strings)
        for word in ("PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND", "(FORCE)", "WITH (FORCE", "ALTER SYSTEM",
                     "ALTER DATABASE", "ALTER ROLE", "SET LOCK_TIMEOUT", "STATEMENT_TIMEOUT = 0",
                     "STATEMENT_TIMEOUT=0 ", "RESET ", "SET SESSION"):
            self.assertNotIn(word, text)
        self.assertEqual(sum("SET STATEMENT_TIMEOUT" in s for s in strings), 1)
        self.assertEqual(sum("STATEMENT_TIMEOUT=30000" in s for s in strings), 1)

    def test_workflow_wiring(self):
        workflow = (HERE.parent / "workflows" / "backend-ci.yml").read_text(encoding="utf-8")
        order = ["Testar executor do delta de auditoria (offline)", "Aplicar migrações",
                 "Executar testes de confirmação de importação (PostgreSQL)", "Diagnóstico operacional do banco",
                 "Delta de recuperação da auditoria de cadastro", "Publicar relatório do delta de auditoria",
                 "Instalar Chromium do Playwright", "Executar E2E frontend autenticado", "Confirmar limpeza do banco"]
        positions = [workflow.index(f"- name: {name}") for name in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("CI_POSTGRES_CONTAINER_ID: ${{ job.services.postgres.id }}", workflow)
        self.assertIn('--report "${{ runner.temp }}/area2-audit-recovery.json"', workflow)
        self.assertIn("path: ${{ runner.temp }}/area2-audit-recovery.json", workflow)
        self.assertEqual(workflow.count("retention-days: 7"), 1)
        self.assertEqual(workflow.count("uses: actions/upload-artifact@v4"), 1)
        self.assertIn('assert result["account_data_entry_audit_events"] == 0, result', workflow)
        self.assertIn('assert result["account_title_references"] == 0, result', workflow)
        self.assertIn('assert result["accounts"] == 0, result', workflow)
        self.assertNotIn(".dump", workflow)


if __name__ == "__main__":
    unittest.main()
