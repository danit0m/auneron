"""Montagem de RIM, fatos e hooks de teste para o plano de controle do run (sem Docker, sem Oracle real)."""

from __future__ import annotations

import copy
from types import SimpleNamespace

from sim.runner import rim as rim_mod
from sim.runner import runner as runner_mod
from sim.runner.declare import DEFAULT_DISK_POLICY
from sim.runner.declare import GIB
from sim.runner.interlock import authorize_rehearsal
from sim.runner.registry import Registry
from sim.stack.lab.run_orchestrator import Hooks

BASE_DIGEST = "python@sha256:" + "b" * 64


def sha(char: str = "a") -> str:
    return (char * 64)[:64]


def declared(last_day: int = 3, floor_day: int = 1, restart_day: int = 2, growth=None, balance_mode: str = "enforce") -> dict:
    trees = {k: "t" + k for k in ("backend", "frontend", "sim_driver", "sim_collector", "sim_evaluator", "sim_oracle",
                                  "sim_live", "sim_runner", "sim_stack")}
    return {
        "scenario_id": "nh-small-v2-s340001", "variant": "nh-small", "seed": 340001, "oracle_version": "v2.2",
        "oracle_sha256": sha("1"), "oracle_manifest_sha256": sha("2"), "derived_from_oracle_sha256": sha("3"),
        "agenda_sha256": sha("4"), "world_sha256": sha("5"), "v2_manifest_sha256": sha("6"), "v2_oracle_sha256": sha("7"),
        "generator_version": "sim-1.5.1", "repo_commit": "c" * 40, "trees": trees, "base_image_digest": BASE_DIGEST,
        "lab_image_ids": {"base": "sha256:" + "1" * 64, "backend": "sha256:" + "2" * 64, "postgres": "sha256:" + "3" * 64},
        "compose_sha256": {"base": sha("8"), "override": sha("9")},
        "params": {"quiescence_sha256": sha("a"), "driver_params_sha256": sha("b"), "o1_limit_ms": 1000,
                   "clock_skew_limit_ms": 3000, "balance_mode": balance_mode},
        "plan_sha256": sha("c"), "preregistration_sha256": sha("d"), "authority_invariants_sha256": sha("e"),
        "timeline": {"d0": "2026-01-05", "utc_offset_minutes": -180, "floor_day": floor_day, "restart_day": restart_day,
                     "last_day": last_day},
        "disk_policy": dict(DEFAULT_DISK_POLICY), "growth_model_sha256": growth,
        "recovery_policy": "R0", "claim_policy_version": "claim.v1",
    }


def observed(d: dict) -> dict:
    return {"driver_manifest_sha256": sha("f"), "driver_manifest_agenda_sha256": d["agenda_sha256"],
            "compose_resolved_sha256": sha("0"),
            "driver_image": {"tree": d["trees"]["sim_driver"], "base_digest": d["base_image_digest"]},
            "collector_image": {"tree": d["trees"]["sim_collector"], "base_digest": d["base_image_digest"]}}


def make_rim(kind: str = "REHEARSAL", **kwargs) -> dict:
    d = declared(**kwargs)
    return rim_mod.build_rim(kind, d, observed(d))


def good_preflight_facts(rim: dict) -> dict:
    d, o = rim["declared"], rim["observed"]
    return {
        "P1": {"head": d["repo_commit"], "tree_clean": True, "index_empty": True, "static_tests_ok": True},
        "P2": {"backend_tree": d["trees"]["backend"], "frontend_tree": d["trees"]["frontend"]},
        "P3": {"oracle_sha256": d["oracle_sha256"], "oracle_manifest_sha256": d["oracle_manifest_sha256"],
               "agenda_sha256": d["agenda_sha256"], "world_sha256": d["world_sha256"],
               "v2_manifest_sha256": d["v2_manifest_sha256"], "v2_oracle_sha256": d["v2_oracle_sha256"],
               "reproduced_in_memory": True},
        "P4": {"residues": 0, "volumes_new": True, "dev_snapshot_before": True},
        "P5": {"base_digest": d["base_image_digest"], "driver_tree": d["trees"]["sim_driver"],
               "collector_tree": d["trees"]["sim_collector"], "build_identity_state": "valid",
               "build_git_sha": d["repo_commit"], "build_dirty": False},
        "P6": {"compose_guard_ok": True, "backend_networks": ["auneron_sim_driver", "auneron_sim_internal"],
               "driver_networks": ["auneron_sim_driver"], "collector_networks": ["auneron_sim_driver"]},
        "P7": {"business_rows": 0},
        "P8": {"users_ok": True, "bindings": 0, "grants": 0, "system_principals": 0, "skills_active": True,
               "mark_paid_version_id": 7, "version_from_created_output": True},
        "P9": {"maintenance_enabled": True, "app_env": "production", "worker_enabled": False, "floor_state": "unset"},
        "P10": {"virtual_now_ms": 0, "d0_first_slot_ms": 3_600_000, "margin_ms": 300_000},
        "P11": {"agenda_sha256": d["agenda_sha256"], "manifest_sha256": o["driver_manifest_sha256"],
                "plan_sha256": d["plan_sha256"], "dr1_clean": True},
        "P12": {"run_id_file_ok": True, "ledger_opened": True, "free_bytes": 100 * GIB},
        "P13": {"awake_acquired": True, "foreign_docker_activity": 0, "dev_untouched": True},
        "P14": {"volumes_created_after_previous_terminal": True, "driver_state_present": False, "collector_state_present": False,
                "business_rows": 0, "marker_rows": 0, "previous_runs_non_terminal": False},
    }


