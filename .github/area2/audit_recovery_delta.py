"""AREA2 data-entry audit recovery delta (TASK047). Stdlib only at import time.

Default: prints the plan and exits 0; no child process, no database.
``--execute`` runs only inside a GitHub Actions job of danit0m/auneron (push to main or
pull request into main) with APP_ENV=test, DATABASE_URL = PostgreSQL on loopback:5432/
auneron_test and the trusted CI_POSTGRES_CONTAINER_ID of the job's postgres:17 service.
These are context checks, NOT cryptographic proof of human approval.

It proves ONLY that the table account_data_entry_audit_events (24 columns, 22 named CHECKs),
its index, its function and enabled trigger, and one synthetic row survive a native
pg_dump/pg_restore into a newly created database at head c3a8f1e6d204 (38 tables), and that
UPDATE/DELETE stay rejected there. The area1 full recovery and Layer B are NOT repeated.
Catalog facts must match strictly, except three named CHECKs whose exact source/target texts are a frozen
allowlist (pg_restore re-renders the varchar ANY array cast); see KNOWN_CHECK_RENDERINGS.

psycopg is imported lazily (execute only). Native tools run through ``docker exec`` in the
service container against 127.0.0.1; the password goes only through the child env
(``-e PGPASSWORD`` carries no value). The dump is a private temp file, never an artifact.
No Docker resource is created, removed, pruned or adopted. Cleanup drops ONLY the database
this run created (same OID + run-marker comment) and TRUNCATEs the audit table ONLY when it
holds exactly this run's row (id + marker + full fingerprint) and was empty before.
Exit codes: 0 PASS / dry plan, 1 FAIL, 2 BLOCKED (gate, precondition or cleanup), 3 ABORTED.
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
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY = "danit0m/auneron"
SOURCE_DB = "auneron_test"
HEAD_REVISION = "c3a8f1e6d204"
TABLE_COUNT = 38
PG_IMAGE = "postgres:17"
TABLE = "account_data_entry_audit_events"
INDEX = "ix_account_data_entry_audit_account_occurred"
INDEX_SUFFIX = "USING btree (account_reference, occurred_at)"
FUNCTION = "account_data_entry_audit_append_only"
TRIGGER = "trg_account_data_entry_audit_append_only"
TRIGGER_TYPE = 1 | 2 | 8 | 16  # pg_trigger.tgtype bits: ROW | BEFORE | DELETE | UPDATE
APPEND_ONLY_MESSAGE = f"{TABLE} is append-only"
COLUMNS = (
    "id", "operation", "channel", "account_reference", "actor_type", "actor_user_id",
    "actor_reference", "auth_session_id", "request_id", "changed_fields", "status_at_operation",
    "before_valor", "after_valor", "before_vencimento", "after_vencimento",
    "before_cliente_sha256", "after_cliente_sha256", "before_email_sha256", "after_email_sha256",
    "before_whatsapp_sha256", "after_whatsapp_sha256", "source_sha256", "source_row", "occurred_at",
)
_DIGESTS = ("before_cliente_sha256", "after_cliente_sha256", "before_email_sha256",
            "after_email_sha256", "before_whatsapp_sha256", "after_whatsapp_sha256", "source_sha256")
CHECKS = frozenset({f"ck_account_data_entry_audit_{name}" for name in (
    "operation", "channel", "legacy_create_only", "account_reference", "actor_type",
    "actor_user_id", "actor_reference", "auth_session_id", "changed_fields_array", "status",
    "source_by_channel", "source_row", "create_shape", "update_shape", "delete_shape",
    *(f"{column}_hex" for column in _DIGESTS))})
EMPTY_AT_START = (TABLE, "users", "accounts", "import_confirmation_receipts")
TARGET_RE = re.compile(r"auneron_audit_recovery_[1-9]\d{0,19}_[1-9]\d{0,2}")
MARKER_RE = re.compile(r"area2-audit-recovery:[1-9]\d{0,19}:[1-9]\d{0,2}:[0-9a-f]{16}")
CONTAINER_RE = re.compile(r"[0-9a-f]{64}")
USER_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1")
# libpq honours these even next to explicit arguments (PGHOSTADDR wins over host): refused.
PG_ENV_OVERRIDES = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE",
                    "PGOPTIONS")
FMT_CONTAINER = "{{.Id}}|{{.Config.Image}}"  # the only docker inspect template ever requested
NATIVE_TOOLS = ("psql", "pg_dump", "pg_restore")
STREAM_CAP, DUMP_CAP, OUTPUT_LIMIT, POLL_SECONDS = 1024 ** 2, 64 * 1024 ** 2, 2000, 0.2
TIMEOUTS = {"inspect": 30, "psql": 30, "pg_dump": 180, "pg_restore": 180}
CONNECT_OPTIONS = "-c statement_timeout=30000 -c lock_timeout=5000 -c TimeZone=UTC"
CONNECT_TIMEOUT = 5
SEED_REFERENCE = 990000001  # synthetic account/actor number; the table has no FK
SEED_VALOR, SEED_VENCIMENTO = Decimal("123.45"), date(2026, 11, 30)
FUNCTION_BODY_LIMIT, FACT_LIMIT = 4096, 16384  # schema_comparison diagnostic bounds (characters)

SQL_IDENTITY = ("SELECT current_database()::text, (SELECT version_num FROM alembic_version), "
                "(SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE')")
SQL_SYSTEM_ID = "SELECT system_identifier::text FROM pg_control_system()"
SQL_COUNTS = "SELECT " + ", ".join(f"(SELECT count(*) FROM {t})" for t in EMPTY_AT_START)
SQL_DB_BY_NAME = ("SELECT oid::bigint, shobj_description(oid, 'pg_database') "
                  "FROM pg_database WHERE datname = %s")
SQL_DB_BY_OID = "SELECT count(*) FROM pg_database WHERE oid = %s"
SQL_COLUMNS = ("SELECT column_name::text FROM information_schema.columns "
               "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position")
SQL_CHECKS = (f"SELECT conname::text, pg_get_constraintdef(oid) FROM pg_constraint "
              f"WHERE conrelid = 'public.{TABLE}'::regclass AND contype = 'c' ORDER BY conname")
SQL_INDEX = ("SELECT indexdef FROM pg_indexes "
             "WHERE schemaname = 'public' AND tablename = %s AND indexname = %s")
SQL_FUNCTION = ("SELECT l.lanname::text, p.prorettype::regtype::text, p.prosrc FROM pg_proc p "
                "JOIN pg_language l ON l.oid = p.prolang JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = %s")
SQL_TRIGGER = (f"SELECT t.tgenabled::text, t.tgtype::int, p.proname::text FROM pg_trigger t "
               f"JOIN pg_proc p ON p.oid = t.tgfoid WHERE t.tgrelid = 'public.{TABLE}'::regclass "
               f"AND t.tgname = %s AND NOT t.tgisinternal")
SQL_ROWS = f"SELECT id, request_id, to_jsonb(e)::text FROM {TABLE} e ORDER BY id"
SQL_SEED = (f"INSERT INTO {TABLE} (operation, channel, account_reference, actor_type, actor_user_id, "
            "actor_reference, request_id, changed_fields, status_at_operation, after_valor, "
            "after_vencimento, after_cliente_sha256) VALUES ('create', 'manual_api', %s, 'user', %s, "
            "%s, %s, '[]'::jsonb, 'aberto', %s, %s, %s) RETURNING id")
SQL_UPDATE_PROBE = f"UPDATE {TABLE} SET request_id = request_id WHERE id = %s"
SQL_DELETE_PROBE = f"DELETE FROM {TABLE} WHERE id = %s"
SQL_LOCK = f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"
SQL_TRUNCATE = f"TRUNCATE TABLE {TABLE}"  # the only TRUNCATE; guarded in truncate_own_row()
SQL_READ_ONLY = "SET TRANSACTION READ ONLY"


def _any_text(column_ref, literals, per_element):
    """Exact pg_get_constraintdef text of ``(column)::text = ANY (...)`` over varchar literals, in one of the
    two renderings seen in CI run 41257863930: whole-array cast to text[] (migrated source) or per-element
    cast to text (pg_restore target). Built only from the frozen constants below, never from catalog input."""
    if per_element:
        array = "ARRAY[" + ", ".join(f"('{v}'::character varying)::text" for v in literals) + "]"
    else:
        array = "(ARRAY[" + ", ".join(f"'{v}'::character varying" for v in literals) + "])::text[]"
    return f"(({column_ref})::text = ANY ({array}))"


# TASK051: the ONLY tolerated source/target CHECK difference. Each name maps to its exact (source, target)
# definition pair; any other difference (literal, operator, type, column, name, presence, reversed or
# otherwise reformatted text) still fails schema_source_target_differ. Equal definitions pass as before.
KNOWN_CHECK_LITERALS = {
    "ck_account_data_entry_audit_channel": ("channel", ("manual_api", "legacy_csv_upload")),
    "ck_account_data_entry_audit_operation": ("operation", ("create", "update", "delete")),
    "ck_account_data_entry_audit_status": ("status_at_operation", ("aberto", "atrasado", "pago")),
}
KNOWN_CHECK_RENDERINGS = {
    name: (f"CHECK ({_any_text(column, literals, False)})", f"CHECK ({_any_text(column, literals, True)})")
    for name, (column, literals) in KNOWN_CHECK_LITERALS.items()}
# Read-only truth-value probes: both renderings must agree with plain membership on fixed trusted values.
# Finite probes support, but do not prove, equivalence; the exact allowlist above is the boundary.
PROBE_REF = "%s::character varying"
PROBE_OUTSIDE = ("", " ", "unknown", "manual-api", "pagó", "créate", "аberto")
CHECK_PROBE_SQL = {name: f"SELECT {_any_text(PROBE_REF, literals, False)}, {_any_text(PROBE_REF, literals, True)}"
                   for name, (_, literals) in KNOWN_CHECK_LITERALS.items()}


def probe_values(literals):
    return (*literals, *(v.upper() for v in literals), *(f"{v} " for v in literals), *PROBE_OUTSIDE, None)


PROBE_COUNT = sum(len(probe_values(literals)) for _, literals in KNOWN_CHECK_LITERALS.values())  # 48


class GateError(Exception):
    """Context or precondition not met: BLOCKED, nothing further is attempted."""


class StepFailed(Exception):
    """A step returned non-zero or a required check did not hold: FAIL."""


class StepAborted(Exception):
    """A child process hit its timeout or output cap and was killed: ABORTED."""


def block(ok, code):
    if not ok:
        raise GateError(code)


def require(ok, code):
    if not ok:
        raise StepFailed(code)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Redactor:
    _dsn = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")

    def __init__(self, values=()):
        self._values = [v for v in values if v]

    def __call__(self, text, limit=OUTPUT_LIMIT):
        for value in self._values:
            text = text.replace(value, "***")
        text = self._dsn.sub(r"\1***@", text)
        return text if limit is None or len(text) <= limit else "[truncated]" + text[-limit:]


@dataclass(frozen=True)
class Context:
    host: str
    user: str
    password: str
    container_id: str
    run_id: str
    attempt: str
    runner_temp: str

    @property
    def target(self):
        return f"auneron_audit_recovery_{self.run_id}_{self.attempt}"


@dataclass
class Ledger:
    initial_audit_count: int | None = None
    marker: str = ""
    seed_attempted: bool = False
    seed_id: int | None = None
    fingerprint: str | None = None
    temp_dir: str = ""
    target_create_attempted: bool = False
    target_oid: int | None = None
    restore_attempted: bool = False


def parse_database_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise GateError("database_url_unparsable") from None
    block(parts.scheme in ("postgresql", "postgresql+psycopg"), "database_url_not_postgresql")
    block(parts.hostname in LOOPBACK_HOSTS and port == 5432, "database_url_not_loopback_5432")
    block(parts.path == f"/{SOURCE_DB}" and not parts.query and not parts.fragment, "database_url_not_auneron_test")
    block(bool(parts.username and USER_RE.fullmatch(parts.username)), "database_url_user")
    block(bool(parts.password), "database_url_password_missing")
    return parts.hostname, parts.username, parts.password


def check_gate(environ):
    """Env-only gate, evaluated before any child process or connection exists."""
    block(environ.get("GITHUB_ACTIONS") == "true", "not_github_actions")
    block(environ.get("GITHUB_REPOSITORY") == REPOSITORY, "wrong_repository")
    event = environ.get("GITHUB_EVENT_NAME")
    if event == "push":
        block(environ.get("GITHUB_REF") == "refs/heads/main", "push_not_main")
    elif event == "pull_request":
        block(environ.get("GITHUB_BASE_REF") == "main", "pull_request_base_not_main")
    else:
        raise GateError("event_not_allowed")
    block(environ.get("APP_ENV") == "test", "app_env_not_test")
    block(not any(name in environ for name in PG_ENV_OVERRIDES), "libpq_env_override_present")
    host, user, password = parse_database_url(environ.get("DATABASE_URL", ""))
    container_id = environ.get("CI_POSTGRES_CONTAINER_ID", "")
    block(bool(CONTAINER_RE.fullmatch(container_id)), "service_container_id_invalid")
    run_id, attempt = environ.get("GITHUB_RUN_ID", ""), environ.get("GITHUB_RUN_ATTEMPT", "")
    block(bool(re.fullmatch(r"[1-9]\d{0,19}", run_id) and re.fullmatch(r"[1-9]\d{0,2}", attempt)),
          "run_id_invalid")
    temp = environ.get("RUNNER_TEMP", "")
    block(bool(temp) and Path(temp).is_absolute() and Path(temp).is_dir(), "runner_temp_invalid")
    ctx = Context(host, user, password, container_id, run_id, attempt, temp)
    block(bool(TARGET_RE.fullmatch(ctx.target)) and ctx.target != SOURCE_DB, "target_name_invalid")
    return ctx


def check_report_path(report, runner_temp):
    path = Path(report).resolve() if report else None
    block(path is not None and path.parent == Path(runner_temp).resolve()
          and path.name == "area2-audit-recovery.json", "report_path_not_in_runner_temp")
    return path


def check_argv(argv, forbidden=()):
    """docker inspect (fixed template) or docker exec of a native PG tool on 127.0.0.1; no env values."""
    if not (isinstance(argv, list) and len(argv) > 2 and all(isinstance(a, str) for a in argv)):
        raise TypeError("argv must be a list of str")
    if any(value and value in a for value in forbidden for a in argv):
        raise ValueError("secret_in_argv")
    if argv[:2] == ["docker", "inspect"]:
        if len(argv) != 7 or argv[2:6] != ["--type", "container", "--format", FMT_CONTAINER] \
                or not CONTAINER_RE.fullmatch(argv[6]):
            raise ValueError("docker_inspect_refused")
        return
    head = ["docker", "exec", "-i", "-e", "PGPASSWORD"] if argv[2:3] == ["-i"] else \
        ["docker", "exec", "-e", "PGPASSWORD"]
    rest = argv[len(head):]
    if argv[:len(head)] != head or len(rest) < 6 or not CONTAINER_RE.fullmatch(rest[0]) \
            or rest[1] not in NATIVE_TOOLS or rest[2:6] != ["-h", "127.0.0.1", "-p", "5432"]:
        raise ValueError("docker_command_refused")
    if any(a in ("-e", "--env", "--env-file") or a.startswith("--env") for a in rest):
        raise ValueError("env_value_in_argv")


def exec_argv(ctx, tool, database, *extra, stdin=False):
    return ["docker", "exec", *(["-i"] if stdin else []), "-e", "PGPASSWORD", ctx.container_id, tool,
            "-h", "127.0.0.1", "-p", "5432", "-U", ctx.user, "-d", database, *extra]


@dataclass
class Result:
    code: int
    out: str
    err: str


class Runner:
    """Structured argv only; stdout/stderr spooled to private files with a byte cap; a timeout or
    cap overflow kills the process group and ABORTS. Killing ``docker exec`` does not stop the tool
    inside the container: cleanup then fails closed (the database stays busy, DROP is refused)."""

    def __init__(self, redact, steps, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep,
                 spool_dir=None, forbidden=()):
        self.redact, self.steps, self.popen, self.clock, self.sleep = redact, steps, popen, clock, sleep
        self.spool_dir, self.forbidden = spool_dir, forbidden

    def __call__(self, name, argv, *, env, timeout, stdin_path=None, stdout_path=None, cap=STREAM_CAP,
                 check=True):
        check_argv(argv, self.forbidden)
        began = self.clock()
        with ExitStack() as stack:
            out_f = stack.enter_context(open(stdout_path, "wb") if stdout_path
                                        else tempfile.TemporaryFile(dir=self.spool_dir))
            err_f = stack.enter_context(tempfile.TemporaryFile(dir=self.spool_dir))
            in_f = stack.enter_context(open(stdin_path, "rb")) if stdin_path else subprocess.DEVNULL
            proc = self.popen(argv, env=env, stdin=in_f, stdout=out_f, stderr=err_f, start_new_session=True)
            state = self._watch(proc, ((out_f, cap), (err_f, STREAM_CAP)), began + timeout)
            if state != "EXITED":
                _kill_group(proc)
            out = _read_capped(out_f) if state == "EXITED" and not stdout_path else ""
            err = _read_capped(err_f) if state == "EXITED" else ""
        status = state if state != "EXITED" else ("OK" if proc.returncode == 0 else "NONZERO")
        self.steps.append({"step": name, "argv": [self.redact(a) for a in argv],
                           "exit": proc.returncode if state == "EXITED" else None,
                           "seconds": round(self.clock() - began, 1), "status": status,
                           "stderr_tail": self.redact(err)[-800:] if status != "OK" else ""})
        if state != "EXITED":
            raise StepAborted(f"{name}:{state.lower()}")
        if check and proc.returncode != 0:
            raise StepFailed(name)
        return Result(proc.returncode, out, err)

    def _watch(self, proc, streams, deadline):
        while True:
            exited = proc.poll() is not None
            if any(os.fstat(f.fileno()).st_size > limit for f, limit in streams):
                return "OUTPUT_CAP"
            if exited:
                return "EXITED"
            if self.clock() >= deadline:
                return "TIMEOUT"
            self.sleep(POLL_SECONDS)


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


class Postgres:
    """Short host connections to the gated server only; psycopg is imported on first use."""

    def __init__(self, ctx, connect=None):
        self.ctx, self._connect = ctx, connect

    def open(self, database, autocommit=False):
        if database not in (SOURCE_DB, self.ctx.target):
            raise ValueError("database_not_allowed")
        if self._connect is None:
            import psycopg  # lazy: never imported by the dry plan or the offline tests

            self._connect = psycopg.connect
        return self._connect(host=self.ctx.host, hostaddr="127.0.0.1", port=5432, user=self.ctx.user,
                             password=self.ctx.password, dbname=database, connect_timeout=CONNECT_TIMEOUT,
                             options=CONNECT_OPTIONS, autocommit=autocommit)


def rows(conn, sql, params=None):
    return [tuple(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql, params=None):
    found = rows(conn, sql, params)
    require(len(found) == 1, "expected_one_row")
    return found[0]


def rejected(conn, sql, params):
    """True only if the statement raised the trigger's error; the transaction is always rolled back."""
    try:
        conn.execute(sql, params)
    except Exception as error:  # psycopg.errors.RaiseException (P0001) is the expected path
        conn.rollback()
        return getattr(error, "sqlstate", None) == "P0001" and APPEND_ONLY_MESSAGE in str(error)
    conn.rollback()
    return False


