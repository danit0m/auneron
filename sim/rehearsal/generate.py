"""
Escrita DETERMINISTICA do bundle da fixture SR-1 (bytes canonicos; mesma seed + mesmo perfil => mesmos bytes).

Arquivos: public_agenda.json, world.json (plano de celulas, equivalente do "mundo"), manifest.json, oracle.json e
fixture_manifest.json (hashes de tudo + hash do perfil). O gerador le SO o perfil (hash fixado) e constantes do pacote.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.rehearsal import oracle as O
from sim.rehearsal import profile as P
from sim.rehearsal import schedule as S

BUNDLE_FILES = ("public_agenda.json", "world.json", "manifest.json", "oracle.json")
MANIFEST_NAME = "fixture_manifest.json"


def _world(plan: S.Plan, profile_sha: str) -> dict:
    rows = [{"n": a.n, "ref": a.ref, "role": a.role, "create_day": a.create_day, "due0": a.due0, "cents": a.cents,
             "esc_day": a.esc_day, "mo_day": a.mo["day"] if a.mo else None, "pay_day": a.pay["day"] if a.pay else None,
             "pay_kind": a.pay["kind"] if a.pay else None, "special": a.special} for a in plan.accounts]
    return {"artifact": "world", "scenario_id": S.SCENARIO_ID, "seed": plan.seed, "scale_permille": plan.scale_permille,
            "generator_version": S.GENERATOR_VERSION, "profile_sha256": profile_sha, "meta": plan.meta, "accounts": rows}


def build_bundle(seed: int = S.SEED, scale_permille: int = 1000, profile_path: Path | None = None) -> dict:
    """-> {nome: bytes}. Falha fechada se o perfil nao bater com o hash fixado."""
    profile = P.load_profile(profile_path)
    profile_sha = hashlib.sha256(P.canonical(profile)).hexdigest()
    plan = S.build_plan(seed, scale_permille)
    agenda = {"artifact": "public_agenda", "scenario_id": S.SCENARIO_ID, "ops": plan.ops}
    oracle = O.oracle_document(plan)
    violations = O.check_ta1_independent(plan.ops, oracle["expectations"])
    if violations:
        raise O.OracleError(f"fixture viola TA-1: {violations[:3]}")
    files = {
        "public_agenda.json": canonical_bytes(agenda),
        "world.json": canonical_bytes(_world(plan, profile_sha)),
        "oracle.json": canonical_bytes(oracle),
    }
    manifest = {"artifact": "manifest", "scenario_id": S.SCENARIO_ID, "seed": seed, "scale_permille": scale_permille,
                "generator_version": S.GENERATOR_VERSION, "d0": S.D0, "days": S.LAST_DAY + 1, "profile_sha256": profile_sha,
                "timeline": {"floor_day": S.FLOOR_DAY, "restart_day": S.RESTART_DAY, "last_day": S.LAST_DAY},
                "artifacts": {name: sha256_bytes(data) for name, data in sorted(files.items())}, "claim": O.CLAIM}
    files["manifest.json"] = canonical_bytes(manifest)
    return files


def fixture_manifest(files: dict, *, seed: int, scale_permille: int) -> dict:
    hashes = {name: sha256_bytes(files[name]) for name in BUNDLE_FILES}
    listing = "".join(f"{hashes[n]} *{n}\n" for n in sorted(hashes))
    return {"artifact": "fixture_manifest", "scenario_id": S.SCENARIO_ID, "seed": seed, "scale_permille": scale_permille,
            "generator_version": S.GENERATOR_VERSION, "files": hashes,
            "bundle_sha256": hashlib.sha256(listing.encode("utf-8")).hexdigest()}


def write_bundle(out_dir, seed: int = S.SEED, scale_permille: int = 1000, profile_path: Path | None = None) -> dict:
    files = build_bundle(seed, scale_permille, profile_path)
    manifest = fixture_manifest(files, seed=seed, scale_permille=scale_permille)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    (out / MANIFEST_NAME).write_bytes(canonical_bytes(manifest))
    return manifest


def verify_bundle(out_dir) -> list:
    """Problemas (lista vazia = integro): arquivos conferem com o `fixture_manifest.json` e com o manifesto interno."""
    import json

    out = Path(out_dir)
    problems = []
    manifest = json.loads((out / MANIFEST_NAME).read_bytes().decode("utf-8"))
    for name in BUNDLE_FILES:
        digest = sha256_bytes((out / name).read_bytes())
        if digest != manifest["files"].get(name):
            problems.append(f"{name}: sha256 difere do fixture_manifest")
    inner = json.loads((out / "manifest.json").read_bytes().decode("utf-8"))
    for name, digest in inner["artifacts"].items():
        if sha256_bytes((out / name).read_bytes()) != digest:
            problems.append(f"{name}: difere do manifest.json interno")
    return problems
