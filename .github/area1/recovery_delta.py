"""AREA1 recovery delta drill (TASK-037). Stdlib only on the host.

Default: prints the plan and exits 0, touching nothing. ``--execute`` additionally
requires the fixed baseline and a GitHub Actions push of the exact drill branch of
danit0m/auneron at the checked-out HEAD; it never runs on a local machine. Backend
code (the UNCHANGED tools backup/restore/verify/application smoke and the import
services) runs only in child processes with an explicit minimal environment; the host
never imports it. Secrets are generated in RAM, passed only through child env (never
argv), redacted before anything is recorded. Child output is spooled to private
unlinked temp files with a per-stream cap; overflow or timeout kills the process group
and ABORTS. Docker inspect is only ever asked for IDs/names/the run label. Only
resources carrying this run's label (and expected image/ID) are cleaned; exact run
names are adopted after a timed-out create; nothing is pruned or deleted by listing.
Exit codes: 0 PASS / dry plan, 1 FAIL, 2 BLOCKED (gate or cleanup), 3 ABORTED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BASELINE = "3b1860c2163a60cdf02534de2e82e81faef1c5ba"
BRANCH_REF = "refs/heads/review/area1-recovery-delta"
REPOSITORY = "danit0m/auneron"
ALLOWED_FILES = (".github/area1/recovery_delta.py", ".github/area1/test_recovery_delta.py",
                 ".github/workflows/area1-recovery-ci.yml")
TOOLS = ("_postgres_recovery_common.py", "backup_postgres.py", "restore_postgres.py",
         "verify_recovery.py", "application_recovery_smoke.py", "verify_build_identity.py")
HEAD_REVISION = "b428d1a6c903"
PG_CONTAINER, SMOKE_CONTAINER = "auneron-postgres", "auneron-recovery-smoke"
BACKEND_IMAGE, PG_IMAGE = "auneron-backend:local", "postgres:17-alpine"
BASE_IMAGES = (PG_IMAGE, "python:3.11-slim")  # may be left on the VM; accounted, never deleted
PG_PORT, SMOKE_PORT = 55434, 18000
SOURCE_DB, TARGET_DB = "auneron_test", "auneron_recovery_drill"
DISK_FLOOR = 5 * 1024 ** 3
LABEL = "auneron.drill"
RUN_ID_RE = re.compile(r"a1d\d{8}t\d{6}[0-9a-f]{6}")
STREAM_CAP, POLL_SECONDS, SMOKE_CALL_TIMEOUT = 2 * 1024 ** 2, 0.2, 120
# The only docker inspect templates ever requested: IDs, names, image and the run label.
FMT_ID, FMT_NAME = "{{.Id}}", "{{.Name}}"
FMT_CONTAINER = '{{.Id}}|{{index .Config.Labels "' + LABEL + '"}}|{{.Image}}'
FMT_IMAGE = '{{.Id}}|{{index .Config.Labels "' + LABEL + '"}}'
FMT_NETWORK = '{{.Id}}|{{index .Labels "' + LABEL + '"}}'
FMT_VOLUME = '{{.Name}}|{{index .Labels "' + LABEL + '"}}'
INSPECT_FORMATS = frozenset({FMT_ID, FMT_NAME, FMT_CONTAINER, FMT_IMAGE, FMT_NETWORK, FMT_VOLUME})
OWNED_FORMAT = {"container": FMT_CONTAINER, "image": FMT_IMAGE, "network": FMT_NETWORK, "volume": FMT_VOLUME}
PROBE_FORMAT = {"container": FMT_ID, "image": FMT_ID, "network": FMT_ID, "volume": FMT_NAME}
VIEWER_EMAIL = "drill-viewer@example.com"
IDEMPOTENCY_KEY = "00000000-0000-4000-8000-000000000001"
CSV_TEXT = ("cliente;valor;vencimento;status\r\nDRILL Synthetic A;100,00;2026-11-15;aberto\r\n"
            "DRILL Synthetic B;250,50;2026-11-30;aberto")
EXPECTED_SEED = {"accounts": 2, "users": 1, "import_confirmation_receipts": 1,
                 "import_confirmation_audit_events": 3}
EXPECTED_CONSTRAINTS = frozenset({
    "uq_import_receipts_actor_key:u", "uq_import_receipts_content_version:u",
    "ck_import_receipts_file_kind:c", "ck_import_receipts_record_count:c",
    "ck_import_receipts_total_positive:c", "ck_import_receipts_identity_version:c",
    "ck_import_receipts_references_array:c"})
LAYER_A_PASS = "Layer A -- Database Recovery Verification: PASS"
LAYER_B_PASS = "Application Recovery Layer B: PASS"
OUTPUT_LIMIT = 4000
FORWARD_BUDGET, TOTAL_BUDGET, READY_BUDGET = 24 * 60, 28 * 60, 120
TIMEOUTS = {"git": 30, "docker_query": 30, "claims": 60, "pull": 300, "build": 900,
            "identity": 120, "create": 60, "upgrade": 300, "seed": 120, "backup": 300,
            "restore": 300, "verify": 300, "delta": 120, "replay": 120, "smoke": 300,
            "cleanup": 180}
TABLE_COUNT_SQL = ("SELECT count(*) FROM information_schema.tables "
                   "WHERE table_schema='public' AND table_type='BASE TABLE'")
CONSTRAINT_SQL = ("SELECT conname || ':' || contype FROM pg_constraint WHERE conrelid = "
                  "'public.import_confirmation_receipts'::regclass AND contype IN ('u','c') ORDER BY 1")
DATA_SQL = {
    "accounts": "SELECT id, cliente, valor::text AS valor, vencimento::text AS vencimento, status FROM accounts",
    "receipts": ("SELECT id, actor_reference, idempotency_key, identity_version, content_sha256, file_kind, "
                 "record_count, total::text AS total, account_references, created_at "
                 "FROM import_confirmation_receipts"),
    "audit": ("SELECT id, action, actor_reference, idempotency_key, content_sha256, account_reference, "
              "row_number, record_count, created_at FROM import_confirmation_audit_events"),
}

# Child code: runs with cwd=backend and an explicit env; asserts identity BEFORE any write.
CHILD_PRELUDE = '''
import json, os
from decimal import Decimal
from sqlalchemy import text
from app.core.config import settings
from app.database.database import SessionLocal, engine
spec = json.loads(os.environ["DRILL_SPEC"])
def need(ok, code):
    if not ok:
        raise SystemExit("drill_check_failed:" + code)
need(settings.environment == spec["app_env"], "app_env")
need(settings.maintenance_enabled is False and settings.intelligence_mode == "off", "flags")
need((engine.url.host, engine.url.port, engine.url.database) == ("127.0.0.1", spec["port"], spec["database"]), "dsn")
with engine.connect() as c:
    need(c.execute(text("SELECT current_database()")).scalar_one() == spec["database"], "current_database")
    need(c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == spec["revision"], "revision")
from app.services import import_preview_parser as parser
from app.services import import_confirmation_plan as planner
from app.services import import_confirmation_execution as execution
from app.services import import_confirmation_repository as repositories
TABLES = ("accounts", "users", "import_confirmation_receipts", "import_confirmation_audit_events")
def counts():
    with engine.connect() as c:
        return {t: c.execute(text("SELECT count(*) FROM " + t)).scalar_one() for t in TABLES}
data = spec["csv"].encode("utf-8")
preview = parser.parse_import_preview("preview.csv", data)
plan = planner.plan_import_confirmation(parser.parse_import_preview, data, "csv",
    expected_file_sha256=preview.file_sha256, expected_content_sha256=preview.content_sha256,
    idempotency_key=spec["key"])
# Owned drill harness declaration (component level), NOT the HTTP route.
repo = repositories.PostgresImportConfirmationRepository(SessionLocal, execution, writer_protocol_verified=True)
'''
SEED_CODE = CHILD_PRELUDE + '''
need(all(v == 0 for v in counts().values()), "source_not_empty")
from app.core.authentication import hash_password, normalize_email
from app.models.user import User
with SessionLocal() as s:
    user = User(name="DRILL Viewer", email=normalize_email(spec["email"]),
                password_hash=hash_password(os.environ["DRILL_VIEWER_PASSWORD"]), role="viewer", active=True)
    s.add(user)
    s.commit()
    user_id = user.id
actor = "user:%d" % user_id
result = execution.execute_import_confirmation(repo, plan, actor_reference=actor)
print(json.dumps({"user_id": user_id, "actor": actor, "replayed": result.replayed,
    "references": list(result.receipt.account_references), "total": str(result.receipt.total),
    "counts": counts()}))
'''
REPLAY_CODE = CHILD_PRELUDE + '''
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
before = counts()
result = execution.execute_import_confirmation(repo, plan, actor_reference=spec["actor"])
out = {"replayed": result.replayed is True,
       "references_equal": list(result.receipt.account_references) == spec["references"],
       "delta_zero": counts() == before, "uniques": {}}
with engine.connect() as c:
    sha = c.execute(text("SELECT content_sha256 FROM import_confirmation_receipts WHERE actor_reference = :a "
                         "AND idempotency_key = :k"), {"a": spec["actor"], "k": spec["key"]}).scalar_one()
probes = {"uq_import_receipts_actor_key": {"actor_reference": spec["actor"], "idempotency_key": spec["key"],
                                           "content_sha256": "b" * 64},
          "uq_import_receipts_content_version": {"actor_reference": "drill:probe",
              "idempotency_key": "00000000-0000-4000-8000-000000000002", "content_sha256": sha}}
with engine.connect() as c:
    outer = c.begin()
    for name, values in probes.items():
        savepoint = c.begin_nested()
        try:
            c.execute(insert(repositories.receipts_table).values(identity_version=1, file_kind="csv",
                record_count=1, total=Decimal("1.00"), account_references=[], **values))
            out["uniques"][name] = ["no_error", None]
        except IntegrityError as error:
            out["uniques"][name] = [getattr(error.orig, "sqlstate", None),
                                    getattr(getattr(error.orig, "diag", None), "constraint_name", None)]
        savepoint.rollback()
    outer.rollback()
out["delta_zero_after_probes"] = counts() == before
print(json.dumps(out))
'''
# B-L1 launch adapter (test-environment difference, runner side only): loads THIS file's
# SmokeLaunchAdapter and rebinds ONLY the `subprocess` name inside the unchanged smoke
# module. The global subprocess module, _common and every other tool stay untouched.
SMOKE_BOOTSTRAP = '''
import importlib.util, os, subprocess, sys
spec = importlib.util.spec_from_file_location("area1_launch_adapter", os.environ["DRILL_ADAPTER_PATH"])
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
from scripts import application_recovery_smoke as smoke
smoke.subprocess = adapter.SmokeLaunchAdapter(subprocess, os.environ["DRILL_RUN_ID"], os.getuid(), os.getgid())
smoke.main()
'''
SMOKE_FLAGS = {"-d": False, "--rm": False, "--name": True, "--network": True, "-p": True, "-e": True,
               "--mount": True}
SMOKE_PASSTHROUGH = (("docker", "stop", SMOKE_CONTAINER),
                     ("docker", "inspect", PG_CONTAINER, "--format", "{{json .NetworkSettings.Networks}}"))


def smoke_launch_argv(argv, *, run_id, uid, gid):
    """Exact transform of the smoke tool's docker argv. Only its `docker run` for the fixed
    name/image/network/port/mounts gains `--user uid:gid --label auneron.drill=<run>`;
    its fixed stop and network probe pass through; anything else is refused."""
    if not (type(uid) is int and type(gid) is int and uid > 0 and gid > 0):
        raise ValueError("smoke_adapter_uid_refused")
    if not (isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id)):
        raise ValueError("smoke_adapter_run_id_refused")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ValueError("smoke_adapter_argv_type")
    if tuple(argv) in SMOKE_PASSTHROUGH:
        return list(argv)
    if argv[:2] != ["docker", "run"] or argv[-1:] != [BACKEND_IMAGE] or argv.count(BACKEND_IMAGE) != 1:
        raise ValueError("smoke_adapter_command_refused")
    options, i, seen = argv[2:-1], 0, {}
    while i < len(options):
        flag = options[i]
        if flag not in SMOKE_FLAGS or (SMOKE_FLAGS[flag] and i + 1 >= len(options)):
            raise ValueError("smoke_adapter_flag_refused")
        seen.setdefault(flag, []).append(options[i + 1] if SMOKE_FLAGS[flag] else None)
        i += 2 if SMOKE_FLAGS[flag] else 1
    mounts = seen.get("--mount", [])
    expected = {"-d": [None], "--rm": [None], "--name": [SMOKE_CONTAINER],
                "--network": [f"auneron-drill-{run_id}"], "-p": [f"127.0.0.1:{SMOKE_PORT}:8000"],
                "-e": ["APP_ENV=development", "MAINTENANCE_ENABLED=false"], "--mount": mounts}
    if seen != expected or len(mounts) != 2 or not all(
            m.startswith("type=bind,source=") and m.endswith(f",target=/run/secrets/{s},readonly")
            for m, s in zip(mounts, ("database_url", "api_key"))):
        raise ValueError("smoke_adapter_shape_refused")
    return argv[:-1] + ["--user", f"{uid}:{gid}", "--label", f"{LABEL}={run_id}", BACKEND_IMAGE]


class SmokeLaunchAdapter:
    """Stands in for `subprocess` inside application_recovery_smoke only: transformed argv, bounded call."""

    def __init__(self, real, run_id, uid, gid, timeout=SMOKE_CALL_TIMEOUT):
        smoke_launch_argv(["docker", "stop", SMOKE_CONTAINER], run_id=run_id, uid=uid, gid=gid)
        self._real, self._run_id, self._uid, self._gid, self._timeout = real, run_id, uid, gid, timeout

    def run(self, argv, **kwargs):
        argv = smoke_launch_argv(argv, run_id=self._run_id, uid=self._uid, gid=self._gid)
        kwargs.setdefault("timeout", self._timeout)
        return self._real.run(argv, **kwargs)


class GateError(Exception):
    """Precondition not met: BLOCKED, nothing forward is attempted."""


class StepFailed(Exception):
    """A step returned non-zero or a required check did not hold."""


class StepAborted(Exception):
    """A step hit its timeout or the overall budget; its process group was killed."""


def require(ok, code):
    if not ok:
        raise StepFailed(code)


class Redactor:
    _dsn = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")

    def __init__(self):
        self._secrets = []

    def add(self, value):
        if value:
            self._secrets.append(value)

    def __call__(self, text, limit=OUTPUT_LIMIT):
        for value in self._secrets:
            text = text.replace(value, "***")
        text = self._dsn.sub(r"\1***@", text)
        return text if limit is None or len(text) <= limit else "[truncated]" + text[-limit:]


@dataclass
class Result:
    code: int
    out: str
    err: str


@dataclass
class Ledger:
    run_id: str
    network_id: str = ""
    volume: str = ""
    pg_container_id: str = ""
    smoke_container_id: str = ""
    backend_image_id: str = ""
    pg_image_id: str = ""
    pg_image_pulled: bool = False
    restore_attempted: bool = False
    temp_dirs: list = field(default_factory=list)

    @property
    def network(self):
        return f"auneron-drill-{self.run_id}"

    @property
    def volume_name(self):
        return f"auneron-drill-pg-{self.run_id}"


def check_argv(argv):
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a list of str")
    if argv[:1] == ["docker"] and "inspect" in argv[1:3]:
        at = argv.index("--format") if argv.count("--format") == 1 else -1
        if at < 0 or argv[at + 1:at + 2] == [] or argv[at + 1] not in INSPECT_FORMATS or "-f" in argv \
                or any(a.startswith("--format=") for a in argv):
            raise ValueError("docker_inspect_requires_allowlisted_format")


class Runner:
    """Structured argv only; output spooled to private unlinked files with a per-stream cap;
    per-step timeout and global budget; overflow or timeout kills the group and ABORTS."""

    def __init__(self, redact, steps, start, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
                 spool_dir=None):
        self.redact, self.steps, self.start, self.popen, self.clock = redact, steps, start, popen, clock
        self.sleep, self.spool_dir, self.budget = sleep, spool_dir, FORWARD_BUDGET

    def __call__(self, name, argv, *, env, timeout, cwd=REPO, check=True):
        check_argv(argv)
        limit = min(timeout, self.start + self.budget - self.clock())
        if limit <= 0:
            raise StepAborted(name)
        began = self.clock()
        with tempfile.TemporaryFile(dir=self.spool_dir) as out_f, tempfile.TemporaryFile(dir=self.spool_dir) as err_f:
            proc = self.popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f,
                              start_new_session=True)
            state = self._watch(proc, (out_f, err_f), began + limit)
            if state != "EXITED":
                _kill_group(proc)
            out, err = (_read_capped(out_f), _read_capped(err_f)) if state == "EXITED" else ("", "")
        if state != "EXITED":  # a killed/cut stream may end inside a secret: no tail is kept at all
            self._record(name, argv, None, began, "", "", f"ABORTED_{state}")
            raise StepAborted(f"{name}:{state.lower()}")
        status = "OK" if proc.returncode == 0 else "NONZERO"
        self._record(name, argv, proc.returncode, began, out, err, status)
        if check and proc.returncode != 0:
            raise StepFailed(name)
        return Result(proc.returncode, out, err)

    def _watch(self, proc, files, deadline):
        while True:
            exited = proc.poll() is not None
            if any(os.fstat(f.fileno()).st_size > STREAM_CAP for f in files):
                return "OUTPUT_CAP"
            if exited:
                return "EXITED"
            if self.clock() >= deadline:
                return "TIMEOUT"
            self.sleep(POLL_SECONDS)

    def _record(self, name, argv, code, began, out, err, status):
        self.steps.append({"step": name, "argv": [self.redact(a) for a in argv], "exit": code,
                           "seconds": round(self.clock() - began, 1), "status": status,
                           "stdout_tail": self.redact(out or "")[-800:],
                           "stderr_tail": self.redact(err or "")[-800:] if status != "OK" else ""})


def _read_capped(handle):
    handle.seek(0)
    return handle.read(STREAM_CAP).decode("utf-8", errors="replace")


def _kill_group(proc):
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def base_env():
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return env


def backend_env(base, *, app_env, database, password, api_key):
    env = dict(base)
    env.update(APP_ENV=app_env, API_KEY=api_key, MAINTENANCE_ENABLED="false", INTELLIGENCE_MODE="off",
               IMPORT_CONFIRMATION_TEST_ENABLED="false",
               DATABASE_URL=f"postgresql+psycopg://auneron:{password}@127.0.0.1:{PG_PORT}/{database}")
    return env


def check_gate(args, environ):
    """Env-only gate; evaluated before any subprocess exists."""
    if args.baseline != BASELINE:
        raise GateError("baseline_mismatch")
    if environ.get("GITHUB_ACTIONS") != "true":
        raise GateError("not_github_actions")
    if environ.get("GITHUB_REPOSITORY") != REPOSITORY:  # context check, not proof of human approval
        raise GateError("wrong_repository")
    if environ.get("GITHUB_EVENT_NAME") != "push":
        raise GateError("event_not_push")
    if environ.get("GITHUB_REF") != BRANCH_REF:
        raise GateError("wrong_branch")
    if not re.fullmatch(r"[0-9a-f]{40}", environ.get("GITHUB_SHA", "")):
        raise GateError("github_sha_missing")
    temp = environ.get("RUNNER_TEMP", "")
    if not temp or Path(temp).resolve().is_relative_to(REPO):
        raise GateError("runner_temp_invalid")


def preflight_repo(run, environ, env, report, repo=REPO):
    def git(*a):
        return run("git", ["git", *a], env=env, timeout=TIMEOUTS["git"], cwd=repo).out.strip()

    head = git("rev-parse", "HEAD")
    if head != environ["GITHUB_SHA"]:
        raise GateError("head_not_github_sha")
    # Baseline must be an ancestor (normal review-fix commits allowed, no amend/force-push needed).
    if run("git", ["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], env=env, timeout=TIMEOUTS["git"],
           cwd=repo, check=False).code != 0:
        raise GateError("baseline_not_ancestor")
    if sorted(git("diff", "--name-only", BASELINE, "HEAD").splitlines()) != sorted(ALLOWED_FILES):
        raise GateError("diff_not_exactly_allowed_files")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise GateError("worktree_dirty")
    if os.path.lexists(repo / "backend" / ".env"):  # existence only, never read
        raise GateError("backend_env_file_present")
    hashes = {}
    for tool in TOOLS:
        path = f"backend/scripts/{tool}"
        if git("rev-parse", f"{BASELINE}:{path}") != git("hash-object", path):
            raise GateError(f"tool_not_baseline:{tool}")
        hashes[tool] = hashlib.sha256((repo / path).read_bytes()).hexdigest()
    report.update(head=head, baseline=BASELINE, tool_sha256=hashes)
    return head


def docker_snapshot(run, env):
    def ids(*a):
        return sorted(run("snapshot", ["docker", *a], env=env, timeout=TIMEOUTS["docker_query"]).out.split())
    return {"containers": ids("ps", "-aq", "--no-trunc"), "volumes": ids("volume", "ls", "-q"),
            "networks": ids("network", "ls", "-q", "--no-trunc"), "images": ids("image", "ls", "-aq", "--no-trunc")}


def _exists(run, env, kind, name):
    return run("probe", ["docker", kind, "inspect", "--format", PROBE_FORMAT[kind], name], env=env,
               timeout=TIMEOUTS["docker_query"], check=False).code == 0


def inspect_owned(run, env, kind, ref):
    """ID/name, run label (and image for containers) only; None when absent."""
    result = run("ownership", ["docker", kind, "inspect", "--format", OWNED_FORMAT[kind], ref], env=env,
                 timeout=TIMEOUTS["docker_query"], check=False)
    return result.out.strip().split("|") if result.code == 0 else None


def pg_name_owned(run, env, ledger):
    """The fixed name used by the unchanged tools must resolve to this run's ledger container."""
    return bool(ledger.pg_container_id and ledger.pg_image_id) and inspect_owned(
        run, env, "container", PG_CONTAINER) == [ledger.pg_container_id, ledger.run_id, ledger.pg_image_id]


