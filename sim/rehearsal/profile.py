"""
Perfil de carga do NH (SO INTEIROS), congelado por hash. O gerador da fixture depende apenas deste arquivo: nenhum nome,
e-mail, valor, vencimento ou referencia do NH passa por aqui (o esquema recusa qualquer string que nao seja chave).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROFILE_PATH = Path(__file__).resolve().parent / "profiles" / "nh_peak_profile_v1.json"
PROFILE_SHA256 = "a6f68eac158c2ef1471bd7dace1977d4608b74c8ab486cd0df010d92b3194d86"   # fixado na extracao; um teste confere
SCHEMA = 1


class ProfileError(ValueError):
    pass


def _only_ints(value, path: str = "$") -> None:
    if isinstance(value, bool) or isinstance(value, float):
        raise ProfileError(f"{path}: so inteiros sao permitidos no perfil")
    if isinstance(value, int):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _only_ints(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.replace("_", "").isalnum():
                raise ProfileError(f"{path}: chave invalida {key!r}")
            _only_ints(item, f"{path}.{key}")
        return
    raise ProfileError(f"{path}: valor nao inteiro ({type(value).__name__}) -- o perfil nao carrega dados de negocio")


REQUIRED = ("schema", "accounts_total", "ops_total", "ops_by_type", "d0_ops", "d0_burst_slot_ops", "max_ops_day",
            "max_ops_slot_after_d0", "approvals_total", "approvals_mo", "approvals_mp", "escalation_ops",
            "escalation_distinct_refs", "escalation_episodes", "assessments", "consults", "due_changes",
            "observed_fact_expectations", "observed_fact_links", "provenance_derivations", "restart_accounts",
            "sweep_items", "late_slot_minutes", "midnight_slots", "same_instant_group_sizes", "concurrent_group_sizes",
            "replay_ops", "authority_rejections", "wrong_escalation_attempts", "slots_total", "days_with_ops", "days_total")


def validate_profile(profile: dict) -> dict:
    _only_ints(profile)
    missing = [k for k in REQUIRED if k not in profile]
    if missing or profile["schema"] != SCHEMA:
        raise ProfileError(f"perfil incompleto ou de outro schema: {missing}")
    return profile


def canonical(profile: dict) -> bytes:
    return (json.dumps(profile, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_profile(path: Path | None = None, *, expected_sha256: str | None = None) -> dict:
    """Carrega e VERIFICA o perfil (hash fixado). `expected_sha256=None` usa a constante do modulo."""
    data = Path(path or PROFILE_PATH).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    pinned = PROFILE_SHA256 if expected_sha256 is None else expected_sha256
    if digest != pinned:
        raise ProfileError(f"perfil adulterado ou nao fixado: sha256 {digest} != {pinned}")
    return validate_profile(json.loads(data.decode("utf-8")))
