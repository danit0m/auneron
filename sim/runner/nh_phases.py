"""
Fases `nh-*` do CLI (SIM-1.5E C.1 + SIM-1.5F): ligam o runner ao lab. Camada FINA e NAO EXERCITADA AO VIVO neste
checkpoint (Docker nao autorizado): os testes conferem os comandos com duplos; a primeira execucao real sera o SR-1.
Qualquer fato que nao possa ser obtido => ausente => preflight reprova (fail-closed). Nenhuma fase corrige o ambiente.

O cenario vem de um BUNDLE (`nh` ou `sr1`); o RIM continua de esquema fechado. Fases que MUDAM o lab: `stage`, `open`,
`preflight` (arma o relogio em D0 08:00 POR ULTIMO) e `run` (so com D0_AUTHORIZATION.json valido). Nenhuma delas emite
autorizacao de D0 para `kind = RUN`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.live import env as E
from sim.live import flow
from sim.live.fingerprint import fingerprint
from sim.runner import bundle as bundle_mod
from sim.runner import containment
from sim.runner import declare
from sim.runner import detach
from sim.runner import growth as growth_mod
from sim.runner import harness_side
from sim.runner import host
from sim.runner import rim as rim_mod
from sim.runner import runner as runner_mod
from sim.runner import sched
from sim.runner import termination
from sim.runner.errors import PreflightFailed
from sim.runner.errors import RunnerError
from sim.runner.lab_ports import lab_ports
from sim.runner.live import LiveServices
from sim.runner.registry import Registry
from sim.stack.lab import driver_lab
from sim.stack.lab import sqlro
from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import lab_home

BUSINESS_TABLES = ("accounts", "account_events", "account_vencimento_changes", "approval_requests", "approval_decisions",
                   "approval_consumptions", "knowledge", "memory_items", "memory_evidence", "work_items")
D0_MARGIN_MS = 5 * 60 * 1000
SR1_SCENARIO_PREFIX = "sim-harness-sr1"


def runs_root() -> Path:
    return lab_home() / "runs"


def bundles_root() -> Path:
    return lab_home() / "bundles"


def inputs_dir(lab) -> Path:
    return lab.live_dir / "nh_inputs"


def _json(path) -> dict:
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def bundle_by_name(name: str) -> bundle_mod.Bundle:
    return bundle_mod.get_bundle(name, bundles_root() / name)


def bundle_for_scenario(scenario_id: str) -> bundle_mod.Bundle:
    return bundle_by_name("sr1" if scenario_id.startswith(SR1_SCENARIO_PREFIX) else "nh")


# ----------------------------------------------------------------------------------------------------------- stage
def phase_stage(lab, bundle_name: str = "nh") -> dict:
    bundle = bundle_by_name(bundle_name)
    work = inputs_dir(lab)
    work.mkdir(parents=True, exist_ok=True)
    oracle = bundle.load("oracle")
    ops = bundle.load("agenda")["ops"]
    version_id = lab.evidence["mark_paid_version_id"]
    timeline = bundle.timeline
    manifest = driver_lab.build_driver_manifest(bundle.agenda_path, bundle.scenario_id, version_id, driver_lab.active_personas(ops),
                                                timeline["d0"], timeline["last_day"] + 1)
    plan = harness_side.compile_plan_for(oracle, ops, d0=timeline["d0"], utc_offset_minutes=timeline["utc_offset_minutes"],
                                         observer_email=lab.config.email(E.OBSERVER))
    prereg = harness_side.preregistration(oracle)
    (work / "public_agenda.json").write_bytes(bundle.agenda_path.read_bytes())
    E.write_json(work / "driver_manifest.json", manifest)
    E.write_json(work / "collection_plan.json", plan)
    (work / "preregistration.json").write_bytes(canonical_bytes(prereg))
    (work / "oracle.json").write_bytes(Path(bundle.files["oracle"]).read_bytes())          # copia host-only (hash e proibido no DR-1)
    flow.stage_secrets(lab)
    flow.stage(lab, {"work": work, "plan": plan}, "nh", flat=True)
    return lab.record("NH-STAGE", True, {"bundle": bundle.name, **{name: E.sha(work / name) for name in
                                          ("public_agenda.json", "driver_manifest.json", "collection_plan.json",
                                           "preregistration.json")}})


# ----------------------------------------------------------------------------------------------------------- open
def _docker_value(lab, *args) -> str:
    res = lab.runner.run(["docker", *args])
    if res.code != 0 or not res.out.strip():
        raise RunnerError(f"docker {' '.join(args)} falhou")
    return res.out.strip()


def _index_tree(path: str) -> str:
    tree = declare.git_out(REPO_ROOT, "write-tree")
    return declare.git_out(REPO_ROOT, "rev-parse", f"{tree}:{path}")


def build_rim(lab, bundle: bundle_mod.Bundle, growth_model_sha256: str | None) -> dict:
    work = inputs_dir(lab)
    base_digest = _docker_value(lab, "image", "inspect", "-f", "{{index .RepoDigests 0}}", "python:3.11-slim")
    ids = {k: _docker_value(lab, "image", "inspect", "-f", "{{.Id}}", lab.config.image(k)) for k in ("base", "backend", "postgres")}
    from sim.driver.params import load_params
    from sim.stack.lab.config import STACK_DIR

    limits = {**declare.DEFAULT_LIMITS, "balance_mode": "enforce" if bundle.kind == "RUN" else "report"}
    declared = declare.build_declared(
        repo=REPO_ROOT, bundle=bundle, base_image_digest=base_digest, lab_image_ids=ids,
        compose_files={"base": lab.config.compose_file, "override": STACK_DIR / driver_lab.OVERRIDE_FILE},
        quiescence_sha256=sha256_bytes(canonical_bytes(lab.config["quiescence"])), driver_params_sha256=load_params().sha256,
        plan_sha256=E.sha(work / "collection_plan.json"), preregistration_sha256=E.sha(work / "preregistration.json"),
        authority_invariants_sha256=harness_side.authority_invariants_sha256(), growth_model_sha256=growth_model_sha256,
        limits=limits)
    observed = {"driver_manifest_sha256": E.sha(work / "driver_manifest.json"),
                "driver_manifest_agenda_sha256": _json(work / "driver_manifest.json")["public_agenda_sha256"],
                "compose_resolved_sha256": E.sha(lab.live_dir / "compose_resolved.json"),
                "driver_image": {"tree": _index_tree("sim/driver"), "base_digest": base_digest},
                "collector_image": {"tree": _index_tree("sim/collector"), "base_digest": base_digest}}
    return rim_mod.build_rim(bundle.kind, declared, observed)


def phase_open(lab, bundle_name: str = "nh", growth_model: Path | None = None, purpose: str = "FIRST",
               parent: str | None = None, reason: str | None = None) -> dict:
    bundle = bundle_by_name(bundle_name)
    growth_sha = None
    if growth_model is not None:
        growth_sha = growth_mod.model_sha256(_json(growth_model))          # valida o modelo (ausente/invalido => erro)
        if E.sha(growth_model) != growth_sha:
            raise RunnerError("growth_model.json nao esta em forma canonica")
    rim = build_rim(lab, bundle, growth_sha)
    registry = Registry(runs_root())
    info, ledger = runner_mod.open_new_run(registry, rim, purpose=purpose, parent_run_id=parent, reason=reason)
    if growth_model is not None:
        (info["run_dir"] / "inputs").mkdir(exist_ok=True)
        (info["run_dir"] / "inputs" / "growth_model.json").write_bytes(Path(growth_model).read_bytes())
    for container in (E.DRIVER, E.COLLECTOR):
        res = E.dexec(lab, container, "sh", "-c", f"printf %s {info['run_id']} > /state/RUN_ID", user="root")
        if res.code != 0:
            raise RunnerError(f"nao foi possivel gravar /state/RUN_ID em {container}")
    return lab.record("NH-OPEN", True, {"run_id": info["run_id"], "rim_sha256": info["rim_sha256"], "attempt": info["attempt"],
                                        "bundle": bundle.name})


# ----------------------------------------------------------------------------------------------------------- preflight
def _forbidden_hashes(lab, run_dir: Path, bundle: bundle_mod.Bundle) -> list:
    found = set()
    for root in (REPO_ROOT / "sim" / "scenarios", REPO_ROOT / "sim" / "oracle" / "artifacts", bundles_root()):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "public_agenda.json":
                found.add(hashlib.sha256(path.read_bytes()).hexdigest())
    for name in ("oracle.json", "preregistration.json"):
        found.add(E.sha(inputs_dir(lab) / name))
    return sorted(found)


def p14_facts(lab, registry: Registry, current_run_id: str) -> dict:
    """Fatos de contaminacao (SIM-1.5F A.9, C-6): stack novo, estados vazios, banco sem negocio e sem marcadores."""
    from sim.rehearsal import contamination

    terminals = [r for r in registry.events() if r["event"] == "TERMINAL"]
    last = terminals[-1]["t"] if terminals else None
    created = []
    res = lab.runner.run(["docker", "volume", "ls", "--filter", "name=auneron_sim", "--format", "{{.Name}}"])
    for name in res.out.split():
        info = lab.runner.run(["docker", "volume", "inspect", "-f", "{{.CreatedAt}}", name])
        created.append(info.out.strip())
    fresh = True if last is None else bool(created) and all(c.replace(" ", "T")[:19] > last[:19] for c in created)
    state = lambda container, path: E.dexec(lab, container, "test", "-e", path).code == 0                      # noqa: E731
    markers = " or ".join(f"cliente ilike '%{m}%' or email ilike '%{m}%'" for m in contamination.MARKERS)
    rows = sqlro.query(lab.runner, lab.config, f"select count(*) from accounts where {markers}")
    business = sum(int(fingerprint(lab.runner, lab.config).get(t, [0])[0]) for t in BUSINESS_TABLES)
    return {"volumes_created_after_previous_terminal": fresh, "driver_state_present": state(E.DRIVER, "/state/driver_state.db"),
            "collector_state_present": state(E.COLLECTOR, "/state/collected.jsonl"), "business_rows": business,
            "marker_rows": int(rows[0][0]), "previous_runs_non_terminal": any(
                run["terminal"] is None for rid, run in registry.runs().items() if rid != current_run_id)}


def collect_preflight_facts(lab, rim: dict, run_dir: Path, bundle: bundle_mod.Bundle, registry: Registry) -> dict:
    g = lab.evidence["gates"]
    work = inputs_dir(lab)

    def git(*a):
        return declare.git_out(REPO_ROOT, *a)

    tests = subprocess.run([sys.executable, "-m", "pytest", "sim/tests", "-q", "-p", "no:cacheprovider"], cwd=REPO_ROOT,
                           capture_output=True, text=True)
    regenerated = None
    try:
        if bundle.name == "nh":
            from sim.oracle import v2_2

            _, files = v2_2.generate_oracle_v2_2("nh-small", 340001, REPO_ROOT / "sim" / "scenarios",
                                                 bundle.files["oracle"].parent.parent / "nh-small-v2-s340001-oracle-v2.1")
            regenerated = (files["oracle.json"] == Path(bundle.files["oracle"]).read_bytes()
                           and files["manifest_v2_2.json"] == Path(bundle.files["oracle_manifest"]).read_bytes())
        else:
            from sim.rehearsal import generate as G

            regenerated = all(G.build_bundle()[n] == (Path(bundle.files["oracle"]).parent / n).read_bytes()
                              for n in G.BUNDLE_FILES)
    except Exception:                                                                    # noqa: BLE001 - qualquer falha reprova
        regenerated = False
    resolved = _json(lab.live_dir / "compose_resolved.json")
    nets = lambda svc: sorted((resolved["services"][svc].get("networks") or {}))           # noqa: E731
    prov = g.get("LIVE-PROVISION", {})
    started = g.get("LIVE-UP-EXT", {}).get("application_started") or {}
    absences = prov.get("absences", {})
    fp = fingerprint(lab.runner, lab.config)
    business_rows = sum(fp.get(t, [0])[0] for t in BUSINESS_TABLES)
    skills = {s["key"]: s for s in prov.get("skills", [])}
    tokens = E.dexec(lab, E.DRIVER, "sh", "-c", "sha256sum /inputs/public_agenda.json /inputs/driver_manifest.json").out.split()
    ctokens = E.dexec(lab, E.COLLECTOR, "sh", "-c", "sha256sum /inputs/collection_plan.json").out.split()
    from sim.runner.lab_ports import dr1_probe

    dr1 = dr1_probe(lab, _forbidden_hashes(lab, run_dir, bundle), with_controls=True)
    ids = [E.dexec(lab, c, "cat", "/state/RUN_ID").out.strip() for c in (E.DRIVER, E.COLLECTOR)]
    others = [n for n in lab.runner.run(["docker", "ps", "--format", "{{.Names}}"]).out.split()
              if not n.startswith(("auneron-sim-", "auneron-backend", "auneron-postgres", "auneron-frontend"))]
    p14 = p14_facts(lab, registry, run_dir.name)
    clock = lab.clock.set(lab.clock.virtual(0, "08:00"))                                 # arma o relogio POR ULTIMO (P10)
    now_ms = int(lab.clock.expected_at(time.time()) * 1000)
    from sim.runner import disk as disk_mod

    return {
        "P1": {"head": git("rev-parse", "HEAD"), "tree_clean": git("status", "--porcelain", "--untracked-files=no") == "",
               "index_empty": git("diff", "--cached", "--name-only") == "", "static_tests_ok": tests.returncode == 0},
        "P2": {"backend_tree": git("rev-parse", "HEAD:backend"), "frontend_tree": git("rev-parse", "HEAD:frontend")},
        "P3": {"oracle_sha256": bundle.sha("oracle"), "oracle_manifest_sha256": bundle.sha("oracle_manifest"),
               "agenda_sha256": bundle.sha("agenda"), "world_sha256": bundle.sha("world"),
               "v2_manifest_sha256": bundle.sha("v2_manifest"), "v2_oracle_sha256": bundle.sha("v2_oracle"),
               "reproduced_in_memory": regenerated},
        "P4": {"residues": 0 if g.get("LIVE-PREP", {}).get("status") == "PASS" else None, "volumes_new": True,
               "dev_snapshot_before": (lab.dir / "dev_snapshot_before.json").exists()},
        "P5": {"base_digest": rim["observed"]["driver_image"]["base_digest"], "driver_tree": rim["observed"]["driver_image"]["tree"],
               "collector_tree": rim["observed"]["collector_image"]["tree"],
               "build_identity_state": started.get("build_identity_state"), "build_git_sha": started.get("build_git_sha"),
               "build_dirty": started.get("build_git_dirty")},
        "P6": {"compose_guard_ok": g.get("LIVE-COMPOSE-CONFIG", {}).get("status") == "PASS", "backend_networks": nets("sim-backend"),
               "driver_networks": nets("sim-driver"), "collector_networks": nets("sim-collector")},
        "P7": {"business_rows": business_rows},
        "P8": {"users_ok": prov.get("status") == "PASS", "bindings": sum(absences.values()), "grants": sum(absences.values()),
               "system_principals": sum(absences.values()), "skills_active": prov.get("status") == "PASS",
               "mark_paid_version_id": lab.evidence.get("mark_paid_version_id"),
               "version_from_created_output": skills.get("account.mark_paid", {}).get("class") == "CREATED"},
        "P9": {"maintenance_enabled": started.get("maintenance_enabled"), "app_env": started.get("environment"),
               "worker_enabled": started.get("evidence_worker_enabled"), "floor_state": started.get("evidence_floor_state")},
        "P10": {"virtual_now_ms": now_ms, "d0_first_slot_ms": int(lab.clock.virtual(0, "09:00").timestamp() * 1000),
                "margin_ms": D0_MARGIN_MS, "clock_target": clock["target"]},
        "P11": {"agenda_sha256": tokens[0] if tokens else None, "manifest_sha256": tokens[2] if len(tokens) > 2 else None,
                "plan_sha256": ctokens[0] if ctokens else None, "dr1_clean": dr1["clean"]},
        "P12": {"run_id_file_ok": all(i == run_dir.name for i in ids), "ledger_opened": (run_dir / "ledger.jsonl").exists(),
                "free_bytes": disk_mod.measure_free(lab_home())},
        "P13": {"awake_acquired": host.keep_awake(), "foreign_docker_activity": len(others),
                "dev_untouched": (lab.dir / "dev_snapshot_before.json").exists()},
        "P14": p14,
        "P15": sched.scan_unreconciled(runs_root(), sched.run_default, alive=host.pid_alive,
                                       owned_of=lambda found_proc: containment.owned_summary(found_proc, sched.run_default)),
    }


def load_growth_model(run_dir: Path, rim: dict) -> dict | None:
    """Modelo de crescimento do `RUN`: validado e conferido com o hash do RIM. Ensaio: None."""
    if rim["kind"] != "RUN":
        return None
    path = run_dir / "inputs" / "growth_model.json"
    if not path.is_file():
        raise PreflightFailed("RUN sem growth_model.json validado (execute o SR-1 e gere o modelo)")
    model = growth_mod.validate_growth_model(_json(path))
    if growth_mod.model_sha256(model) != rim["declared"]["growth_model_sha256"]:
        raise PreflightFailed("growth_model.json difere do hash do RIM")
    return model


def phase_preflight(lab, run_id: str) -> dict:
    registry = Registry(runs_root())
    run_dir = runs_root() / run_id
    rim = _json(run_dir / "rim.json")
    bundle = bundle_for_scenario(rim["declared"]["scenario_id"])
    ledger = runner_mod.RunLedger(run_dir)
    facts = collect_preflight_facts(lab, rim, run_dir, bundle, registry)
    model = load_growth_model(run_dir, rim)
    projected = None if model is None else model["projected_growth_bytes"]
    try:
        runner_mod.run_preflight(ledger, registry, rim, facts, projected_growth_bytes=projected)
    except PreflightFailed as error:
        return lab.record("NH-PREFLIGHT", False, {"run_id": run_id, "failures": str(error)[:2000]})
    return lab.record("NH-PREFLIGHT", True, {"run_id": run_id})


# ----------------------------------------------------------------------------------------------------------- run
def apply_launch_env(proc: Path) -> dict:
    """O processo destacado nao herda o ambiente do shell: carrega o do `launch.json` (SIM-1.5F C.2)."""
    doc = detach.read_launch(proc)
    if doc is None:
        return {}
    for key, value in doc.get("env", {}).items():
        os.environ[key] = str(value)
    return doc


def start_containment(proc: Path, stop):
    """Vigia do motor (SIM-1.5G.4). So sob o Task Scheduler (launch.json); la o motor `cmd` DEVE ser verificavel, senao o run NAO
    inicia (exit.json CONTAINMENT_UNAVAILABLE, `process_code` 3 = violacao de contrato da CLI) e nada e executado. Ao perder o motor:
    STOP gracioso e, passados 120 s, `exit.json` ENGINE_LOST + saida dura (o R0 sela UNCLEAN_EXIT na proxima partida)."""
    launch = detach.read_launch(proc) or {}

    def hard_exit() -> None:
        detach.write_terminal(proc, termination.EXIT_CODES["ENGINE_LOST"], "ENGINE_LOST", process_code=1, detail="motor perdido: saida dura apos a graca")
        os._exit(termination.EXIT_CODES["ENGINE_LOST"])

    def cmdline_of(pid):
        found = sched.process_chain(sched.run_default, pid)
        return found[0].get("cmd") if found and found[0].get("pid") == pid else None

    try:
        _, guard = containment.install(proc, ops=containment.native_ops(), cmdline_of=cmdline_of, schedule_pid=launch.get("schedule_pid"),
                                       graceful=lambda: stop.request("ENGINE_LOST"), hard_exit=hard_exit, grace_s=containment.NH_GRACE_S,
                                       applicable=launch.get("mechanism") == sched.MECHANISM, require=True)
    except containment.ContainmentUnavailable as error:
        detach.write_terminal(proc, termination.EXIT_CODES["CONTAINMENT_UNAVAILABLE"], "CONTAINMENT_UNAVAILABLE", process_code=3, detail=str(error)[:300])
        raise
    return guard


def phase_run(lab, run_id: str) -> dict:
    registry = Registry(runs_root())
    run_dir = runs_root() / run_id
    proc = detach.proc_dir(runs_root(), run_id)
    stop = host.StopFlag(proc / detach.STOP)
    guard = start_containment(proc, stop)                    # SIM-1.5G.4: antes de qualquer trabalho; motor nao verificavel => nao inicia
    rim = _json(run_dir / "rim.json")
    bundle = bundle_for_scenario(rim["declared"]["scenario_id"])
    ledger = runner_mod.RunLedger(run_dir)
    work = inputs_dir(lab)
    oracle, plan = _json(work / "oracle.json"), _json(work / "collection_plan.json")
    ops = _json(work / "public_agenda.json")["ops"]
    model = load_growth_model(run_dir, rim)
    hooks, state = flow.build_hooks(lab, floor=True)
    ttl = flow.o1_ttl_minutes(lab)
    ports = lab_ports(lab, run_dir, hooks_state=state, forbidden_hashes=_forbidden_hashes(lab, run_dir, bundle), oracle=oracle,
                      inputs_dir=work, ttl_minutes=ttl)
    containers = ports.containers()
    live = LiveServices(
        run_dir=run_dir, run_id=run_id, plan=plan, agenda_ops=ops, timeline=rim["declared"]["timeline"], ports=ports, kind=rim["kind"],
        baseline={"accounts": 0, "approvals": 0, "work_items": 0},
        expected_containers={k: v["started_at"] for k, v in containers.items()},
        expected_networks={k: v["networks"] for k, v in containers.items()},
        barrier_hard_ms=int(lab.config["quiescence"]["hard_timeout_s"]) * 1000)
    stop.install()
    services = live.services(hooks, awake=host.keep_awake)
    if model is not None:
        services.growth_per_day_bytes = growth_mod.per_virtual_day_bytes(model, rim["declared"]["timeline"]["last_day"] + 1)
    nh = runner_mod.NhRunner(run_dir=run_dir, rim=rim, ledger=ledger, registry=registry, services=services, stop=stop)
    beat = host.HeartbeatWriter(proc / detach.HEARTBEAT, lambda: nh.status)
    beat.start()

    def cleanup() -> None:
        beat.stop()
        host.release_awake()
        if guard is not None:
            guard.finish()

    # exit.json: `code` semantico inalterado (0 COMPLETED / 2 outro estado / 1 excecao); `process_code` = o que a CLI devolve
    # (0 / 1 / 1 ou 3 se RunnerError). Falha ao gravar a evidencia nunca vira sucesso; a causa original e preservada.
    result = detach.run_and_record_exit(proc, nh.run, cleanup, lost=lambda: bool(guard is not None and guard.lost))
    return lab.record("NH-RUN", result["state"] == "COMPLETED", result)
