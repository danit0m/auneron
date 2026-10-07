"""
Runtime do Evidence Collector (Design Freeze V1.1 E-3). Executa itens do
PLANO nos momentos `safe_at` e grava o que o produto respondeu, sem julgar.

Regras normativas:
* um item so e executado pelo seu proprio gatilho (kind + day/at); coleta
  fora do ponto e INVALIDA (`verify_records`);
* item `requires` (barreira de floor/restart) so roda depois da barreira;
* leitura 5xx/transporte: ate 3 tentativas (GET e seguro) e depois
  HarnessError; nunca coleta parcial silenciosa;
* paginacao percorrida ate o fim; lista truncada => HarnessError.
"""

from __future__ import annotations

import json
import time
from datetime import date
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from sim.collector.client import TransportError
from sim.collector.plan_schema import COLLECTION_TRIGGERS
from sim.collector.plan_schema import validate_plan


class HarnessError(RuntimeError):
    """Falha do instrumento de coleta: o run e INVALID."""


def virtual_utc(d0: date, day: int, at: str, offset_minutes: int) -> datetime:
    hours, minutes = (int(part) for part in at.split(":"))
    local = datetime.combine(d0 + timedelta(days=day), dtime(hours, minutes),
                             tzinfo=timezone(timedelta(minutes=offset_minutes)))
    return local.astimezone(timezone.utc)


def iso_day(d0: date, day: int) -> str:
    return (d0 + timedelta(days=day)).isoformat()


def select_items(plan: dict, kind: str, day, at) -> list:
    if kind not in COLLECTION_TRIGGERS:
        raise HarnessError(f"gatilho desconhecido: {kind}")
    chosen = []
    for item in plan["items"]:
        if item["safe_at"] != kind or item.get("channel", "http") != "http":
            continue
        if kind == "end_of_run" or kind in ("after_floor_barrier", "after_restart_barrier"):
            chosen.append(item)
        elif kind == "after_daily_barrier" and item["day"] == day:
            chosen.append(item)
        elif kind in ("pre_slot", "post_slot") and item["day"] == day and item["at"] == at:
            chosen.append(item)
    return chosen