def schema_facts(conn, bodies=None):
    """Catalog facts only (no rows). ``bodies`` collects the raw source of FUNCTION, kept out of the facts."""
    functions = rows(conn, SQL_FUNCTION, (FUNCTION,))
    if bodies is not None:
        bodies.extend(src for _, _, src in functions)
    return {
        "columns": [r[0] for r in rows(conn, SQL_COLUMNS, (TABLE,))],
        "checks": [list(r) for r in rows(conn, SQL_CHECKS)],
        "index": [r[0] for r in rows(conn, SQL_INDEX, (TABLE, INDEX))],
        "function": [[lang, ret, sha256_text(src), APPEND_ONLY_MESSAGE in src] for lang, ret, src in functions],
        "trigger": [list(r) for r in rows(conn, SQL_TRIGGER, (TRIGGER,))],
    }


def _bounded_json(value, limit=FACT_LIMIT):
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else {"truncated": True, "chars": len(text), "json_prefix": text[:limit]}


def _bounded_body(src, limit=FUNCTION_BODY_LIMIT):
    return {"text": src[:limit], "chars": len(src), "truncated": len(src) > limit}


def compare_schema(source, target, source_bodies, target_bodies):
    """Diagnostic only (TASK050): reports which catalog facts differ, verbatim and bounded. It never decides
    the outcome; ``facts_match`` (strict equality plus the TASK051 exact allowlist) is the gate."""
    keys = sorted(set(source) | set(target))
    differing = [k for k in keys if source.get(k) != target.get(k)]
    comparison = {"equal": source == target, "differing_keys": differing}
    if differing:
        comparison["details"] = {k: {
            "source": _bounded_json(source.get(k)), "target": _bounded_json(target.get(k)),
            "source_count": len(source.get(k) or []), "target_count": len(target.get(k) or []),
            "source_only": _bounded_json([x for x in source.get(k) or [] if x not in (target.get(k) or [])]),
            "target_only": _bounded_json([x for x in target.get(k) or [] if x not in (source.get(k) or [])]),
        } for k in differing}
    if "function" in differing:  # raw body of FUNCTION only; no other function or object is read
        comparison["function_bodies"] = {"name": FUNCTION, "limit_chars": FUNCTION_BODY_LIMIT,
                                         "source": [_bounded_body(s) for s in source_bodies],
                                         "target": [_bounded_body(s) for s in target_bodies]}
    return comparison


