"""
SIM-1.5 -- integracao ADITIVA ao lab (estatico): override do compose, guarda
estendida (E-1), Dockerfiles minimos, manifesto/version_id, sonda DR-1, O-1 e
orquestrador de slots. Os 23 arquivos do SIM-1.4 permanecem byte a byte.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest
import yaml

from sim.collector.chainlog import ChainLog
from sim.driver.agenda import load_manifest
from sim.stack.lab import driver_lab
from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import STACK_DIR
from sim.stack.lab.config import load_config
from sim.stack.lab.driver_lab import DRIVER_NETWORK
from sim.stack.lab.driver_lab import assert_driver_compose_safe
from sim.stack.lab.driver_lab import check_resolved_driver_compose
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.run_orchestrator import Hooks
from sim.stack.lab.run_orchestrator import RunOrchestrator
from sim.tests.collector_support import export_refs
from sim.tests.collector_support import load_agenda_ops
from sim.tests.collector_support import load_oracle
from sim.tests.collector_support import make_collector
from sim.tests.conftest import SIM_ROOT
from sim.tests.driver_support import make_driver
from sim.tests.driver_support import write_inputs
from sim.tests.fake_product import FakeProduct

CONFIG = load_config()
BASE = yaml.safe_load((STACK_DIR / "docker-compose.sim.yml").read_text(encoding="utf-8"))
OVERRIDE_TEXT = (STACK_DIR / driver_lab.OVERRIDE_FILE).read_text(encoding="utf-8")
OVERRIDE = yaml.safe_load(OVERRIDE_TEXT)
FROZEN = json.loads((SIM_ROOT / "tests" / "sim14_frozen_hashes.json").read_text(encoding="utf-8"))


def merged_resolved() -> dict:
    """Forma equivalente ao `docker compose config --format json` do stack COM o override (sem Docker)."""
    services = copy.deepcopy(BASE["services"])
    for name, spec in OVERRIDE["services"].items():
        if name in services:
            networks = list(services[name].get("networks") or [])
            networks += [n for n in spec.get("networks", []) if n not in networks]
            services[name]["networks"] = networks
        else:
            services[name] = copy.deepcopy(spec)
    volumes = {**BASE["volumes"], **OVERRIDE["volumes"]}
    resolved = {"name": BASE["name"], "services": {}, "volumes": copy.deepcopy(volumes),
                "networks": {**copy.deepcopy(BASE["networks"]), **copy.deepcopy(OVERRIDE["networks"])}}
    for service, spec in services.items():
        out = copy.deepcopy(spec)
        out["image"] = spec["image"].replace("${SIM_IMAGE_TAG:?SIM_IMAGE_TAG}", CONFIG.image_tag)
        out["ports"] = []
        for port in spec.get("ports") or []:
            host_ip, published, target = port.split(":")
            out["ports"].append({"host_ip": host_ip, "published": published, "target": int(target)})
        out["volumes"] = []
        for volume in spec.get("volumes") or []:
            source = volume.split(":")[0]
            out["volumes"].append({"type": "volume" if source in volumes else "bind", "source": source})
        env = {k: str(v) for k, v in (spec.get("environment") or {}).items()}
        if "DATABASE_URL" in env:
            env["DATABASE_URL"] = env["DATABASE_URL"].replace("${SIM_DB_PASSWORD:?SIM_DB_PASSWORD}", "x")
        out["environment"] = env
        nets = spec.get("networks")
        out["networks"] = {n: None for n in nets} if isinstance(nets, list) else (nets or {})
        resolved["services"][service] = out
    return resolved


# --------------------------------------------------------------------------
# aditivo: nada do SIM-1.4 mudou
# --------------------------------------------------------------------------
def test_the_23_sim14_files_are_byte_identical_to_the_proven_candidate():
    assert len(FROZEN) == 23
    for rel, digest in FROZEN.items():
        assert hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest() == digest, rel


# --------------------------------------------------------------------------
# E-1: topologia de rede e guarda estendida
# --------------------------------------------------------------------------
def test_override_topology_matches_the_freeze():
    resolved = merged_resolved()
    assert check_resolved_driver_compose(resolved, CONFIG) == []
    nets = {name: set(spec["networks"]) for name, spec in resolved["services"].items()}
    assert nets["sim-postgres"] == {"auneron_sim_internal"} and nets["sim-migration"] == {"auneron_sim_internal"}
    assert nets["sim-backend"] == {"auneron_sim_internal", DRIVER_NETWORK}
    assert nets["sim-driver"] == nets["sim-collector"] == {DRIVER_NETWORK}
    assert resolved["networks"][DRIVER_NETWORK]["internal"] is True
    for name in ("sim-driver", "sim-collector"):
        spec = resolved["services"][name]
        assert spec["ports"] == [] and "network_mode" not in spec and "extra_hosts" not in spec
        assert all(v["type"] == "volume" for v in spec["volumes"])
        assert all(k.startswith(driver_lab.ALLOWED_ENV_PREFIXES) for k in spec["environment"])


def test_override_never_mentions_forbidden_channels_outside_comments():
    body = "\n".join(line for line in OVERRIDE_TEXT.splitlines() if not line.lstrip().startswith("#"))
    for token in ("127.0.0.1", "host.docker.internal", "docker.sock", "network_mode", "extra_hosts", "oracle",
                  "world", "5432", "8100", "DATABASE_URL", "API_KEY", "bind"):
        assert token not in body, token


MUTATIONS = {
    "driver_network_mode_host": lambda r: r["services"]["sim-driver"].__setitem__("network_mode", "host"),
    "driver_published_port": lambda r: r["services"]["sim-driver"].__setitem__(
        "ports", [{"host_ip": "127.0.0.1", "published": "9000", "target": 9000}]),
    "driver_on_product_network": lambda r: r["services"]["sim-driver"]["networks"].__setitem__(
        "auneron_sim_internal", None),
    "collector_on_product_network": lambda r: r["services"]["sim-collector"]["networks"].__setitem__(
        "auneron_sim_internal", None),
    "postgres_on_driver_network": lambda r: r["services"]["sim-postgres"]["networks"].__setitem__(DRIVER_NETWORK, None),
    "backend_loses_driver_network": lambda r: r["services"]["sim-backend"]["networks"].pop(DRIVER_NETWORK),
    "backend_loses_internal_network": lambda r: r["services"]["sim-backend"]["networks"].pop("auneron_sim_internal"),
    "driver_network_not_internal": lambda r: r["networks"][DRIVER_NETWORK].__setitem__("internal", False),
    "driver_network_missing_internal": lambda r: r["networks"][DRIVER_NETWORK].pop("internal"),
    "driver_bind_mount": lambda r: r["services"]["sim-driver"]["volumes"][0].__setitem__("type", "bind"),
    "driver_docker_socket": lambda r: r["services"]["sim-driver"]["volumes"].append(
        {"type": "volume", "source": "/var/run/docker.sock"}),
    "driver_foreign_volume": lambda r: r["services"]["sim-driver"]["volumes"][0].__setitem__("source", "backend_data"),
    "driver_db_url": lambda r: r["services"]["sim-driver"]["environment"].__setitem__("DATABASE_URL", "x"),
    "collector_api_key_env": lambda r: r["services"]["sim-collector"]["environment"].__setitem__("API_KEY", "x"),
    "driver_extra_hosts": lambda r: r["services"]["sim-driver"].__setitem__("extra_hosts", ["h:1.1.1.1"]),
    "driver_host_docker_internal": lambda r: r["services"]["sim-driver"]["environment"].__setitem__(
        "SIM_DRIVER_HOME", "host.docker.internal"),
    "driver_privileged": lambda r: r["services"]["sim-driver"].__setitem__("privileged", True),
    "driver_unknown_image": lambda r: r["services"]["sim-driver"].__setitem__("image", "python:3.11"),
    "driver_container_name": lambda r: r["services"]["sim-driver"].__setitem__("container_name", "driver"),
    "volume_outside_prefix": lambda r: r["volumes"]["auneron_sim_driver_state"].__setitem__("name", "driver_state"),
    "extra_service": lambda r: r["services"].__setitem__("sim-extra", copy.deepcopy(r["services"]["sim-driver"])),
    "sim14_port_8000": lambda r: r["services"]["sim-backend"]["ports"][0].__setitem__("published", "8000"),
    "sim14_no_cache_off": lambda r: r["services"]["sim-backend"]["environment"].__setitem__("FAKETIME_NO_CACHE", "0"),
    "extra_network": lambda r: r["networks"].__setitem__("backend_default", {"name": "backend_default"}),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_extended_guard_refuses_every_mutation(name):
    resolved = merged_resolved()
    MUTATIONS[name](resolved)
    assert check_resolved_driver_compose(resolved, CONFIG), name
    with pytest.raises(GuardViolation):
        assert_driver_compose_safe(resolved, CONFIG)


def test_dockerfiles_are_minimal_and_isolated():
    for name, copy_path, forbidden in (("driver.Dockerfile", "sim/driver", ("sim/collector", "sim/oracle")),
                                       ("collector.Dockerfile", "sim/collector", ("sim/driver", "sim/oracle"))):
        text = (STACK_DIR / name).read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        assert lines[0] == "FROM python:3.11-slim"
        assert [line for line in lines if line.startswith("COPY")] == [f"COPY {copy_path} ./{copy_path}"]
        assert not any(line.startswith(("ADD", "RUN pip", "ENV PIP")) for line in lines) and "pip " not in text
        assert lines[-2].startswith("USER ") and "USER root" not in text
        body = "\n".join(lines)
        for token in forbidden + ("oracle", "world", "scenarios", "backend/"):
            assert token not in body.lower() or token in ("sim/oracle",) and token not in body
        assert ": > /app/sim/__init__.py" in text                 # pacote raiz vazio: nenhuma claim embarcada


# --------------------------------------------------------------------------
# manifesto do Driver, version_id, segredos
# --------------------------------------------------------------------------
def test_version_id_is_captured_from_the_created_output_and_fails_closed():
    created = ("Skill 'account.mark_paid' registrada e publicada com sucesso.\n  skill_id: 4\n"
               "  skill_version_id: 17\n  execution_mode: mutating\n")
    assert driver_lab.parse_version_id(created) == 17
    for bad in ("Skill 'account.mark_paid' ja registrada (id=4, status=active). Nada foi alterado.\n", "",
                "skill_version_id: x", created + "  skill_version_id: 18\n"):
        with pytest.raises(GuardViolation):
            driver_lab.parse_version_id(bad)


def test_driver_manifest_has_no_oracle_material_and_validates(tmp_path):
    ops = load_agenda_ops()
    agenda, _ = write_inputs(tmp_path)
    personas = driver_lab.active_personas(ops)
    assert len(personas) == 7 and "harness" not in personas
    manifest = driver_lab.build_driver_manifest(agenda, "scenario-x", 17, personas, "2026-01-05", 180)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_manifest(path)["mark_paid_version_id"] == 17
    text = json.dumps(manifest).lower()
    assert "oracle" not in text and "world" not in text
    with pytest.raises(Exception):
        driver_lab.build_driver_manifest(agenda, "scenario-x", 0, personas, "2026-01-05", 180)


def test_secrets_are_minimal_per_container():
    secrets = {"db_password": "DB", "api_key": "KEY", "users": {"sim-faturamento": "p1", "sim-gerente-fin": "p2",
                                                               "sim-admin": "p3", driver_lab.OBSERVER_USER: "po"}}
    driver = driver_lab.driver_secrets(secrets, ["sim-faturamento", "sim-gerente-fin"])
    collector = driver_lab.collector_secrets(secrets)
    assert driver == {"api_key": "KEY", "passwords": {"sim-faturamento": "p1", "sim-gerente-fin": "p2"}}
    assert collector == {"api_key": "KEY", "observer_password": "po"}
    assert "DB" not in json.dumps([driver, collector]) and "p3" not in json.dumps([driver, collector])
    assert (driver_lab.OBSERVER_USER, driver_lab.OBSERVER_ROLE) == ("sim-harness-observer", "manager")


# --------------------------------------------------------------------------
# DR-1: sonda de isolamento
# --------------------------------------------------------------------------
def run_probe(tmp_path, forbidden_hashes=(), env=None):
    root = tmp_path / "root"
    environment = dict(os.environ, SIM_PROBE_SKIP_NET="1", SIM_PROBE_ROOTS=json.dumps([str(root)]), **(env or {}))
    arg = json.dumps({"forbidden_hashes": list(forbidden_hashes), "name_needles": list(driver_lab.PROBE_NAME_NEEDLES)})
    result = subprocess.run([sys.executable, "-c", driver_lab.PROBE_SCRIPT, arg], capture_output=True, text=True,
                            env=environment, timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_probe_passes_on_a_clean_tree_and_detects_every_leak(tmp_path):
    root = tmp_path / "root"
    (root / "inputs").mkdir(parents=True)
    (root / "inputs" / "agenda.json").write_text("{}", encoding="utf-8")
    clean = run_probe(tmp_path)
    assert clean["files_scanned"] >= 1 and driver_lab.judge_probe(clean, network=False) == []

    (root / "inputs" / "Oracle.JSON").write_text("{}", encoding="utf-8")
    assert driver_lab.judge_probe(run_probe(tmp_path), network=False)
    (root / "inputs" / "Oracle.JSON").unlink()

    secret = b'{"expectations": []}'
    (root / "inputs" / "innocent.txt").write_bytes(secret)
    digest = hashlib.sha256(secret).hexdigest()
    leaked = run_probe(tmp_path, [digest])
    assert leaked["hash_matches"] and driver_lab.judge_probe(leaked, network=False)
    (root / "inputs" / "innocent.txt").unlink()

    polluted = run_probe(tmp_path, env={"SIM_FOO": "/data/oracle/x"})
    assert polluted["env_findings"] == ["SIM_FOO"] and driver_lab.judge_probe(polluted, network=False)


def test_probe_judge_checks_authorized_and_forbidden_channels_and_mounts():
    ok = {"files_scanned": 3, "mounts": ["/state", "/inputs"], "name_matches": [], "hash_matches": [],
          "env_findings": [], "ready": 200, "postgres": "failed:gaierror", "loopback_8100": "failed:ConnectionRefusedError",
          "external": "failed:timeout", "host_docker_internal": "failed:gaierror",
          "dev_names": {"auneron-backend": "failed:gaierror", "postgres": "failed:gaierror"}}
    assert driver_lab.judge_probe(ok) == []
    cases = {
        "ready": dict(ok, ready="error:URLError"), "postgres": dict(ok, postgres="connected"),
        "loopback": dict(ok, loopback_8100="connected"), "external": dict(ok, external="connected"),
        "docker_internal": dict(ok, host_docker_internal="resolves"),
        "dev_name": dict(ok, dev_names={"auneron-postgres": "resolves"}),
        "mount": dict(ok, mounts=["/state", "/inputs", "/repo"]), "empty_scan": dict(ok, files_scanned=0),
    }
    for name, result in cases.items():
        assert driver_lab.judge_probe(result), name


# --------------------------------------------------------------------------
# O-1
# --------------------------------------------------------------------------
def test_o1_sample_and_threshold_review_never_relaxes_itself():
    login = {"expires_at": "2026-01-06T00:00:00.400Z", "authenticated_at": "2026-01-05T16:00:00.100Z"}
    sample = driver_lab.o1_sample(login, 480, target_epoch=datetime_epoch("2026-01-05T16:00:00Z"))
    assert sample == {"app_minus_db_s": 0.3, "app_minus_target_s": 0.4, "db_minus_target_s": 0.1}
    assert driver_lab.o1_judge([sample], 1.0) == {"status": "OK", "max_abs_s": 0.4, "limit_s": 1.0, "samples": 1}
    drifty = {"app_minus_db_s": -1.4, "app_minus_target_s": -1.3, "db_minus_target_s": 0.1}
    verdict = driver_lab.o1_judge([sample, drifty], 1.0)
    assert verdict["status"] == "THRESHOLD_REVIEW_REQUIRED" and verdict["limit_s"] == 1.0
    assert driver_lab.o1_judge([], 1.0)["status"] == "THRESHOLD_REVIEW_REQUIRED"


def datetime_epoch(text: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


# --------------------------------------------------------------------------
# orquestrador do harness
# --------------------------------------------------------------------------
class Recorder:
    def __init__(self, slots, harness_at=None):
        self.slots, self.harness_at, self.log = slots, harness_at, []

    def hooks(self) -> Hooks:
        def run_slot(day, at):
            self.log.append(("driver", day, at))
            return {"harness_ops": [99] if (day, at) == self.harness_at else []}

        return Hooks(
            clock_set=lambda d, a: self.log.append(("clock", d, a)),
            driver_slots=lambda: self.slots,
            driver_run_slot=run_slot,
            driver_export_refs=lambda: {"refs": {}, "due_days": {}},
            driver_mark_harness_done=lambda seq, note: self.log.append(("harness_done", seq)),
            collector_load_refs=lambda refs: None,
            collector_trigger=lambda kind, d, a: self.log.append(("collect", kind, d, a)) or {},
            collector_mark_barrier=lambda name: self.log.append(("mark", name)),
            daily_barrier=lambda d: self.log.append(("barrier", "daily", d)) or {},
            restart_backend=lambda: self.log.append(("restart",)) or {},
            restart_barrier=lambda: self.log.append(("barrier", "restart")) or {},
            floor_activation=lambda: self.log.append(("floor",)) or {},
        )


def test_orchestrator_order_pre_driver_post_barrier_collect_and_late_slots():
    rec = Recorder([(0, "09:00"), (0, "17:30"), (0, "20:59"), (1, "10:00")])
    RunOrchestrator(rec.hooks()).run(0, 1)
    seq = rec.log
    index = {item: i for i, item in enumerate(seq)}
    order = [("collect", "pre_slot", 0, "09:00"), ("driver", 0, "09:00"), ("collect", "post_slot", 0, "09:00"),
             ("clock", 0, "17:30"), ("collect", "post_slot", 0, "17:30"), ("clock", 0, "18:00"),
             ("barrier", "daily", 0), ("collect", "after_daily_barrier", 0, "18:00"), ("clock", 0, "20:59"),
             ("driver", 0, "20:59")]
    positions = [index[item] for item in order]
    assert positions == sorted(positions)
    assert seq.index(("collect", "after_daily_barrier", 0, "18:00")) < seq.index(("clock", 0, "20:59"))
    assert [i for i in seq if i[0] == "collect" and i[1] == "end_of_run"] == []        # run parcial


def test_orchestrator_floor_restart_and_end_of_run_ordering():
    rec = Recorder([(13, "10:00"), (14, "09:00"), (90, "11:00"), (179, "10:00")], harness_at=(90, "11:00"))
    RunOrchestrator(rec.hooks()).run(13, 179)
    log = rec.log
    floor, first_d14 = log.index(("floor",)), log.index(("driver", 14, "09:00"))
    assert log.index(("mark", "after_floor_barrier")) == floor + 1 and floor < first_d14
    restart = log.index(("restart",))
    assert (log[restart + 1], log[restart + 2], log[restart + 3]) == (
        ("barrier", "restart"), ("mark", "after_restart_barrier"), ("harness_done", 99))
    assert log.index(("driver", 90, "11:00")) < restart < log.index(("collect", "post_slot", 90, "11:00"))
    assert log[-1] == ("collect", "end_of_run", 179, "23:59")
    days_with_barrier = [e[2] for e in log if e[:2] == ("barrier", "daily")]
    assert days_with_barrier == list(range(13, 180))                  # uma barreira por dia virtual


def test_orchestrator_drives_the_real_driver_and_collector_end_to_end(tmp_path):
    plan = compile_plan_small()
    driver, fake, store, evidence, _ = make_driver(tmp_path)
    collector, _, chain, _ = make_collector(tmp_path, fake, plan)
    from sim.driver.runner import virtual_utc

    log: list = []

    def clock_set(day, at):
        fake.now = virtual_utc(driver.d0, day, at, driver.offset)

    def run_slot(day, at):
        result = driver.run_slot(day, at)
        return result

    hooks = Hooks(
        clock_set=clock_set, driver_slots=driver.slot_keys, driver_run_slot=run_slot,
        driver_export_refs=lambda: {"refs": store.all_refs(), "due_days": store.all_due()},
        driver_mark_harness_done=driver.mark_harness_done,
        collector_load_refs=lambda refs: (collector.home / "refs.json").write_text(json.dumps(refs), encoding="utf-8"),
        collector_trigger=collector.trigger, collector_mark_barrier=collector.mark_barrier,
        daily_barrier=lambda day: log.append(day) or {}, restart_backend=lambda: {}, restart_barrier=lambda: {},
        floor_activation=lambda: {})
    RunOrchestrator(hooks).run(0, 16)
    assert log == list(range(17)) and store.counts()["done"] > 0
    assert ChainLog.verify(chain.path)[0] and len(ChainLog.read(chain.path)) > 0
    export_refs(driver, collector)


def compile_plan_small():
    from sim.oracle.collection_plan import compile_plan

    return compile_plan(load_oracle(), load_agenda_ops())


# --------------------------------------------------------------------------
# provisionamento do observador e captura do version_id (D-1.5-9)
# --------------------------------------------------------------------------
class ScriptedRunner:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def run(self, args, **kwargs):
        from sim.stack.lab.runner import Result

        self.calls.append((args, kwargs))
        code, out = self.replies.pop(0)
        return Result(code, out, "")


CREATED_PAID = ("Skill 'account.mark_paid' registrada e publicada com sucesso.\n  skill_id: 4\n"
                "  skill_version_id: 21\n  execution_mode: mutating\n")
CATALOG_OK = "account.mark_paid|active|published|mutating|internal_python\n"


def test_capture_mark_paid_version_created_exists_and_fail_closed():
    runner = ScriptedRunner([(0, CREATED_PAID), (0, CATALOG_OK)])
    found = driver_lab.capture_mark_paid_version(runner, CONFIG)
    assert found["version_id"] == 21 and found["class"] == "CREATED"
    assert runner.calls[0][0][:3] == ["docker", "exec", "auneron-sim-backend"] and "psql" in runner.calls[1][0]

    again = "Skill 'account.mark_paid' ja registrada (id=4, status=active). Nada foi alterado.\n"
    known = driver_lab.capture_mark_paid_version(ScriptedRunner([(0, again), (0, CATALOG_OK)]), CONFIG, 21)
    assert known["version_id"] == 21 and known["class"] == "EXISTS_EQUIVALENT"
    with pytest.raises(GuardViolation, match="fail closed"):
        driver_lab.capture_mark_paid_version(ScriptedRunner([(0, again), (0, CATALOG_OK)]), CONFIG)       # sem id conhecido
    with pytest.raises(GuardViolation):
        driver_lab.capture_mark_paid_version(ScriptedRunner([(1, "Traceback")]), CONFIG)


def test_observer_is_provisioned_like_the_other_users_and_never_as_a_persona():
    created = "Usu\u00e1rio criado: sim-harness-observer@nova-horizonte.example.com role=manager\n"
    secrets = {"users": {driver_lab.OBSERVER_USER: "observer-password-1"}}
    runner = ScriptedRunner([(0, created)])
    result = driver_lab.provision_observer(runner, CONFIG, secrets, client_factory=None)
    assert result["class"] == "CREATED" and result["role"] == "manager"
    args, kwargs = runner.calls[0]
    assert args[:4] == ["docker", "exec", "-i", "auneron-sim-backend"] and "--role" in args
    assert args[args.index("--role") + 1] == "manager" and kwargs["input_text"] == "observer-password-1\nobserver-password-1\n"
    assert driver_lab.OBSERVER_USER not in {p["user"] for p in CONFIG["personas"]}
