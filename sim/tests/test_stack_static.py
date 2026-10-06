"""
SIM-1.4 -- gate ESTATICO do stack `auneron_sim` (sem Docker, sem rede).
Precisa passar ANTES de qualquer recurso live (CODE APPLY autorizado).
"""

from __future__ import annotations

import ast
import copy
import json
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
import yaml

from sim.stack.lab import activation
from sim.stack.lab import provision
from sim.stack.lab import sqlro
from sim.stack.lab.clock import ClockController
from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import STACK_DIR
from sim.stack.lab.config import lab_home
from sim.stack.lab.config import load_config
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.guard import check_resolved_compose
from sim.stack.lab.guard import require_container
from sim.stack.lab.guard import require_http_target
from sim.stack.lab.guard import require_project
from sim.stack.lab.labsecrets import PLACEHOLDER_MARKERS
from sim.stack.lab.labsecrets import load_or_create
from sim.stack.lab.quiescence import HarnessError
from sim.stack.lab.quiescence import barrier
from sim.stack.lab.quiescence import log_tokens
from sim.stack.lab.runner import Result
from sim.stack.lab.runner import Runner

CONFIG = load_config()
COMPOSE_TEXT = (STACK_DIR / "docker-compose.sim.yml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TEXT)


def normalized_compose() -> dict:
    """Forma equivalente ao `docker compose config --format json` (sem Docker)."""
    resolved = {"name": COMPOSE["name"], "services": {}, "volumes": copy.deepcopy(COMPOSE["volumes"]),
                "networks": copy.deepcopy(COMPOSE["networks"])}
    for service, spec in COMPOSE["services"].items():
        out = copy.deepcopy(spec)
        out["image"] = spec["image"].replace("${SIM_IMAGE_TAG:?SIM_IMAGE_TAG}", CONFIG.image_tag)
        out["ports"] = []
        for port in spec.get("ports") or []:
            host_ip, published, target = port.split(":")
            out["ports"].append({"host_ip": host_ip, "published": published, "target": int(target)})
        out["volumes"] = []
        for volume in spec.get("volumes") or []:
            source = volume.split(":")[0]
            kind = "volume" if source in COMPOSE["volumes"] else "bind"
            out["volumes"].append({"type": kind, "source": source})
        env = {k: str(v) for k, v in (spec.get("environment") or {}).items()}
        if "DATABASE_URL" in env:
            env["DATABASE_URL"] = env["DATABASE_URL"].replace("${SIM_DB_PASSWORD:?SIM_DB_PASSWORD}", "x")
        out["environment"] = env
        nets = spec.get("networks")
        out["networks"] = {n: None for n in nets} if isinstance(nets, list) else (nets or {})
        resolved["services"][service] = out
    return resolved


# -- compose do lab ---------------------------------------------------------

def test_compose_identity_is_lab_only():
    assert COMPOSE["name"] == "auneron-sim" == CONFIG.project
    for service, spec in COMPOSE["services"].items():
        assert service.startswith("sim-"), service
        assert spec["container_name"].startswith("auneron-sim-"), service
        assert spec["image"].split(":")[0] in CONFIG["docker"]["images"].values(), service
    assert set(COMPOSE["volumes"]) == {"auneron_sim_pgdata", "auneron_sim_clock"}
    assert all(v["name"].startswith("auneron_sim_") for v in COMPOSE["volumes"].values())
    assert list(COMPOSE["networks"]) == ["auneron_sim_internal"]


def test_compose_guard_accepts_lab_and_refuses_mutations():
    resolved = normalized_compose()
    assert check_resolved_compose(resolved, CONFIG) == []
    mutations = [
        lambda r: r.__setitem__("name", "backend"),
        lambda r: r["services"]["sim-backend"]["ports"][0].__setitem__("published", "8000"),
        lambda r: r["services"]["sim-backend"]["ports"][0].__setitem__("host_ip", "0.0.0.0"),
        lambda r: r["services"]["sim-postgres"]["ports"][0].__setitem__("published", "5432"),
        lambda r: r["services"]["sim-backend"]["environment"].__setitem__(
            "DATABASE_URL", "postgresql+psycopg://a:b@postgres:5432/auneron_test"),
        lambda r: r["services"]["sim-postgres"]["environment"].__setitem__("POSTGRES_DB", "auneron"),
        lambda r: r["services"]["sim-backend"]["environment"].__setitem__("APP_ENV", "development"),
        lambda r: r["services"]["sim-backend"]["environment"].__setitem__("FAKETIME_NO_CACHE", "0"),
        lambda r: r["services"]["sim-backend"]["volumes"][0].__setitem__("type", "bind"),
        lambda r: r["services"]["sim-backend"].__setitem__("container_name", "auneron-backend"),
        lambda r: r["services"]["sim-backend"].__setitem__("image", "backend-backend:latest"),
        lambda r: r["services"]["sim-backend"]["networks"].__setitem__("backend_default", None),
        lambda r: r["volumes"]["auneron_sim_pgdata"].__setitem__("name", "backend_postgres_data"),
        lambda r: r["networks"]["auneron_sim_internal"].__setitem__("name", "backend_default"),
    ]
    for index, mutate in enumerate(mutations):
        mutated = copy.deepcopy(resolved)
        mutate(mutated)
        assert check_resolved_compose(mutated, CONFIG), index


def test_compose_clock_invariants_sim_clock_1_and_2():
    for service, spec in COMPOSE["services"].items():
        for volume in spec.get("volumes") or []:
            assert volume.split(":")[0] in COMPOSE["volumes"], (service, volume)   # nunca bind mount
        env = spec.get("environment") or {}
        if "FAKETIME_TIMESTAMP_FILE" in env:
            assert env["FAKETIME_NO_CACHE"] == "1"
            assert env["FAKETIME_DONT_FAKE_MONOTONIC"] == "1"
            assert env["TZ"] == "America/Sao_Paulo"
            assert "auneron_sim_clock:/sim_clock:ro" in spec["volumes"], service
    assert COMPOSE["services"]["sim-clock"]["network_mode"] == "none"
    assert "FAKETIME_CACHE_DURATION" not in COMPOSE_TEXT


def test_compose_production_profile_and_sim_maint_1():
    env = COMPOSE["services"]["sim-backend"]["environment"]
    for key, value in CONFIG["backend_profile"].items():
        if key == "TZ":
            continue
        assert str(env[key]) == str(value), key
    assert env["MAINTENANCE_ENABLED"] == "true"
    assert env["DATABASE_URL"].endswith("@sim-postgres:5432/auneron_sim")
    assert env["API_KEY"] == "${SIM_API_KEY:?SIM_API_KEY}"
    assert env["ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR"] == "${SIM_EVIDENCE_FLOOR:-}"
    assert "127.0.0.1" not in env["FORWARDED_ALLOW_IPS"] and "localhost" not in env["FORWARDED_ALLOW_IPS"]
    assert env["CORS_ORIGINS"].startswith("https://")
    for key, value in CONFIG["intervals"].items():
        assert env[key] == str(value), key


def test_compose_has_no_dev_test_references():
    lowered = COMPOSE_TEXT.lower()
    for token in ("auneron_test", "auneron_dev", "backend_postgres", "backend_default", "env_file",
                  "../", "host.docker.internal", "0.0.0.0"):
        assert token not in lowered, token
    ports = [p for spec in COMPOSE["services"].values() for p in spec.get("ports") or []]
    assert sorted(ports) == ["127.0.0.1:5434:5432", "127.0.0.1:8100:8000"]
    for port in CONFIG["http"]["forbidden_ports"]:
        assert not any(p.split(":")[1] == str(port) for p in ports)
    for literal in re.findall(r"\$\{([A-Z_]+)", COMPOSE_TEXT):
        assert literal.startswith("SIM_"), literal


def test_dockerfiles_are_minimal_derivations():
    backend = (STACK_DIR / "backend-faketime.Dockerfile").read_text(encoding="utf-8")
    assert "ARG BASE_IMAGE" in backend and "FROM ${BASE_IMAGE}" in backend
    assert "libfaketime" in backend and backend.strip().splitlines()[-1].strip() == "USER auneron"
    assert "COPY" not in backend and "GIT_SHA" not in backend and "GIT_DIRTY" not in backend
    postgres = (STACK_DIR / "postgres-faketime.Dockerfile").read_text(encoding="utf-8")
    assert f"FROM {CONFIG['docker']['postgres_base_image']}" in postgres
    assert "libfaketime" in postgres


# -- configuracao congelada ---------------------------------------------------

def test_lab_config_frozen_values():
    assert CONFIG.commit == "d100ce23462989dd1047ceabf0a30dc7e5b0d62d"
    assert CONFIG["target"]["alembic_head"] == "5c1e7a90d2b4"
    q = CONFIG["quiescence"]
    assert (q["event_wait_timeout_s"], q["time_floor_s"], q["stability_gap_s"], q["stability_max_attempts"],
            q["hard_timeout_s"]) == (300, 120, 60, 3, 600)
    assert CONFIG["startup"]["ready_timeout_s"] == 180
    assert CONFIG["clock"]["max_skew_s"] == 3
    assert (CONFIG["clock"]["d0"], CONFIG["clock"]["floor_day"], CONFIG["clock"]["floor_at"]) == \
        ("2026-01-05", 14, "08:00")
    assert len(q["events_before_floor"]) == 7
    assert q["events_after_floor_extra"] == ["escalation_payment_observation.recovery_completed"]
    assert CONFIG.image("backend") == "auneron-sim-backend:d100ce234629"
    assert CONFIG["http"]["base_url"] == "http://127.0.0.1:8100"


def test_personas_probe_and_skills():
    personas = CONFIG["personas"]
    assert len(personas) == 9
    roles = {p["user"]: p["role"] for p in personas}
    assert roles["sim-gerente-fin"] == roles["sim-coordenador-fin"] == "manager"
    assert roles["sim-controller"] == "viewer" and roles["sim-admin"] == "administrator"
    assert CONFIG["probe"] == {"user": "sim-harness-probe", "name": "[SIM-HARNESS] probe", "role": "analyst"}
    assert CONFIG["probe"]["user"] not in roles
    assert [s["key"] for s in CONFIG["skills"]] == ["account.mark_overdue", "account.mark_paid"]
    assert all("developer" != p["role"] for p in personas)


# -- guardas ------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8100", "https://127.0.0.1:8100",
                                 "http://127.0.0.1:5432", "http://127.0.0.1:8080"])
