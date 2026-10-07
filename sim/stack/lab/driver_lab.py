"""
Integracao ADITIVA do SIM-1.5 ao lab (Design Freeze V1 + V1.1). Nao altera
nenhum modulo do SIM-1.4: o compose base continua o mesmo PROVADO; aqui ficam

* a guarda do compose estendido (override com Driver/Collector);
* o manifesto do Driver e a captura do `version_id` (D-1.5-9, fail closed);
* segredos minimos por container;
* a sonda de isolamento do DR-1 (script + juiz puro);
* a medicao O-1 (relogio da aplicacao via login) e o juiz do limiar.

Este modulo NAO le dados de cenario por nome: caminhos e ids chegam por
parametro (o lab continua cego ao conteudo do cenario).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from sim.driver.agenda import AGENDA_SHA_KEY
from sim.driver.agenda import validate_manifest
from sim.stack.lab import provision
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.guard import check_resolved_compose
from sim.stack.lab.guard import require_container

DRIVER_NETWORK = "auneron_sim_driver"
OVERRIDE_FILE = "docker-compose.sim.driver.yml"
NEW_IMAGES = {"driver": "auneron-sim-driver", "collector": "auneron-sim-collector"}
NEW_VOLUMES = frozenset({"auneron_sim_driver_state", "auneron_sim_driver_inputs", "auneron_sim_collector_state",
                         "auneron_sim_collector_inputs"})
EXPECTED_SERVICES = frozenset({"sim-clock", "sim-postgres", "sim-migration", "sim-backend", "sim-driver",
                               "sim-collector"})
ISOLATED = ("sim-driver", "sim-collector")
FORBIDDEN_KEYS = ("network_mode", "extra_hosts", "privileged", "cap_add", "devices", "pid", "ipc", "ports",
                  "volumes_from", "env_file", "dns", "security_opt")
ALLOWED_ENV_PREFIXES = ("SIM_DRIVER_", "SIM_COLLECTOR_", "PYTHON")
OBSERVER_USER = "sim-harness-observer"
OBSERVER_NAME = "[SIM-HARNESS] observer"
OBSERVER_ROLE = "manager"
SKILL_VERSION_RE = re.compile(r"^\s*skill_version_id:\s*(\d+)\s*$", re.MULTILINE)


# --------------------------------------------------------------------------
# guarda do compose estendido (E-1)
# --------------------------------------------------------------------------
def check_resolved_driver_compose(resolved: dict, config) -> list:
    """Valida o `docker compose config --format json` do stack COM o override.
    Reaproveita a guarda do SIM-1.4 sobre a parte original (invariantes intactos)."""
    problems: list = []
    services = resolved.get("services") or {}
    if set(services) != EXPECTED_SERVICES:
        problems.append(f"servicos: {sorted(services)}")
    prefix_c = config["docker"]["required_prefix_container"]
    prefix_v = config["docker"]["required_prefix_volume"]
    internal = config["docker"]["network"]
    images = set(config["docker"]["images"].values()) | set(NEW_IMAGES.values())

    # 1) parte original: guarda do SIM-1.4 aplicada a uma copia SEM o override
    reduced = copy.deepcopy(resolved)
    for name in ISOLATED:
        reduced["services"].pop(name, None)
    if "sim-backend" in reduced["services"]:
        reduced["services"]["sim-backend"]["networks"] = {internal: None}
    for volume in NEW_VOLUMES:
        reduced.get("volumes", {}).pop(volume, None)
    reduced.get("networks", {}).pop(DRIVER_NETWORK, None)
    problems.extend(check_resolved_compose(reduced, config))

    # 2) redes: propriedade, nao endereco
    nets = {name: set((spec.get("networks") or {}).keys()) for name, spec in services.items()}
    for name in ("sim-postgres", "sim-migration"):
        if nets.get(name) != {internal}:
            problems.append(f"{name}: redes {sorted(nets.get(name, []))} (esperado so a interna)")
    if nets.get("sim-backend") != {internal, DRIVER_NETWORK}:
        problems.append(f"sim-backend: redes {sorted(nets.get('sim-backend', []))}")
    for name in ISOLATED:
        if nets.get(name) != {DRIVER_NETWORK}:
            problems.append(f"{name}: redes {sorted(nets.get(name, []))} (esperado so a do Driver)")
    top = resolved.get("networks") or {}
    if set(top) != {internal, DRIVER_NETWORK}:
        problems.append(f"redes top-level: {sorted(top)}")
    driver_net = top.get(DRIVER_NETWORK, {})
    if driver_net.get("internal") is not True or driver_net.get("name") != DRIVER_NETWORK:
        problems.append("rede do Driver precisa ser `internal: true` e chamar auneron_sim_driver")
    if top.get(internal, {}).get("internal") is True:
        problems.append("a rede interna do produto nao deveria ser internal (SIM-1.4 inalterada)")

    # 3) Driver/Collector: capability minima
    for name in ISOLATED:
        spec = services.get(name, {})
        if not str(spec.get("container_name", "")).startswith(prefix_c):
            problems.append(f"{name}: container {spec.get('container_name')}")
        if str(spec.get("image", "")).split(":")[0] not in images:
            problems.append(f"{name}: imagem {spec.get('image')}")
        for key in FORBIDDEN_KEYS:
            if spec.get(key):
                problems.append(f"{name}: `{key}` proibido")
        for volume in spec.get("volumes") or []:
            if volume.get("type") != "volume":
                problems.append(f"{name}: montagem nao nativa ({volume.get('type')})")
            if "docker.sock" in json.dumps(volume):
                problems.append(f"{name}: socket do Docker montado")
            if volume.get("source") not in NEW_VOLUMES:
                problems.append(f"{name}: volume fora do lab {volume.get('source')}")
        for key in (spec.get("environment") or {}):
            if not key.startswith(ALLOWED_ENV_PREFIXES):
                problems.append(f"{name}: variavel de ambiente fora do contrato: {key}")
        text = json.dumps(spec).lower()
        for token in ("oracle", "world", "host.docker.internal", "8100", "5434", "5432"):
            if token in text:
                problems.append(f"{name}: referencia proibida `{token}`")
    for volume, spec in (resolved.get("volumes") or {}).items():
        if not str(spec.get("name", "")).startswith(prefix_v):
            problems.append(f"volume {spec.get('name')}")
    return problems


def assert_driver_compose_safe(resolved: dict, config) -> None:
    problems = check_resolved_driver_compose(resolved, config)
    if problems:
        raise GuardViolation("compose estendido viola a guarda: " + "; ".join(problems))


# --------------------------------------------------------------------------
# manifesto do Driver, version_id e segredos
# --------------------------------------------------------------------------
def parse_version_id(output: str) -> int:
    """`skill_version_id` da saida CREATED do runbook. Ausente => fail closed."""
    found = SKILL_VERSION_RE.findall(output)
    if len(found) != 1:
        raise GuardViolation("skill_version_id ausente/ambiguo na saida do runbook (fail closed, D-1.5-9)")
    return int(found[0])


MARK_PAID_SKILL = {"key": "account.mark_paid", "script": "scripts.register_account_mark_paid_skill"}


def provision_observer(runner, config, secrets: dict, client_factory) -> dict:
    """`sim-harness-observer` (manager, NAO persona) pelas MESMAS regras rerun-safe do SIM-1.4."""
    password = secrets["users"][OBSERVER_USER]
    result = provision.create_user(runner, config, user=OBSERVER_USER, name=OBSERVER_NAME, role=OBSERVER_ROLE,
                                   password=password)
    status, detail = provision.classify_user_creation(
        result.code, result.text,
        lambda: provision.verify_user(client_factory, config, user=OBSERVER_USER, name=OBSERVER_NAME,
                                      role=OBSERVER_ROLE, password=password),
        OBSERVER_ROLE)
    return {"resource": f"user:{OBSERVER_USER}", "role": OBSERVER_ROLE, "class": status, "detail": detail}


def capture_mark_paid_version(runner, config, known_version_id: int | None = None) -> dict:
    """`version_id` da skill `account.mark_paid` (D-1.5-9). Registra via runbook; o id so aparece na saida
    CREATED. `ja registrada` sem id conhecido no manifesto do lab => fail closed (nunca SQL)."""
    require_container(provision.BACKEND, config)
    result = runner.run(["docker", "exec", provision.BACKEND, "python", "-m", MARK_PAID_SKILL["script"]])
    status, detail = provision.classify_skill_registration(
        result.code, result.text, lambda: provision.skill_catalog_ok(runner, config, MARK_PAID_SKILL["key"]))
    if status == provision.CREATED:
        return {"class": status, "version_id": parse_version_id(result.text), "detail": detail}
    if status == provision.EXISTS_EQUIVALENT and known_version_id is not None:
        return {"class": status, "version_id": known_version_id, "detail": "id vindo do manifesto do lab"}
    raise GuardViolation(f"version_id indisponivel (classe {status}): fail closed")


def build_driver_manifest(agenda_path, scenario_id: str, version_id: int, personas, d0: str, days: int) -> dict:
    data = Path(agenda_path).read_bytes()
    manifest = {
        "schema": "sim.driver.manifest.v1", "scenario_id": scenario_id, "d0": d0, "days": days,
        AGENDA_SHA_KEY: hashlib.sha256(data).hexdigest(), "mark_paid_version_id": version_id,
        "personas": sorted(personas),
    }
    return validate_manifest(manifest)


def driver_secrets(secrets: dict, personas) -> dict:
    return {"api_key": secrets["api_key"], "passwords": {p: secrets["users"][p] for p in personas}}


def collector_secrets(secrets: dict) -> dict:
    return {"api_key": secrets["api_key"], "observer_password": secrets["users"][OBSERVER_USER]}


def active_personas(agenda_ops: list) -> list:
    return sorted({op["actor"] for op in agenda_ops if op["actor"] != "harness"})


# --------------------------------------------------------------------------
# DR-1: sonda de isolamento (script executado DENTRO do container)
# --------------------------------------------------------------------------
PROBE_SCRIPT = r'''
import hashlib, json, os, socket, sys, urllib.request

args = json.loads(sys.argv[1])
roots = json.loads(os.environ.get("SIM_PROBE_ROOTS", json.dumps(
    ["/app", "/inputs", "/state", "/tmp", "/home", "/mnt", "/media", "/opt", "/srv", "/root"])))
forbidden_hashes = set(args["forbidden_hashes"])
needles = [n.lower() for n in args["name_needles"]]
out = {"name_matches": [], "hash_matches": [], "files_scanned": 0, "mounts": [], "env_findings": []}

for root in roots:
    for base, dirs, files in os.walk(root):
        for name in files + dirs:
            if any(n in name.lower() for n in needles):
                out["name_matches"].append(os.path.join(base, name))
        for name in files:
            path = os.path.join(base, name)
            try:
                if os.path.getsize(path) > 64 * 1024 * 1024 or not os.path.isfile(path):
                    continue
                digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
            except OSError:
                continue
            out["files_scanned"] += 1
            if digest in forbidden_hashes:
                out["hash_matches"].append(path)

if os.path.exists("/proc/self/mountinfo"):
    for line in open("/proc/self/mountinfo", encoding="utf-8"):
        point = line.split()[4]
        if not point.startswith(("/proc", "/sys", "/dev", "/etc/hostname", "/etc/hosts", "/etc/resolv.conf")) and point != "/":
            out["mounts"].append(point)

for key, value in os.environ.items():
    text = (key + "=" + value).lower()
    if any(n in text for n in needles) or any(h in text for h in forbidden_hashes):
        out["env_findings"].append(key)

if not os.environ.get("SIM_PROBE_SKIP_NET"):
    def fetch(url):
        try:
            with urllib.request.urlopen(url, timeout=10) as reply:
                return reply.status
        except Exception as error:
            return "error:" + type(error).__name__
    def connect(host, port):
        try:
            socket.create_connection((host, port), timeout=3).close()
            return "connected"
        except Exception as error:
            return "failed:" + type(error).__name__
    def resolve(host):
        try:
            socket.getaddrinfo(host, None)
            return "resolves"
        except Exception as error:
            return "failed:" + type(error).__name__
    out["ready"] = fetch("http://sim-backend:8000/ready")
    out["postgres"] = connect("sim-postgres", 5432)
    out["loopback_8100"] = connect("127.0.0.1", 8100)
    out["external"] = connect("1.1.1.1", 443)
    out["host_docker_internal"] = resolve("host.docker.internal")
    out["dev_names"] = {name: resolve(name) for name in (
        "auneron-backend", "auneron-postgres", "auneron-frontend", "postgres", "backend", "frontend")}
print(json.dumps(out, sort_keys=True))
'''
PROBE_NAME_NEEDLES = ("oracle", "world")
EXPECTED_MOUNTS = frozenset({"/state", "/inputs"})


def judge_probe(result: dict, *, network: bool = True) -> list:
    """DR-1: lista de falhas (vazia = PASS). (a) Oracle inacessivel, (b) canal autorizado
    funciona, (c) canais nao autorizados falham."""
    failures: list = []
    if result.get("name_matches"):
        failures.append(f"nomes de oracle/world presentes: {result['name_matches']}")
    if result.get("hash_matches"):
        failures.append(f"copia por conteudo de artefato proibido: {result['hash_matches']}")
    if result.get("env_findings"):
        failures.append(f"ambiente com referencia a oracle/world: {result['env_findings']}")
    unexpected = sorted(set(result.get("mounts", [])) - EXPECTED_MOUNTS)
    if unexpected:
        failures.append(f"montagens fora do contrato: {unexpected}")
    if result.get("files_scanned", 0) <= 0:
        failures.append("varredura vazia (sonda inconclusiva)")
    if network:
        if result.get("ready") != 200:
            failures.append(f"canal autorizado indisponivel: ready={result.get('ready')}")
        for key in ("postgres", "loopback_8100", "external"):
            if not str(result.get(key, "")).startswith("failed"):
                failures.append(f"canal proibido acessivel: {key}={result.get(key)}")
        if not str(result.get("host_docker_internal", "")).startswith("failed"):
            failures.append("host.docker.internal resolve")
        for name, state in (result.get("dev_names") or {}).items():
            if not str(state).startswith("failed"):
                failures.append(f"nome do DEV resolve no Driver: {name}")
    return failures


# --------------------------------------------------------------------------
# O-1: relogio da aplicacao com resolucao sub-segundo (via login)
# --------------------------------------------------------------------------
def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def o1_sample(login_body: dict, ttl_minutes: int, target_epoch: float) -> dict:
    """app_t = expires_at - TTL (relogio da APLICACAO); db_t = authenticated_at (relogio do DB)."""
    app = (_utc(login_body["expires_at"]) - timedelta(minutes=ttl_minutes)).timestamp()
    db = _utc(login_body["authenticated_at"]).timestamp()
    return {"app_minus_db_s": round(app - db, 6), "app_minus_target_s": round(app - target_epoch, 6),
            "db_minus_target_s": round(db - target_epoch, 6)}


def o1_judge(samples: list, max_skew_s: float) -> dict:
    """Acima do limiar => THRESHOLD_REVIEW_REQUIRED (volta ao PO). NUNCA afrouxa sozinho."""
    worst = max((abs(v) for sample in samples for v in sample.values()), default=0.0)
    status = "OK" if samples and worst <= max_skew_s else "THRESHOLD_REVIEW_REQUIRED"
    return {"status": status, "max_abs_s": round(worst, 6), "limit_s": max_skew_s, "samples": len(samples)}
