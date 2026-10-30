"""Offline tests for recovery_delta.py: fakes only, no backend import, no docker/DB/network."""

import ast
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recovery_delta as rd  # noqa: E402

HEAD = "f" * 40
RUN_ID = "a1d20261030t120000abcdef"
GOOD_ENV = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": rd.BRANCH_REF,
            "GITHUB_SHA": HEAD, "GITHUB_REPOSITORY": rd.REPOSITORY}
PG_IMG, BE_IMG = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def boom(*args, **kwargs):
    raise AssertionError("no subprocess may be started")


class FakeRun:
    """Scripted stand-in for Runner: responder(argv) -> (code, out). Enforces the real argv filter."""

    def __init__(self, responder):
        self.responder, self.calls, self.budget = responder, [], rd.FORWARD_BUDGET

    def __call__(self, name, argv, *, env, timeout, cwd=rd.REPO, check=True):
        rd.check_argv(argv)
        self.calls.append(argv)
        code, out = self.responder(argv)
        if check and code != 0:
            raise rd.StepFailed(name)
        return rd.Result(code, out, "")


def git_responder(overrides=None):
    answers = {"rev-parse HEAD": HEAD, "merge-base": 0, "diff": "\n".join(rd.ALLOWED_FILES),
               "status": "", "rev-parse blob": "abc", "hash-object": "abc"}
    answers.update(overrides or {})

    def respond(argv):
        if argv[1] == "rev-parse":
            return 0, answers["rev-parse HEAD" if argv[2] == "HEAD" else "rev-parse blob"]
        if argv[1] == "merge-base":
            return answers["merge-base"], ""
        return 0, answers[argv[1]]
    return respond


class Args:
    def __init__(self, baseline=rd.BASELINE):
        self.baseline = baseline


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(GOOD_ENV, RUNNER_TEMP=self.tmp)

    def test_default_is_dry_and_starts_nothing(self):
        self.assertEqual(rd.main([], environ={}, popen=boom), 0)

    def test_execute_refused_outside_github_actions(self):
        argv = ["--execute", "--baseline", rd.BASELINE, "--report", os.path.join(self.tmp, "r.json")]
        self.assertEqual(rd.main(argv, environ={"RUNNER_TEMP": self.tmp}, popen=boom), 2)

    def test_gate_refusals(self):
        cases = {"baseline_mismatch": (Args("0" * 40), self.env),
                 "wrong_repository": (Args(), dict(self.env, GITHUB_REPOSITORY="someone/fork")),
                 "wrong_branch": (Args(), dict(self.env, GITHUB_REF="refs/heads/main")),
                 "event_not_push": (Args(), dict(self.env, GITHUB_EVENT_NAME="pull_request")),
                 "github_sha_missing": (Args(), dict(self.env, GITHUB_SHA="")),
                 "runner_temp_invalid": (Args(), dict(self.env, RUNNER_TEMP=str(rd.REPO / "x")))}
        for code, (args, env) in cases.items():
            with self.subTest(code), self.assertRaisesRegex(rd.GateError, code):
                rd.check_gate(args, env)

    def test_report_must_live_in_runner_temp(self):
        argv = ["--execute", "--baseline", rd.BASELINE, "--report", str(rd.REPO / "report.json")]
        self.assertEqual(rd.main(argv, environ=self.env, popen=boom), 2)


class RepoPreflightTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / "backend" / "scripts").mkdir(parents=True)
        for tool in rd.TOOLS:
            (self.repo / "backend" / "scripts" / tool).write_text("x")

    def check(self, overrides=None):
        return rd.preflight_repo(FakeRun(git_responder(overrides)), GOOD_ENV, {}, {}, repo=self.repo)

    def test_clean_repo_passes_and_hashes_tools(self):
        report = {}
        run = FakeRun(git_responder())
        rd.preflight_repo(run, GOOD_ENV, {}, report, repo=self.repo)
        self.assertEqual(sorted(report["tool_sha256"]), sorted(rd.TOOLS))
        self.assertIn(["git", "merge-base", "--is-ancestor", rd.BASELINE, "HEAD"], run.calls)

    def test_review_fix_commits_allowed_when_baseline_is_ancestor(self):
        # No exact-parent requirement: only ancestry + cumulative 3-file diff + tool blobs + clean HEAD.
        self.assertEqual(self.check(), HEAD)

    def test_refusals(self):
        cases = {"head_not_github_sha": {"rev-parse HEAD": "e" * 40},
                 "baseline_not_ancestor": {"merge-base": 1},
                 "diff_not_exactly_allowed_files": {"diff": "\n".join(rd.ALLOWED_FILES + ("backend/app/x.py",))},
                 "worktree_dirty": {"status": "?? stray"},
                 "tool_not_baseline": {"hash-object": "changed"}}
        for code, override in cases.items():
            with self.subTest(code), self.assertRaisesRegex(rd.GateError, code):
                self.check(override)

    def test_env_file_presence_refused_without_reading(self):
        (self.repo / "backend" / ".env").write_text("SECRET")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("read")), \
                self.assertRaisesRegex(rd.GateError, "backend_env_file_present"):
            self.check()


