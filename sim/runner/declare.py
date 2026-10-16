"""
Monta a parte DECLARADA do RIM a partir do repositorio e dos artefatos congelados (somente leitura no host: git e
arquivos). Fatos que so o Docker conhece (digest da imagem base, IDs de imagem) entram como argumentos.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from sim.runner import rim as rim_mod
from sim.runner.errors import RimError

GIB = 2 ** 30
NH_TIMELINE = {"d0": "2026-01-05", "utc_offset_minutes": -180, "floor_day": 14, "restart_day": 90, "last_day": 179}
DEFAULT_DISK_POLICY = {"preflight_min_bytes": 15 * GIB, "warn_bytes": 10 * GIB, "abort_bytes": 6 * GIB,
                       "reserve_bytes": 8 * GIB, "growth_factor_permille": 1500}
DEFAULT_LIMITS = {"o1_limit_ms": 1000, "clock_skew_limit_ms": 3000, "balance_mode": "enforce"}
TREE_PATHS = {"backend": "backend", "frontend": "frontend", "sim_driver": "sim/driver", "sim_collector": "sim/collector",
              "sim_evaluator": "sim/evaluator", "sim_oracle": "sim/oracle", "sim_live": "sim/live",
              "sim_runner": "sim/runner", "sim_stack": "sim/stack"}


def sha_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_out(repo, *args) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RimError(f"git {' '.join(args)} falhou: {res.stderr.strip()[:200]}")
    return res.stdout.strip()


def trees(repo, ref: str = "HEAD") -> dict:
    """Arvores Git de `ref` (o COMMIT, nao a arvore de trabalho): o RIM nomeia o que esta versionado."""
    return {key: git_out(repo, "rev-parse", f"{ref}:{path}") for key, path in TREE_PATHS.items()}


def build_declared(*, repo, scenario_dir=None, oracle_dir=None, scenario_id: str | None = None, variant: str | None = None,
                   seed: int | None = None, oracle_version: str | None = None, generator_version: str | None = None,
                   base_image_digest: str, lab_image_ids: dict, compose_files: dict, quiescence_sha256: str,
                   driver_params_sha256: str, plan_sha256: str, preregistration_sha256: str, authority_invariants_sha256: str,
                   growth_model_sha256: str | None, ref: str = "HEAD", timeline: dict | None = None,
                   disk_policy: dict | None = None, limits: dict | None = None, bundle=None) -> dict:
    """Parte DECLARADA do RIM. Com `bundle` (NH ou fixture) os campos de cenario vem dele; sem `bundle`, dos diretorios do NH."""
    if bundle is None:
        from sim.runner.bundle import Bundle

        scenario_dir, oracle_dir = Path(scenario_dir), Path(oracle_dir)
        suffix = oracle_version.replace(".", "_")
        manifest = json.loads((oracle_dir / f"manifest_{suffix}.json").read_bytes().decode("utf-8"))
        files = {"agenda": scenario_dir / "public_agenda.json", "world": scenario_dir / "world.json",
                 "v2_manifest": scenario_dir / "manifest.json", "v2_oracle": scenario_dir / "oracle.json",
                 "oracle": oracle_dir / "oracle.json", "oracle_manifest": oracle_dir / f"manifest_{suffix}.json"}
        bundle = Bundle(name="nh", kind="RUN", scenario_id=scenario_id, variant=variant, seed=seed, oracle_version=oracle_version,
                        generator_version=generator_version, files=files,
                        derived_from_oracle_sha256=manifest["derived_from"]["oracle.json"], timeline=dict(timeline or NH_TIMELINE))
    limit_values = dict(limits or DEFAULT_LIMITS)
    return {
        "scenario_id": bundle.scenario_id, "variant": bundle.variant, "seed": bundle.seed,
        "oracle_version": bundle.oracle_version, "oracle_sha256": bundle.sha("oracle"),
        "oracle_manifest_sha256": bundle.sha("oracle_manifest"),
        "derived_from_oracle_sha256": bundle.derived_from_oracle_sha256,
        "agenda_sha256": bundle.sha("agenda"), "world_sha256": bundle.sha("world"),
        "v2_manifest_sha256": bundle.sha("v2_manifest"), "v2_oracle_sha256": bundle.sha("v2_oracle"),
        "generator_version": bundle.generator_version,
        "repo_commit": git_out(repo, "rev-parse", ref), "trees": trees(repo, ref),
        "base_image_digest": base_image_digest, "lab_image_ids": dict(lab_image_ids),
        "compose_sha256": {"base": sha_file(compose_files["base"]), "override": sha_file(compose_files["override"])},
        "params": {"quiescence_sha256": quiescence_sha256, "driver_params_sha256": driver_params_sha256, **limit_values},
        "plan_sha256": plan_sha256, "preregistration_sha256": preregistration_sha256,
        "authority_invariants_sha256": authority_invariants_sha256,
        "timeline": dict(timeline or bundle.timeline), "disk_policy": dict(disk_policy or DEFAULT_DISK_POLICY),
        "growth_model_sha256": growth_model_sha256,
        "recovery_policy": rim_mod.RECOVERY_POLICY, "claim_policy_version": rim_mod.CLAIM_POLICY_VERSION,
    }