def preflight_docker(run, env, ledger, environ, report, port_free=None, disk_free=None):
    run("docker_info", ["docker", "info", "--format", "{{.ServerVersion}}"], env=env, timeout=TIMEOUTS["docker_query"])
    for kind, name in (("container", PG_CONTAINER), ("container", SMOKE_CONTAINER), ("image", BACKEND_IMAGE),
                       ("network", ledger.network), ("volume", ledger.volume_name)):
        if _exists(run, env, kind, name):
            raise GateError(f"foreign_resource_present:{kind}:{name}")
    for port in (PG_PORT, SMOKE_PORT):
        if not (port_free or _port_free)(port):
            raise GateError(f"port_busy:{port}")
    for path in (environ["RUNNER_TEMP"], "/"):
        if (disk_free or (lambda p: shutil.disk_usage(p).free))(path) < DISK_FLOOR:
            raise GateError(f"disk_below_floor:{path}")
    snapshot = docker_snapshot(run, env)
    report["pre_snapshot_counts"] = {k: len(v) for k, v in snapshot.items()}
    return snapshot


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def psql(run, env, ledger, database, sql, name="psql"):
    argv = ["docker", "exec", ledger.pg_container_id, "psql", "-U", "auneron", "-d", database,
            "-v", "ON_ERROR_STOP=1", "-tAc", sql]
    return run(name, argv, env=env, timeout=TIMEOUTS["delta"]).out.strip()