class InspectFilterTests(unittest.TestCase):
    def test_raw_inspect_refused(self):
        for argv in (["docker", "container", "inspect", "x"], ["docker", "inspect", "x"],
                     ["docker", "inspect", "--format", "{{json .Config.Env}}", "x"],
                     ["docker", "inspect", "--format", "{{json .}}", "x"],
                     ["docker", "image", "inspect", "-f", "{{.Id}}", "x"],
                     ["docker", "image", "inspect", "--format={{json .}}", "x"],
                     ["docker", "image", "inspect", "--format", rd.FMT_ID, "--format", "{{json .}}", "x"],
                     ["docker", "image", "inspect", "x", "--format"]):
            with self.subTest(argv), self.assertRaises(ValueError):
                rd.check_argv(argv)

    def test_allowed_formats_carry_only_ids_names_label_image(self):
        for fmt in rd.INSPECT_FORMATS:
            self.assertNotIn("Env", fmt)
            self.assertNotIn("json", fmt)
            rd.check_argv(["docker", "container", "inspect", "--format", fmt, "x"])

    def test_runner_refuses_raw_inspect_before_spawning(self):
        run = rd.Runner(rd.Redactor(), [], 0.0, popen=boom, clock=lambda: 0.0)
        with self.assertRaises(ValueError):
            run("probe", ["docker", "container", "inspect", "auneron-postgres"], env={}, timeout=5)


class DockerPreflightTests(unittest.TestCase):
    def preflight(self, existing=(), port_free=True, disk=10 * 1024 ** 3):
        def respond(argv):
            if argv[2:3] == ["inspect"]:
                return (0 if argv[-1] in existing else 1), ""
            return 0, ""
        run = FakeRun(respond)
        rd.preflight_docker(run, {}, rd.Ledger("r1"), {"RUNNER_TEMP": "/tmp"}, {},
                            port_free=lambda p: port_free, disk_free=lambda p: disk)
        return run

    def test_fresh_daemon_passes_and_probes_request_only_ids(self):
        run = self.preflight()
        probes = [c for c in run.calls if c[2:3] == ["inspect"]]
        self.assertEqual(len(probes), 5)
        self.assertTrue(all(c[3] == "--format" and c[4] in (rd.FMT_ID, rd.FMT_NAME) for c in probes))

    def test_foreign_fixed_resources_abort(self):
        for name in (rd.PG_CONTAINER, rd.SMOKE_CONTAINER, rd.BACKEND_IMAGE):
            with self.subTest(name), self.assertRaisesRegex(rd.GateError, "foreign_resource_present"):
                self.preflight(existing=(name,))

    def test_port_and_disk(self):
        with self.assertRaisesRegex(rd.GateError, "port_busy"):
            self.preflight(port_free=False)
        with self.assertRaisesRegex(rd.GateError, "disk_below_floor"):
            self.preflight(disk=rd.DISK_FLOOR - 1)