def test_http_guard_refuses_non_lab_targets(url):
    with pytest.raises(GuardViolation):
        require_http_target(url, CONFIG)


def test_http_guard_accepts_lab_target():
    require_http_target("http://127.0.0.1:8100", CONFIG)


@pytest.mark.parametrize("name", ["auneron-backend", "auneron-postgres", "backend-backend-1", "auneron_sim_x"])
def test_container_guard_refuses_non_lab(name):
    with pytest.raises(GuardViolation):
        require_container(name, CONFIG)


@pytest.mark.parametrize("project", ["backend", "auneron", "auneron-sim-2", ""])
def test_project_guard_refuses_other_projects(project):
    with pytest.raises(GuardViolation):
        require_project(project, CONFIG)


@pytest.mark.parametrize("sql", ["insert into users values (1)", "update skills set status='x'",
                                 "delete from users", "drop table users", "select 1; truncate users",
                                 "create table x(a int)", "grant all on users to x"])
def test_sql_readonly_refuses_writes_before_touching_docker(sql):
    with pytest.raises(GuardViolation):
        sqlro.query(None, CONFIG, sql)


# -- provisionamento rerun-safe ----------------------------------------------

def _never():
    raise AssertionError("verificacao nao deveria rodar")


def test_user_classification():
    assert provision.classify_user_creation(0, "Usuário criado: a@b role=analyst", _never, "analyst")[0] == \
        provision.CREATED
    assert provision.classify_user_creation(0, "Usuário criado: a@b role=viewer", _never, "analyst")[0] == \
        provision.UNEXPECTED_ERROR
    dup = provision.DUPLICATE_USER_MESSAGE
    assert provision.classify_user_creation(1, dup, lambda: (True, "ok"), "analyst")[0] == \
        provision.EXISTS_EQUIVALENT
    assert provision.classify_user_creation(1, dup, lambda: (False, "role"), "analyst")[0] == \
        provision.EXISTS_DIFFERENT
    assert provision.classify_user_creation(1, "Traceback", _never, "analyst")[0] == provision.UNEXPECTED_ERROR
    assert provision.classify_user_creation(0, "", _never, "analyst")[0] == provision.UNEXPECTED_ERROR
    assert provision.classify_user_creation(0, dup, _never, "analyst")[0] == provision.UNEXPECTED_ERROR


