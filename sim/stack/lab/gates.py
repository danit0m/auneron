"""
Gates S-1..S-10 do SIM-1.4 (Design Freeze V1.1, sec. 7).

Cada fase grava sua evidencia em `<lab_home>/<instance>/evidence.json`
(fora do repositorio). GuardViolation => ABORT imediato, sem corrigir o
ambiente. Nenhum dado de cenario (nh-*-v2, world/agenda, Oracle) e usado.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sim.stack.lab import activation
from sim.stack.lab import build
from sim.stack.lab import devsnapshot
from sim.stack.lab import provision
from sim.stack.lab import sqlro
from sim.stack.lab.clock import ClockController
from sim.stack.lab.compose import compose
from sim.stack.lab.compose import compose_env
from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import STACK_DIR
from sim.stack.lab.config import lab_home
from sim.stack.lab.config import load_config
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.guard import assert_compose_safe
from sim.stack.lab.guard import check_resolved_compose
from sim.stack.lab.guard import require_container
from sim.stack.lab.guard import require_http_target
from sim.stack.lab.guard import require_project
from sim.stack.lab.labhttp import LabClient
from sim.stack.lab.labsecrets import all_values
from sim.stack.lab.labsecrets import instance_dir
from sim.stack.lab.labsecrets import load_or_create
from sim.stack.lab.quiescence import HarnessError
from sim.stack.lab.quiescence import backend_logs_since
from sim.stack.lab.quiescence import barrier
from sim.stack.lab.runner import Runner

BACKEND = "auneron-sim-backend"
DEV_NAMES = ("postgres", "backend", "frontend", "migration", "auneron-postgres", "auneron-backend",
             "auneron-frontend", "auneron-postgres-prod", "auneron-backend-prod")
IMPOSSIBLE_EVENT = "sim.harness.impossible_event"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_records(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                records.append(json.loads(line))
            except ValueError:
                pass
    return records


class Lab:
    def __init__(self, instance_id: str | None = None, new: bool = False) -> None:
        self.config = load_config()
        home = lab_home()
        home.mkdir(parents=True, exist_ok=True)
        current = home / "current_instance"
        if instance_id is None and not new and current.exists():
            instance_id = current.read_text(encoding="utf-8").strip()
        if instance_id is None:
            instance_id = "sim14-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        current.write_text(instance_id, encoding="utf-8")
        self.instance_id = instance_id
        self.dir = instance_dir(instance_id)
        users = [p["user"] for p in self.config["personas"]] + [self.config["probe"]["user"]]
        self.secrets = load_or_create(instance_id, users)
        self.runner = Runner(all_values(self.secrets))
        self.evidence_file = self.dir / "evidence.json"
        if self.evidence_file.exists():
            self.evidence = json.loads(self.evidence_file.read_text(encoding="utf-8"))
        else:
            self.evidence = {"instance_id": instance_id, "config_sha256": self.config.sha256,
                             "commit": self.config.commit, "gates": {}}
        self.clock = ClockController(self.runner, self.config, self.dir / "clock_state.json")

    # -- infraestrutura -------------------------------------------------
    def save(self) -> None:
        self.evidence_file.write_text(json.dumps(self.evidence, indent=1, sort_keys=True, default=str),
                                      encoding="utf-8")

    def record(self, gate: str, passed: bool, data: dict) -> dict:
        entry = {"status": "PASS" if passed else "FAIL", "recorded_at_real_utc": _now_iso(), **data}
        self.evidence["gates"][gate] = entry
        self.save()
        return entry

    def env(self) -> dict:
        return compose_env(self.config, self.secrets, self.evidence.get("floor_t0") or "")

    def client(self) -> LabClient:
        return LabClient(self.config, self.secrets["api_key"])

    def login(self, user: str) -> LabClient:
        client = self.client()
        response = client.login(self.config.email(user), self.secrets["users"][user])
        if response.status != 200:
            raise HarnessError(f"login {user} = {response.status}")
        return client

    def wait_ready(self) -> dict:
        timeout = self.config["startup"]["ready_timeout_s"]
        started = time.time()
        last = None
        while time.time() - started <= timeout:
            try:
                response = self.client().request("GET", "/ready", timeout=10)
                last = response.status
                if response.status == 200:
                    return {"ready": True, "after_s": round(time.time() - started, 1), "body": response.json()}
            except OSError as error:
                last = type(error).__name__
            time.sleep(3)
        return {"ready": False, "last": last, "timeout_s": timeout}

    def startup_record(self, since_epoch: float) -> dict | None:
        text = backend_logs_since(self.runner, self.config, int(since_epoch))
        started = [r for r in log_records(text) if r.get("message") == "application_started"]
        if not started:
            return None
        keep = ("environment", "maintenance_enabled", "database_online", "evidence_worker_enabled",
                "evidence_floor", "evidence_floor_state", "evidence_floor_age_seconds", "forwarded_allow_ips")
        record = started[-1]
        out = {k: record.get(k) for k in keep}
        out.update({k: v for k, v in record.items() if k.startswith(("build_", "git_", "producer"))})
        return out

    def skew(self) -> dict:
        client = self.client()
        t0 = time.time()
        response = client.request("GET", "/health")
        t1 = time.time()
        rows = sqlro.query(self.runner, self.config, "select extract(epoch from clock_timestamp())")
        t2 = time.time()
        app = response.date.timestamp() if response.date else None
        db = float(rows[0][0])
        expected_app = self.clock.expected_at((t0 + t1) / 2)
        expected_db = self.clock.expected_at((t1 + t2) / 2)
        out = {
            "virtual_expected": datetime.fromtimestamp(expected_db, self.clock.tz).isoformat(timespec="seconds"),
            "app_minus_expected_s": None if app is None else round(app - expected_app, 2),
            "db_minus_expected_s": round(db - expected_db, 2),
            "app_minus_db_s": None if app is None else round(app - db, 2),
            "db_sample_uncertainty_s": round((t2 - t1) / 2, 2),
        }
        limit = self.config["clock"]["max_skew_s"]
        values = [out["app_minus_expected_s"], out["db_minus_expected_s"], out["app_minus_db_s"]]
        out["ok"] = all(v is not None and abs(v) <= limit for v in values)
        return out

    def technical_snapshot(self, probe: LabClient) -> dict:
        """Sec. 5.2: so sinais tecnicos; campos de tempo excluidos."""
        snap = {}
        health = self.client().request("GET", "/health")
        body = health.json() or {}
        snap["health"] = [health.status, body.get("status"), body.get("version")]
        ready = self.client().request("GET", "/ready")
        body = ready.json() or {}
        snap["ready"] = [ready.status, body.get("status"), body.get("database")]
        me = probe.request("GET", "/auth/me")
        user = (me.json() or {}).get("user", {})
        snap["probe_me"] = [me.status, user.get("id"), user.get("role"), user.get("active")]
        item_id = self.evidence.get("probe_work_item", {}).get("id")
        if item_id is not None:
            item = probe.request("GET", f"/work-items/{item_id}")
            body = item.json() or {}
            snap["probe_item"] = [item.status, body.get("id"), body.get("version"), body.get("status"),
                                  body.get("title")]
        accounts = probe.request("GET", "/accounts/")
        snap["accounts"] = [accounts.status, accounts.json() if accounts.status == 200 else None]
        return snap

    def run_barrier(self, since_epoch: float, events: list, params: dict | None = None) -> dict:
        probe = self.login(self.config["probe"]["user"])
        return barrier(self.runner, self.config, since_epoch=since_epoch, expected_events=events,
                       snapshot_fn=lambda: self.technical_snapshot(probe), params=params)

    def events(self) -> list:
        q = self.config["quiescence"]
        extra = q["events_after_floor_extra"] if self.evidence.get("floor_t0") else []
        return list(q["events_before_floor"]) + list(extra)

    def backend_exec(self, *args, timeout: int = 300):
        require_container(BACKEND, self.config)
        return self.runner.run(["docker", "exec", BACKEND, *args], timeout=timeout)


# ---------------------------------------------------------------------------
# guarda (antes de qualquer recurso)
# ---------------------------------------------------------------------------

def guard_negatives(lab: Lab, resolved: dict) -> dict:
    out = {}
    for label, func in (
        ("http_port_8000", lambda: require_http_target("http://127.0.0.1:8000", lab.config)),
        ("http_host_localhost_8100", lambda: require_http_target("http://localhost:8100", lab.config)),
        ("project_backend", lambda: require_project("backend", lab.config)),
        ("container_auneron_backend", lambda: require_container("auneron-backend", lab.config)),
        ("container_auneron_postgres", lambda: require_container("auneron-postgres", lab.config)),
    ):
        try:
            func()
            out[label] = "NOT_REFUSED"
        except GuardViolation:
            out[label] = "REFUSED"
    mutations = {
        "project_name_backend": lambda r: r.__setitem__("name", "backend"),
        "port_8000": lambda r: r["services"]["sim-backend"]["ports"][0].__setitem__("published", "8000"),
        "port_0_0_0_0": lambda r: r["services"]["sim-backend"]["ports"][0].__setitem__("host_ip", "0.0.0.0"),
        "database_auneron_test": lambda r: r["services"]["sim-backend"]["environment"].__setitem__(
            "DATABASE_URL", "postgresql+psycopg://x:y@postgres:5432/auneron_test"),
        "bind_mount_clock": lambda r: r["services"]["sim-backend"]["volumes"][0].__setitem__("type", "bind"),
        "no_cache_off": lambda r: r["services"]["sim-backend"]["environment"].__setitem__("FAKETIME_NO_CACHE", "0"),
        "app_env_development": lambda r: r["services"]["sim-backend"]["environment"].__setitem__(
            "APP_ENV", "development"),
        "volume_dev_name": lambda r: next(iter(r["volumes"].values())).__setitem__("name", "backend_postgres_data"),
    }
    for label, mutate in mutations.items():
        mutated = copy.deepcopy(resolved)
        mutate(mutated)
        out[f"compose_{label}"] = "REFUSED" if check_resolved_compose(mutated, lab.config) else "NOT_REFUSED"
    return out


def phase_guard(lab: Lab) -> dict:
    resolved = assert_compose_safe(lab.runner, lab.config, lab.env())
    negatives = guard_negatives(lab, resolved)
    services = sorted(resolved.get("services", {}))
    passed = all(v == "REFUSED" for v in negatives.values())
    return lab.record("GUARD", passed, {"resolved_services": services, "problems": [], "negatives": negatives})


def phase_snapshot(lab: Lab, label: str) -> dict:
    snap = devsnapshot.capture(lab.runner, lab.dir / f"dev_snapshot_{label}.json")
    data = {"counts": {k: len(v) for k, v in snap["docker"].items()}, "auneron_test": snap["auneron_test"],
            "sha256": hashlib.sha256((lab.dir / f"dev_snapshot_{label}.json").read_bytes()).hexdigest()}
    ok = "error" not in snap["auneron_test"]
    if label == "after":
        # comparado ao snapshot ANTES da 1a operacao live E ao pre-snapshot canonico
        for base in ("before", "pre_canonical"):
            path = lab.dir / f"dev_snapshot_{base}.json"
            if path.exists():
                diff = devsnapshot.diff(json.loads(path.read_text(encoding="utf-8")), snap)
                data[f"diff_vs_{base}"] = diff
                ok = ok and not diff
            else:
                data[f"diff_vs_{base}"] = "MISSING"
                ok = False
    return lab.record(f"DEV_SNAPSHOT_{label.upper()}", ok, data)


def phase_s1_reuse(lab: Lab) -> dict:
    """Reuso do S-1 so com prova MECANICA de que os insumos das imagens nao mudaram:
    mesmos image IDs do S-1; Dockerfiles derivados sem COPY/ADD (o contexto nao entra na
    imagem) e com mtime anterior ao build; commit inalterado; C1-C5 re-verificados."""
    s1 = lab.evidence["gates"]["S-1"]
    current_ids = {name: build.image_id(lab.runner, name) for name in s1["image_ids"]}
    dockerfiles = {}
    built_at = datetime.fromisoformat(s1["recorded_at_real_utc"].replace("Z", "+00:00")).timestamp()
    for name in ("backend-faketime.Dockerfile", "postgres-faketime.Dockerfile"):
        path = STACK_DIR / name
        text = path.read_text(encoding="utf-8")
        dockerfiles[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "mtime_before_s1": path.stat().st_mtime < built_at,
                             "no_copy_add": not any(line.strip().upper().startswith(("COPY", "ADD"))
                                                    for line in text.splitlines())}
    base_verify = build.verify_identity(lab.runner, lab.config.image("base"), lab.config.commit)
    backend_verify = build.verify_identity(lab.runner, lab.config.image("backend"), lab.config.commit)
    data = {"image_ids_s1": s1["image_ids"], "image_ids_now": current_ids,
            "image_ids_identical": current_ids == s1["image_ids"], "dockerfiles": dockerfiles,
            "commit": lab.config.commit, "base_verify": base_verify, "backend_verify": backend_verify}
    passed = (data["image_ids_identical"] and all(d["mtime_before_s1"] and d["no_copy_add"]
                                                  for d in dockerfiles.values())
              and _verify_ok(base_verify) and _verify_ok(backend_verify))
    return lab.record("S-1_REUSE", passed, data)


# ---------------------------------------------------------------------------
# S-1 .. S-10
# ---------------------------------------------------------------------------

def _verify_ok(verify: dict | None) -> bool:
    return bool(verify) and verify["code"] == 0 and "PASS" in verify["verdict"] and \
        not any(c.startswith("[FAIL]") for c in verify["checks"])


def phase_build(lab: Lab) -> dict:
    evidence = build.build_all(lab.runner, lab.config, lab_home() / "build")
    passed = (evidence["base_build"]["code"] == 0 and _verify_ok(evidence.get("base_verify"))
              and evidence.get("backend_build", {}).get("code") == 0 and _verify_ok(evidence.get("backend_verify"))
              and evidence.get("postgres_build", {}).get("code") == 0
              and all(evidence.get("image_ids", {}).values()))
    return lab.record("S-1", passed, evidence)


def phase_up(lab: Lab) -> dict:
    env = lab.env()
    data = {}
    clock_up = compose(lab.runner, lab.config, env, "up", "-d", "sim-clock")
    data["clock_up_code"] = clock_up.code
    if clock_up.code != 0:
        return lab.record("S-2", False, {**data, "tail": clock_up.text[-600:]})
    data["clock_d0"] = lab.clock.set(lab.clock.virtual(0, "08:00"))
    since = time.time()
    up = compose(lab.runner, lab.config, env, "up", "-d")
    data["up_code"] = up.code
    data["up_tail"] = up.text[-600:]
    lab.evidence["s2_up"] = {"up_code": up.code, "since": since}
    lab.save()
    return _s2_checks(lab, since, data)


def _s2_checks(lab: Lab, since: float, data: dict) -> dict:
    """Verificacoes do S-2 (somente leitura: nenhuma recriacao, nenhum salto de relogio)."""
    data["ready"] = lab.wait_ready()
    migration = lab.runner.run(["docker", "inspect", "-f", "{{.State.ExitCode}}", "auneron-sim-migration"])
    data["migration_exit"] = migration.out.strip()
    current = lab.backend_exec("python", "-m", "alembic", "current")
    data["alembic_current"] = {"code": current.code, "stdout": current.out.strip()}
    check = lab.backend_exec("python", "-m", "alembic", "check")
    data["alembic_check"] = {"code": check.code, "stdout": check.out.strip()[-300:]}
    data["alembic_version_sql"] = sqlro.query(lab.runner, lab.config, "select version_num from alembic_version")
    data["application_started"] = lab.startup_record(since)
    head = lab.config["target"]["alembic_head"]
    started = data["application_started"] or {}
    passed = (data.get("up_code", lab.evidence.get("s2_up", {}).get("up_code")) == 0 and data["ready"]["ready"]
              and data["migration_exit"] == "0"
              and current.code == 0 and current.out.strip() == f"{head} (head)" and check.code == 0
              and data["alembic_version_sql"] == [[head]]
              and started.get("environment") == "production" and started.get("maintenance_enabled") is True
              and started.get("evidence_floor_state") == "unset"
              and started.get("build_identity_state") == "valid" and started.get("build_git_dirty") == "false"
              and started.get("build_git_sha") == lab.config.commit)
    return lab.record("S-2", passed, data)


def phase_s2_recheck(lab: Lab) -> dict:
    previous = lab.evidence["gates"].get("S-2", {})
    lab.evidence.setdefault("s2_first_attempt", previous)
    data = {"recheck": True, "up_code": lab.evidence["s2_up"]["up_code"] if "s2_up" in lab.evidence
            else previous.get("up_code"), "clock_d0": previous.get("clock_d0"),
            "previous_status": previous.get("status"),
            "previous_failure": "parser do harness: revisao do alembic current esta no stdout"}
    return _s2_checks(lab, 0, data)


def phase_provision(lab: Lab) -> dict:
    factory = lab.client
    first = provision.provision(lab.runner, lab.config, lab.secrets, factory)
    second = provision.provision(lab.runner, lab.config, lab.secrets, factory)
    negative = provision.negative_divergent_role(lab.runner, lab.config, lab.secrets, factory)
    absences = provision.forbidden_absences(lab.runner, lab.config)
    catalog = {s["key"]: provision.skill_catalog_ok(lab.runner, lab.config, s["key"])[1]
               for s in lab.config["skills"]}
    passed = (all(r["class"] == provision.CREATED for r in first)
              and all(r["class"] == provision.EXISTS_EQUIVALENT for r in second)
              and negative["class"] == provision.EXISTS_DIFFERENT
              and all(v == 0 for v in absences.values()))
    return lab.record("S-5", passed, {"first_pass": first, "second_pass": second, "negative": negative,
                                      "absences": absences, "skill_catalog": catalog})


def _work_body(lab: Lab, subject_user_id: int, title_suffix: str, key_suffix: str) -> dict:
    return {
        "work_type": "task",
        "title": f"[SIM-HARNESS] {title_suffix} {lab.instance_id}",
        "work_key": f"sim-harness:{key_suffix}:{lab.instance_id}",
        "scope": {"type": "user", "subject_user_id": subject_user_id},
    }


def phase_rbac(lab: Lab) -> dict:
    probe = lab.login(lab.config["probe"]["user"])
    manager = lab.login("sim-gerente-fin")
    viewer = lab.login("sim-controller")
    analyst_pending = probe.request("GET", "/approvals/pending-count")
    manager_pending = manager.request("GET", "/approvals/pending-count")
    viewer_id = ((viewer.request("GET", "/auth/me").json() or {}).get("user") or {}).get("id")
    viewer_create = viewer.request("POST", "/work-items", _work_body(lab, viewer_id, "rbac-probe", "rbac-probe"))
    data = {"analyst_pending_count": analyst_pending.status, "manager_pending_count": manager_pending.status,
            "viewer_create_work_item": viewer_create.status}
    passed = analyst_pending.status == 403 and manager_pending.status == 200 and viewer_create.status == 403
    return lab.record("S-6", passed, data)


def phase_probe(lab: Lab) -> dict:
    probe = lab.login(lab.config["probe"]["user"])
    me = (probe.request("GET", "/auth/me").json() or {}).get("user") or {}
    headers = {"Idempotency-Key": f"sim-harness:{lab.instance_id}:restart-probe"}
    response = probe.request("POST", "/work-items", _work_body(lab, me["id"], "restart-probe", "restart-probe"),
                             headers=headers)
    body = response.json() or {}
    item = body.get("work_item") or {}
    lab.evidence["probe_user_id"] = me.get("id")
    lab.evidence["probe_work_item"] = {k: item.get(k) for k in ("id", "version", "status", "title", "work_key")}
    passed = response.status == 201 and body.get("created") is True and item.get("id") is not None
    return lab.record("PROBE", passed, {"http_status": response.status, "created": body.get("created"),
                                        "item": lab.evidence["probe_work_item"]})


def phase_clock(lab: Lab) -> dict:
    samples = [{"day": 0, **lab.skew()}]
    for day in range(1, 11):
        lab.clock.set(lab.clock.virtual(day, "18:00"))
        time.sleep(2)
        samples.append({"day": day, **lab.skew()})
    controls = clock_controls(lab)
    passed = len(samples) == 11 and all(s["ok"] for s in samples) and controls["ok"]
    return lab.record("S-3", passed, {"jumps": 10, "samples": samples, "controls": controls,
                                      "rounding_tolerance_s": lab.clock.ROUNDING_TOLERANCE_S,
                                      "native_volume": True, "no_cache": "1"})


def clock_controls(lab: Lab) -> dict:
    """Controles do anti-retrocesso no lab: retrocesso MATERIAL recusado (nenhuma
    escrita no relogio); reaplicar ~o mesmo instante aceito (nao e retrocesso)."""
    out = {}
    state_before = (lab.dir / "clock_state.json").read_text(encoding="utf-8")
    for label, target in (("rollback_1_day", lab.clock.expected_now() - timedelta(days=1)),
                          ("rollback_10_s", lab.clock.expected_now() - timedelta(seconds=10))):
        try:
            lab.clock.set(target)
            out[label] = "ACCEPTED"
        except GuardViolation:
            out[label] = "REFUSED"
    out["state_unchanged_after_refusals"] = (lab.dir / "clock_state.json").read_text(encoding="utf-8") == state_before
    current = lab.clock.expected_now() - timedelta(milliseconds=400)   # dentro da tolerancia de 1 s
    try:
        lab.clock.set(current)
        out["reapply_same_instant"] = "ACCEPTED"
    except GuardViolation:
        out["reapply_same_instant"] = "REFUSED"
    time.sleep(1)
    out["skew_after_reapply"] = lab.skew()
    out["ok"] = (out["rollback_1_day"] == out["rollback_10_s"] == "REFUSED" and out["state_unchanged_after_refusals"]
                 and out["reapply_same_instant"] == "ACCEPTED" and out["skew_after_reapply"]["ok"])
    return out


def phase_quiescence(lab: Lab) -> dict:
    days = []
    for day in (11, 12, 13):
        since = time.time()
        jump = lab.clock.set(lab.clock.virtual(day, "18:00"))
        try:
            result = lab.run_barrier(since, lab.events())
            days.append({"day": day, "jump": jump, "barrier": result, "skew": lab.skew(), "ok": True})
        except HarnessError as error:
            days.append({"day": day, "jump": jump, "error": str(error), "ok": False})
            break
    negative_params = {"event_wait_timeout_s": 20, "hard_timeout_s": 60}
    try:
        lab.run_barrier(time.time(), lab.events() + [IMPOSSIBLE_EVENT], params=negative_params)
        negative = {"result": "NO_ERROR"}
    except HarnessError as error:
        negative = {"result": "HARNESS_ERROR", "error": str(error)}
    negative.update({"params": negative_params, "impossible_event": IMPOSSIBLE_EVENT})
    passed = len(days) == 3 and all(d["ok"] for d in days) and negative["result"] == "HARNESS_ERROR"
    return lab.record("S-7", passed, {"days": days, "negative": negative,
                                      "workers_without_signal_gap": lab.config["quiescence"]["workers_without_signal"]})


def phase_floor(lab: Lab) -> dict:
    data = {}
    floor_day = lab.config["clock"]["floor_day"]
    hours, minutes = map(int, lab.config["clock"]["floor_at"].split(":"))
    before = f"{hours if minutes else hours - 1:02d}:{(minutes - 1) % 60:02d}"
    data["clock_pre"] = lab.clock.set(lab.clock.virtual(floor_day, before))
    data["preflight_pre"] = activation.capture_and_check(lab.runner, lab.config)
    data["clock_floor"] = lab.clock.set(lab.clock.virtual(floor_day, lab.config["clock"]["floor_at"]))
    time.sleep(1)
    data["preflight"] = activation.capture_and_check(lab.runner, lab.config)
    t0 = data["preflight"].get("t0")
    if not (activation.preflight_ok(data["preflight_pre"]) and activation.preflight_ok(data["preflight"])):
        return lab.record("S-8", False, data)
    lab.evidence["floor_t0"] = t0
    lab.save()
    # T0 e o valor CAPTURADO do clock_timestamp() do PostgreSQL (nunca 08:00:00.000000 fabricado)
    data["t0_virtual_local"] = datetime.fromisoformat(t0.replace("Z", "+00:00")).astimezone(
        lab.clock.tz).isoformat()
    since = time.time()
    recreate = compose(lab.runner, lab.config, lab.env(), "up", "-d", "--no-deps", "--force-recreate", "sim-backend")
    data["recreate_code"] = recreate.code
    data["ready"] = lab.wait_ready()
    data["application_started"] = lab.startup_record(since)
    started = data["application_started"] or {}
    try:
        data["barrier"] = lab.run_barrier(since, lab.events())
        barrier_ok = True
    except HarnessError as error:
        data["barrier_error"] = str(error)
        barrier_ok = False
    data["skew"] = lab.skew()
    passed = (recreate.code == 0 and data["ready"]["ready"] and started.get("evidence_floor") == t0
              and started.get("evidence_floor_state") == "armed" and started.get("evidence_worker_enabled") is True
              and barrier_ok)
    return lab.record("S-8", passed, data)


def _identity(lab: Lab) -> dict:
    personas = {}
    for persona in lab.config["personas"]:
        client = lab.login(persona["user"])
        user = (client.request("GET", "/auth/me").json() or {}).get("user") or {}
        personas[persona["user"]] = {k: user.get(k) for k in ("id", "email", "name", "role", "active")}
    skills = {s["key"]: sqlro.query(lab.runner, lab.config, (
        "select s.skill_key, s.status, v.version, v.status, v.execution_mode, v.runtime_kind "
        "from skills s join skill_versions v on v.skill_id = s.id "
        f"where s.skill_key = '{s['key']}' order by v.id")) for s in lab.config["skills"]}
    alembic = sqlro.query(lab.runner, lab.config, "select version_num from alembic_version")
    return {"personas": personas, "skills": skills, "alembic_version": alembic}


def phase_restart(lab: Lab) -> dict:
    data = {}
    probe = lab.login(lab.config["probe"]["user"])
    me_before = (probe.request("GET", "/auth/me").json() or {}).get("user") or {}
    item_id = lab.evidence["probe_work_item"]["id"]
    keys = ("id", "version", "status", "title", "work_key")
    item_before = {k: (probe.request("GET", f"/work-items/{item_id}").json() or {}).get(k) for k in keys}
    identity_before = _identity(lab)
    since = time.time()
    require_container(BACKEND, lab.config)
    restart = lab.runner.run(["docker", "restart", BACKEND], timeout=300)
    data["restart_code"] = restart.code
    data["ready"] = lab.wait_ready()
    data["application_started"] = lab.startup_record(since)
    try:
        data["barrier"] = lab.run_barrier(since, lab.events())
        barrier_ok = True
    except HarnessError as error:
        data["barrier_error"] = str(error)
        barrier_ok = False
    me_after_response = probe.request("GET", "/auth/me")
    me_after = (me_after_response.json() or {}).get("user") or {}
    item_after = {k: (probe.request("GET", f"/work-items/{item_id}").json() or {}).get(k) for k in keys}
    identity_after = _identity(lab)
    headers = {"Idempotency-Key": f"sim-harness:{lab.instance_id}:restart-probe"}
    replay = probe.request("POST", "/work-items",
                           _work_body(lab, me_before["id"], "restart-probe", "restart-probe"), headers=headers)
    replay_body = replay.json() or {}
    listing = probe.request("GET", f"/work-items?scope_type=user&subject_user_id={me_before['id']}")
    items = (listing.json() or {}).get("items") or []
    same_key = [i for i in items if i.get("work_key") == lab.evidence["probe_work_item"]["work_key"]]
    data.update({
        "same_session_me_status": me_after_response.status,
        "same_session_same_id": me_after.get("id") == me_before.get("id"),
        "item_before": item_before, "item_after": item_after,
        "identity_identical": identity_before == identity_after,
        "identity_before": identity_before,
        "replay_status": replay.status, "replay_created": replay_body.get("created"),
        "replay_duplicate": replay_body.get("duplicate"),
        "replay_same_id": (replay_body.get("work_item") or {}).get("id") == item_id,
        "listing_status": listing.status, "items_with_work_key": len(same_key),
    })
    if not data["identity_identical"]:
        data["identity_after"] = identity_after
    started = data["application_started"] or {}
    passed = (restart.code == 0 and data["ready"]["ready"] and barrier_ok
              and started.get("evidence_floor_state") == "armed"
              and me_after_response.status == 200 and data["same_session_same_id"]
              and item_before == item_after and data["identity_identical"]
              and replay.status == 200 and replay_body.get("created") is False and data["replay_same_id"]
              and listing.status == 200 and len(same_key) == 1)
    return lab.record("S-9", passed, data)


MASK = "***"
IDENTITY_REFUSAL = "corresponde à identidade esperada"


def guard_probe_env(resolved_env: dict, secrets: dict, database_url: str) -> dict:
    """Env da sonda da guarda do produto. O `docker compose config` chega MASCARADO
    pelo Runner (segredos -> ***): os segredos vem da instancia, nunca da saida
    mascarada; qualquer *** remanescente => falha fechada (defeito visto no live)."""
    env = {k: str(v) for k, v in resolved_env.items() if v is not None}
    for key in ("LD_PRELOAD", "FAKETIME_TIMESTAMP_FILE", "FAKETIME_NO_CACHE", "FAKETIME_DONT_FAKE_MONOTONIC"):
        env.pop(key, None)
    env["API_KEY"] = secrets["api_key"]
    env["DATABASE_URL"] = database_url
    leftovers = sorted(k for k, v in env.items() if MASK in v)
    if leftovers:
        raise HarnessError(f"env da sonda com valor mascarado: {leftovers}")
    return env


def _product_guard_probe(lab: Lab, label: str, database_url: str, marker: str) -> dict:
    """Container avulso `auneron-sim-guardprobe` na rede do lab: o perfil
    production do produto deve RECUSAR o banco divergente (2a linha) -- e
    pelo motivo certo (identidade do banco), comprovado pela mensagem."""
    name = "auneron-sim-guardprobe"
    require_container(name, lab.config)
    resolved = assert_compose_safe(lab.runner, lab.config, lab.env())
    env_spec = guard_probe_env(resolved["services"]["sim-backend"]["environment"], lab.secrets, database_url)
    process_env = dict(lab.env())
    process_env.update(env_spec)
    args = ["docker", "run", "--rm", "--name", name, "--network", lab.config["docker"]["network"]]
    for key in sorted(env_spec):
        args += ["-e", key]
    args += [lab.config.image("backend"), "python", "-c", "from app.core.config import settings; print('LOADED')"]
    result = lab.runner.run(args, env=process_env, timeout=300)
    error_lines = [line.strip() for line in result.text.splitlines() if "Value error" in line]
    reason_ok = any(IDENTITY_REFUSAL in line and marker in line for line in error_lines)
    refused = result.code != 0 and reason_ok and "LOADED" not in result.out
    return {"case": label, "code": result.code, "refused": refused, "reason_identity_mismatch": reason_ok,
            "error_lines": error_lines}


def phase_isolation(lab: Lab) -> dict:
    resolved = assert_compose_safe(lab.runner, lab.config, lab.env())
    negatives = guard_negatives(lab, resolved)
    dns = {}
    for name in DEV_NAMES:
        result = lab.backend_exec("python", "-c", f"import socket; print(socket.gethostbyname('{name}'))", timeout=60)
        dns[name] = "RESOLVES" if result.code == 0 else "NOT_RESOLVED"
    pw = lab.secrets["db_password"]
    product = [
        _product_guard_probe(lab, "database_name_mismatch",
                             f"postgresql+psycopg://sim:{pw}@sim-postgres:5432/postgres", "(nome)"),
        _product_guard_probe(lab, "database_host_mismatch",
                             f"postgresql+psycopg://sim:{pw}@auneron-sim-postgres:5432/auneron_sim", "(host)"),
    ]
    leftover = lab.runner.run(["docker", "ps", "-a", "--filter", "name=auneron-sim-guardprobe", "-q"]).out.strip()
    passed = (all(v == "REFUSED" for v in negatives.values()) and all(v == "NOT_RESOLVED" for v in dns.values())
              and all(p["refused"] for p in product) and not leftover)
    return lab.record("S-4", passed, {"guard_negatives": negatives, "dev_dns": dns, "product_guard": product,
                                      "guardprobe_leftover": bool(leftover),
                                      "note": "snapshot DEV identico e auneron_test intacto: DEV_SNAPSHOT_AFTER"})


def phase_reset(lab: Lab) -> dict:
    """Recomeca o lab DESCARTAVEL do zero (mesma instancia, mesmas imagens):
    `down -v` guardado; tentativa anterior arquivada; estado do relogio zerado
    porque o volume do relogio foi destruido. Nunca toca nada fora do prefixo."""
    down = compose(lab.runner, lab.config, lab.env(), "down", "-v", "--remove-orphans")
    attempt = {"reset_at_real_utc": _now_iso(), "down_code": down.code,
               "gates": {k: v for k, v in lab.evidence["gates"].items() if k not in
                         ("GUARD", "DEV_SNAPSHOT_BEFORE", "S-1")}}
    lab.evidence.setdefault("attempts", []).append(attempt)
    for key in list(attempt["gates"]):
        lab.evidence["gates"].pop(key)
    for key in ("s2_up", "s2_first_attempt", "probe_work_item", "probe_user_id", "floor_t0"):
        lab.evidence.pop(key, None)
    state = lab.dir / "clock_state.json"
    if state.exists():
        state.unlink()
    leftovers = lab.runner.run(["docker", "ps", "-a", "--filter", "name=auneron-sim", "-q"]).out.strip()
    passed = down.code == 0 and not leftovers
    return lab.record("RESET", passed, {"down_code": down.code, "leftover_containers": bool(leftovers)})


def phase_teardown(lab: Lab) -> dict:
    down = compose(lab.runner, lab.config, lab.env(), "down", "-v", "--remove-orphans")
    containers = lab.runner.run(["docker", "ps", "-a", "--filter", "name=auneron-sim", "--format", "{{.Names}}"]).out.split()
    volumes = [v for v in lab.runner.run(["docker", "volume", "ls", "-q"]).out.split() if v.startswith("auneron_sim")]
    networks = [n for n in lab.runner.run(["docker", "network", "ls", "--format", "{{.Name}}"]).out.split()
                if n.startswith("auneron_sim") or n.startswith("auneron-sim")]
    images = sorted(lab.runner.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]).out.split())
    sim_images = [i for i in images if i.startswith("auneron-sim-")]
    expected_images = sorted(lab.config.image(k) for k in ("base", "backend", "postgres"))
    # qualquer container (com ou sem prefixo) derivado das imagens do lab = residuo
    ancestors = {}
    for image in expected_images:
        found = lab.runner.run(["docker", "ps", "-a", "--filter", f"ancestor={image}",
                                "--format", "{{.Names}}"]).out.split()
        ancestors[image] = found
    home = lab_home()
    git_status = lab.runner.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=all"]).out
    staged = lab.runner.run(["git", "-C", str(REPO_ROOT), "diff", "--cached", "--name-only"]).out.strip()
    tracked_diff = lab.runner.run(["git", "-C", str(REPO_ROOT), "diff", "--name-only"]).out.strip()
    data = {
        "down_code": down.code, "containers": containers, "volumes": volumes, "networks": networks,
        "sim_images": sim_images, "expected_images_preserved": all(i in sim_images for i in expected_images),
        "containers_from_lab_images": ancestors,
        "secrets_outside_repo": REPO_ROOT.resolve() not in home.parents and home != REPO_ROOT.resolve(),
        "git_status_porcelain": git_status.splitlines(), "staged": staged, "tracked_diff": tracked_diff,
    }
    passed = (down.code == 0 and not containers and not volumes and not networks
              and not any(ancestors.values()) and data["expected_images_preserved"] and data["secrets_outside_repo"]
              and not staged and not tracked_diff)
    return lab.record("S-10", passed, data)
