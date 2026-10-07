"""
Fluxos do harness live: entrega de cenarios aos containers, Hooks do `RunOrchestrator` do candidato apontando
para os CLIs que rodam DENTRO dos containers (camada fina `docker exec`), ativacao do floor com o override,
pipeline completo (Driver + Collector + barreira diaria real + Evaluator offline) e medicao O-1.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sim.evaluator.evaluate import evaluate
from sim.live import env as E
from sim.live import judges
from sim.live import scenarios as S
from sim.oracle.collection_plan import compile_plan
from sim.stack.lab import activation
from sim.stack.lab import driver_lab
from sim.stack.lab.quiescence import HarnessError
from sim.stack.lab.run_orchestrator import Hooks
from sim.stack.lab.run_orchestrator import RunOrchestrator


class ExecError(RuntimeError):
    """Falha da camada `docker exec` (o CLI do container saiu com codigo diferente de 0)."""


def driver_env(tag: str) -> dict:
    return {"SIM_DRIVER_HOME": f"/state/{tag}", "SIM_DRIVER_AGENDA": f"/inputs/{tag}/public_agenda.json",
            "SIM_DRIVER_MANIFEST": f"/inputs/{tag}/driver_manifest.json"}


def collector_env(tag: str) -> dict:
    return {"SIM_COLLECTOR_HOME": f"/state/{tag}", "SIM_COLLECTOR_PLAN": f"/inputs/{tag}/collection_plan.json"}


def stage_secrets(lab) -> None:
    """Segredos MINIMOS por container (o Driver so as personas ativas; o Collector so o observador)."""
    work = lab.live_dir / "secrets"
    E.write_json(work / "driver_secrets.json", driver_lab.driver_secrets(lab.secrets, E.DRIVER_PERSONAS))
    E.write_json(work / "collector_secrets.json", driver_lab.collector_secrets(lab.secrets))
    E.stage_inputs(lab, E.DRIVER, {"secrets.json": work / "driver_secrets.json"})
    E.stage_inputs(lab, E.COLLECTOR, {"secrets.json": work / "collector_secrets.json"})


def prepare(lab, builder, oracle: dict | None, version_id: int) -> dict:
    """Escreve agenda/manifesto (e plano de coleta + Oracle tecnico, se houver) no host."""
    work = lab.live_dir / f"scen_{builder.tag}"
    info = builder.write(work, version_id)
    info["work"] = work
    if oracle is not None:
        E.write_json(work / "oracle_tech.json", oracle)
        plan = compile_plan(oracle, info["ops"], S.D0, int(lab.config["clock"]["utc_offset_minutes"]),
                            lab.config.email(E.OBSERVER))
        E.write_json(work / "collection_plan.json", plan)
        info["plan"] = plan
    return info


def stage(lab, info: dict, tag: str, flat: bool = False) -> dict:
    """Entrega agenda/manifesto ao Driver (e o plano ao Collector). `flat=True` usa /inputs e /state diretos."""
    sub = "" if flat else tag
    E.stage_inputs(lab, E.DRIVER, {"public_agenda.json": info["work"] / "public_agenda.json",
                                   "driver_manifest.json": info["work"] / "driver_manifest.json"}, sub)
    envs = {"driver": {} if flat else driver_env(tag), "collector": {}}
    if "plan" in info:
        E.stage_inputs(lab, E.COLLECTOR, {"collection_plan.json": info["work"] / "collection_plan.json"}, sub)
        envs["collector"] = {} if flat else collector_env(tag)
    return envs


# ----------------------------------------------------------------------------------- floor (D14) com o override
def activate_floor(lab, floor_day: int = S.FLOOR_DAY) -> dict:
    """Procedimento REAL (EVIDENCE_ACTIVATION_PROCEDURE): T0 CAPTURADO do relogio do PostgreSQL, validado, e o
    backend RECRIADO com o floor. Diferente de `gates.phase_floor` do SIM-1.4 so em um ponto: a recriacao usa o
    compose COM o override (o backend precisa continuar nas DUAS redes)."""
    cfg = lab.config
    hours, minutes = map(int, cfg["clock"]["floor_at"].split(":"))
    before = f"{hours if minutes else hours - 1:02d}:{(minutes - 1) % 60:02d}"
    data = {"clock_pre": lab.clock.set(lab.clock.virtual(floor_day, before))}
    data["preflight_pre"] = activation.capture_and_check(lab.runner, cfg)
    data["clock_floor"] = lab.clock.set(lab.clock.virtual(floor_day, cfg["clock"]["floor_at"]))
    time.sleep(1)
    data["preflight"] = activation.capture_and_check(lab.runner, cfg)
    if not (activation.preflight_ok(data["preflight_pre"]) and activation.preflight_ok(data["preflight"])):
        lab.record("FLOOR", False, data)
        raise HarnessError("preflight do floor falhou")
    t0 = data["preflight"]["t0"]
    lab.evidence["floor_t0"] = t0
    lab.save()
    since = time.time()
    recreate = E.compose_ext(lab, "up", "-d", "--no-deps", "--force-recreate", "sim-backend")
    data["recreate_code"] = recreate.code
    data["ready"] = lab.wait_ready()
    data["application_started"] = lab.startup_record(since)
    started = data["application_started"] or {}
    data["barrier"] = lab.run_barrier(since, lab.events())
    data["networks_after_recreate"] = sorted(json.loads(lab.runner.run(
        ["docker", "inspect", E.BACKEND]).out)[0]["NetworkSettings"]["Networks"])
    ok = (recreate.code == 0 and data["ready"]["ready"] and started.get("evidence_floor") == t0
          and started.get("evidence_floor_state") == "armed" and started.get("evidence_worker_enabled") is True
          and data["networks_after_recreate"] == sorted({"auneron_sim_internal", driver_lab.DRIVER_NETWORK}))
    lab.record("FLOOR", ok, data)
    if not ok:
        raise HarnessError("floor nao ficou armado (ou o backend perdeu uma das redes)")
    return data


# ----------------------------------------------------------------------------------- hooks docker exec
def build_hooks(lab, *, denv=None, cenv=None, floor: bool = False):
    state = {"since": None, "barriers": [], "triggers": [], "slots": [], "floor": None}
    denv, cenv = denv or {}, cenv or {}

    def checked(res, what):
        payload = E.parse_last_json(res)
        if res.code != 0:
            raise ExecError(f"{what}: exit {res.code}: {payload}")
        return payload

    def clock_set(day, at):
        if at == "18:00":
            state["since"] = time.time()
        lab.clock.set(lab.clock.virtual(day, at))

    def slots():
        res = E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "slots", env=denv)
        return [tuple(s) for s in checked(res, "slots")]

    def run_slot(day, at):
        res = E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "run-slot", "--day", str(day), "--at", at, env=denv)
        payload = checked(res, f"run-slot {day} {at}")
        state["slots"].append({"day": day, "at": at, "executed": payload["executed"], "harness_ops": payload["harness_ops"]})
        return payload

    def export_refs():
        return checked(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "export-refs", env=denv), "export-refs")

    def load_refs(data):
        checked(E.dexec(lab, E.COLLECTOR, "python", "-m", "sim.collector.cli", "load-refs", stdin=json.dumps(data), env=cenv),
                "load-refs")

    def trigger(kind, day, at):
        res = E.dexec(lab, E.COLLECTOR, "python", "-m", "sim.collector.cli", "trigger", "--kind", kind, "--day", str(day),
                      "--at", at, env=cenv)
        payload = checked(res, f"trigger {kind} {day} {at}")
        state["triggers"].append(payload)
        return payload

    def mark_barrier(name):
        return checked(E.dexec(lab, E.COLLECTOR, "python", "-m", "sim.collector.cli", "mark-barrier", "--name", name,
                               env=cenv), "mark-barrier")

    def daily_barrier(day):
        result = lab.run_barrier(state["since"], lab.events())
        state["barriers"].append({"day": day, "barrier": result})
        return result

    def mark_done(seq, note):
        return checked(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "mark-harness-done", "--seq", str(seq),
                               "--note", note, env=denv), "mark-harness-done")

    def floor_activation():
        if not floor:
            return {}
        state["floor"] = activate_floor(lab)
        return state["floor"]

    hooks = Hooks(clock_set=clock_set, driver_slots=slots, driver_run_slot=run_slot, driver_export_refs=export_refs,
                  driver_mark_harness_done=mark_done, collector_load_refs=load_refs, collector_trigger=trigger,
                  collector_mark_barrier=mark_barrier, daily_barrier=daily_barrier, restart_backend=lambda: {},
                  restart_barrier=lambda: {}, floor_activation=floor_activation)
    return hooks, state


def provenance_facts(lab) -> dict:
    """Fato do PRODUTO via CLI somente leitura (executado pelo harness; o Collector nao o le)."""
    res = E.dexec(lab, E.BACKEND, "python", "scripts/evidence_provenance_report.py", "report")
    facts = {"provenance_exit": res.code}
    try:
        facts["provenance_report"] = json.loads(res.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        facts["provenance_report"] = None
        facts["provenance_raw_tail"] = res.text[-300:]
    return facts


def run_pipeline(lab, info: dict, oracle: dict, tag: str, *, first_day: int, last_day: int, flat: bool = False,
                 floor: bool = False, with_provenance: bool = False) -> dict:
    """Driver + Collector em containers + barreira diaria real + Evaluator offline. Devolve a avaliacao e a evidencia."""
    denv = {} if flat else driver_env(tag)
    cenv = {} if flat else collector_env(tag)
    hooks, state = build_hooks(lab, denv=denv, cenv=cenv, floor=floor)
    orch = RunOrchestrator(hooks, floor_day=S.FLOOR_DAY if floor else 99, last_day=last_day)
    started = time.time()
    events = orch.run(first_day=first_day, last_day=last_day)
    seconds = round(time.time() - started)
    state_dir = "/state" if flat else f"/state/{tag}"
    driver_lines = E.cat_json_lines(lab, E.DRIVER, f"{state_dir}/driver_evidence.jsonl")
    collected = E.cat_json_lines(lab, E.COLLECTOR, f"{state_dir}/collected.jsonl")
    refs = E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "export-refs", env=denv))
    facts = provenance_facts(lab) if with_provenance else {}
    plan = info["plan"]
    evaluation = evaluate(oracle, plan, collected, driver_lines, refs, facts)
    work = info["work"]
    E.write_json(work / "evaluation.json", evaluation)
    E.write_json(work / "refs.json", refs)
    E.write_json(work / "facts.json", facts)
    (work / "driver_evidence.jsonl").write_text("\n".join(json.dumps(r) for r in driver_lines), encoding="utf-8")
    (work / "collected.jsonl").write_text("\n".join(json.dumps(r) for r in collected), encoding="utf-8")
    from sim.collector.collect import missing_items
    from sim.collector.collect import verify_records
    return {"evaluation": evaluation, "events": len(events), "seconds": seconds, "state": state,
            "driver_lines": driver_lines, "collected": collected, "refs": refs, "facts": facts,
            "problems": verify_records(plan, collected), "missing": missing_items(plan, collected),
            "distinction": judges.distinguish(evaluation["results"])}


# ----------------------------------------------------------------------------------- O-1
def o1_ttl_minutes(lab) -> int:
    res = E.dexec(lab, E.BACKEND, "python", "-c",
                  "from app.core.config import settings; print(settings.auth_session_ttl_minutes)")
    return int(res.out.strip().splitlines()[-1])


def o1_measure(lab, ttl: int, label: str) -> dict:
    client = lab.client()
    t0 = time.time()
    response = client.login(lab.config.email(E.OBSERVER), lab.secrets["users"][E.OBSERVER])
    t1 = time.time()
    if response.status != 200:
        raise HarnessError(f"login do observador = {response.status}")
    sample = driver_lab.o1_sample(response.json(), ttl, lab.clock.expected_at((t0 + t1) / 2))
    sample.update({"label": label, "rtt_s": round(t1 - t0, 3)})
    return sample