def test_skill_classification_never_infers_equivalence():
    ok = lambda: (True, "rows")  # noqa: E731
    bad = lambda: (False, "rows")  # noqa: E731
    exists = "Skill 'account.mark_paid' ja registrada (id=1, status=active)"
    created = "Skill 'account.mark_paid' registrada e publicada com sucesso."
    assert provision.classify_skill_registration(0, exists, ok)[0] == provision.EXISTS_EQUIVALENT
    assert provision.classify_skill_registration(0, exists, bad)[0] == provision.EXISTS_DIFFERENT
    assert provision.classify_skill_registration(
        0, "Skill 'x' ja registrada (id=1, status=draft)", _never)[0] == provision.EXISTS_DIFFERENT
    assert provision.classify_skill_registration(0, created, ok)[0] == provision.CREATED
    assert provision.classify_skill_registration(0, created, bad)[0] == provision.UNEXPECTED_ERROR
    assert provision.classify_skill_registration(1, exists, _never)[0] == provision.UNEXPECTED_ERROR
    assert provision.classify_skill_registration(0, "ok", _never)[0] == provision.UNEXPECTED_ERROR
    assert set(provision.PASSING) == {provision.CREATED, provision.EXISTS_EQUIVALENT}


# -- segredos -------------------------------------------------------------------