def postgres_run_argv(ledger):
    # POSTGRES_PASSWORD without a value: docker reads it from the child env, never argv.
    return ["docker", "run", "-d", "--name", PG_CONTAINER, "--label", f"{LABEL}={ledger.run_id}",
            "--network", ledger.network, "-p", f"127.0.0.1:{PG_PORT}:5432",
            "-v", f"{ledger.volume_name}:/var/lib/postgresql/data", "-e", "POSTGRES_USER=auneron",
            "-e", f"POSTGRES_DB={SOURCE_DB}", "-e", "POSTGRES_PASSWORD", ledger.pg_image_id]


def parse_layer_a(text):
    require(LAYER_A_PASS in text.splitlines(), "layer_a_pass_line_missing")
    tables = re.search(r"^Tabelas verificadas: (\d+)$", text, re.M)
    revision = re.search(r"^Revisão Alembic: (\S+)$", text, re.M)
    require(tables and revision, "layer_a_output_unparsed")
    require(revision.group(1) == HEAD_REVISION, "layer_a_revision_not_head")
    return int(tables.group(1)), revision.group(1)


def require_smoke_pass(result):
    # BLOCKED exits 0 in the unchanged tool: only the explicit PASS line counts.
    lines = result.out.splitlines()
    require(result.code == 0 and LAYER_B_PASS in lines, "layer_b_not_pass")
    require(not any(line.startswith("Application Recovery Layer B: ") and line != LAYER_B_PASS
                    for line in lines), "layer_b_ambiguous")


