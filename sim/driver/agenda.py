"""
Leitor da agenda publica DO DRIVER (stdlib apenas). Nao usa o manifesto do
cenario: ele lista os hashes dos artefatos do lado do Oracle, que o Driver nao
pode receber (D-1.5-5). A integridade vem de `driver_manifest.json`
(`public_agenda_sha256`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sim.driver.dispatch import PUBLIC_OPS
from sim.driver.errors import HarnessError

PUBLIC_OP_KEYS_V2 = frozenset({
    "seq", "day", "at", "actor", "op",
    "rec_ref", "cliente", "email", "whatsapp", "valor", "vencimento_day",
    "code", "decision", "group", "mode",
    "ref", "expected_status", "idempotency_key",
})
FORBIDDEN_MANIFEST_MARKERS = ("oracle", "world")
AGENDA_SHA_KEY = "public_agenda_sha256"
MANIFEST_KEYS = frozenset({"schema", "scenario_id", "d0", "days", AGENDA_SHA_KEY,
                           "mark_paid_version_id", "personas"})


def validate_manifest(manifest: dict) -> dict:
    extra = set(manifest) - MANIFEST_KEYS
    if extra:
        raise HarnessError(f"driver_manifest com chaves nao previstas: {sorted(extra)}")
    missing = MANIFEST_KEYS - set(manifest)
    if missing:
        raise HarnessError(f"driver_manifest sem chaves: {sorted(missing)}")
    text = json.dumps(manifest).lower()
    if any(marker in text for marker in FORBIDDEN_MANIFEST_MARKERS):
        raise HarnessError("driver_manifest menciona oracle/world: separacao violada")
    version = manifest.get("mark_paid_version_id")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise HarnessError("driver_manifest sem mark_paid_version_id valido (fail closed, D-1.5-9)")
    return manifest


def load_manifest(path) -> dict:
    return validate_manifest(json.loads(Path(path).read_bytes().decode("utf-8")))


def load_agenda(path, manifest: dict) -> list:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest[AGENDA_SHA_KEY]:
        raise HarnessError("public_agenda.json nao confere com o driver_manifest")
    agenda = json.loads(data.decode("utf-8"))
    if agenda.get("scenario_id") != manifest["scenario_id"]:
        raise HarnessError("scenario_id da agenda diverge do driver_manifest")
    ops = agenda["ops"]
    previous = (-1, "")
    for index, op in enumerate(ops):
        if set(op) - PUBLIC_OP_KEYS_V2 or op["op"] not in PUBLIC_OPS:
            raise HarnessError(f"op {index}: campo/op nao publico")
        if op["seq"] != index + 1 or (op["day"], op["at"]) < previous:
            raise HarnessError(f"op {index}: agenda fora de ordem")
        previous = (op["day"], op["at"])
    return ops