def test_lab_home_refuses_repo(monkeypatch):
    monkeypatch.setenv("AUNERON_SIM_HOME", str(REPO_ROOT / "sim" / "state"))
    with pytest.raises(RuntimeError):
        lab_home()
    monkeypatch.delenv("AUNERON_SIM_HOME")
    assert REPO_ROOT.resolve() not in lab_home().parents


def test_secrets_are_generated_outside_repo_and_meet_production_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("AUNERON_SIM_HOME", str(tmp_path / "home"))
    data = load_or_create("t1", ["u1", "u2"])
    again = load_or_create("t1", ["u1", "u2"])
    assert data == again
    key = data["api_key"]
    assert len(key) >= 32 and len(set(key)) >= 12
    assert not any(m in key.lower() for m in PLACEHOLDER_MARKERS)
    assert len(data["users"]["u1"]) >= 12
    other = load_or_create("t2", ["u1"])
    assert other["api_key"] != key and other["db_password"] != data["db_password"]
    assert (tmp_path / "home" / "t1" / "secrets.json").exists()


def test_guard_probe_env_never_uses_masked_values():
    from sim.stack.lab.gates import guard_probe_env

    masked = {"API_KEY": "***", "DATABASE_URL": "postgresql+psycopg://sim:***@sim-postgres:5432/auneron_sim",
              "APP_ENV": "production", "LD_PRELOAD": "/x.so", "FAKETIME_NO_CACHE": "1"}
    secrets = {"api_key": "k" * 40}
    env = guard_probe_env(masked, secrets, "postgresql+psycopg://sim:pw@sim-postgres:5432/postgres")
    assert env["API_KEY"] == "k" * 40 and "LD_PRELOAD" not in env and "FAKETIME_NO_CACHE" not in env
    assert env["DATABASE_URL"].endswith("/postgres")
    with pytest.raises(HarnessError):
        guard_probe_env({**masked, "CORS_ORIGINS": "https://***"}, secrets, "postgresql+psycopg://a:b@h:5432/d")