class Collector:
    def __init__(self, plan: dict, client, session, home, chain, *, sleep=time.sleep, clock=time.time,
                 attempts: int = 3, backoff_s: float = 2) -> None:
        validate_plan(plan)
        self.plan = plan
        self.client = client
        self.session = session
        self.home = Path(home)
        self.chain = chain
        self.sleep = sleep
        self.clock = clock
        self.attempts = attempts
        self.backoff = backoff_s
        self.d0 = date.fromisoformat(plan["clock"]["d0"])
        self.offset = int(plan["clock"]["utc_offset_minutes"])
        self._now = virtual_utc(self.d0, 0, "00:00", self.offset)
        self._done = {record["item_id"] for record in chain.read(chain.path)} if chain.path.exists() else set()

    # --------------------------------------------------------------- contexto
    def context(self) -> dict:
        path = self.home / "context.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"completed_barriers": []}

    def mark_barrier(self, name: str) -> None:
        context = self.context()
        if name not in context["completed_barriers"]:
            context["completed_barriers"].append(name)
        (self.home / "context.json").write_text(json.dumps(context), encoding="utf-8")

    def refs(self) -> dict:
        path = self.home / "refs.json"
        if not path.exists():
            raise HarnessError("refs.json ausente: o harness deve exportar as refs do Driver antes da coleta")
        return json.loads(path.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- gatilho
    def trigger(self, kind: str, day: int, at: str) -> dict:
        self._now = virtual_utc(self.d0, day, at, self.offset)
        refs = self.refs()
        completed = set(self.context()["completed_barriers"])
        collected = skipped = 0
        selected = select_items(self.plan, kind, day, at)
        for item in selected:                       # precondicoes de TODOS antes de coletar qualquer um
            missing = [name for name in item["requires"] if name not in completed]
            if missing:
                raise HarnessError(f"{item['item_id']} exige barreira(s) {missing} ainda nao concluida(s)")
        for item in selected:
            if item["item_id"] in self._done:
                skipped += 1
                continue
            self._collect(item, refs, {"kind": kind, "day": day, "at": at})
            collected += 1
        return {"trigger": kind, "day": day, "at": at, "collected": collected, "already_collected": skipped}

    # ---------------------------------------------------------------- coleta
    def _bind(self, item: dict, refs: dict) -> dict:
        values = {}
        for name, spec in item["bind"].items():
            if "day" in spec:
                values[name] = iso_day(self.d0, int(spec["day"]))
            elif "current_of" in spec:
                due = refs.get("due_days", {}).get(spec["current_of"])
                if due is None:
                    raise HarnessError(f"vencimento corrente desconhecido para {spec['current_of']}")
                values[name] = iso_day(self.d0, int(due))
            elif "rec_ref" in spec:
                entry = refs["refs"].get(spec["rec_ref"])
                if not entry or "account_id" not in entry:
                    raise HarnessError(f"{item['item_id']}: rec_ref sem conta: {spec['rec_ref']}")
                values[name] = entry["account_id"]
            else:
                entry = refs["refs"].get(spec["ref"])
                key = {"approval_id": "approval_id", "work_item_id": "work_item_id", "due": "due"}[name]
                if not entry or key not in entry:
                    raise HarnessError(f"{item['item_id']}: ref sem {key}: {spec['ref']}")
                values[name] = entry[key]
        return values

    @staticmethod
    def _fill(text: str, values: dict) -> str:
        for name, value in values.items():
            text = text.replace("{" + name + "}", str(value))
        return text

    def _get(self, path: str):
        last = None
        relogged = False
        for attempt in range(1, self.attempts + 1):
            cookie = self.session.cookie(self._now)
            try:
                response = self.client.get(path, cookie)
            except TransportError as error:
                last = f"transport:{error}"
            else:
                if response.status == 401 and not relogged:
                    relogged = True
                    self.session.invalidate()
                    continue
                if response.status < 500:
                    return response
                last = f"status {response.status}"
            if attempt < self.attempts:
                self.sleep(self.backoff)
        raise HarnessError(f"leitura falhou apos {self.attempts} tentativas: {path}: {last}")

    def _collect(self, item: dict, refs: dict, trigger: dict) -> None:
        values = self._bind(item, refs)
        path = self._fill(item["path"], values)
        query = {key: self._fill(str(value), values) for key, value in item["query"].items()}
        status, body, pages = self._fetch(path, query, item["paging"])
        self.chain.append({
            "kind": "collected", "item_id": item["item_id"], "route": item["route"], "satisfies": item["satisfies"],
            "path": path + (("?" + "&".join(f"{k}={v}" for k, v in query.items())) if query else ""),
            "trigger": trigger, "status": status, "body": body, "pages": pages, "t_real": round(self.clock(), 6)})
        self._done.add(item["item_id"])

    def _fetch(self, path: str, query: dict, paging: str):
        def url(extra: dict) -> str:
            merged = {**query, **extra}
            return path + (("?" + "&".join(f"{k}={v}" for k, v in merged.items())) if merged else "")

        if paging == "none":
            response = self._get(url({}))
            return response.status, response.json(), 1
        items: list = []
        pages = 0
        if paging == "after_id":
            cursor = None
            while True:
                response = self._get(url({"after_id": cursor} if cursor else {}))
                body = response.json()
                pages += 1
                if response.status != 200 or not isinstance(body, dict):
                    return response.status, body, pages
                items.extend(body["items"])
                cursor = body.get("next_cursor")
                if cursor is None:
                    return 200, {"items": items}, pages
        if paging == "skip":
            limit, skip = int(query["limit"]), 0
            while True:
                response = self._get(url({"skip": skip}))
                body = response.json()
                pages += 1
                if response.status != 200 or not isinstance(body, list):
                    return response.status, body, pages
                items.extend(body)
                if len(body) < limit:
                    return 200, {"items": items}, pages
                skip += limit
        if paging == "cursor":
            cursor = None
            while True:
                response = self._get(url({"cursor": cursor} if cursor else {}))
                body = response.json()
                pages += 1
                if response.status != 200 or not isinstance(body, dict):
                    return response.status, body, pages
                items.extend(body["items"])
                page = body.get("page") or {}
                if not page.get("has_more"):
                    return 200, {"items": items}, pages
                cursor = page["next_cursor"]
        if paging == "work_list":
            response = self._get(url({}))
            body = response.json()
            if response.status == 200 and len(body["items"]) >= int(query["limit"]):
                raise HarnessError(f"lista de work-items truncada no limite {query['limit']}: {path}")
            return response.status, body, 1
        raise HarnessError(f"paginacao desconhecida: {paging}")


def verify_records(plan: dict, records: list) -> list:
    """Problemas de integridade da coleta (lista vazia = ok): item fora do
    proprio gatilho, item desconhecido ou duplicado."""
    by_id = {item["item_id"]: item for item in plan["items"]}
    problems, seen = [], set()
    for record in records:
        item = by_id.get(record["item_id"])
        if item is None:
            problems.append(f"item desconhecido {record['item_id']}")
            continue
        if record["item_id"] in seen:
            problems.append(f"item coletado duas vezes {record['item_id']}")
        seen.add(record["item_id"])
        trigger = record["trigger"]
        ok = trigger["kind"] == item["safe_at"]
        if item["safe_at"] == "after_daily_barrier":
            ok = ok and trigger["day"] == item["day"]
        elif item["safe_at"] in ("pre_slot", "post_slot"):
            ok = ok and trigger["day"] == item["day"] and trigger["at"] == item["at"]
        if not ok:
            problems.append(f"{record['item_id']} coletado fora do seu momento seguro ({trigger})")
    return problems


def missing_items(plan: dict, records: list, kinds=None) -> list:
    done = {record["item_id"] for record in records}
    return [item["item_id"] for item in plan["items"]
            if item.get("channel", "http") == "http" and item["item_id"] not in done
            and (kinds is None or item["safe_at"] in kinds)]
