"""
Carga das entradas normativas da Nova Horizonte (YAML -> estruturas).

O hash de cada entrada e o SHA-256 do JSON canonico do YAML ja parseado
(independe de quebra de linha/SO).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from sim.generator.canonical import sha256_of
from sim.generator.timeline import Calendar
from sim.generator.timeline import calendar_from_config

COMPANY_DIR = Path(__file__).resolve().parents[1] / "company" / "nova_horizonte"
INPUT_FILES = (
    "company.yaml",
    "population.yaml",
    "calendar.yaml",
    "cases.yaml",
    "names_grammar.yaml",
)


def cents(value: str) -> int:
    amount = Decimal(value)
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"valor com mais de 2 casas: {value}")
    return int(amount * 100)


def money(value_cents: int) -> str:
    return f"{value_cents // 100}.{value_cents % 100:02d}"


@dataclass(frozen=True)
class Inputs:
    company: dict
    population: dict
    calendar_raw: dict
    cases: dict
    names: dict
    calendar: Calendar
    hashes: dict

    def variant(self, name: str) -> dict:
        variants = self.population["variants"]
        if name not in variants:
            raise KeyError(f"variante desconhecida: {name}")
        return variants[name]


def load_inputs(directory: Path = COMPANY_DIR) -> Inputs:
    loaded: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for name in INPUT_FILES:
        data = yaml.safe_load((directory / name).read_text(encoding="utf-8"))
        loaded[name] = data
        hashes[name] = sha256_of(data)
    return Inputs(
        company=loaded["company.yaml"],
        population=loaded["population.yaml"],
        calendar_raw=loaded["calendar.yaml"],
        cases=loaded["cases.yaml"],
        names=loaded["names_grammar.yaml"],
        calendar=calendar_from_config(loaded["calendar.yaml"]),
        hashes=hashes,
    )