def check_seed(payload):
    require(payload.get("replayed") is False and payload.get("total") == "350.50", "seed_receipt")
    require(payload.get("counts") == EXPECTED_SEED, "seed_counts")
    refs = payload.get("references")
    require(isinstance(refs, list) and len(refs) == 2 and len(set(refs)) == 2, "seed_references")
    require(re.fullmatch(r"user:[1-9]\d*", str(payload.get("actor"))), "seed_actor")


def check_replay(payload):
    for key in ("replayed", "references_equal", "delta_zero", "delta_zero_after_probes"):
        require(payload.get(key) is True, f"replay_{key}")
    uniques = payload.get("uniques") or {}
    for name in ("uq_import_receipts_actor_key", "uq_import_receipts_content_version"):
        require(uniques.get(name) == ["23505", name], f"unique_not_enforced:{name}")


def check_constraints(source, target):
    require(set(target.splitlines()) == EXPECTED_CONSTRAINTS, "target_constraints_mismatch")
    require(source.splitlines() == target.splitlines(), "constraints_source_target_differ")


def parse_json_line(text, code):
    lines = [line for line in text.splitlines() if line.startswith("{")]
    require(len(lines) == 1, code)
    return json.loads(lines[0])


def drill(run, ledger, envs, report, head, password_file):
    base, src, tgt = envs["base"], envs["source"], envs["target"]
    claims = run("claims", [sys.executable, "backend/scripts/verify_build_identity.py", "claims"], env=base,
                 timeout=TIMEOUTS["claims"]).out.split()
    require(claims == [f"GIT_SHA={head}", "GIT_DIRTY=false"], "build_claims")
    if not _exists(run, base, "image", PG_IMAGE):
        run("pull", ["docker", "pull", PG_IMAGE], env=base, timeout=TIMEOUTS["pull"])
        ledger.pg_image_pulled = True
    ledger.pg_image_id = run("pg_image_id", ["docker", "image", "inspect", "--format", "{{.Id}}", PG_IMAGE],
                             env=base, timeout=TIMEOUTS["docker_query"]).out.strip()
    run("build", ["docker", "build", "-f", "backend/Dockerfile", "-t", BACKEND_IMAGE, "--label",
                  f"{LABEL}={ledger.run_id}", "--build-arg", f"GIT_SHA={head}", "--build-arg", "GIT_DIRTY=false",
                  "."], env=base, timeout=TIMEOUTS["build"])
    ledger.backend_image_id = run("image_id", ["docker", "image", "inspect", "--format", "{{.Id}}",
                                               BACKEND_IMAGE], env=base, timeout=TIMEOUTS["docker_query"]).out.strip()
    run("identity", [sys.executable, "backend/scripts/verify_build_identity.py", "verify", "--image",
                     BACKEND_IMAGE, "--git-rev", head, "--require-clean"], env=base, timeout=TIMEOUTS["identity"])
    ledger.network_id = run("network", ["docker", "network", "create", "--label", f"{LABEL}={ledger.run_id}",
                                        ledger.network], env=base, timeout=TIMEOUTS["create"]).out.strip()
    ledger.volume = run("volume", ["docker", "volume", "create", "--label", f"{LABEL}={ledger.run_id}",
                                   ledger.volume_name], env=base, timeout=TIMEOUTS["create"]).out.strip()
    ledger.pg_container_id = run("postgres", postgres_run_argv(ledger), env=envs["docker_pg"],
                                 timeout=TIMEOUTS["create"]).out.strip()
    require(re.fullmatch(r"[0-9a-f]{64}", ledger.pg_container_id), "container_id")
    deadline = time.monotonic() + READY_BUDGET
    while run("ready", ["docker", "exec", ledger.pg_container_id, "pg_isready", "-h", "127.0.0.1", "-p", "5432",
                        "-U", "auneron", "-d", SOURCE_DB], env=base, timeout=10, check=False).code != 0:
        if time.monotonic() > deadline:
            raise StepAborted("postgres_ready")
        time.sleep(2)
    require(psql(run, base, ledger, SOURCE_DB, "SELECT 1") == "1", "local_trust_auth")
    run("upgrade", [sys.executable, "-m", "alembic", "upgrade", "head"], env=src, timeout=TIMEOUTS["upgrade"],
        cwd=BACKEND)
    require(psql(run, base, ledger, SOURCE_DB, "SELECT version_num FROM alembic_version") == HEAD_REVISION,
            "source_revision")
    spec = {"port": PG_PORT, "revision": HEAD_REVISION, "csv": CSV_TEXT, "key": IDEMPOTENCY_KEY,
            "email": VIEWER_EMAIL}
    seed_env = dict(src, DRILL_SPEC=json.dumps(dict(spec, app_env="test", database=SOURCE_DB)),
                    DRILL_VIEWER_PASSWORD=envs["viewer_password"])
    seed = parse_json_line(run("seed", [sys.executable, "-c", SEED_CODE], env=seed_env, timeout=TIMEOUTS["seed"],
                               cwd=BACKEND).out, "seed_output")
    check_seed(seed)
    report["seed"] = {"actor": seed["actor"], "counts": seed["counts"], "total": seed["total"]}
    dump_dir = Path(tempfile.mkdtemp(prefix="area1-dump-", dir=envs["runner_temp"]))
    ledger.temp_dirs.append(str(dump_dir))

    def owned_name():  # the unchanged tools address the container by its fixed name
        require(pg_name_owned(run, base, ledger), "pg_name_not_owned")

    owned_name()
    backup = run("backup", [sys.executable, "-m", "scripts.backup_postgres", "--output-dir", str(dump_dir)],
                 env=src, timeout=TIMEOUTS["backup"], cwd=BACKEND).out
    dumps = sorted(dump_dir.glob("backup_*.dump"))
    require(len(dumps) == 1, "backup_artifact")
    digest = hashlib.sha256(dumps[0].read_bytes()).hexdigest()
    require(f"SHA-256: {digest}" in backup.splitlines(), "backup_checksum")
    report["backup"] = {"sha256": digest, "bytes": dumps[0].stat().st_size}
    owned_name()
    ledger.restore_attempted = True
    run("restore", [sys.executable, "-m", "scripts.restore_postgres", "--backup-file", str(dumps[0]),
                    "--target-dsn", f"postgresql+psycopg://auneron@127.0.0.1:{PG_PORT}/{TARGET_DB}",
                    "--target-database", TARGET_DB], env=src, timeout=TIMEOUTS["restore"], cwd=BACKEND)
    owned_name()
    verify = run("verify", [sys.executable, "-m", "scripts.verify_recovery", "--source-database", SOURCE_DB,
                            "--target-database", TARGET_DB], env=src, timeout=TIMEOUTS["verify"], cwd=BACKEND).out
    tables, revision = parse_layer_a(verify)
    counted = {db: int(psql(run, base, ledger, db, TABLE_COUNT_SQL)) for db in (SOURCE_DB, TARGET_DB)}
    require(counted == {SOURCE_DB: tables, TARGET_DB: tables}, "table_count")
    report["layer_a"] = {"status": "PASS", "tables": tables, "revision": revision}
    check_constraints(psql(run, base, ledger, SOURCE_DB, CONSTRAINT_SQL),
                      psql(run, base, ledger, TARGET_DB, CONSTRAINT_SQL))
    require(psql(run, base, ledger, TARGET_DB, "SELECT string_agg(jsonb_typeof(account_references), ',') "
                 "FROM import_confirmation_receipts") == "array", "references_jsonb_array")
    data = {}
    for name, sql in DATA_SQL.items():
        wrapped = f"SELECT coalesce(json_agg(r ORDER BY r.id)::text, '[]') FROM ({sql}) r"
        pair = [psql(run, base, ledger, db, wrapped) for db in (SOURCE_DB, TARGET_DB)]
        require(pair[0] == pair[1] and pair[0] != "[]", f"data_differs:{name}")
        data[name] = hashlib.sha256(pair[0].encode()).hexdigest()
    report["delta"] = {"constraints": sorted(EXPECTED_CONSTRAINTS), "data_sha256": data}
    replay_env = dict(tgt, DRILL_SPEC=json.dumps(dict(spec, app_env="development", database=TARGET_DB,
                                                      actor=seed["actor"], references=seed["references"])))
    replay = parse_json_line(run("replay", [sys.executable, "-c", REPLAY_CODE], env=replay_env,
                                 timeout=TIMEOUTS["replay"], cwd=BACKEND).out, "replay_output")
    check_replay(replay)
    report["replay"] = dict(replay, status="PASS", level="component (repository harness, not HTTP route)")
    password_file.write_text(envs["viewer_password"], encoding="utf-8")
    smoke_env = dict(src, SMOKE_LOGIN_EMAIL=VIEWER_EMAIL, SMOKE_LOGIN_PASSWORD_FILE=str(password_file),
                     DRILL_RUN_ID=ledger.run_id, DRILL_ADAPTER_PATH=str(Path(__file__).resolve()))
    owned_name()
    smoke = run("smoke", [sys.executable, "-c", SMOKE_BOOTSTRAP], env=smoke_env, timeout=TIMEOUTS["smoke"],
                cwd=BACKEND, check=False)
    require_smoke_pass(smoke)
    report["layer_b"] = "PASS"
    report["layer_b_launch_adapter"] = ("runner-side --user <runner uid:gid> + run label on the smoke docker run "
                                        "only; smoke tool source unchanged")