class RedactionAndArgvTests(unittest.TestCase):
    def test_dsn_and_registered_secrets_redacted(self):
        redact = rd.Redactor()
        redact.add("s3cr3t-api-key")
        text = redact("postgresql+psycopg://auneron:pw-123@127.0.0.1:55434/auneron_test key=s3cr3t-api-key")
        self.assertNotIn("pw-123", text)
        self.assertNotIn("s3cr3t-api-key", text)
        self.assertTrue(redact("x" * 5000).startswith("[truncated]"))

    def test_backend_env_is_minimal_and_explicit(self):
        env = rd.backend_env({"PATH": "/bin"}, app_env="test", database="auneron_test", password="P", api_key="K")
        self.assertEqual(env["DATABASE_URL"], "postgresql+psycopg://auneron:P@127.0.0.1:55434/auneron_test")
        self.assertEqual((env["INTELLIGENCE_MODE"], env["MAINTENANCE_ENABLED"]), ("off", "false"))
        self.assertNotIn("GITHUB_TOKEN", rd.base_env())

    def test_postgres_password_never_in_argv(self):
        ledger = rd.Ledger("r1", pg_image_id=PG_IMG)
        argv = rd.postgres_run_argv(ledger)
        self.assertIn("POSTGRES_PASSWORD", argv)
        self.assertFalse(any(a.startswith("POSTGRES_PASSWORD=") for a in argv))
        self.assertIn(f"{rd.LABEL}=r1", argv)
        self.assertEqual(argv[-1], ledger.pg_image_id)


class FakeProc:
    """Writes into the REAL spool files the Runner hands to popen; hangs until killed if asked."""

    def __init__(self, stdout, stderr, code=0, out=b"", err=b"", hang=False):
        stdout.write(out)
        stderr.write(err)
        stdout.flush()
        stderr.flush()
        self.pid, self.code, self.hang, self.killed, self.returncode = 4242, code, hang, False, None

    def poll(self):
        if self.hang and not self.killed:
            return None
        self.returncode = -9 if self.killed else self.code
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()

    def kill(self):
        self.killed = True


def fake_popen(holder=None, **behaviour):
    def popen(argv, **kwargs):
        assert kwargs["stdout"] is not subprocess.PIPE and kwargs["start_new_session"] is True
        proc = FakeProc(kwargs["stdout"], kwargs["stderr"], **behaviour)
        if holder is not None:
            holder.append(proc)
        return proc
    return popen


class RunnerTests(unittest.TestCase):
    def runner(self, popen, clock=lambda: 0.0, redact=None):
        steps = []
        return rd.Runner(redact or rd.Redactor(), steps, 0.0, popen=popen, clock=clock, sleep=lambda s: None,
                         spool_dir=tempfile.mkdtemp()), steps

    def killpg_for(self, procs):
        def killpg(pid, sig):
            for proc in procs:
                proc.killed = True
        return mock.patch.object(os, "killpg", killpg, create=True)

    def test_timeout_kills_group_and_aborts(self):
        procs, ticks = [], iter(range(0, 10 ** 6))
        with self.killpg_for(procs):
            run, steps = self.runner(fake_popen(procs, hang=True), clock=lambda: float(next(ticks)))
            with self.assertRaisesRegex(rd.StepAborted, "timeout"):
                run("x", ["docker", "ps"], env={}, timeout=5)
        self.assertTrue(procs[0].killed)
        self.assertEqual(steps[-1]["status"], "ABORTED_TIMEOUT")

    def test_running_stream_overflow_kills_group_and_aborts(self):
        procs = []
        big = b"Z" * (rd.STREAM_CAP + 1)
        with self.killpg_for(procs):
            run, steps = self.runner(fake_popen(procs, out=big, hang=True))
            with self.assertRaisesRegex(rd.StepAborted, "output_cap"):
                run("x", ["docker", "logs"], env={}, timeout=600)
        self.assertTrue(procs[0].killed)
        self.assertEqual((steps[-1]["status"], steps[-1]["stdout_tail"]), ("ABORTED_OUTPUT_CAP", ""))

    def test_overflow_after_exit_on_stderr_still_aborts_never_ok(self):
        procs = []
        with self.killpg_for(procs):
            run, steps = self.runner(fake_popen(procs, err=b"E" * (rd.STREAM_CAP + 10), code=0))
            with self.assertRaises(rd.StepAborted):
                run("x", ["docker", "ps"], env={}, timeout=5)
        self.assertEqual(steps[-1]["status"], "ABORTED_OUTPUT_CAP")

    def test_output_at_cap_is_kept_bounded_and_redacted(self):
        redact = rd.Redactor()
        redact.add("TOPSECRET")
        payload = b"TOPSECRET " + b"y" * (rd.STREAM_CAP - 20) + b" TOPSECRET"
        run, steps = self.runner(fake_popen(out=payload), redact=redact)
        result = run("x", ["docker", "ps"], env={}, timeout=5)
        self.assertEqual(len(result.out), rd.STREAM_CAP)
        self.assertLessEqual(len(steps[-1]["stdout_tail"]), 800)
        self.assertNotIn("TOPSECRET", json.dumps(steps))

    def test_nonzero_fails_and_shell_strings_refused(self):
        run, _ = self.runner(fake_popen(code=1))
        with self.assertRaises(rd.StepFailed):
            run("x", ["false"], env={}, timeout=5)
        with self.assertRaises(TypeError):
            run("x", "docker ps", env={}, timeout=5)

    def test_exhausted_budget_aborts_before_spawning(self):
        run = rd.Runner(rd.Redactor(), [], -rd.FORWARD_BUDGET, popen=boom, clock=lambda: 0.0)
        with self.assertRaises(rd.StepAborted):
            run("x", ["docker", "ps"], env={}, timeout=5)