def test_runner_masks_secrets():
    runner = Runner(["s3cr3t-value"])
    assert runner.mask("x s3cr3t-value y") == "x *** y"


def test_runner_stdin_is_exact_lf_bytes():
    """Senha 2x por stdin (create_user/getpass): nunca "\\r\\n" (defeito visto no 1o live)."""
    import sys

    result = Runner().run([sys.executable, "-c", "import sys; print(repr(sys.stdin.buffer.read()))"],
                          input_text="ab\ncd\n")
    assert result.out.strip() == repr(b"ab\ncd\n")


# -- relogio ----------------------------------------------------------------------

class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append(args)
        return Result(0, "", "")


def test_clock_forward_only_and_lab_container(tmp_path):
    runner = FakeRunner()
    clock = ClockController(runner, CONFIG, tmp_path / "clock.json")
    d1 = clock.virtual(1, "18:00")
    assert d1.isoformat() == "2026-01-06T18:00:00-03:00"
    clock.set(d1)
    clock.set(clock.virtual(2, "18:00"))
    with pytest.raises(GuardViolation):
        clock.set(d1)
    state = json.loads((tmp_path / "clock.json").read_text())
    state["real"] -= 120
    state["offset"] = int(round(clock.virtual(2, "18:00").timestamp() - state["real"]))
    (tmp_path / "clock.json").write_text(json.dumps(state))
    with pytest.raises(GuardViolation):
        clock.set(clock.virtual(2, "18:00"))   # mesmo alvo 2 min depois = retrocesso de 2 min
    assert ClockController.ROUNDING_TOLERANCE_S == 1
    now_virtual = clock.expected_now()
    clock.set(now_virtual - timedelta(milliseconds=400))     # ~mesmo instante: aceito
    with pytest.raises(GuardViolation):
        clock.set(clock.expected_now() - timedelta(seconds=10))   # retrocesso material: recusado
    assert all(call[:3] == ["docker", "exec", "auneron-sim-clock"] for call in runner.calls)
    assert all("/sim_clock/now" in call[-1] for call in runner.calls)
    expected = clock.expected_now()
    assert abs((expected - clock.virtual(2, "18:00")).total_seconds() - 120) < 5   # 2 min virtuais depois


# -- quiescencia ---------------------------------------------------------------------

class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class LogRunner:
    def __init__(self, lines):
        self.text = "\n".join(lines)

    def run(self, args, **kwargs):
        assert args[:3] == ["docker", "logs", "--since"] and args[-1] == "auneron-sim-backend"
        return Result(0, self.text, "")


EVENTS = ['{"message": "x", "event": "a.completed"}', '{"message": "b_done"}', "INFO plain line"]


def test_log_tokens_reads_event_and_message():
    assert log_tokens("\n".join(EVENTS)) == {"x", "a.completed", "b_done"}


def test_barrier_passes_with_events_floor_and_stability():
    clock = FakeTime()
    result = barrier(LogRunner(EVENTS), CONFIG, since_epoch=clock.t, expected_events=["a.completed", "b_done"],
                     snapshot_fn=lambda: {"s": 1}, sleep=clock.sleep, now=clock.now)
    assert result["time_floor_satisfied"] and result["stable_after_attempts"] == 1
    assert clock.t - 1000.0 >= CONFIG["quiescence"]["time_floor_s"]


