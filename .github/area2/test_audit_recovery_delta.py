"""Offline tests for audit_recovery_delta.py: stdlib + fakes only; no child process, DB, Docker or network."""

import ast
import contextlib
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


def boom(*args, **kwargs):
    raise AssertionError("no child process or connection may be started")


def good_facts():
    return {"columns": [(c,) for c in rd.COLUMNS],
            "checks": [(name, f"CHECK ({name})") for name in sorted(rd.CHECKS)],
            "index": [(f"CREATE INDEX {rd.INDEX} ON public.{rd.TABLE} {rd.INDEX_SUFFIX}",)],
            "function": [("plpgsql", "trigger", FUNCTION_SRC)],
            "trigger": [("O", rd.TRIGGER_TYPE, rd.FUNCTION)]}


class FakePgError(Exception):
    def __init__(self, sqlstate, message):
        super().__init__(message)
        self.sqlstate = sqlstate


class Cursor:
    def __init__(self, result):
        self._result = result

    def fetchall(self):
        return list(self._result)


class FakeServer:
    """In-memory stand-in for the service PostgreSQL. Writes apply immediately (no MVCC)."""

    def __init__(self):
        self.system_id, self.docker_system_id = "7400000000000000001", "7400000000000000001"
        self.dbs = {rd.SOURCE_DB: self.new_db(16384)}
        self.counts = {"users": 0, "accounts": 0, "import_confirmation_receipts": 0}
        self.calls, self.dropped, self.truncated, self.next_oid, self.next_id = [], [], 0, 30000, 1
        self.hooks, self.fail = {}, set()

    def new_db(self, oid, comment=None, facts=None):
        return {"oid": oid, "comment": comment, "rows": [], "facts": facts or good_facts(),
                "revision": rd.HEAD_REVISION, "tables": rd.TABLE_COUNT, "update_rejected": True,
                "delete_rejected": True}

    def connect(self, **kw):
        assert kw["hostaddr"] == "127.0.0.1" and kw["port"] == 5432 and kw["password"] == PASSWORD
        assert kw["options"] == rd.CONNECT_OPTIONS and kw["dbname"] in self.dbs
        return FakeConn(self, kw["dbname"])

    def sql(self, kind):
        return [sql for _, sql in self.calls if sql.startswith(kind) or sql == kind]


class FakeConn:
    def __init__(self, server, db):
        self.server, self.db = server, db

    def execute(self, sql, params=None):
        s, d = self.server, self.server.dbs[self.db]
        s.calls.append((self.db, sql))
        facts = d["facts"]
        name = sql.split('"')[1] if '"' in sql else None
        if sql == rd.SQL_IDENTITY:
            return Cursor([(self.db, d["revision"], d["tables"])])
        if sql == rd.SQL_SYSTEM_ID:
            return Cursor([(s.system_id,)])
        if sql == rd.SQL_COUNTS:
            return Cursor([(len(d["rows"]), s.counts["users"], s.counts["accounts"],
                            s.counts["import_confirmation_receipts"])])
        if sql == rd.SQL_DB_BY_NAME:
            found = s.dbs.get(params[0])
            return Cursor([(found["oid"], found["comment"])] if found else [])
        if sql == rd.SQL_DB_BY_OID:
            return Cursor([(sum(1 for x in s.dbs.values() if x["oid"] == params[0]),)])
        for key, query in (("columns", rd.SQL_COLUMNS), ("checks", rd.SQL_CHECKS), ("index", rd.SQL_INDEX),
                           ("function", rd.SQL_FUNCTION), ("trigger", rd.SQL_TRIGGER)):
            if sql == query:
                return Cursor(facts[key])
        if sql == rd.SQL_ROWS:
            return Cursor([(r["id"], r["request_id"], r["json"]) for r in d["rows"]])
        if sql == rd.SQL_SEED:
            assert self.db == rd.SOURCE_DB
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
        if sql == rd.SQL_LOCK:
            return Cursor([])
        if sql == rd.SQL_TRUNCATE:
            assert self.db == rd.SOURCE_DB
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
            if "drop" in s.fail:
                raise FakePgError("55006", "database is being accessed by other users")
            s.dropped.append(name)
            del s.dbs[name]
            return Cursor([])
        if sql == f"SELECT count(*) FROM {rd.TABLE}":
            return Cursor([(len(d["rows"]),)])
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


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
                {"rows": source["rows"], "facts": source["facts"]}).encode()
            Path(stdout_path).write_bytes(payload)
            s.hooks.get("after_dump", lambda: None)()
            return rd.Result(0, "", "")
        if tool == "pg_restore":
            data = Path(stdin_path).read_bytes()
            assert data.startswith(b"PGDMP")
            restored = json.loads(data[5:])
            target = s.dbs[argv[argv.index("-d") + 1]]
            target["rows"] = [dict(r) for r in restored["rows"]]
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
        self.assertEqual(plan["head_revision"], "c3a8f1e6d204")


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
                                             "temp_files": "REMOVED", "final_state": "VERIFIED"})
        self.assertEqual((server.dropped, server.truncated), ([TARGET], 1))
        self.assertEqual(server.dbs[rd.SOURCE_DB]["rows"], [])
        self.assertEqual(list(server.dbs), [rd.SOURCE_DB])
        self.assertEqual(leftovers, [])

    def test_pass_proves_delta_and_cleans_only_owned_state(self):
        result, out, report, server, fake_run, leftovers = run_main()
        self.assertEqual((result, report["outcome"]), (0, "PASS"), report)
        delta = report["delta"]
        self.assertEqual((delta["tables"], delta["revision"], delta["columns"]), (38, "c3a8f1e6d204", 24))
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
        self.assertEqual(server.sql("CREATE DATABASE "), [])
        self.assertEqual((server.dropped, server.truncated), ([], 0))
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
        for key, value, code in (("revision", "b428d1a6c903", "source_head_not_c3a8f1e6d204"),
                                 ("tables", 37, "source_table_count_not_38")):
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
                self.assertEqual(server.truncated, 1)
                self.assertEqual(server.dbs[rd.SOURCE_DB]["rows"], [])
                self.assertEqual(leftovers, [])

    def test_non_custom_dump_fails(self):
        server = FakeServer()
        fake_run = FakeRun(server)
        fake_run.dump_bytes = b"-- plain SQL"
        result, _, report, _, _, _ = run_main(server=server, fake_run=fake_run)
        self.assertEqual((result, report["error"]), (1, "StepFailed: dump_not_custom_format"))


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
        self.assertEqual(sum("TRUNCATE TABLE" in s.upper() for s in self.code_strings()), 1)
        self.assertEqual(rd.SQL_TRUNCATE, "TRUNCATE TABLE account_data_entry_audit_events")

    def test_destructive_statements_live_only_in_their_guards(self):
        def users(name):
            return sorted({f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                           for n in ast.walk(f) if isinstance(n, ast.Name) and n.id == name})
        self.assertEqual(users("SQL_TRUNCATE"), ["truncate_own_row"])
        drops = sorted({f.name for f in ast.walk(self.TREE) if isinstance(f, ast.FunctionDef)
                        for n in ast.walk(f) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and "DROP DATABASE" in n.value})
        self.assertEqual(drops, ["drop_target"])

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
        self.assertNotIn(".dump", workflow)


if __name__ == "__main__":
    unittest.main()