SMOKE_NET = f"auneron-drill-{RUN_ID}"
SMOKE_RUN = ["docker", "run", "-d", "--rm", "--name", rd.SMOKE_CONTAINER, "--network", SMOKE_NET,
             "-p", f"127.0.0.1:{rd.SMOKE_PORT}:8000", "-e", "APP_ENV=development", "-e", "MAINTENANCE_ENABLED=false",
             "--mount", "type=bind,source=/tmp/auneron-recovery-smoke-x/database_url,"
                        "target=/run/secrets/database_url,readonly",
             "--mount", "type=bind,source=/tmp/auneron-recovery-smoke-x/api_key,target=/run/secrets/api_key,readonly",
             rd.BACKEND_IMAGE]


class SmokeLaunchAdapterTests(unittest.TestCase):
    def transform(self, argv, uid=1001, gid=127, run_id=RUN_ID):
        return rd.smoke_launch_argv(argv, run_id=run_id, uid=uid, gid=gid)

    def test_exact_insertion_before_image_and_nothing_else(self):
        out = self.transform(list(SMOKE_RUN))
        self.assertEqual(out, SMOKE_RUN[:-1] + ["--user", "1001:127", "--label", f"{rd.LABEL}={RUN_ID}",
                                                rd.BACKEND_IMAGE])
        self.assertFalse(any("PASSWORD" in a or "postgresql" in a or "API_KEY" in a for a in out))

    def test_fixed_stop_and_network_probe_pass_through_unchanged(self):
        for argv in rd.SMOKE_PASSTHROUGH:
            self.assertEqual(self.transform(list(argv)), list(argv))

    def test_refusals(self):
        def swap(old, new):
            return [new if a == old else a for a in SMOKE_RUN]
        bad = {"wrong name": swap(rd.SMOKE_CONTAINER, "other"),
               "wrong image": swap(rd.BACKEND_IMAGE, "evil:latest"),
               "image twice": SMOKE_RUN[:-1] + ["-e", rd.BACKEND_IMAGE, rd.BACKEND_IMAGE],
               "repeated user": SMOKE_RUN[:-1] + ["--user", "0:0", rd.BACKEND_IMAGE],
               "short user": SMOKE_RUN[:-1] + ["-u", "0", rd.BACKEND_IMAGE],
               "privileged": SMOKE_RUN[:-1] + ["--privileged", rd.BACKEND_IMAGE],
               "label already": SMOKE_RUN[:-1] + ["--label", "x=y", rd.BACKEND_IMAGE],
               "extra env": SMOKE_RUN[:-1] + ["-e", "X=1", rd.BACKEND_IMAGE],
               "foreign network": swap(SMOKE_NET, "host"),
               "public port": swap(f"127.0.0.1:{rd.SMOKE_PORT}:8000", f"0.0.0.0:{rd.SMOKE_PORT}:8000"),
               "mount elsewhere": [a.replace("/run/secrets/api_key", "/etc/api_key") for a in SMOKE_RUN],
               "name equals form": swap("--name", "--name=" + rd.SMOKE_CONTAINER),
               "other command": ["docker", "exec", rd.SMOKE_CONTAINER, "sh"],
               "arbitrary": ["sh", "-c", "id"],
               "stop other": ["docker", "stop", rd.PG_CONTAINER]}
        for label, argv in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.transform(argv)
        for uid, gid in ((0, 0), (0, 127), (1001, 0), (True, 127), ("1001", 127), (-1, 127)):
            with self.subTest(uid=uid, gid=gid), self.assertRaisesRegex(ValueError, "uid"):
                self.transform(list(SMOKE_RUN), uid=uid, gid=gid)
        with self.assertRaisesRegex(ValueError, "run_id"):
            self.transform(list(SMOKE_RUN), run_id="r1; rm -rf /")

    def test_adapter_bounds_calls_and_refuses_bad_identity_up_front(self):
        real = mock.Mock()
        adapter = rd.SmokeLaunchAdapter(real, RUN_ID, 1001, 127)
        adapter.run(list(SMOKE_RUN), check=True, capture_output=True, text=True)
        argv, kwargs = real.run.call_args
        self.assertIn("--user", argv[0])
        self.assertEqual(kwargs["timeout"], rd.SMOKE_CALL_TIMEOUT)
        with self.assertRaises(ValueError):
            rd.SmokeLaunchAdapter(real, RUN_ID, 0, 0)
        self.assertFalse(hasattr(adapter, "Popen"))

    def test_bootstrap_rebinds_only_the_smoke_modules_subprocess(self):
        pkg = types.ModuleType("scripts")
        smoke = types.ModuleType("scripts.application_recovery_smoke")
        smoke.subprocess = subprocess
        exec("def main():\n"
             f"    subprocess.run({SMOKE_RUN!r}, check=True, capture_output=True, text=True)\n"
             f"    subprocess.run(['docker', 'stop', {rd.SMOKE_CONTAINER!r}], check=False)\n", smoke.__dict__)
        pkg.application_recovery_smoke = smoke
        recorded = []
        env = {"DRILL_ADAPTER_PATH": rd.__file__, "DRILL_RUN_ID": RUN_ID}
        with mock.patch.dict(sys.modules, {"scripts": pkg, "scripts.application_recovery_smoke": smoke}), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(subprocess, "run", lambda argv, **k: recorded.append((argv, k))), \
                mock.patch.object(os, "getuid", lambda: 1001, create=True), \
                mock.patch.object(os, "getgid", lambda: 127, create=True):
            exec(rd.SMOKE_BOOTSTRAP, {"__name__": "area1_bootstrap_test"})
            self.assertIsNot(smoke.subprocess, subprocess)
            self.assertIs(sys.modules["subprocess"], subprocess)
        self.assertEqual(recorded[0][0][-5:], ["--user", "1001:127", "--label", f"{rd.LABEL}={RUN_ID}",
                                               rd.BACKEND_IMAGE])
        self.assertEqual(recorded[1][0], ["docker", "stop", rd.SMOKE_CONTAINER])
        self.assertTrue(all(k["timeout"] == rd.SMOKE_CALL_TIMEOUT for _, k in recorded))

    def test_adapter_matches_the_unchanged_smoke_tool_source(self):
        # Reads (never imports) the baseline tool: its docker run flags/constants are what the adapter accepts.
        tree = ast.parse((rd.BACKEND / "scripts" / "application_recovery_smoke.py").read_text(encoding="utf-8"))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)}
        self.assertEqual((consts["SMOKE_CONTAINER_NAME"], consts["SMOKE_IMAGE"], consts["SMOKE_HOST_PORT"]),
                         (rd.SMOKE_CONTAINER, rd.BACKEND_IMAGE, rd.SMOKE_PORT))
        start = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start_smoke_container")
        argv = next(n for n in ast.walk(start) if isinstance(n, ast.List))
        flags = [e.value for e in argv.elts if isinstance(e, ast.Constant) and str(e.value).startswith("-")]
        self.assertEqual(sorted(set(flags)), sorted(rd.SMOKE_FLAGS))
        literals = [e.value for e in argv.elts if isinstance(e, ast.Constant)]
        self.assertEqual(literals[:3], ["docker", "run", "-d"])
        self.assertIn("APP_ENV=development", literals)
        self.assertIn("MAINTENANCE_ENABLED=false", literals)