def facts_match(source, target):
    """TASK051 gate: every fact is strictly equal, except that a CHECK listed in KNOWN_CHECK_RENDERINGS may
    differ by exactly its frozen (source, target) pair. Returns (matched, names accepted via that pair)."""
    if set(source) != set(target) or any(source[k] != target[k] for k in source if k != "checks"):
        return False, []
    src, tgt = source.get("checks"), target.get("checks")
    if not (isinstance(src, list) and isinstance(tgt, list) and len(src) == len(tgt)):
        return False, []
    accepted = []
    for s, t in zip(src, tgt):
        if s == t:
            continue
        if not (isinstance(s, (list, tuple)) and isinstance(t, (list, tuple)) and len(s) == len(t) == 2
                and s[0] == t[0] and KNOWN_CHECK_RENDERINGS.get(s[0]) == (s[1], t[1])):
            return False, []
        accepted.append(s[0])
    return True, accepted


def probe_check_renderings(conn):
    """Read-only transaction; one parameterised SELECT per fixed value; no table, no catalog text used."""
    conn.rollback()
    conn.execute(SQL_READ_ONLY)
    count, mismatches = 0, []
    for name in sorted(CHECK_PROBE_SQL):
        literals = KNOWN_CHECK_LITERALS[name][1]
        for value in probe_values(literals):
            src, tgt = one(conn, CHECK_PROBE_SQL[name], (value, value))
            expected = None if value is None else value in literals
            count += 1
            if not (src is expected and tgt is expected):
                mismatches.append({"check": name, "value": value, "expected": expected, "source_form": src,
                                   "target_form": tgt})
    conn.rollback()
    return {"count": count, "expected_count": PROBE_COUNT, "passed": count - len(mismatches),
            "mismatches": mismatches}


