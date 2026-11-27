"""Configuracao por arquivo TOML, sem segredos (so caminhos). Chaves desconhecidas sao recusadas."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .profiles import DEFAULT_ENABLED, PROFILES

LOOPBACK = "127.0.0.1"
_FP = re.compile(r"^[0-9a-f]{64}$")

# tetos: a configuracao pode apertar, nunca afrouxar o plano
CEIL_QUEUE = 2
CEIL_PER_MINUTE = 10
CEIL_TOTAL_TIMEOUT = 120.0
CEIL_CONNECTIONS = 4


def is_allowed_bind_host(host: object) -> bool:
    """So o literal 127.0.0.1. Nada de 0.0.0.0, ::, localhost, nomes ou outros loopbacks."""
    return host == LOOPBACK


class ConfigError(Exception):
    """Configuracao invalida. A mensagem nomeia a chave, nunca o valor."""


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    max_connections: int
    handshake_timeout: float
    header_timeout: float
    body_timeout: float
    idle_timeout: float
    write_timeout: float
    ca_file: Path
    cert_file: Path
    key_file: Path
    min_tls: str
    allowed_fingerprints: frozenset[str]
    ollama_port: int
    queue_size: int
    per_minute: int
    total_timeout: float
    log_dir: Path
    retention_days: int
    enabled_profiles: frozenset[str]
    exposure_unverifiable: str  # "refuse" (padrao) ou "allow"


_SPEC: dict[str, dict[str, type | tuple[type, ...]]] = {
    "server": {
        "host": str,
        "port": int,
        "max_connections": int,
        "handshake_timeout": (int, float),
        "header_timeout": (int, float),
        "body_timeout": (int, float),
        "idle_timeout": (int, float),
        "write_timeout": (int, float),
    },
    "tls": {
        "ca_file": str,
        "cert_file": str,
        "key_file": str,
        "min_version": str,
        "allowed_client_fingerprints": list,
    },
    "ollama": {"port": int},
    "limits": {"queue_size": int, "per_minute": int, "total_timeout": (int, float)},
    "logging": {"dir": str, "retention_days": int},
    "profiles": {"enabled": list},
    "startup": {"exposure_unverifiable": str},
}


def _need(section: dict[str, Any], sname: str, key: str) -> Any:
    if key not in section:
        raise ConfigError(f"falta {sname}.{key}")
    return section[key]


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise ConfigError("arquivo de configuracao ilegivel ou invalido") from None
    for sname in raw:
        if sname not in _SPEC:
            raise ConfigError(f"secao desconhecida: {sname}")
    for sname, keys in _SPEC.items():
        section = raw.get(sname)
        if sname in ("profiles", "startup") and section is None:
            continue
        if not isinstance(section, dict):
            raise ConfigError(f"falta secao {sname}")
        for key, value in section.items():
            if key not in keys:
                raise ConfigError(f"chave desconhecida: {sname}.{key}")
            expected = keys[key]
            if isinstance(value, bool) or not isinstance(value, expected):
                raise ConfigError(f"tipo invalido: {sname}.{key}")
    enabled = raw.get("profiles", {}).get("enabled", list(DEFAULT_ENABLED))
    if not enabled or not all(isinstance(e, str) and e in PROFILES for e in enabled):
        raise ConfigError("profiles.enabled invalido")
    unverifiable = raw.get("startup", {}).get("exposure_unverifiable", "refuse")
    if unverifiable not in ("refuse", "allow"):
        raise ConfigError("startup.exposure_unverifiable deve ser refuse ou allow")
    s, t, o, lim, lg = (raw[k] for k in ("server", "tls", "ollama", "limits", "logging"))
    base = Path(path).resolve().parent

    def p(sec: dict[str, Any], sname: str, key: str) -> Path:
        q = Path(_need(sec, sname, key))
        return q if q.is_absolute() else (base / q)

    host = _need(s, "server", "host")
    if not is_allowed_bind_host(host):
        raise ConfigError("server.host deve ser 127.0.0.1")
    port = _need(s, "server", "port")
    ollama_port = _need(o, "ollama", "port")
    for name, v in (("server.port", port), ("ollama.port", ollama_port)):
        if not (1 <= v <= 65535):
            raise ConfigError(f"{name} fora da faixa")
    if port == ollama_port:
        raise ConfigError("server.port igual a ollama.port")
    maxc = s.get("max_connections", CEIL_CONNECTIONS)
    if not (1 <= maxc <= CEIL_CONNECTIONS):
        raise ConfigError("server.max_connections fora da faixa")
    timeouts = {}
    for key, default in (
        ("handshake_timeout", 5.0),
        ("header_timeout", 10.0),
        ("body_timeout", 20.0),
        ("idle_timeout", 15.0),
        ("write_timeout", 10.0),
    ):
        v = float(s.get(key, default))
        if not (0.05 <= v <= 60):
            raise ConfigError(f"server.{key} fora da faixa")
        timeouts[key] = v
    min_tls = t.get("min_version", "1.3")
    if min_tls not in ("1.2", "1.3"):
        raise ConfigError("tls.min_version deve ser 1.2 ou 1.3")
    fps_raw = _need(t, "tls", "allowed_client_fingerprints")
    fps = frozenset(fps_raw)
    if not fps or not all(isinstance(f, str) and _FP.match(f) for f in fps_raw):
        raise ConfigError("tls.allowed_client_fingerprints invalido (sha256 hex minusculo)")
    queue = lim.get("queue_size", CEIL_QUEUE)
    per_min = lim.get("per_minute", CEIL_PER_MINUTE)
    total = float(lim.get("total_timeout", CEIL_TOTAL_TIMEOUT))
    if not (0 <= queue <= CEIL_QUEUE):
        raise ConfigError("limits.queue_size fora da faixa")
    if not (1 <= per_min <= CEIL_PER_MINUTE):
        raise ConfigError("limits.per_minute fora da faixa")
    if not (0.1 <= total <= CEIL_TOTAL_TIMEOUT):
        raise ConfigError("limits.total_timeout fora da faixa")
    retention = lg.get("retention_days", 30)
    if not (1 <= retention <= 30):
        raise ConfigError("logging.retention_days fora da faixa")
    return Config(
        host=host,
        port=port,
        max_connections=maxc,
        handshake_timeout=timeouts["handshake_timeout"],
        header_timeout=timeouts["header_timeout"],
        body_timeout=timeouts["body_timeout"],
        idle_timeout=timeouts["idle_timeout"],
        write_timeout=timeouts["write_timeout"],
        ca_file=p(t, "tls", "ca_file"),
        cert_file=p(t, "tls", "cert_file"),
        key_file=p(t, "tls", "key_file"),
        min_tls=min_tls,
        allowed_fingerprints=fps,
        ollama_port=ollama_port,
        queue_size=queue,
        per_minute=per_min,
        total_timeout=total,
        log_dir=p(lg, "logging", "dir"),
        retention_days=retention,
        enabled_profiles=frozenset(enabled),
        exposure_unverifiable=unverifiable,
    )
