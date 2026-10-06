"""Segredos sinteticos por instancia, guardados FORA do repositorio."""

from __future__ import annotations

import json
import os
import secrets as _secrets
from pathlib import Path

from sim.stack.lab.config import lab_home

PLACEHOLDER_MARKERS = (
    "change_me", "change-me", "replace_me", "replace-me", "troque",
    "example", "auneron_dev", "auneron-dev",
)


def _api_key() -> str:
    while True:
        value = _secrets.token_hex(32)
        if len(set(value)) >= 12 and not any(m in value for m in PLACEHOLDER_MARKERS):
            return value


def instance_dir(instance_id: str) -> Path:
    path = lab_home() / instance_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_or_create(instance_id: str, users: list[str]) -> dict:
    path = instance_dir(instance_id) / "secrets.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"db_password": _secrets.token_hex(24), "api_key": _api_key(), "users": {}}
    for user in users:
        data["users"].setdefault(user, _secrets.token_urlsafe(18))
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return data


def all_values(data: dict) -> list[str]:
    return [data["db_password"], data["api_key"], *data["users"].values()]
