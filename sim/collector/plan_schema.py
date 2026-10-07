"""
Esquema do PLANO DE COLETA SEM VALORES (Design Freeze V1.1 E-3.4). O Collector
so aceita planos que passem por `validate_plan`: nenhum campo de resposta
esperada, nenhuma camada `policy`/`ideal`, nenhum caso de teste.
"""

from __future__ import annotations

from sim.collector.allowlist import COLLECTOR_ROUTES
from sim.collector.allowlist import CapabilityViolation
from sim.collector.allowlist import route_name

PLAN_SCHEMA = "sim.collection_plan.v1"
ITEM_KEYS = frozenset({
    "item_id", "route", "path", "bind", "query", "paging", "safe_at", "day", "at", "requires", "principal",
    "satisfies", "channel",
})
# Nomes que carregam VERDADE ESPERADA: proibidos em qualquer profundidade.
FORBIDDEN_FIELDS = frozenset({
    "policy", "ideal", "semantics", "factors", "case_ids", "finding", "abstain_reason", "expected", "value",
    "expectation", "rows", "severities", "decision", "profile", "fact_at", "evidence_at", "hold_until",
})
COLLECTION_TRIGGERS = frozenset({
    "pre_slot", "post_slot", "after_daily_barrier", "after_floor_barrier", "after_restart_barrier", "end_of_run",
})
# `after_restart_barrier` continua sendo uma BARREIRA (marcada pelo orquestrador e usada em `requires`), mas NAO e um
# momento de coleta: o orquestrador nunca dispara essa coleta, entao um item com esse `safe_at` jamais seria coletado
# (SIM-1.5B, F-D90-2). O plano recusa a configuracao impossivel em vez de deixa-la virar HARNESS_ERROR no run.
UNSUPPORTED_SAFE_AT = frozenset({"after_restart_barrier"})
PAGING = frozenset({"none", "after_id", "skip", "cursor", "work_list"})
ROUTE_NAMES = frozenset(name for name, _, _ in COLLECTOR_ROUTES)
CHANNELS = frozenset({"http", "cli"})
OBSERVER = "sim-harness-observer"


class PlanInvalid(RuntimeError):
    pass


def _walk(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path, key
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def sample_path(item: dict) -> str:
    path = item["path"]
    for name in item.get("bind", {}):
        path = path.replace("{" + name + "}", "2026-01-05" if name == "due" else "1")
    query = "&".join(f"{key}={value}" for key, value in item.get("query", {}).items())
    return path + (f"?{query}" if query else "")


def validate_plan(plan: dict) -> None:
    if plan.get("schema") != PLAN_SCHEMA:
        raise PlanInvalid("schema do plano desconhecido")
    for location, key in _walk(plan):
        if key in FORBIDDEN_FIELDS:
            raise PlanInvalid(f"campo de verdade esperada no plano: {key} em {location}")
    seen = set()
    for item in plan["items"]:
        extra = set(item) - ITEM_KEYS
        if extra:
            raise PlanInvalid(f"chaves nao previstas no item {item.get('item_id')}: {sorted(extra)}")
        if item["item_id"] in seen:
            raise PlanInvalid(f"item duplicado {item['item_id']}")
        seen.add(item["item_id"])
        if item["safe_at"] not in COLLECTION_TRIGGERS or item.get("paging", "none") not in PAGING:
            raise PlanInvalid(f"safe_at/paging invalido em {item['item_id']}")
        if item["safe_at"] in UNSUPPORTED_SAFE_AT:
            raise PlanInvalid(f"safe_at={item['safe_at']} nao e um momento de coleta suportado ({item['item_id']}): "
                              "use `requires` com um safe_at suportado")
        if item.get("channel", "http") not in CHANNELS or item["principal"] != OBSERVER:
            raise PlanInvalid(f"canal/principal invalido em {item['item_id']}")
        if item["safe_at"] in ("pre_slot", "post_slot") and (item.get("at") is None or item.get("day") is None):
            raise PlanInvalid(f"{item['safe_at']} sem (day, at): {item['item_id']}")
        if item["safe_at"] == "after_daily_barrier" and item.get("day") is None:
            raise PlanInvalid(f"after_daily_barrier sem day: {item['item_id']}")
        if item.get("channel", "http") == "http":
            try:
                allowed = item["route"] in ROUTE_NAMES and route_name(sample_path(item)) == item["route"]
            except CapabilityViolation:
                allowed = False
            if not allowed:
                raise PlanInvalid(f"rota/path do item {item['item_id']} fora da allowlist")