ADOPTABLE = (  # ledger field, kind, exact run-known name; adopted only with this run's label
    ("network_id", "network", lambda l: l.network),
    ("volume", "volume", lambda l: l.volume_name),
    ("pg_container_id", "container", lambda l: PG_CONTAINER),
    ("backend_image_id", "image", lambda l: BACKEND_IMAGE),
)


def adopt(run, env, ledger):
    """After a create that timed out before its ID reached the ledger: adopt ONLY the exact
    run-known name carrying this run's label (containers: also the expected image). A
    present but unlabelled/foreign resource is REFUSED and never touched."""
    status = {}
    for field_name, kind, name in ADOPTABLE:
        if getattr(ledger, field_name):
            continue
        fields = inspect_owned(run, env, kind, name(ledger))
        if fields is None:
            continue
        ok = len(fields) >= 2 and fields[0] and fields[1] == ledger.run_id
        if kind == "container":
            ok = ok and len(fields) == 3 and bool(ledger.pg_image_id) and fields[2] == ledger.pg_image_id
        if kind == "volume":
            ok = ok and fields[0] == ledger.volume_name
        if ok:
            setattr(ledger, field_name, fields[0])
            status[f"adopt_{kind}"] = "ADOPTED"
        else:
            status[f"adopt_{kind}"] = f"FAILED:REFUSED:unowned_{kind}_at_run_name"
    return status


