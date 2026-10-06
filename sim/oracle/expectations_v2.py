"""
Expectativas do Oracle v2 (SIM-1.3 Design Freeze V1, secoes 6, 7, 9, 10).

Reusa o `Builder` v1 (ciclo de vida, Knowledge, status, consultas,
escalonamento, observed_fact, classificacao) -- na v2 nao ha F1, portanto
nenhuma expectativa de aprovacao F1 e emitida pelo Builder base -- e
acrescenta as expectativas dos corredores reais, cada uma com FONTE
mecanica publica declarada em `KIND_SOURCES`:

* governed_request_exists / governed_decision / governed_execution;
* effective_expiry (derivada de expires_at, nunca do status);
* authority_violation_rejected (C-AUTH-2);
* business_effect_verified (so corredores verificaveis: mark_paid e
  mark_overdue humano; nunca F1);
* nba_applied_rules (nomes reais);
* behavior_pattern_present.

`work_outcome_evaluated` NUNCA e emitido (RESERVED/GAP). Nenhuma
expectativa afirma causalidade.
"""

from __future__ import annotations

from sim.generator.timeline import to_hhmm
from sim.generator.timeline import to_minutes
from sim.oracle import rules_v2
from sim.oracle.expectations import Builder

KIND_SOURCES = {
    "account_status": "GET /accounts/{id}",
    "lifecycle_state": "GET /accounts/{id} (receivable_lifecycle)",
    "knowledge_transition": "GET /brain (Knowledge receivable_lifecycle)",
    "classification": "GET /accounts/{id}/classification",
    "behavior_pattern_present": "GET /memory (client_behavior_payment_pattern)",
    "mark_overdue_eligible": "GET /recommendations/mark-overdue/accounts/{id}/episodes/{due}",
    "escalation_eligible": "GET /recommendations/human-escalation/accounts/{id}/episodes/{due}",
    "nba_decision": "GET /nba-policy/accounts/{id}/episodes/{due}",
    "nba_applied_rules": "GET /nba-policy/accounts/{id}/episodes/{due} (applied_rules)",
    "governed_request_exists": "GET /approvals (approval:read)",
    "governed_decision": "GET /approvals/{id}",
    "governed_execution": "HTTP response recorded by the Driver",
    "effective_expiry": "GET /approvals/{id} (expires_at; status may remain pending)",
    "authority_violation_rejected": "HTTP response recorded by the Driver (403/409)",
    "escalation_work_item_exists": "GET /work-items/{id}",
    "vencimento_change_count": "GET /customer-context or /outcomes evidence",
    "account_event_count": "GET /outcomes/accounts/{id}/episodes/{due} (evidence)",
    "business_effect_verified": "GET /outcomes/accounts/{id}/episodes/{due} (effect_verification)",
    "observed_fact_count": "GET /work-items/{id}/escalation-observations",
    "observed_fact_links_event": "GET /work-items/{id}/escalation-observations",
    "provenance_present": "scripts/evidence_provenance_report.py (read-only CLI)",
    "human_assessment_count": "GET /work-items/{id}/escalation-observations",
    "must_not_exist": "GET /work-items + /outcomes",
    "http_status": "HTTP response recorded by the Driver",
    "no_duplicate_effect": "GET /approvals + /work-items + /outcomes (persistent invariants)",
    "not_representable": "(none: world-only fact)",
}


class BuilderV2(Builder):
    def __init__(self, world, inputs) -> None:
        super().__init__(world, inputs)
        self.by_title: dict[str, list] = {}
        for request in world.governed:
            self.by_title.setdefault(request["rec_ref"], []).append(request)

    def title_expectations(self, title) -> None:
        super().title_expectations(title)
        for consult in title.consults:
            day, minute = consult["at"]
            episode = {"rec_ref": title.ref, "vencimento_day": consult["venc"]}
            names = rules_v2.nba_applied_rules(
                consult["mark_overdue_available"], consult["escalate_available"],
                consult["days_overdue"], title.valor,
            )
            self.add("nba_applied_rules", episode, day, list(names), minute=minute)
        for request in self.by_title.get(title.ref, []):
            self.governed_expectations(title, request)

    def _effect_day(self, at) -> int:
        day, minute = at
        snapshot = to_minutes("18:00")
        return day if minute < snapshot - 30 else min(day + 1, self.cal.last_day)

    def governed_expectations(self, title, request) -> None:
        last = self.cal.last_day
        subject = {"rec_ref": title.ref, "ref": request["ref"], "kind": request["kind"]}
        cases = request.get("cases") or None
        created_day, created_minute = request["created"]
        value = {"requester": request["requester"]}
        if request["kind"] == "mark_paid":
            value["expected_status"] = request["expected_status"]
        else:
            value["vencimento_day"] = request["venc"]
        self.add("governed_request_exists", subject, created_day, value, minute=created_minute, cases=cases)
        for violation in request["violations"]:
            self.add("authority_violation_rejected", subject, violation["at"][0],
                     {"attempt": violation["attempt"], "actor": violation["actor"],
                      "effect": "none"}, minute=violation["at"][1], cases=["C-AUTH-2"])
        if request["decision"] is not None:
            self.add("governed_decision", subject, request["decided_at"][0],
                     {"decision": request["decision"], "decider": request["decider"]},
                     minute=request["decided_at"][1], cases=cases)
        if request["state"] == "expired" and request["decision"] is None:
            expires = request["expires"]
            if expires[0] <= last:
                self.add("effective_expiry", subject, expires[0],
                         {"expires_at": {"day": expires[0], "at": to_hhmm(expires[1])},
                          "status_may_remain": "pending",
                          "retry": "none" if request["kind"] == "mark_overdue" else "persona_rerequest"},
                         minute=expires[1], cases=cases)
        for execution in request["executions"]:
            at = execution["at"]
            result = {"result": execution["result"], "executor": execution["actor"]}
            if execution.get("reason"):
                result["reason"] = execution["reason"]
            self.add("governed_execution", subject, at[0], result, minute=at[1], cases=cases)
            if execution["result"] == "succeeded":
                self.add("business_effect_verified", subject, self._effect_day(at), "verified", cases=cases)

    def classification_expectations(self) -> None:
        super().classification_expectations()
        snap = self.snapshot
        by_email: dict = {}
        for customer in self.world.customers:
            by_email.setdefault(customer.email, []).append(customer)
        for email in sorted(by_email):
            members = by_email[email]
            titles = [t for c in members for t in c.titles]
            report_days = sorted({t.reported[0] for t in titles if t.reported is not None})
            previous = None
            for day in report_days + [self.cal.last_day]:
                cycles, _ = self._policy_cycles(titles, (day, snap), members[0])
                present = len(cycles) >= 3
                if present == previous and day != self.cal.last_day:
                    continue
                previous = present
                anchor = min(titles, key=lambda t: t.ref).ref if titles else None
                self.add("behavior_pattern_present", {"email": email, "anchor_rec_ref": anchor},
                         day, present,
                         semantics="known_limit" if len(members) > 1 else "fact",
                         factors={"shared_email"} if len(members) > 1 else None)


def build_expectations_v2(world, inputs) -> tuple[list, dict]:
    builder = BuilderV2(world, inputs)
    items = builder.build()
    return items, builder.classification_labels