class RequiredCheckTests(unittest.TestCase):
    GOOD_REPLAY = {"replayed": True, "references_equal": True, "delta_zero": True, "delta_zero_after_probes": True,
                   "uniques": {"uq_import_receipts_actor_key": ["23505", "uq_import_receipts_actor_key"],
                               "uq_import_receipts_content_version":
                                   ["23505", "uq_import_receipts_content_version"]}}

    def test_smoke_blocked_with_exit_zero_fails(self):
        for out, code in (("Application Recovery Layer B: BLOCKED\n", 0), ("Application Recovery Layer B: FAIL", 1),
                          ("", 0), (rd.LAYER_B_PASS, 1)):
            with self.subTest(out), self.assertRaises(rd.StepFailed):
                rd.require_smoke_pass(rd.Result(code, out, ""))
        rd.require_smoke_pass(rd.Result(0, rd.LAYER_B_PASS + "\n/health OK", ""))

    def test_layer_a_requires_pass_line_and_head(self):
        good = f"Tabelas verificadas: 28\nRevisão Alembic: {rd.HEAD_REVISION}\n{rd.LAYER_A_PASS}\n"
        self.assertEqual(rd.parse_layer_a(good), (28, rd.HEAD_REVISION))
        for bad in (good.replace(rd.LAYER_A_PASS, ""), good.replace(rd.HEAD_REVISION, "c4718a49cebf")):
            with self.subTest(bad), self.assertRaises(rd.StepFailed):
                rd.parse_layer_a(bad)

    def test_replay_checks_cannot_be_omitted(self):
        rd.check_replay(self.GOOD_REPLAY)
        mutations = [{"replayed": False}, {"delta_zero": False}, {"uniques": {}},
                     {"uniques": {"uq_import_receipts_actor_key": ["23505", "uq_import_receipts_actor_key"],
                                  "uq_import_receipts_content_version": ["no_error", None]}}]
        for change in mutations + [{k: None} for k in ("references_equal", "delta_zero_after_probes")]:
            with self.subTest(change), self.assertRaises(rd.StepFailed):
                rd.check_replay(dict(self.GOOD_REPLAY, **change))

    def test_constraints_exact(self):
        text = "\n".join(sorted(rd.EXPECTED_CONSTRAINTS))
        rd.check_constraints(text, text)
        missing = "\n".join(sorted(rd.EXPECTED_CONSTRAINTS)[1:])
        with self.assertRaises(rd.StepFailed):
            rd.check_constraints(missing, missing)

    def test_seed_expectations(self):
        good = {"replayed": False, "total": "350.50", "counts": dict(rd.EXPECTED_SEED), "references": [1, 2],
                "actor": "user:1"}
        rd.check_seed(good)
        for change in ({"total": "350.5"}, {"references": [1, 1]}, {"actor": "drill:user"},
                       {"counts": dict(rd.EXPECTED_SEED, import_confirmation_audit_events=2)}):
            with self.subTest(change), self.assertRaises(rd.StepFailed):
                rd.check_seed(dict(good, **change))


