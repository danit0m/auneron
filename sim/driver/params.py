"""Parametros CONGELADOS do Driver (Design Freeze V1.1: D-1.5.2-5, A-2, O-1).

Mudar qualquer valor exige nova versao do arquivo e aparece no manifesto do
run; o CODE APPLY nunca os ajusta em silencio (teste estatico de valores).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PARAMS_FILE = Path(__file__).resolve().parent / "params.json"


@dataclass(frozen=True)
class Params:
    raw: dict
    sha256: str

    def __getitem__(self, key):
        return self.raw[key]


def load_params(path: Path = PARAMS_FILE) -> Params:
    data = Path(path).read_bytes()
    return Params(raw=json.loads(data.decode("utf-8")), sha256=hashlib.sha256(data).hexdigest())