def good_growth(db: int = 100_000_000, vhdx: int = 10 * GIB) -> dict:
    return {"db_bytes": db, "vhdx_bytes": vhdx, "state_bytes": {"driver": 1_000_000, "collector": 2_000_000},
            "evidence_bytes": 3_000_000, "log_bytes": {"backend": 1_000, "driver": 100}, "top_relations": [["accounts", 4096]]}


def good_day_facts(ops_through_day: int = 0, **over) -> dict:
    facts = {
        "time": {"real_open": "r0", "real_close": "r1", "virtual_open": "v0", "virtual_close": "v1", "clock_skew_ms": 100,
                 "barrier_total_ms": 175_000, "barrier_events_ok": True, "barrier_hard_ms": 600_000, "o1_ms": 50},
        "driver": {"counts": {"done": ops_through_day}, "in_flight": 0, "agenda_ops_through_day": ops_through_day},
        "collector": {"expected_items": 0, "collected_items": 0, "chain_ok": True, "missing": 0, "problems": 0},
        "balance": {"accounts": [0, 0]},
        "infra": {"containers_ok": True, "unexpected_restarts": [], "topology_ok": True, "run_id_ok": True},
        "disk": {"free_bytes": 50 * GIB},
        "growth": good_growth(),
        "authority": {"status_lines": 0, "chain_lines": 0, "tripwire": []},
        "logs": [],
    }
    for path, value in over.items():
        group, key = path.split("__")
        facts[group][key] = value
    return facts


class Recorder:
    """Hooks que registram a ordem; `fail` = {hook: excecao} dispara na primeira chamada."""

    def __init__(self, slots, harness_at=None, fail=None):
        self.slots, self.harness_at, self.fail, self.log = slots, harness_at, dict(fail or {}), []

    def _do(self, name, *args):
        self.log.append((name, *args))
        exc = self.fail.pop(name, None)
        if exc is not None:
            raise exc
        return {}

    def hooks(self) -> Hooks:
        def run_slot(day, at):
            self._do("driver", day, at)
            return {"harness_ops": [99] if (day, at) == self.harness_at else []}

        return Hooks(
            clock_set=lambda d, a: self._do("clock", d, a), driver_slots=lambda: self.slots, driver_run_slot=run_slot,
            driver_export_refs=lambda: {"refs": {}, "due_days": {}}, driver_mark_harness_done=lambda s, n: self._do("harness_done", s),
            collector_load_refs=lambda refs: None, collector_trigger=lambda k, d, a: self._do("collect", k, d, a),
            collector_mark_barrier=lambda n: self._do("mark", n), daily_barrier=lambda d: self._do("barrier", d),
            restart_backend=lambda: self._do("restart_backend"), restart_barrier=lambda: self._do("restart_barrier"),
            floor_activation=lambda: self._do("floor"))


def setup_run(tmp_path, *, kind="REHEARSAL", token=True, recorder=None, alive=lambda pid: False, facts_mutator=None,
              **rim_kwargs) -> SimpleNamespace:
    """Abre o run, passa o preflight e (opcionalmente) emite o token de ensaio. Devolve tudo o que o teste precisa."""
    rim = make_rim(kind, **rim_kwargs)
    registry = Registry(tmp_path / "runs")
    info, ledger = runner_mod.open_new_run(registry, rim, purpose="FIRST")
    facts = good_preflight_facts(rim)
    if facts_mutator:
        facts_mutator(facts)
    runner_mod.run_preflight(ledger, registry, rim, facts, projected_growth_bytes=GIB)
    if token and kind == "REHEARSAL":
        authorize_rehearsal(info["run_dir"], info["run_id"], info["rim_sha256"], kind, "PO", "teste")
    return SimpleNamespace(rim=rim, registry=registry, info=info, ledger=ledger, run_dir=info["run_dir"],
                           run_id=info["run_id"], recorder=recorder, alive=alive)


def services_for(run, *, slots=None, harness_at=None, fail=None, day_facts=None, finalize=None, probe=None, preserve=None,
                 awake=lambda: True):
    recorder = run.recorder or Recorder(slots if slots is not None else [], harness_at, fail)
    run.recorder = recorder
    calls = {"preserve": [], "probe": [], "finalize": 0}

    def default_day_facts(day):
        return good_day_facts()

    def default_finalize():
        calls["finalize"] += 1
        return {"violations": []}

    def default_probe(day, when):
        calls["probe"].append((day, when))
        return {"clean": True}

    def default_preserve(cls):
        calls["preserve"].append(cls)
        return {"ok": True}

    run.calls = calls
    return runner_mod.RunServices(hooks=recorder.hooks(), day_facts=day_facts or default_day_facts,
                                  finalize=finalize or default_finalize, preserve=preserve or default_preserve,
                                  probe=probe or default_probe, awake=awake)


def make_runner(run, services, **kwargs):
    from sim.runner.host import RunLock

    lock = kwargs.pop("lock", None) or RunLock(run.run_dir, pid=lambda: 4242, alive=run.alive)
    return runner_mod.NhRunner(run_dir=run.run_dir, rim=run.rim, ledger=run.ledger, registry=run.registry, services=services,
                               lock=lock, **kwargs)


def events(run) -> list:
    from sim.runner.ledger import RunLedger

    return [r["event"] for r in RunLedger(run.run_dir).events()]


def mutated(obj: dict, path: str, value) -> dict:
    out = copy.deepcopy(obj)
    node = out
    *parents, last = path.split(".")
    for key in parents:
        node = node[key]
    node[last] = value
    return out
