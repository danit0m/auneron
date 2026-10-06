"""
Agenda publica: a UNICA entrada do futuro Driver (Design Freeze v2, 8.1).

Contem apenas o que as personas fazem (e uma operacao de harness, o
restart). Nunca contem perfis, casos, fatos do mundo ou expectativas.
`PublicAgendaReader` e a unica forma suportada de ler a agenda: so abre
`manifest.json` e `public_agenda.json`, verifica o hash e entrega a agenda
DIA A DIA.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.generator.canonical import read_json
from sim.generator.canonical import sha256_bytes
from sim.generator.timeline import to_hhmm

PUBLIC_FILE = "public_agenda.json"
MANIFEST_FILE = "manifest.json"
PUBLIC_OP_KEYS = frozenset({
    "seq", "day", "at", "actor", "op",
    "rec_ref", "cliente", "email", "whatsapp", "valor", "vencimento_day",
    "code", "decision", "group", "mode",
})
PUBLIC_OPS = frozenset({
    "create_receivable", "settle_receivable", "change_due_date", "consult_nba",
    "materialize_escalation", "record_assessment", "decide_mark_overdue",
    "restart_backend",
})


class SeparationViolation(RuntimeError):
    """Violacao da separacao do Oracle: o cenario e INVALID (secao 8.5)."""


def build_public_agenda(world, scenario_id: str) -> dict:
    ordered = sorted(world.ops, key=lambda item: (item[0], item[1], item[2]))
    ops = []
    for seq, (day, minute, _, record) in enumerate(ordered, start=1):
        op = {"seq": seq, "day": day, "at": to_hhmm(minute), **record}
        unknown = set(op) - PUBLIC_OP_KEYS
        if unknown or op["op"] not in PUBLIC_OPS:
            raise SeparationViolation(f"campo/op nao publico na agenda: {sorted(unknown)} {op['op']}")
        ops.append(op)
    return {
        "artifact": "public_agenda",
        "scenario_id": scenario_id,
        "ops": ops,
    }


class PublicAgendaReader:
    """Leitor do Driver: so a agenda publica, entregue dia a dia."""

    def __init__(self, scenario_dir: Path) -> None:
        self.scenario_dir = Path(scenario_dir)
        manifest = read_json(self.scenario_dir / MANIFEST_FILE)
        data = (self.scenario_dir / PUBLIC_FILE).read_bytes()
        expected = manifest["artifacts"][PUBLIC_FILE]
        if sha256_bytes(data) != expected:
            raise SeparationViolation("public_agenda.json nao confere com o manifesto")
        agenda = json.loads(data.decode("utf-8"))
        self._ops = agenda["ops"]
        self.scenario_id = agenda["scenario_id"]
        self.days = int(manifest["days"])

    def open(self, name: str):
        if name not in (PUBLIC_FILE, MANIFEST_FILE):
            raise SeparationViolation(f"o Driver nao pode abrir {name}")
        return (self.scenario_dir / name).open("rb")

    def ops_for_day(self, day: int) -> list:
        return [op for op in self._ops if op["day"] == day]

    def iter_days(self):
        for day in range(self.days):
            yield day, self.ops_for_day(day)