class Daemon:
    """Tiny fake Docker daemon keyed by name/ID; answers only the allowlisted inspect formats."""

    def __init__(self, label=RUN_ID):
        self.label = label
        self.objects = {
            ("container", "c1"): ["c1", label, PG_IMG], ("container", rd.PG_CONTAINER): ["c1", label, PG_IMG],
            ("image", BE_IMG): [BE_IMG, label], ("image", rd.BACKEND_IMAGE): [BE_IMG, label],
            ("network", "n1"): ["n1", label], ("network", f"auneron-drill-{label}"): ["n1", label],
            ("volume", f"auneron-drill-pg-{label}"): [f"auneron-drill-pg-{label}", label]}
        self.pre = {"containers": ["c0"], "volumes": ["v0"], "networks": ["n0"], "images": ["i0"]}
        self.post = dict(self.pre)
        self.base_images = {}

    def __call__(self, argv):
        if argv[1:3] == ["image", "inspect"] and argv[4] == rd.FMT_ID:
            tag = argv[-1]
            return (0, self.base_images[tag]) if tag in self.base_images else (1, "")
        if argv[2:3] == ["inspect"]:
            kind, ref = ("container" if argv[1] == "inspect" else argv[1]), argv[-1]
            fields = self.objects.get((kind, ref))
            return (0, "|".join(fields)) if fields else (1, "")
        if argv[:2] == ["docker", "exec"]:
            return 0, "0"
        listing = {("ps",): "containers", ("volume", "ls"): "volumes", ("network", "ls"): "networks",
                   ("image", "ls"): "images"}
        for prefix, key in listing.items():
            if tuple(argv[1:1 + len(prefix)]) == prefix:
                return 0, " ".join(self.post[key])
        return 0, ""