def cleanup(run, ledger, envs, pre):
    run.budget = TOTAL_BUDGET
    base, status, label = envs["base"], {}, ledger.run_id

    def attempt(name, action):
        try:
            status[name] = action() or "REMOVED"
        except Exception as error:  # every failure is reported, none hides another
            status[name] = f"FAILED:{type(error).__name__}:{error}"

    try:
        status.update(adopt(run, base, ledger))
    except Exception as error:
        status["adopt"] = f"FAILED:{type(error).__name__}:{error}"

    def smoke():
        fields = inspect_owned(run, base, "container", SMOKE_CONTAINER)
        if fields is None:
            return "ABSENT"
        require(bool(ledger.backend_image_id) and len(fields) == 3 and fields[0]
                and fields[1:] == [label, ledger.backend_image_id], "smoke_residual_not_owned")
        ledger.smoke_container_id = fields[0]
        run("rm_smoke", ["docker", "rm", "-f", fields[0]], env=base, timeout=TIMEOUTS["cleanup"])

    def recovery_db():
        if not (ledger.restore_attempted and ledger.pg_container_id):
            return "NOT_CREATED"
        # The guard tool addresses the fixed name: refuse unless it is still this run's container.
        require(pg_name_owned(run, base, ledger), "recovery_db_name_not_owned_drop_refused")
        run("drop_recovery", [sys.executable, "-m", "scripts.restore_postgres", "--cleanup", "--target-database",
                              TARGET_DB], env=envs["source"], timeout=TIMEOUTS["cleanup"], cwd=BACKEND)
        left = psql(run, base, ledger, "postgres", f"SELECT count(*) FROM pg_database WHERE datname = '{TARGET_DB}'")
        require(left == "0", "recovery_db_still_present")
        return "DROPPED_VERIFIED"

    def owned_then_rm(kind, field_name, expected, rm_argv):
        def action():
            ref = getattr(ledger, field_name)
            if not ref:
                return "NOT_CREATED"
            require(inspect_owned(run, base, kind, ref) == expected(ref), f"{kind}_not_owned")
            run(f"rm_{kind}", rm_argv(ref), env=base, timeout=TIMEOUTS["cleanup"])
        return action

    attempt("smoke_container", smoke)
    attempt("recovery_database", recovery_db)
    attempt("postgres_container", owned_then_rm("container", "pg_container_id",
                                                lambda r: [r, label, ledger.pg_image_id],
                                                lambda r: ["docker", "rm", "-f", "-v", r]))
    attempt("volume", owned_then_rm("volume", "volume", lambda r: [r, label],
                                    lambda r: ["docker", "volume", "rm", r]))
    attempt("network", owned_then_rm("network", "network_id", lambda r: [r, label],
                                     lambda r: ["docker", "network", "rm", r]))
    attempt("backend_image", owned_then_rm("image", "backend_image_id", lambda r: [r, label],
                                           lambda r: ["docker", "image", "rm", r]))
    for path in ledger.temp_dirs:
        shutil.rmtree(path, ignore_errors=True)
    status["temp_dirs"] = "REMOVED" if not any(os.path.lexists(p) for p in ledger.temp_dirs) else "FAILED"

    def snapshot():
        post = docker_snapshot(run, base)
        for key in ("containers", "volumes", "networks"):
            require(post[key] == pre[key], f"post_snapshot_{key}_differs")
        require(set(pre["images"]) <= set(post["images"]), "foreign_image_removed")
        extra = set(post["images"]) - set(pre["images"])
        known = set()
        for tag in BASE_IMAGES:  # base images we pulled/built FROM: accounted by exact tag, never deleted
            probe = run("base_image", ["docker", "image", "inspect", "--format", FMT_ID, tag], env=base,
                        timeout=TIMEOUTS["docker_query"], check=False)
            if probe.code == 0:
                known.add(probe.out.strip())
        require(extra <= known, "unowned_image_present")
        return f"UNCHANGED (base images left, not deleted: {len(extra)})"
    attempt("post_snapshot", snapshot)
    ok = all(v in ("REMOVED", "ABSENT", "NOT_CREATED", "DROPPED_VERIFIED", "ADOPTED") or v.startswith("UNCHANGED")
             for v in status.values())
    return ok, status


