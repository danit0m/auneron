"""
Guarda EXTERNA do laboratorio (1a linha de defesa; a 2a e o perfil
`production` do proprio produto). Qualquer violacao => ABORT, sem tentar
corrigir o ambiente.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit


class GuardViolation(RuntimeError):
    pass


def require_http_target(base_url: str, config) -> None:
    parsed = urlsplit(base_url)
    host = parsed.netloc
    if parsed.scheme != "http" or host not in config["http"]["allowed_hosts"]:
        raise GuardViolation(f"alvo HTTP fora da allowlist: {host}")
    if parsed.port in config["http"]["forbidden_ports"]:
        raise GuardViolation(f"porta proibida: {parsed.port}")


def require_container(name: str, config) -> None:
    if not name.startswith(config["docker"]["required_prefix_container"]):
        raise GuardViolation(f"container fora do prefixo do lab: {name}")


def require_project(project: str, config) -> None:
    if project != config["docker"]["project"]:
        raise GuardViolation(f"projeto compose diferente do lab: {project}")


def check_resolved_compose(resolved: dict, config) -> list:
    """Valida o `docker compose config --format json` ANTES de qualquer up."""
    problems = []
    if resolved.get("name") != config["docker"]["project"]:
        problems.append(f"name={resolved.get('name')}")
    prefix_c = config["docker"]["required_prefix_container"]
    prefix_v = config["docker"]["required_prefix_volume"]
    images = set(config["docker"]["images"].values())
    for service, spec in (resolved.get("services") or {}).items():
        container = spec.get("container_name", "")
        if not container.startswith(prefix_c):
            problems.append(f"{service}: container {container}")
        image = (spec.get("image") or "").split(":")[0]
        if image not in images:
            problems.append(f"{service}: imagem {image}")
        for port in spec.get("ports") or []:
            host_ip = port.get("host_ip")
            published = str(port.get("published"))
            if host_ip != "127.0.0.1" or published not in ("8100", "5434"):
                problems.append(f"{service}: porta {host_ip}:{published}")
        for volume in spec.get("volumes") or []:
            if volume.get("type") != "volume":
                problems.append(f"{service}: volume nao nativo ({volume.get('type')})")
        env = spec.get("environment") or {}
        if "DATABASE_URL" in env and not env["DATABASE_URL"].endswith("@sim-postgres:5432/auneron_sim"):
            problems.append(f"{service}: DATABASE_URL fora do lab")
        if "POSTGRES_DB" in env and env["POSTGRES_DB"] != config["database"]["name"]:
            problems.append(f"{service}: POSTGRES_DB {env['POSTGRES_DB']}")
        if "APP_ENV" in env and env["APP_ENV"] != "production":
            problems.append(f"{service}: APP_ENV {env['APP_ENV']}")
        if env.get("FAKETIME_TIMESTAMP_FILE") and env.get("FAKETIME_NO_CACHE") != "1":
            problems.append(f"{service}: FAKETIME_NO_CACHE ausente")
        nets = spec.get("networks") or {}
        if spec.get("network_mode") != "none" and set(nets) != {config["docker"]["network"]}:
            problems.append(f"{service}: redes {sorted(nets)}")
    for key, volume in (resolved.get("volumes") or {}).items():
        if not str(volume.get("name", "")).startswith(prefix_v):
            problems.append(f"volume {volume.get('name')}")
    for key, network in (resolved.get("networks") or {}).items():
        if network.get("name") != config["docker"]["network"]:
            problems.append(f"rede {network.get('name')}")
    return problems


def resolved_compose(runner, config, env) -> dict:
    result = runner.run(
        ["docker", "compose", "-p", config.project, "-f", str(config.compose_file), "config", "--format", "json"],
        env=env,
    )
    if result.code != 0:
        raise GuardViolation(f"compose config falhou: {result.err[-300:]}")
    return json.loads(result.out)


def assert_compose_safe(runner, config, env) -> dict:
    resolved = resolved_compose(runner, config, env)
    problems = check_resolved_compose(resolved, config)
    if problems:
        raise GuardViolation("compose do lab viola a guarda: " + "; ".join(problems))
    return resolved
