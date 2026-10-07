"""
Ambiente do harness live: instancia do lab, compose COM o override (guarda estendida antes de qualquer
comando), `docker exec`/`docker cp` guardados pelo prefixo `auneron-sim-`, entrega de entradas em volumes
NATIVOS (nunca bind mount) e leitura de evidencia dos containers.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from datetime import timezone
from pathlib import Path

from sim.stack.lab import driver_lab
from sim.stack.lab import gates
from sim.stack.lab import labsecrets
from sim.stack.lab.config import lab_home
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.guard import require_container
from sim.stack.lab.runner import Runner

REPO = Path(__file__).resolve().parents[2]
OVERRIDE = REPO / "sim" / "stack" / driver_lab.OVERRIDE_FILE
OBSERVER = driver_lab.OBSERVER_USER
DRIVER = "auneron-sim-driver"
COLLECTOR = "auneron-sim-collector"
BACKEND = "auneron-sim-backend"
POSTGRES = "auneron-sim-postgres"
# personas que o Driver pode usar nos cenarios tecnicos (sem viewer/admin)
DRIVER_PERSONAS = ("sim-faturamento", "sim-receber", "sim-cobranca-1", "sim-cobranca-2", "sim-cobranca-3",
                   "sim-gerente-fin", "sim-coordenador-fin")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_instance_id(prefix: str = "sim15a") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def open_lab(instance: str, new: bool = False) -> gates.Lab:
    """Lab do SIM-1.4 (config/segredos/clock/evidencia) + observador no mapa de segredos + diretorio `live/`."""
    lab = gates.Lab(instance, new=new)
    users = [p["user"] for p in lab.config["personas"]] + [lab.config["probe"]["user"], OBSERVER]
    lab.secrets = labsecrets.load_or_create(lab.instance_id, users)
    lab.runner = Runner(labsecrets.all_values(lab.secrets))
    lab.clock.runner = lab.runner
    lab.live_dir = lab.dir / "live"
    lab.live_dir.mkdir(exist_ok=True)
    return lab


def sha(path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return path


def find_s1_evidence() -> tuple:
    """(instance_id, gate) do S-1 PASS mais recente guardado em `lab_home` (reuso mecanico, D-1.5.1-2)."""
    best = None
    for evidence in sorted(lab_home().glob("*/evidence.json")):
        gate = json.loads(evidence.read_text(encoding="utf-8")).get("gates", {}).get("S-1")
        if gate and gate.get("status") == "PASS":
            best = (evidence.parent.name, gate)
    if best is None:
        raise GuardViolation("nenhuma evidencia S-1 PASS no lab_home: construa as imagens antes (fase `build` do SIM-1.4)")
    return best


# ------------------------------------------------------------------------------ compose / docker
def compose_base_args(lab) -> list:
    return ["docker", "compose", "-p", lab.config.project, "-f", str(lab.config.compose_file), "-f", str(OVERRIDE)]


def resolved_compose(lab) -> dict:
    cfg = lab.runner.run([*compose_base_args(lab), "config", "--format", "json"], env=lab.env())
    if cfg.code != 0:
        raise GuardViolation(f"compose config falhou: {cfg.err[-400:]}")
    return json.loads(cfg.out)


def compose_ext(lab, *args, timeout: int = 1800, guard: bool = True):
    """docker compose COM o override. A guarda estendida roda ANTES de qualquer comando."""
    if guard:
        driver_lab.assert_driver_compose_safe(resolved_compose(lab), lab.config)
    return lab.runner.run([*compose_base_args(lab), *args], env=lab.env(), timeout=timeout)


def dexec(lab, container: str, *args, stdin: str | None = None, timeout: int = 600, env: dict | None = None,
          user: str | None = None):
    require_container(container, lab.config)
    cmd = ["docker", "exec"]
    if stdin is not None:
        cmd.append("-i")
    if user:
        cmd += ["-u", user]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd += [container, *args]
    return lab.runner.run(cmd, input_text=stdin, timeout=timeout)


def cp_in(lab, container: str, local: Path, remote: str):
    require_container(container, lab.config)
    return lab.runner.run(["docker", "cp", str(local), f"{container}:{remote}"])


def cp_out(lab, container: str, remote: str, local: Path):
    require_container(container, lab.config)
    Path(local).parent.mkdir(parents=True, exist_ok=True)
    return lab.runner.run(["docker", "cp", f"{container}:{remote}", str(local)])


def container_user(container: str) -> str:
    return {DRIVER: "simdrv", COLLECTOR: "simcol"}[container]


def stage_inputs(lab, container: str, files: dict, subdir: str = "") -> None:
    """Copia arquivos para /inputs[/subdir] (volume nativo) e devolve a posse ao usuario nao-root."""
    target = f"/inputs/{subdir}".rstrip("/")
    user = container_user(container)
    if subdir:
        res = dexec(lab, container, "sh", "-c", f"mkdir -p {target} /state/{subdir} && chown -R {user}:{user} {target} /state/{subdir}",
                    user="root")
        if res.code != 0:
            raise RuntimeError(res.err[-300:])
    for name, local in files.items():
        tmp = Path(tempfile.mkdtemp(prefix="sim15-in-")) / name
        shutil.copyfile(local, tmp)
        res = cp_in(lab, container, tmp, f"{target}/{name}")
        shutil.rmtree(tmp.parent, ignore_errors=True)
        if res.code != 0:
            raise RuntimeError(f"docker cp {name} -> {container}: {res.err[-200:]}")
    res = dexec(lab, container, "sh", "-c", f"chown -R {user}:{user} {target} && chmod 600 {target}/* 2>/dev/null; ls -la {target}",
                user="root")
    if res.code != 0:
        raise RuntimeError(res.err[-300:])


def parse_last_json(result):
    lines = result.out.strip().splitlines()
    try:
        return json.loads(lines[-1]) if lines else {}
    except ValueError:
        return {"unparsed": result.text[-400:], "code": result.code}


def cat_json_lines(lab, container: str, path: str) -> list:
    res = dexec(lab, container, "cat", path)
    if res.code != 0:
        raise RuntimeError(f"cat {path}: {res.err[-200:]}")
    return [json.loads(line) for line in res.out.splitlines() if line.strip()]
