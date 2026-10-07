"""
Compilador do PLANO DE COLETA SEM VALORES (Design Freeze V1.1 E-3.4).

Lado do harness: le o Oracle v2.1 e a agenda PUBLICA e emite, para o Evidence
Collector, so ROTEAMENTO (rota, bindings de refs, momento seguro). Nenhum
valor esperado, nenhuma camada `policy`/`ideal`. O Driver nunca recebe este
plano; o Collector nunca recebe o Oracle.
"""

from __future__ import annotations

from sim.collector.plan_schema import OBSERVER
from sim.collector.plan_schema import PLAN_SCHEMA
from sim.collector.plan_schema import validate_plan

_ORDER = {"pre_slot": 0, "post_slot": 1, "after_daily_barrier": 2, "after_floor_barrier": 3,
          "after_restart_barrier": 4, "end_of_run": 5}


class _Items:
    def __init__(self) -> None:
        self.by_key: dict = {}

    def add(self, route: str, path: str, bind: dict, query: dict, paging: str, safe_at: str, day, at,
            requires, exp_id: str, channel: str = "http") -> None:
        if safe_at == "end_of_run":
            day = None
        key = (route, path, repr(sorted(bind.items(), key=str)), repr(sorted(query.items())), safe_at, day, at,
               tuple(requires), channel)
        item = self.by_key.get(key)
        if item is None:
            item = {"route": route, "path": path, "bind": bind, "query": query, "paging": paging,
                    "safe_at": safe_at, "day": day, "at": at, "requires": list(requires), "principal": OBSERVER,
                    "satisfies": [], "channel": channel}
            self.by_key[key] = item
        item["satisfies"].append(exp_id)


def _indexes(agenda_ops: list) -> tuple:
    anchor: dict = {}
    escalation: dict = {}
    by_rec: dict = {}
    for op in agenda_ops:
        if op["op"] == "create_receivable":
            current = anchor.get(op["email"])
            if current is None or op["rec_ref"] < current:
                anchor[op["email"]] = op["rec_ref"]
        elif op["op"] == "materialize_escalation":
            escalation.setdefault((op["rec_ref"], op["vencimento_day"]), op["ref"])
            refs = by_rec.setdefault(op["rec_ref"], [])
            if op["ref"] not in refs:
                refs.append(op["ref"])
    return anchor, escalation, by_rec


