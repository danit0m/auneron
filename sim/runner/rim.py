"""
Run Identity Manifest (RIM) e `run_id` (SIM-1.5E B.1/B.2; D-1.5E-2).

O RIM fixa O QUE sera executado (nao quando, nem onde). E um JSON canonico de ESQUEMA FECHADO (chave desconhecida =
erro): horarios, caminhos do host, `instance_id`, tentativa, `parent` e nome da maquina nao tem onde entrar.
`run_id = RUN-<rim12>-A<nn>`: `rim12` = 12 hex do sha256 do RIM canonico; `nn` e alocado pelo registro, monotonico,
nunca reutilizado (inclusive para runs ABORTED/INVALID).
"""

from __future__ import annotations

import re

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner.errors import RimError

RIM_SCHEMA = "sim.run.rim.v1"
KINDS = ("RUN", "REHEARSAL")
RUN_ID_RE = re.compile(r"^RUN-([0-9a-f]{12})-A([0-9]{2})$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_POLICY = "R0"
CLAIM_POLICY_VERSION = "claim.v1"

_SHA = "sha256"
_STR, _INT, _BOOL = "str", "int", "bool"

# esquema FECHADO. valor: tipo | dict (objeto fechado) ; "?" no nome = pode ser None (opcional declarado)
DECLARED_SCHEMA = {
    "scenario_id": _STR, "variant": _STR, "seed": _INT,
    "oracle_version": _STR, "oracle_sha256": _SHA, "oracle_manifest_sha256": _SHA,
    "derived_from_oracle_sha256": _SHA,
    "agenda_sha256": _SHA, "world_sha256": _SHA, "v2_manifest_sha256": _SHA, "v2_oracle_sha256": _SHA,
    "generator_version": _STR,
    "repo_commit": _STR,
    "trees": {"backend": _STR, "frontend": _STR, "sim_driver": _STR, "sim_collector": _STR, "sim_evaluator": _STR,
              "sim_oracle": _STR, "sim_live": _STR, "sim_runner": _STR, "sim_stack": _STR},
    "base_image_digest": _STR,
    "lab_image_ids": {"base": _STR, "backend": _STR, "postgres": _STR},
    "compose_sha256": {"base": _SHA, "override": _SHA},
    "params": {"quiescence_sha256": _SHA, "driver_params_sha256": _SHA, "o1_limit_ms": _INT, "clock_skew_limit_ms": _INT},
    "plan_sha256": _SHA, "preregistration_sha256": _SHA, "authority_invariants_sha256": _SHA,
    "timeline": {"d0": _STR, "utc_offset_minutes": _INT, "floor_day": _INT, "restart_day": _INT, "last_day": _INT},
    "disk_policy": {"preflight_min_bytes": _INT, "warn_bytes": _INT, "abort_bytes": _INT, "reserve_bytes": _INT,
                    "growth_factor_permille": _INT},
    "growth_model_sha256": "sha256?",           # RUN exige; REHEARSAL mede
    "recovery_policy": _STR, "claim_policy_version": _STR,
}
OBSERVED_SCHEMA = {
    "driver_manifest_sha256": _SHA, "driver_manifest_agenda_sha256": _SHA, "compose_resolved_sha256": _SHA,
    "driver_image": {"tree": _STR, "base_digest": _STR}, "collector_image": {"tree": _STR, "base_digest": _STR},
}
TOP_KEYS = ("schema", "kind", "declared", "observed")


def _check(value, spec, path: str) -> None:
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            raise RimError(f"{path}: objeto esperado")
        extra = sorted(set(value) - set(spec))
        missing = sorted(set(spec) - set(value))
        if extra or missing:
            raise RimError(f"{path}: esquema fechado (chaves a mais {extra}, ausentes {missing})")
        for key, sub in spec.items():
            _check(value[key], sub, f"{path}.{key}")
        return
    optional = spec.endswith("?")
    spec = spec.rstrip("?")
    if value is None:
        if optional:
            return
        raise RimError(f"{path}: nulo nao permitido")
    if spec == _STR and not (isinstance(value, str) and value.strip()):
        raise RimError(f"{path}: string nao vazia esperada")
    if spec == _INT and (not isinstance(value, int) or isinstance(value, bool)):
        raise RimError(f"{path}: inteiro esperado")
    if spec == _BOOL and not isinstance(value, bool):
        raise RimError(f"{path}: booleano esperado")
    if spec == _SHA and not (isinstance(value, str) and HEX64.match(value)):
        raise RimError(f"{path}: sha256 (64 hex minusculos) esperado")


def build_rim(kind: str, declared: dict, observed: dict) -> dict:
    """Valida e devolve o RIM. `RUN` exige modelo de crescimento medido (SR-1); `REHEARSAL` o mede."""
    if kind not in KINDS:
        raise RimError(f"kind invalido: {kind!r}")
    rim = {"schema": RIM_SCHEMA, "kind": kind, "declared": declared, "observed": observed}
    validate_rim(rim)
    return rim


def validate_rim(rim: dict) -> None:
    if not isinstance(rim, dict) or set(rim) != set(TOP_KEYS):
        raise RimError(f"RIM: chaves de topo devem ser {TOP_KEYS}")
    if rim["schema"] != RIM_SCHEMA:
        raise RimError("RIM: schema desconhecido")
    if rim["kind"] not in KINDS:
        raise RimError("RIM: kind invalido")
    _check(rim["declared"], DECLARED_SCHEMA, "declared")
    _check(rim["observed"], OBSERVED_SCHEMA, "observed")
    declared = rim["declared"]
    if declared["recovery_policy"] != RECOVERY_POLICY:
        raise RimError("RIM: o 1o run exige recovery_policy R0 (sem resume)")
    timeline = declared["timeline"]
    if not 0 <= timeline["floor_day"] <= timeline["last_day"] or not 0 <= timeline["restart_day"] <= timeline["last_day"]:
        raise RimError("RIM: floor_day/restart_day fora de 0..last_day")
    if rim["kind"] == "RUN" and declared["growth_model_sha256"] is None:
        raise RimError("RIM: um RUN exige modelo de crescimento medido (growth_model_sha256) -- rode o ensaio antes")
    limits = declared["disk_policy"]
    if not 0 < limits["abort_bytes"] <= limits["warn_bytes"] <= limits["preflight_min_bytes"]:
        raise RimError("RIM: limiares de disco devem satisfazer 0 < abort <= warn <= preflight_min")


def canonical_rim_bytes(rim: dict) -> bytes:
    validate_rim(rim)
    return canonical_bytes(rim)


def rim_sha256(rim: dict) -> str:
    return sha256_bytes(canonical_rim_bytes(rim))


def rim12(rim: dict) -> str:
    return rim_sha256(rim)[:12]


def make_run_id(rim_sha: str, attempt: int) -> str:
    if not HEX64.match(rim_sha):
        raise RimError("sha256 do RIM invalido")
    if not 1 <= attempt <= 99:
        raise RimError("tentativa fora de 1..99")
    return f"RUN-{rim_sha[:12]}-A{attempt:02d}"


def parse_run_id(run_id: str) -> tuple:
    match = RUN_ID_RE.match(run_id or "")
    if not match:
        raise RimError(f"run_id fora do formato RUN-<rim12>-A<nn>: {run_id!r}")
    attempt = int(match.group(2))
    if attempt < 1:
        raise RimError("tentativa 00 nao existe")
    return match.group(1), attempt


def observed_mismatches(rim: dict) -> list:
    """O que foi OBSERVADO na abertura contra o que foi DECLARADO (nada observado pode contradizer o declarado)."""
    validate_rim(rim)
    declared, observed = rim["declared"], rim["observed"]
    problems = []
    if observed["driver_manifest_agenda_sha256"] != declared["agenda_sha256"]:
        problems.append("agenda do driver_manifest != agenda declarada")
    for name in ("driver_image", "collector_image"):
        if observed[name]["base_digest"] != declared["base_image_digest"]:
            problems.append(f"{name}: digest da imagem base != declarado")
    trees = declared["trees"]
    if observed["driver_image"]["tree"] != trees["sim_driver"]:
        problems.append("imagem do Driver construida de arvore != sim_driver declarada")
    if observed["collector_image"]["tree"] != trees["sim_collector"]:
        problems.append("imagem do Collector construida de arvore != sim_collector declarada")
    return problems
