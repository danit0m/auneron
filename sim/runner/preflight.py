"""
Preflight P1..P13 (SIM-1.5D sec. 5) como JULGAMENTO PURO sobre fatos observados. A coleta dos fatos (git, docker,
banco, relogio) e a camada fina de `sim.runner.live`; aqui so se decide. Fato ausente = reprovado (fail-closed).
Preflight NAO muta o negocio e nao avanca o relogio de negocio.
"""

from __future__ import annotations

from sim.runner import disk as disk_mod

BUSINESS_ZERO = "business_rows"
REQUIRED = {
    "P1": ("head", "tree_clean", "index_empty", "static_tests_ok"),
    "P2": ("backend_tree", "frontend_tree"),
    "P3": ("oracle_sha256", "oracle_manifest_sha256", "agenda_sha256", "world_sha256", "v2_manifest_sha256",
           "v2_oracle_sha256", "reproduced_in_memory"),
    "P4": ("residues", "volumes_new", "dev_snapshot_before"),
    "P5": ("base_digest", "driver_tree", "collector_tree", "build_identity_state", "build_git_sha", "build_dirty"),
    "P6": ("compose_guard_ok", "backend_networks", "driver_networks", "collector_networks"),
    "P7": (BUSINESS_ZERO,),
    "P8": ("users_ok", "bindings", "grants", "system_principals", "skills_active", "mark_paid_version_id",
           "version_from_created_output"),
    "P9": ("maintenance_enabled", "app_env", "worker_enabled", "floor_state"),
    "P10": ("virtual_now_ms", "d0_first_slot_ms", "margin_ms"),
    "P11": ("agenda_sha256", "manifest_sha256", "plan_sha256", "dr1_clean"),
    "P12": ("run_id_file_ok", "ledger_opened", "free_bytes"),
    "P13": ("awake_acquired", "foreign_docker_activity", "dev_untouched"),
}
NETWORKS_BACKEND = frozenset({"auneron_sim_driver", "auneron_sim_internal"})
NETWORKS_DRIVER = frozenset({"auneron_sim_driver"})


def missing(facts: dict) -> list:
    out = []
    for check, keys in REQUIRED.items():
        group = facts.get(check)
        if not isinstance(group, dict):
            out.append(check)
            continue
        out += [f"{check}.{key}" for key in keys if key not in group]
    return out


def judge_preflight(facts: dict, rim: dict, *, projected_growth_bytes: int | None = None) -> list:
    """-> lista de {check, detail}. Vazia = PREFLIGHT_PASSED pode ser gravado."""
    absent = missing(facts)
    if absent:
        return [{"check": "FACTS", "detail": f"fatos ausentes: {absent}"}]
    d = rim["declared"]
    o = rim["observed"]
    fails: list = []

    def fail(check: str, detail: str) -> None:
        fails.append({"check": check, "detail": detail})

    p = facts["P1"]
    if p["head"] != d["repo_commit"] or not p["tree_clean"] or not p["index_empty"] or not p["static_tests_ok"]:
        fail("P1", f"HEAD/arvore/indice/testes: {p}")
    p = facts["P2"]
    if p["backend_tree"] != d["trees"]["backend"] or p["frontend_tree"] != d["trees"]["frontend"]:
        fail("P2", "backend/frontend diferem das arvores declaradas")
    p = facts["P3"]
    expected3 = {"oracle_sha256": d["oracle_sha256"], "oracle_manifest_sha256": d["oracle_manifest_sha256"],
                 "agenda_sha256": d["agenda_sha256"], "world_sha256": d["world_sha256"],
                 "v2_manifest_sha256": d["v2_manifest_sha256"], "v2_oracle_sha256": d["v2_oracle_sha256"]}
    diffs = [k for k, v in expected3.items() if p[k] != v]
    if diffs or p["reproduced_in_memory"] is not True:
        fail("P3", f"hashes divergentes {diffs} ou Oracle nao reproduzido em memoria")
    p = facts["P4"]
    if p["residues"] != 0 or p["volumes_new"] is not True or p["dev_snapshot_before"] is not True:
        fail("P4", f"stack novo/residuos/snapshot DEV: {p}")
    p = facts["P5"]
    if (p["base_digest"] != d["base_image_digest"] or p["driver_tree"] != d["trees"]["sim_driver"]
            or p["collector_tree"] != d["trees"]["sim_collector"] or p["build_identity_state"] != "valid"
            or p["build_git_sha"] != d["repo_commit"] or p["build_dirty"] is not False):
        fail("P5", f"imagens/identidade de build: {p}")
    p = facts["P6"]
    if (p["compose_guard_ok"] is not True or set(p["backend_networks"]) != NETWORKS_BACKEND
            or set(p["driver_networks"]) != NETWORKS_DRIVER or set(p["collector_networks"]) != NETWORKS_DRIVER):
        fail("P6", f"compose/topologia: {p}")
    if facts["P7"][BUSINESS_ZERO] != 0:
        fail("P7", f"banco com {facts['P7'][BUSINESS_ZERO]} linhas de negocio antes do D0")
    p = facts["P8"]
    if (p["users_ok"] is not True or p["bindings"] != 0 or p["grants"] != 0 or p["system_principals"] != 0
            or p["skills_active"] is not True or not isinstance(p["mark_paid_version_id"], int)
            or p["version_from_created_output"] is not True):
        fail("P8", f"provisionamento: {p}")
    p = facts["P9"]
    if (p["maintenance_enabled"] is not True or p["app_env"] != "production" or p["worker_enabled"] is not False
            or p["floor_state"] != "unset"):
        fail("P9", f"ambiente do backend: {p}")
    p = facts["P10"]
    if not p["virtual_now_ms"] < p["d0_first_slot_ms"] - p["margin_ms"]:
        fail("P10", "relogio virtual ja dentro da margem do primeiro slot do D0 (armar por ultimo)")
    p = facts["P11"]
    if (p["agenda_sha256"] != d["agenda_sha256"] or p["manifest_sha256"] != o["driver_manifest_sha256"]
            or p["plan_sha256"] != d["plan_sha256"] or p["dr1_clean"] is not True):
        fail("P11", f"entradas dos containers != RIM ou DR-1 sujo: {p}")
    p = facts["P12"]
    if p["run_id_file_ok"] is not True or p["ledger_opened"] is not True:
        fail("P12", "run_id/ledger nao consistentes")
    verdict = disk_mod.judge_preflight(p["free_bytes"], d["disk_policy"], projected_growth_bytes, rim["kind"])
    if not verdict.ok:
        fail("P12", "disco: " + "; ".join(verdict.reasons))
    p = facts["P13"]
    if p["awake_acquired"] is not True or p["foreign_docker_activity"] != 0 or p["dev_untouched"] is not True:
        fail("P13", f"maquina/docker/DEV: {p}")
    return fails
