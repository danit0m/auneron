"""
Adaptadores FINOS portas -> lab (`docker exec`/`docker cp`/`docker inspect`/SQL somente leitura). Sem logica de
decisao: tudo o que decide vive em `sim.runner.live`/`daily`/`runner`. NESTE CHECKPOINT so e exercitado com duplos
(comandos conferidos); a primeira execucao real e o SR-1.

Todo acesso a container passa por `sim.live.env.dexec`/`cp_in`/`cp_out`, que impoem o prefixo `auneron-sim-`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path

from sim.live import env as E
from sim.live import flow
from sim.runner import disk as disk_mod
from sim.runner import harness_side
from sim.runner.errors import RunnerError
from sim.runner.live import LOG_SERVICES
from sim.runner.live import Ports
from sim.stack.lab import driver_lab
from sim.stack.lab import sqlro
from sim.stack.lab.config import lab_home

CONTAINERS = {"backend": E.BACKEND, "driver": E.DRIVER, "collector": E.COLLECTOR, "postgres": E.POSTGRES,
              "clock": "auneron-sim-clock"}
STATE = "/state"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lines(result) -> list:
    if result.code != 0:
        raise RunnerError(f"leitura incremental falhou: {result.err[-200:]}")
    return [line for line in result.out.splitlines() if line.strip()]


def inspect_containers(lab) -> dict:
    out = {}
    for short, name in CONTAINERS.items():
        res = lab.runner.run(["docker", "inspect", name])
        if res.code != 0:
            out[short] = {"running": False, "started_at": None, "networks": []}
            continue
        data = json.loads(res.out)[0]
        out[short] = {"running": bool(data["State"]["Running"]), "started_at": data["State"]["StartedAt"],
                      "networks": sorted(data["NetworkSettings"]["Networks"])}
    return out


def dr1_probe(lab, forbidden: list, *, with_controls: bool) -> dict:
    """Sonda de isolamento (mesmo script e juiz do DR-1) nos DOIS containers. `clean` = nenhum achado (e, quando pedido,
    com os controles positivos disparando: sem eles um 'limpo' nao prova nada)."""
    args = json.dumps({"forbidden_hashes": sorted(forbidden), "name_needles": list(driver_lab.PROBE_NAME_NEEDLES)})

    def run(container, extra_args=None, env=None):
        res = E.dexec(lab, container, "python", "-", extra_args or args, stdin=driver_lab.PROBE_SCRIPT, env=env, timeout=300)
        if res.code != 0:
            raise RunnerError(f"sonda falhou em {container}: {res.text[-300:]}")
        return json.loads(res.out.strip().splitlines()[-1])

    findings, controls_ok = {}, True
    for container in (E.DRIVER, E.COLLECTOR):
        findings[container] = driver_lab.judge_probe(run(container))
        if with_controls:
            E.dexec(lab, container, "sh", "-c", "mkdir -p /tmp/leak && : > /tmp/leak/oracle.json")
            planted = run(container, env={"SIM_PLANTED_ORACLE": "1"})
            E.dexec(lab, container, "python", "-c", "open('/tmp/leak/y.bin','wb').write(b'planted-control')")
            control_args = json.dumps({"forbidden_hashes": [hashlib.sha256(b"planted-control").hexdigest()],
                                       "name_needles": ["oracle", "world"]})
            hashed = run(container, extra_args=control_args)
            controls_ok = controls_ok and (any("oracle.json" in m for m in planted["name_matches"])
                                           and "SIM_PLANTED_ORACLE" in planted["env_findings"]
                                           and any(m.endswith("y.bin") for m in hashed["hash_matches"]))
            E.dexec(lab, container, "rm", "-rf", "/tmp/leak")
            findings[container] += driver_lab.judge_probe(run(container))
    clean = all(not v for v in findings.values()) and controls_ok
    return {"clean": clean, "findings": findings, "controls_fired": controls_ok if with_controls else None}


def parse_du(output: str) -> int:
    """`du -sk` => bytes. Saida invalida => erro (nunca zero)."""
    fields = output.split()
    if not fields or not fields[0].isdigit():
        raise RunnerError(f"saida de du invalida: {output[:60]!r}")
    return int(fields[0]) * 1024


def vhdx_path() -> Path:
    override = os.environ.get("AUNERON_DOCKER_VHDX")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RunnerError("LOCALAPPDATA ausente: nao ha como localizar o VHDX do Docker")
    return Path(base) / "Docker" / "wsl" / "disk" / "docker_data.vhdx"


def vhdx_bytes() -> int:
    path = vhdx_path()
    if not path.is_file():
        raise RunnerError(f"VHDX do Docker nao encontrado: {path}")
    return path.stat().st_size


def directory_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in Path(root).rglob("*") if p.is_file())


TOP_RELATIONS_SQL = ("select c.relname, pg_total_relation_size(c.oid) from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                     "where n.nspname = 'public' and c.relkind = 'r' order by 2 desc limit 10")


def lab_ports(lab, run_dir, *, hooks_state: dict, forbidden_hashes: list, oracle: dict, inputs_dir: Path,
              ttl_minutes: int) -> Ports:
    run_dir = Path(run_dir)
    cuts = {name: int(time.time()) for name in LOG_SERVICES}

    def new_driver_lines(already: int) -> list:
        return _lines(E.dexec(lab, E.DRIVER, "sh", "-c", f"tail -n +{already + 1} {STATE}/driver_evidence.jsonl"))

    def new_collector_lines(already: int) -> list:
        return _lines(E.dexec(lab, E.COLLECTOR, "sh", "-c", f"tail -n +{already + 1} {STATE}/collected.jsonl"))

    def driver_status() -> dict:
        return E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "status"))

    def db_counts() -> dict:
        rows = sqlro.query(lab.runner, lab.config,
                           "select (select count(*) from accounts), (select count(*) from approval_requests), "
                           "(select count(*) from work_items)")
        return {"accounts": int(rows[0][0]), "approvals": int(rows[0][1]), "work_items": int(rows[0][2])}

    def run_id_files() -> list:
        out = []
        for container in (E.DRIVER, E.COLLECTOR):
            res = E.dexec(lab, container, "cat", f"{STATE}/RUN_ID")
            out.append(res.out.strip() if res.code == 0 else None)
        return out

    def skew_ms() -> int:
        sample = lab.skew()
        values = [sample.get(k) for k in ("app_minus_expected_s", "db_minus_expected_s", "app_minus_db_s")]
        if any(v is None for v in values):
            raise RunnerError("skew do relogio sem medida completa")
        return int(round(max(abs(v) for v in values) * 1000))

    def o1_ms() -> int:
        sample = flow.o1_measure(lab, ttl_minutes, "nh-day")
        return int(round(max(abs(sample[k]) for k in ("app_minus_db_s", "app_minus_target_s", "db_minus_target_s")) * 1000))

    def log_slice(service: str) -> bytes:
        since, cuts[service] = cuts[service], int(time.time())
        res = lab.runner.run(["docker", "logs", "--since", str(since), CONTAINERS[service]])
        return (res.out + res.err).encode("utf-8")

    def preserve(failure_class: str, target: Path) -> dict:
        out_dir = Path(target) / "preserved"
        results = {}
        for label, container in (("driver_state", E.DRIVER), ("collector_state", E.COLLECTOR)):
            res = E.cp_out(lab, container, STATE, out_dir / label)
            results[label] = res.code == 0
        for short, name in CONTAINERS.items():
            res = lab.runner.run(["docker", "inspect", name])
            (out_dir / "inspect").mkdir(parents=True, exist_ok=True)
            (out_dir / "inspect" / f"{short}.json").write_text(res.out, encoding="utf-8")
            results[f"inspect_{short}"] = res.code == 0
        try:
            rows = sqlro.query(lab.runner, lab.config, "select count(*) from accounts")
            results["accounts_at_abort"] = int(rows[0][0])
        except Exception as error:                                              # noqa: BLE001 - preservar nunca falha o abort
            results["accounts_at_abort_error"] = str(error)[:120]
        results["class"] = failure_class
        return results

    def finalize_extra(target: Path) -> dict:
        target = Path(target)
        for sub in ("harness", "inputs", "product"):
            (target / sub).mkdir(exist_ok=True)
        refs = E.parse_last_json(E.dexec(lab, E.DRIVER, "python", "-m", "sim.driver.cli", "export-refs"))
        E.write_json(target / "harness" / "refs.json", refs)
        facts = flow.provenance_facts(lab)
        E.write_json(target / "harness" / "facts.json", facts)
        E.write_json(target / "harness" / "harness_facts.json", {
            "barriers": hooks_state.get("barriers", []), "floor": hooks_state.get("floor"), "restart": hooks_state.get("restart"),
            "timeline": hooks_state.get("timeline", [])})
        for name in inputs_dir.iterdir():
            if name.is_file():
                (target / "inputs" / name.name).write_bytes(name.read_bytes())
        compose = lab.live_dir / "compose_resolved.json"
        if compose.exists():
            (target / "product" / "compose_resolved.json").write_bytes(compose.read_bytes())
        for snap in ("dev_snapshot_before.json", "dev_snapshot_after.json"):
            if (lab.dir / snap).exists():
                (target / "product" / snap).write_bytes((lab.dir / snap).read_bytes())
        verdict = harness_side.verify_authority_offline(target, oracle)
        return {"violations": verdict["violations"]}

    def growth() -> dict:
        db = int(sqlro.query(lab.runner, lab.config, "select pg_database_size(current_database())")[0][0])
        top = sqlro.query(lab.runner, lab.config, TOP_RELATIONS_SQL)
        state = {}
        for short, container in (("driver", E.DRIVER), ("collector", E.COLLECTOR)):
            res = E.dexec(lab, container, "du", "-sk", STATE)
            if res.code != 0:
                raise RunnerError(f"du falhou em {container}")
            state[short] = parse_du(res.out)
        return {"db_bytes": db, "vhdx_bytes": vhdx_bytes(), "state_bytes": state, "evidence_bytes": directory_bytes(run_dir),
                "top_relations": [[row[0], int(row[1])] for row in top]}

    return Ports(
        new_driver_lines=new_driver_lines, new_collector_lines=new_collector_lines, driver_status=driver_status,
        db_counts=db_counts, containers=lambda: inspect_containers(lab), run_id_files=run_id_files,
        last_barrier=lambda: (hooks_state["barriers"][-1]["barrier"] if hooks_state.get("barriers") else None),
        skew_ms=skew_ms, o1_ms=o1_ms, free_bytes=lambda: disk_mod.measure_free(lab_home()),
        virtual_now=lambda: lab.clock.expected_now().isoformat(timespec="seconds"), real_now=_utc_now,
        log_slice=log_slice,
        probe=lambda day, when: dr1_probe(lab, forbidden_hashes, with_controls=True),
        preserve=preserve, finalize_extra=finalize_extra, growth=growth)