def check_schema(facts, where):
    require(tuple(facts["columns"]) == COLUMNS, f"{where}_columns")
    names = [name for name, _ in facts["checks"]]
    require(len(names) == len(CHECKS) == 22 and set(names) == CHECKS, f"{where}_check_constraints")
    require(len(facts["index"]) == 1 and facts["index"][0].endswith(INDEX_SUFFIX), f"{where}_index")
    require(len(facts["function"]) == 1 and facts["function"][0][:2] == ["plpgsql", "trigger"]
            and facts["function"][0][3] is True, f"{where}_function")
    require(facts["trigger"] == [["O", TRIGGER_TYPE, FUNCTION]], f"{where}_trigger_not_enabled")


def own_row(found, ledger):
    return (len(found) == 1 and found[0][0] == ledger.seed_id and found[0][1] == ledger.marker
            and ledger.fingerprint is not None and sha256_text(found[0][2]) == ledger.fingerprint)


def seed_params(marker):
    digest = hashlib.sha256(b"auneron:account-data-entry-audit:v1:cliente\x00" + marker.encode()).hexdigest()
    return (SEED_REFERENCE, SEED_REFERENCE, f"user:{SEED_REFERENCE}", marker, SEED_VALOR, SEED_VENCIMENTO,
            digest)