class CleanupTests(unittest.TestCase):
    def ledger(self, **kw):
        fields = dict(network_id="n1", volume=f"auneron-drill-pg-{RUN_ID}", pg_container_id="c1",
                      backend_image_id=BE_IMG, pg_image_id=PG_IMG)
        fields.update(kw)
        return rd.Ledger(RUN_ID, **fields)

    def cleanup(self, daemon, ledger=None):
        run = FakeRun(daemon)
        ok, status = rd.cleanup(run, ledger or self.ledger(), {"base": {}, "source": {}}, daemon.pre)
        return run, ok, status

    def test_owned_resources_removed(self):
        ledger = self.ledger(restore_attempted=True)
        run, ok, status = self.cleanup(Daemon(), ledger)
        self.assertTrue(ok, status)
        self.assertEqual(status["recovery_database"], "DROPPED_VERIFIED")
        self.assertIn(["docker", "rm", "-f", "-v", "c1"], run.calls)
        self.assertIn(["docker", "volume", "rm", ledger.volume], run.calls)

    def test_name_swapped_to_foreign_container_refuses_drop_and_touches_nothing_foreign(self):
        daemon = Daemon()
        daemon.objects[("container", rd.PG_CONTAINER)] = ["foreign", "", "sha256:" + "c" * 64]
        run, ok, status = self.cleanup(daemon, self.ledger(restore_attempted=True))
        self.assertFalse(ok)
        self.assertIn("drop_refused", status["recovery_database"])
        self.assertFalse(any("--cleanup" in c for c in run.calls))
        self.assertFalse(any("foreign" in c for c in run.calls if "rm" in c[1:3]))
        self.assertIn(["docker", "rm", "-f", "-v", "c1"], run.calls)  # ours, by ledger ID + label

    def test_forward_ownership_check_rejects_swapped_name(self):
        daemon = Daemon()
        daemon.objects[("container", rd.PG_CONTAINER)] = ["foreign", RUN_ID, PG_IMG]
        self.assertFalse(rd.pg_name_owned(FakeRun(daemon), {}, self.ledger()))
        self.assertTrue(rd.pg_name_owned(FakeRun(Daemon()), {}, self.ledger()))

    def test_timed_out_creates_are_adopted_by_exact_name_and_label_then_removed(self):
        ledger = self.ledger(network_id="", volume="", pg_container_id="", backend_image_id="")
        run, ok, status = self.cleanup(Daemon(), ledger)
        self.assertTrue(ok, status)
        for kind in ("network", "volume", "container", "image"):
            self.assertEqual(status[f"adopt_{kind}"], "ADOPTED")
        self.assertEqual((ledger.network_id, ledger.pg_container_id, ledger.backend_image_id), ("n1", "c1", BE_IMG))
        for argv in (["docker", "rm", "-f", "-v", "c1"], ["docker", "network", "rm", "n1"],
                     ["docker", "image", "rm", BE_IMG]):
            self.assertIn(argv, run.calls)

    def test_unlabelled_or_wrong_image_at_run_name_is_refused_untouched(self):
        cases = {"network": (("network", f"auneron-drill-{RUN_ID}"), ["nX", ""]),
                 "container": (("container", rd.PG_CONTAINER), ["cX", RUN_ID, "sha256:" + "d" * 64]),
                 "image": (("image", rd.BACKEND_IMAGE), ["sha256:" + "e" * 64, "other-run"])}
        for kind, (key, fields) in cases.items():
            with self.subTest(kind):
                daemon = Daemon()
                daemon.objects[key] = fields
                ledger = self.ledger(**{{"network": "network_id", "container": "pg_container_id",
                                         "image": "backend_image_id"}[kind]: ""})
                run, ok, status = self.cleanup(daemon, ledger)
                self.assertFalse(ok)
                self.assertIn("REFUSED", status[f"adopt_{kind}"])
                self.assertFalse(any(fields[0] in c for c in run.calls if "rm" in c[1:3]))

    def test_smoke_residual_needs_label_expected_image_and_id(self):
        for fields, owned in ((["s1", RUN_ID, BE_IMG], True), (["s1", "", BE_IMG], False),
                              (["s1", RUN_ID, "sha256:" + "f" * 64], False), (["", RUN_ID, BE_IMG], False)):
            with self.subTest(fields):
                daemon = Daemon()
                daemon.objects[("container", rd.SMOKE_CONTAINER)] = fields
                run, ok, status = self.cleanup(daemon)
                self.assertEqual(ok, owned, status)
                self.assertEqual(["docker", "rm", "-f", "s1"] in run.calls, owned)

    def test_container_without_run_label_refused(self):
        daemon = Daemon()
        daemon.objects[("container", "c1")] = ["c1", "someone-else", PG_IMG]
        run, ok, status = self.cleanup(daemon)
        self.assertFalse(ok)
        self.assertFalse(any(c[:3] == ["docker", "rm", "-f"] and "c1" in c for c in run.calls))

    def test_post_snapshot_drift_blocks(self):
        daemon = Daemon()
        daemon.post = dict(daemon.pre, containers=["c0", "leftover"])
        _, ok, status = self.cleanup(daemon)
        self.assertFalse(ok)
        self.assertTrue(status["post_snapshot"].startswith("FAILED"))

    def test_only_base_images_may_remain_unowned_extra_image_blocks(self):
        daemon = Daemon()
        daemon.post = dict(daemon.pre, images=["i0", "sha256:pg"])
        daemon.base_images = {rd.PG_IMAGE: "sha256:pg"}
        _, ok, status = self.cleanup(daemon)
        self.assertTrue(ok, status)
        daemon.post = dict(daemon.pre, images=["i0", "sha256:pg", "sha256:stray"])
        _, ok, status = self.cleanup(daemon)
        self.assertFalse(ok)
        self.assertIn("unowned_image_present", status["post_snapshot"])

    def test_cleanup_never_requests_raw_inspect(self):
        run, _, _ = self.cleanup(Daemon(), self.ledger(restore_attempted=True, network_id="", volume=""))
        inspects = [c for c in run.calls if "inspect" in c[1:3]]
        self.assertTrue(inspects)
        self.assertTrue(all(c[c.index("--format") + 1] in rd.INSPECT_FORMATS for c in inspects))


class MainOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(GOOD_ENV, RUNNER_TEMP=self.tmp)
        self.report = os.path.join(self.tmp, "report.json")
        self.argv = ["--execute", "--baseline", rd.BASELINE, "--report", self.report]

    def run_main(self, drill, cleaned=True, popen=None):
        def echo(argv, **kwargs):
            return FakeProc(kwargs["stdout"], kwargs["stderr"], out=" ".join(argv).encode())
        with mock.patch.object(rd, "preflight_repo", return_value=HEAD), \
                mock.patch.object(rd, "preflight_docker", return_value={}), \
                mock.patch.object(rd, "drill", drill), \
                mock.patch.object(rd, "cleanup", return_value=(cleaned, {"x": "y"})) as cleanup:
            code = rd.main(self.argv, environ=self.env, popen=popen or echo)
        return code, json.loads(Path(self.report).read_text()), cleanup

    def test_cleanup_failure_turns_pass_into_blocked(self):
        code, report, _ = self.run_main(lambda *a: None, cleaned=False)
        self.assertEqual((code, report["outcome"], report["cleanup_status"]), (2, "BLOCKED", "BLOCKED"))

    def test_abort_still_cleans_owned_resources(self):
        def drill(*a):
            raise rd.StepAborted("smoke")
        code, report, cleanup = self.run_main(drill)
        self.assertEqual((code, report["outcome"]), (3, "ABORTED"))
        cleanup.assert_called_once()

    def test_real_output_overflow_is_aborted_not_pass(self):
        procs = []

        def drill(run, *a):
            run("chatty", ["docker", "logs", "x"], env={}, timeout=60)

        def killpg(pid, sig):
            for proc in procs:
                proc.killed = True
        with mock.patch.object(os, "killpg", killpg, create=True), mock.patch.object(rd, "POLL_SECONDS", 0):
            code, report, cleanup = self.run_main(
                drill, popen=fake_popen(procs, out=b"x" * (rd.STREAM_CAP * 2), hang=True))
        self.assertEqual((code, report["outcome"]), (3, "ABORTED"))
        self.assertEqual(report["steps"][-1]["status"], "ABORTED_OUTPUT_CAP")
        cleanup.assert_called_once()

    def test_report_never_contains_secrets(self):
        seen = {}

        def drill(run, ledger, envs, report, head, password_file):
            seen.update(pg=envs["docker_pg"]["POSTGRES_PASSWORD"], api=envs["source"]["API_KEY"])
            run("echo", ["echo", envs["source"]["DATABASE_URL"], envs["viewer_password"]], env={}, timeout=5)
        code, _, _ = self.run_main(drill)
        text = Path(self.report).read_text()
        self.assertEqual(code, 0)
        self.assertNotIn(seen["pg"], text)
        self.assertNotIn(seen["api"], text)
        self.assertIn("***", text)


if __name__ == "__main__":
    unittest.main()