def compile_plan(oracle: dict, agenda_ops: list, d0: str = "2026-01-05", utc_offset_minutes: int = -180,
                 observer_email: str = "sim-harness-observer@nova-horizonte.example.com") -> dict:
    anchor_by_email, esc_by_episode, esc_by_rec = _indexes(agenda_ops)
    items = _Items()
    for exp in oracle["expectations"]:
        kind, safe_at = exp["kind"], exp["safe_at"]
        if safe_at in ("in_slot", "not_collected") or exp["evidence_class"] != "PRODUCT_FACT":
            continue
        subject, day, at = exp["subject"], exp["day"], exp.get("at")
        requires = exp.get("requires", [])
        exp_id = exp["exp_id"]
        add = lambda route, path, bind, query, paging, trigger=safe_at, d=day, a=None, ch="http": items.add(  # noqa: E731
            route, path, bind, query, paging, trigger, d, a, requires, exp_id, ch)
        rec = subject.get("rec_ref")
        account = {"account_id": {"rec_ref": rec}} if rec else {}
        if kind in ("account_status", "lifecycle_state"):
            add("account", "/accounts/{account_id}", account, {}, "none")
        elif kind == "classification":
            anchor = {"account_id": {"rec_ref": anchor_by_email[subject["email"]]}}
            add("classification", "/accounts/{account_id}/classification", anchor, {}, "none")
        elif kind == "behavior_pattern_present":
            anchor = {"account_id": {"rec_ref": subject["anchor_rec_ref"]}}
            add("memories", "/memories", anchor,
                {"scope_type": "account", "account_id": "{account_id}",
                 "memory_key": "client_behavior_payment_pattern", "status": "active", "limit": "100"}, "cursor")
        elif kind == "knowledge_transition":
            add("brain", "/brain/", account, {"account_id": "{account_id}", "limit": "500"}, "skip")
        elif kind in ("mark_overdue_eligible", "escalation_eligible"):
            route, base = (("mark_overdue_eligibility", "/recommendations/mark-overdue")
                           if kind == "mark_overdue_eligible"
                           else ("human_escalation_eligibility", "/recommendations/human-escalation"))
            add(route, base + "/accounts/{account_id}/episodes/{due}",
                {**account, "due": {"day": subject["vencimento_day"]}}, {}, "none", a=at)
        elif kind == "governed_request_exists":
            add("approvals", "/approvals", {}, {"limit": "100"}, "after_id")
        elif kind in ("governed_decision", "effective_expiry"):
            add("approval", "/approvals/{approval_id}", {"approval_id": {"ref": subject["ref"]}}, {}, "none")
        elif kind == "escalation_work_item_exists":
            ref = esc_by_episode.get((rec, subject["vencimento_day"]))
            if ref is not None:
                add("work_item", "/work-items/{work_item_id}", {"work_item_id": {"ref": ref}}, {}, "none")
            else:
                add("work_items", "/work-items", account,
                    {"scope_type": "account", "account_id": "{account_id}", "limit": "100"}, "work_list")
        elif kind in ("human_assessment_count", "observed_fact_count", "observed_fact_links_event"):
            exact = esc_by_episode.get((rec, subject.get("vencimento_day")))
            # sem escalonamento do episodio exato (ex.: `episode_changed`) ou contagem do titulo:
            # le TODOS os escalonamentos do titulo; o Evaluator decide o que conta.
            refs = [exact] if exact is not None else list(esc_by_rec.get(rec, []))
            if not refs:
                raise ValueError(f"{exp_id}: sem escalonamento na agenda para {rec}")
            for ref in refs:
                add("observations", "/work-items/{work_item_id}/escalation-observations",
                    {"work_item_id": {"ref": ref}}, {"limit": "100"}, "after_id")
        elif kind == "business_effect_verified":
            add("outcome", "/outcomes/accounts/{account_id}/episodes/{due}",
                {**account, "due": {"ref": subject["ref"]}}, {}, "none")
        elif kind == "account_event_count":
            add("outcome", "/outcomes/accounts/{account_id}/episodes/{due}",
                {**account, "due": {"current_of": rec}}, {}, "none")
        elif kind == "must_not_exist":
            add("work_items", "/work-items", account,
                {"scope_type": "account", "account_id": "{account_id}", "limit": "100"}, "work_list")
        elif kind == "no_duplicate_effect":
            policy = exp["policy"]
            if "observed_fact_max" in policy:
                refs = list(esc_by_rec.get(rec, []))
                for ref in refs:
                    add("observations", "/work-items/{work_item_id}/escalation-observations",
                        {"work_item_id": {"ref": ref}}, {"limit": "100"}, "after_id")
                if not refs:                      # sem escalonamento na agenda: prova de ausencia via listagem
                    add("work_items", "/work-items", account,
                        {"scope_type": "account", "account_id": "{account_id}", "limit": "100"}, "work_list")
            else:
                add("work_items", "/work-items", account,
                    {"scope_type": "account", "account_id": "{account_id}", "limit": "100"}, "work_list")
                if "live_approval_requests_per_episode_max" in policy:
                    add("approvals", "/approvals", {}, {"limit": "100"}, "after_id")
        elif kind == "provenance_aggregate":
            add("cli", "scripts/evidence_provenance_report.py report", {}, {}, "none", ch="cli")
        else:
            raise ValueError(f"{exp_id}: kind sem rota de coleta no compilador: {kind}")

    def order(item: dict):
        return (_ORDER[item["safe_at"]], item["day"] if item["day"] is not None else 10**6, item["at"] or "",
                item["route"], item["path"], repr(item["bind"]), repr(item["query"]))

    ordered = sorted(items.by_key.values(), key=order)
    for number, item in enumerate(ordered, start=1):
        item["item_id"] = f"I-{number:06d}"
        item["satisfies"] = sorted(set(item["satisfies"]))
    plan = {"schema": PLAN_SCHEMA, "execution_scenario_id": oracle["execution_scenario_id"],
            "clock": {"d0": d0, "utc_offset_minutes": utc_offset_minutes}, "observer_email": observer_email,
            "items": ordered}
    validate_plan(plan)
    return plan
