"""
Serializacao canonica dos artefatos do SIM (Design Freeze v2, secao 9).

JSON com chaves ordenadas, separadores compactos, UTF-8, sem floats (dinheiro
sempre em string decimal) e uma quebra de linha final. O SHA-256 e calculado
sobre os bytes exatos gravados -- iguais em qualquer SO.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise TypeError(f"float proibido em artefato canonico: {path}")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"chave nao-string em {path}")
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")


def canonical_bytes(value: Any) -> bytes:
    _reject_floats(value)
    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return text.encode("utf-8") + b"\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def write_canonical(path: Path, value: Any) -> str:
    data = canonical_bytes(value)
    path.write_bytes(data)
    return sha256_bytes(data)


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))