def drill(ctx, db, run, ledger, report, envs):
    fields = run("inspect", ["docker", "inspect", "--type", "container", "--format", FMT_CONTAINER,
                             ctx.container_id], env=envs["base"], timeout=TIMEOUTS["inspect"]).out.strip()
    block(fields.split("|") == [ctx.container_id, PG_IMAGE], "service_container_mismatch")
    with closing(db.open(SOURCE_DB)) as conn:
        current, revision, tables = one(conn, SQL_IDENTITY)
        block(current == SOURCE_DB, "source_current_database")
        block(revision == HEAD_REVISION, "source_head_not_c3a8f1e6d204")
        block(tables == TABLE_COUNT, "source_table_count_not_38")
        host_system = one(conn, SQL_SYSTEM_ID)[0]
        counts = one(conn, SQL_COUNTS)
        block(all(c == 0 for c in counts), "source_not_empty_ci_baseline")
        block(not rows(conn, SQL_DB_BY_NAME, (ctx.target,)), "target_preexists")
        source_bodies = []
        source_facts = schema_facts(conn, source_bodies)
        conn.rollback()
    docker_system = run("system_identifier", exec_argv(ctx, "psql", SOURCE_DB, "-X", "-v", "ON_ERROR_STOP=1",
                                                       "-tAc", SQL_SYSTEM_ID),
                        env=envs["pg"], timeout=TIMEOUTS["psql"]).out.strip()
    block(bool(re.fullmatch(r"\d+", docker_system)) and docker_system == host_system, "server_identity_mismatch")
    ledger.initial_audit_count = counts[0]
    check_schema(source_facts, "source")
    report["source"] = {"database": SOURCE_DB, "revision": revision, "tables": tables,
                        "baseline_counts": dict(zip(EMPTY_AT_START, counts)), "same_server": True}

    ledger.marker = f"area2-audit-recovery:{ctx.run_id}:{ctx.attempt}:{secrets.token_hex(8)}"
    require(bool(MARKER_RE.fullmatch(ledger.marker)), "marker_invalid")
    with closing(db.open(SOURCE_DB)) as conn:
        ledger.seed_attempted = True
        ledger.seed_id = one(conn, SQL_SEED, seed_params(ledger.marker))[0]
        found = rows(conn, SQL_ROWS)
        require(len(found) == 1 and found[0][:2] == (ledger.seed_id, ledger.marker), "seed_readback")
        ledger.fingerprint = sha256_text(found[0][2])
        conn.commit()
    report["seed"] = {"id": ledger.seed_id, "marker": ledger.marker, "fingerprint_sha256": ledger.fingerprint}

    ledger.temp_dir = tempfile.mkdtemp(prefix="area2-audit-", dir=ctx.runner_temp)  # mode 0700
    dump = Path(ledger.temp_dir) / "source.dump"
    os.close(os.open(dump, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    run("pg_dump", exec_argv(ctx, "pg_dump", SOURCE_DB, "-Fc", "--no-owner", "--no-acl"), env=envs["pg"],
        timeout=TIMEOUTS["pg_dump"], stdout_path=dump, cap=DUMP_CAP)
    data = dump.read_bytes()
    require(data.startswith(b"PGDMP"), "dump_not_custom_format")
    report["dump"] = {"format": "custom", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    with closing(db.open(SOURCE_DB, autocommit=True)) as conn:
        block(not rows(conn, SQL_DB_BY_NAME, (ctx.target,)), "target_preexists")
        ledger.target_create_attempted = True
        conn.execute(f'CREATE DATABASE "{ctx.target}" TEMPLATE template0')
        oid, comment = one(conn, SQL_DB_BY_NAME, (ctx.target,))
        require(comment is None, "target_comment_unexpected")
        conn.execute(f"COMMENT ON DATABASE \"{ctx.target}\" IS '{ledger.marker}'")
        require(one(conn, SQL_DB_BY_NAME, (ctx.target,)) == (oid, ledger.marker), "target_comment_not_set")
        ledger.target_oid = oid
    report["target"] = {"database": ctx.target, "oid": ledger.target_oid}

    ledger.restore_attempted = True
    run("pg_restore", exec_argv(ctx, "pg_restore", ctx.target, "--no-owner", "--no-acl", "--exit-on-error",
                                "--single-transaction", stdin=True),
        env=envs["pg"], timeout=TIMEOUTS["pg_restore"], stdin_path=dump)

    with closing(db.open(ctx.target)) as conn:
        current, revision, tables = one(conn, SQL_IDENTITY)
        require((current, revision, tables) == (ctx.target, HEAD_REVISION, TABLE_COUNT), "target_identity")
        target_bodies = []
        target_facts = schema_facts(conn, target_bodies)
        comparison = compare_schema(source_facts, target_facts, source_bodies, target_bodies)
        matched, accepted = facts_match(source_facts, target_facts)
        # "equal" stays the raw comparison; acceptance of known renderings is reported separately
        comparison["accepted_known_check_renderings"] = accepted
        comparison["decision"] = ("EQUAL" if comparison["equal"] else
                                  "EQUAL_EXCEPT_KNOWN_CHECK_RENDERINGS" if matched else "DIFFERENT")
        report["schema_comparison"] = comparison
        check_schema(target_facts, "target")
        report["check_rendering_probes"] = probes = probe_check_renderings(conn)
        require(matched, "schema_source_target_differ")
        require(probes["count"] == PROBE_COUNT and probes["mismatches"] == [], "check_rendering_probe_mismatch")
        target_rows = rows(conn, SQL_ROWS)
        require(own_row(target_rows, ledger), "target_row_not_restored_exactly")
        conn.rollback()
        for name, sql in (("update", SQL_UPDATE_PROBE), ("delete", SQL_DELETE_PROBE)):
            require(rejected(conn, sql, (ledger.seed_id,)), f"target_{name}_not_rejected")
        require(rows(conn, SQL_ROWS) == target_rows, "target_row_changed_by_probe")
        conn.rollback()
    with closing(db.open(SOURCE_DB)) as conn:
        source_rows = rows(conn, SQL_ROWS)
        conn.rollback()
    require(own_row(source_rows, ledger), "source_row_changed")
    table_digest = sha256_text("\n".join(r[2] for r in source_rows))
    require(table_digest == sha256_text("\n".join(r[2] for r in target_rows)), "table_hash_differs")
    report["delta"] = {"tables": tables, "revision": revision, "columns": len(COLUMNS),
                       "check_constraints": sorted(CHECKS), "index": INDEX, "function": FUNCTION,
                       "trigger": {"name": TRIGGER, "tgenabled": "O", "tgtype": TRIGGER_TYPE},
                       "audit_rows": 1, "table_sha256": table_digest,
                       "update_rejected": True, "delete_rejected": True}


def drop_target(ctx, db, ledger):
    if not ledger.target_create_attempted:
        return "NOT_CREATED"
    require(bool(TARGET_RE.fullmatch(ctx.target)) and ctx.target != SOURCE_DB, "target_name_guard")
    with closing(db.open(SOURCE_DB, autocommit=True)) as conn:
        found = rows(conn, SQL_DB_BY_NAME, (ctx.target,))
        if not found:
            return "ABSENT"
        if ledger.target_oid is None or found[0] != (ledger.target_oid, ledger.marker):
            return "REFUSED:target_not_owned"
        conn.execute(f'DROP DATABASE "{ctx.target}"')
        require(not rows(conn, SQL_DB_BY_NAME, (ctx.target,))
                and one(conn, SQL_DB_BY_OID, (ledger.target_oid,))[0] == 0, "target_still_present")
    return "DROPPED_VERIFIED"


def truncate_own_row(db, ledger):
    if not ledger.seed_attempted:
        return "NOT_SEEDED"
    with closing(db.open(SOURCE_DB)) as conn:
        conn.execute(SQL_LOCK)  # bounded by lock_timeout; held until commit/rollback
        found = rows(conn, SQL_ROWS)
        if not found:
            conn.rollback()
            return "ABSENT"
        if ledger.initial_audit_count != 0 or not own_row(found, ledger):
            conn.rollback()
            return "REFUSED:unexpected_source_rows"
        conn.execute(SQL_TRUNCATE)
        conn.commit()
    return "TRUNCATED"


def remove_temp(ledger):
    if not ledger.temp_dir:
        return "NOT_CREATED"
    shutil.rmtree(ledger.temp_dir, ignore_errors=True)
    require(not os.path.lexists(ledger.temp_dir), "temp_dir_still_present")
    return "REMOVED"


def final_state(ctx, db):
    with closing(db.open(SOURCE_DB)) as conn:
        audit = one(conn, f"SELECT count(*) FROM {TABLE}")[0]
        target = rows(conn, SQL_DB_BY_NAME, (ctx.target,))
        conn.rollback()
    require(audit == 0, "source_audit_not_empty")
    require(not target, "target_present")
    return "VERIFIED"


CLEAN = frozenset({"NOT_CREATED", "ABSENT", "DROPPED_VERIFIED", "NOT_SEEDED", "TRUNCATED", "REMOVED",
                   "VERIFIED"})


def cleanup(ctx, db, ledger):
    status = {}
    for name, action in (("target_database", lambda: drop_target(ctx, db, ledger)),
                         ("source_audit_row", lambda: truncate_own_row(db, ledger)),
                         ("temp_files", lambda: remove_temp(ledger)),
                         ("final_state", lambda: final_state(ctx, db))):
        try:
            status[name] = action()
        except Exception as error:  # every failure is reported; none hides another
            status[name] = f"FAILED:{type(error).__name__}:{error}"
    return all(v in CLEAN for v in status.values()), status


def plan_summary():
    return {"mode": "DRY_PLAN", "executed": False, "repository": REPOSITORY,
            "events": ["push refs/heads/main", "pull_request base main"], "source_database": SOURCE_DB,
            "head_revision": HEAD_REVISION, "tables": TABLE_COUNT, "table": TABLE, "checks": len(CHECKS),
            "index": INDEX, "function": FUNCTION, "trigger": TRIGGER, "service_image": PG_IMAGE,
            "target_database": "auneron_audit_recovery_<GITHUB_RUN_ID>_<GITHUB_RUN_ATTEMPT>",
            "timeouts": TIMEOUTS, "dump_cap_bytes": DUMP_CAP, "stream_cap_bytes": STREAM_CAP,
            "requires": "--execute --report $RUNNER_TEMP/area2-audit-recovery.json inside the gated CI job"}


def main(argv=None, environ=None, popen=subprocess.Popen, connect=None, clock=time.monotonic, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(plan_summary(), indent=2))
        return 0
    environ = os.environ if environ is None else environ
    try:
        ctx = check_gate(environ)
        report_path = check_report_path(args.report, ctx.runner_temp)
    except GateError as error:
        print(json.dumps({"outcome": "BLOCKED", "gate": str(error), "executed": False}))
        return 2
    redact = Redactor((ctx.password, environ.get("DATABASE_URL", "")))
    report = {"task": "AREA2-TASK047", "executed": True, "steps": [],
              "claim": "audit table/constraints/index/function+trigger/one row survive native dump/restore only"}
    run = Runner(redact, report["steps"], popen=popen, clock=clock, sleep=sleep, spool_dir=ctx.runner_temp,
                 forbidden=(ctx.password,))
    base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
    envs = {"base": base, "pg": dict(base, PGPASSWORD=ctx.password)}
    db, ledger, outcome = Postgres(ctx, connect), Ledger(), "FAIL"
    try:
        drill(ctx, db, run, ledger, report, envs)
        outcome = "PASS"
    except GateError as error:
        outcome, report["error"] = "BLOCKED", redact(str(error))
    except StepAborted as error:
        outcome, report["error"] = "ABORTED", redact(str(error))
    except Exception as error:
        report["error"] = redact(f"{type(error).__name__}: {error}")
    finally:
        cleaned, report["cleanup"] = cleanup(ctx, db, ledger)
        if not cleaned:
            outcome = "BLOCKED" if outcome == "PASS" else outcome
            report["cleanup_status"] = "BLOCKED"
        report["ledger"] = asdict(ledger)
        report["outcome"] = outcome
        text = redact(json.dumps(report, indent=2, default=str), limit=None)
        report_path.write_text(text, encoding="utf-8")
        print(text)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "ABORTED": 3}[outcome]


if __name__ == "__main__":
    sys.exit(main())