def test_barrier_missing_event_is_harness_error():
    clock = FakeTime()
    with pytest.raises(HarnessError):
        barrier(LogRunner(EVENTS), CONFIG, since_epoch=clock.t, expected_events=["never.happens"],
                snapshot_fn=lambda: {}, sleep=clock.sleep, now=clock.now, params={"event_wait_timeout_s": 20})


def test_barrier_instability_is_harness_error():
    clock = FakeTime()
    counter = iter(range(100))
    with pytest.raises(HarnessError):
        barrier(LogRunner(EVENTS), CONFIG, since_epoch=clock.t, expected_events=["a.completed"],
                snapshot_fn=lambda: {"n": next(counter)}, sleep=clock.sleep, now=clock.now)


# -- floor -------------------------------------------------------------------------

STRICT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")


def test_floor_t0_is_captured_never_typed():
    out = "T0=2026-01-19T11:00:00.123456Z\nclock_source=postgresql database=auneron_sim\n"
    assert activation.parse_t0(out) == "2026-01-19T11:00:00.123456Z"
    assert activation.parse_t0("ACTIVATION PREFLIGHT FAIL: db_unavailable") is None
    future = activation.future_of("2026-01-19T11:00:00.123456Z")
    assert STRICT.fullmatch(future) and future == "2026-01-19T12:00:00.123456Z"


class PreflightRunner:
    """Simula o script real: contrato em stdout + warnings em stderr (visto no live)."""
    WARN = "UserWarning: directory \"/run/secrets\" does not exist\n" * 5

    def run(self, args, **kwargs):
        assert args[:3] == ["docker", "exec", "auneron-sim-backend"]
        command = args[5:]
        if command == ["capture"]:
            return Result(0, "T0=2026-01-19T11:00:01.542080Z\nclock_source=postgresql database=auneron_sim\n",
                          self.WARN)
        if command == ["check", "2026-01-19T11:00:01.542080Z"]:
            return Result(0, "OK T0=2026-01-19T11:00:01.542080Z\ndb_now=x age_seconds=1\n", self.WARN)
        return Result(2, "ACTIVATION PREFLIGHT FAIL: future\n", self.WARN)


def test_floor_preflight_criterion_uses_stdout_not_stderr_tail():
    evidence = activation.capture_and_check(PreflightRunner(), CONFIG)
    assert evidence["t0"] == "2026-01-19T11:00:01.542080Z"
    assert evidence["negative_future_value"] == "2026-01-19T12:00:01.542080Z"
    assert activation.preflight_ok(evidence)
    assert not activation.preflight_ok({**evidence, "negative_future_code": 0})
    assert not activation.preflight_ok({**evidence, "negative_future_stdout": "ACTIVATION PREFLIGHT FAIL: stale"})
    assert not activation.preflight_ok({**evidence, "check_code": 2})
    assert not activation.preflight_ok({**evidence, "t0": None})


def test_floor_day_is_d14_0800_local():
    clock = ClockController(FakeRunner(), CONFIG, None)
    floor = clock.virtual(CONFIG["clock"]["floor_day"], CONFIG["clock"]["floor_at"])
    assert floor.astimezone(timezone.utc) == datetime(2026, 1, 19, 11, 0, tzinfo=timezone.utc)
    assert floor - clock.virtual(0, "08:00") == timedelta(days=14)


# -- fronteiras do harness ---------------------------------------------------------------

def test_stack_never_reads_scenario_data_or_imports_generator():
    for path in STACK_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("public_agenda", "world.json", "oracle.json", "nh-small", "nh-standard"):
            assert token not in text, (path, token)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("sim.generator", "sim.oracle", "sim.scenarios")), path


def test_stack_files_are_lf_and_ascii_safe():
    for path in [*STACK_DIR.rglob("*.py"), *STACK_DIR.rglob("*.yml"), *STACK_DIR.rglob("*.yaml"),
                 *STACK_DIR.rglob("*.Dockerfile"), *STACK_DIR.rglob("*.md")]:
        assert b"\r\n" not in path.read_bytes(), path