def plan_summary():
    return {"mode": "DRY_PLAN", "executed": False, "baseline": BASELINE, "branch_ref": BRANCH_REF,
            "fixed_names": [PG_CONTAINER, SMOKE_CONTAINER, BACKEND_IMAGE], "ports": [PG_PORT, SMOKE_PORT],
            "disk_floor_bytes": DISK_FLOOR, "timeouts": TIMEOUTS, "head_revision": HEAD_REVISION,
            "stream_cap_bytes": STREAM_CAP, "repository": REPOSITORY,
            "layer_b_launch_adapter": "--user <runner uid:gid> --label on the smoke docker run only",
            "requires": "--execute --baseline <BASELINE> inside GitHub Actions push of the drill branch"}


def main(argv=None, environ=None, popen=subprocess.Popen, port_free=None, disk_free=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--baseline", default="")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if not args.execute:
        print(json.dumps(plan_summary(), indent=2))
        return 0
    try:
        check_gate(args, environ)
        report_path = Path(args.report).resolve()
        if not args.report or not report_path.is_relative_to(Path(environ["RUNNER_TEMP"]).resolve()):
            raise GateError("report_path_outside_runner_temp")
    except GateError as error:
        print(json.dumps({"outcome": "BLOCKED", "gate": str(error), "executed": False}))
        return 2
    redact = Redactor()
    pg_password, api_key, viewer_password = (secrets.token_urlsafe(n) for n in (32, 32, 24))
    for value in (pg_password, api_key, viewer_password):
        redact.add(value)
    run_id = "a1d" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S") + secrets.token_hex(3)
    report = {"run_id": run_id, "steps": [], "executed": True}
    run = Runner(redact, report["steps"], time.monotonic(), popen=popen, spool_dir=environ["RUNNER_TEMP"])
    ledger = Ledger(run_id)
    base = base_env()
    envs = {"base": base, "runner_temp": environ["RUNNER_TEMP"], "viewer_password": viewer_password,
            "docker_pg": dict(base, POSTGRES_PASSWORD=pg_password),
            "source": backend_env(base, app_env="test", database=SOURCE_DB, password=pg_password, api_key=api_key),
            "target": backend_env(base, app_env="development", database=TARGET_DB, password=pg_password,
                                  api_key=api_key)}
    secret_dir = Path(tempfile.mkdtemp(prefix="area1-secret-", dir=environ["RUNNER_TEMP"]))
    ledger.temp_dirs.append(str(secret_dir))
    password_file = secret_dir / "smoke_password"
    outcome, pre = "FAIL", None
    try:
        head = preflight_repo(run, environ, base, report)
        pre = preflight_docker(run, base, ledger, environ, report, port_free, disk_free)
        drill(run, ledger, envs, report, head, password_file)
        outcome = "PASS"
    except GateError as error:
        outcome, report["error"] = "BLOCKED", redact(str(error))
    except StepAborted as error:
        outcome, report["error"] = "ABORTED", redact(str(error))
    except Exception as error:
        report["error"] = redact(f"{type(error).__name__}: {error}")
    finally:
        if pre is not None:
            cleaned, report["cleanup"] = cleanup(run, ledger, envs, pre)
        else:
            shutil.rmtree(secret_dir, ignore_errors=True)
            cleaned, report["cleanup"] = not os.path.lexists(secret_dir), {"docker": "NOTHING_CREATED"}
        if not cleaned:
            outcome = "BLOCKED" if outcome == "PASS" else outcome
            report["cleanup_status"] = "BLOCKED"
        report["ledger"] = {k: v for k, v in asdict(ledger).items() if k != "temp_dirs"}
        report["outcome"] = outcome
        text = redact(json.dumps(report, indent=2, default=str), limit=None)
        report_path.write_text(text, encoding="utf-8")
        print(text)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "ABORTED": 3}[outcome]


if __name__ == "__main__":
    sys.exit(main())
