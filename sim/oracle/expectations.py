"""
Expectativas do Oracle (Design Freeze v2, secoes 8.2-8.4).

Cada expectativa tem a camada `policy` (o que as regras congeladas do
produto, replicadas em `sim.oracle.rules`, mandam fazer com o que o Auneron
podia saber) e, quando ha sentido, a camada `ideal` (o que um financeiro
competente concluiria). Quando policy != ideal, `factors` explica a
diferenca e `finding` aponta o achado -- isso e PRODUCT_FINDING, nunca erro
do run.

Instantes de avaliacao: snapshot das 18:00 do dia (padrao) ou o instante da
operacao. `semantics`: fact | correlation_only | abstain |
not_representable | known_limit | calibration_pending.
"""

from __future__ import annotations

from sim.generator.timeline import to_hhmm
from sim.generator.timeline import to_minutes
from sim.oracle import rules

FACTOR_FINDINGS = {
    "utc_date": "G-SIM-9",
    "calendar": "F-CALENDAR",
    "settlement_lag": "G-SIM-11",
    "window": "F-NO-WINDOW",
    "shared_email": "G-SIM-12",
}
IDEAL_WINDOW_DAYS = 90


class Builder:
    def __init__(self, world, inputs) -> None:
        self.world = world
        self.cal = inputs.calendar
        self.snapshot = to_minutes(inputs.calendar.intraday["snapshot"])
        self.items: list = []
        self.classification_labels: dict = {}

    def add(self, kind: str, subject: dict, day: int, policy, *, minute: int | None = None,
            ideal=None, semantics: str = "fact", factors=None, cases=None) -> None:
        item = {
            "kind": kind,
            "subject": subject,
            "day": day,
            "policy": policy,
            "semantics": semantics,
        }
        if minute is not None and minute != self.snapshot:
            item["at"] = to_hhmm(minute)
        if ideal is not None:
            item["ideal"] = ideal
        if factors:
            item["factors"] = sorted(factors)
            findings = sorted({FACTOR_FINDINGS[f] for f in factors})
            item["finding"] = findings
        if cases:
            item["case_ids"] = sorted(cases)
        self.items.append(item)

    # ---------------------------------------------------------------------
    def build(self) -> list:
        for title in self.world.titles:
            self.title_expectations(title)
        self.classification_expectations()
        ordered = sorted(
            self.items,
            key=lambda e: (e["day"], to_minutes(e.get("at", to_hhmm(self.snapshot))),
                           _subject_key(e["subject"]), e["kind"]),
        )
        for number, item in enumerate(ordered, start=1):
            item["exp_id"] = f"E-{number:06d}"
        return ordered

    # ---------------------------------------------------------------------
    def multi_attempt(self, title, until) -> bool:
        return any(
            r["created"] <= until and (r["attempt"] > 1 or r["outcome"] in ("expired", "superseded"))
            for r in title.requests
        )

    def title_expectations(self, title) -> None:
        cal = self.cal
        last = cal.last_day
        snap = self.snapshot
        subject = {"rec_ref": title.ref}
        issue_day = title.issue[0]

        # -- ciclo de vida (policy x ideal) e transicoes de Knowledge --------
        previous = None
        transitions: list = []
        end_day = last
        for day in range(issue_day, last + 1):
            at = (day, snap)
            if title.issue > at:
                continue
            due = title.venc_at(at)
            status = title.status_at(at)
            policy = rules.lifecycle_state(status, due, day)
            ideal_paid = title.evidence is not None and title.evidence <= at
            ideal = "paid" if ideal_paid else rules.lifecycle_state("aberto", cal.effective_due(due), day)
            if not transitions or transitions[-1] != policy:
                transitions.append(policy)
            pair = (policy, ideal)
            if pair != previous or day == last:
                factors = set()
                if policy != ideal:
                    if (policy == "paid") != (ideal == "paid"):
                        factors.add("settlement_lag")
                    if cal.effective_due(due) != due:
                        factors.add("calendar")
                self.add("lifecycle_state", subject, day, policy,
                         ideal=ideal if ideal != policy else None, factors=factors)
                previous = pair
            if policy == "paid" and ideal == "paid":
                end_day = day
                break
        self.add("knowledge_transition", subject, min(end_day, last), transitions)

        # -- status financeiro no Auneron --------------------------------------
        for at, status in title.status_log[1:]:
            if at[0] > last:
                continue
            pending = status == "atrasado" and self.multi_attempt(title, at)
            self.add("account_status", subject, at[0], status,
                     semantics="calibration_pending" if pending else "fact")
        final_at = (last, snap)
        final_status = title.status_at(final_at)
        if final_status != "pago" and self.multi_attempt(title, final_at):
            final_semantics = "calibration_pending"
        else:
            final_semantics = "fact"
        self.add("account_status", subject, last, final_status, semantics=final_semantics)

        # -- deteccao e aprovacao (corredor F1) ---------------------------------
        # So a 1a tentativa de cada episodio e expectativa firme. O ciclo de
        # expiracao/retentativa (G-SIM-10) e UM mecanismo ainda nao calibrado:
        # vira uma unica expectativa calibration_pending por episodio, com a
        # previsao da replica embutida (nao uma expectativa por tentativa).
        for request in title.requests:
            if request["attempt"] != 1:
                continue
            episode = [r for r in title.requests if r["venc"] == request["venc"]]
            req_subject = {"rec_ref": title.ref, "vencimento_day": request["venc"]}
            self.add("approval_request_exists", req_subject, request["created"][0], True)
            if request["outcome"] in ("approved", "rejected"):
                self.add("approval_decided", req_subject, request["closed"][0], request["outcome"])
            elif request["outcome"] in ("expired", "superseded"):
                final = episode[-1]["outcome"] or "open_at_end"
                self.add("approval_expired", req_subject, request["closed"][0],
                         {"first_attempt": request["outcome"],
                          "predicted_attempts": len(episode),
                          "predicted_final": final},
                         semantics="calibration_pending")

        # -- consultas do NBA (Decision) -----------------------------------------
        for consult in title.consults:
            day, minute = consult["at"]
            episode = {"rec_ref": title.ref, "vencimento_day": consult["venc"]}
            self.add("escalation_eligible", episode, day,
                     consult["escalate_available"], minute=minute)
            self.add("mark_overdue_eligible", episode, day,
                     consult["mark_overdue_available"], minute=minute,
                     semantics="calibration_pending" if self.multi_attempt(title, consult["at"]) else "fact")
            decision = rules.nba_decision(consult["mark_overdue_available"], consult["escalate_available"],
                                          consult["days_overdue"], title.valor)
            value = {"decision_type": decision.decision_type,
                     "selected_actions": list(decision.selected_actions)}
            if decision.trigger_rules:
                value["trigger_rules"] = list(decision.trigger_rules)
            self.add("nba_decision", episode, day, value, minute=minute,
                     semantics="calibration_pending" if self.multi_attempt(title, consult["at"]) else "fact")

        # -- escalonamento (Action) -----------------------------------------------
        for workitem in title.workitems:
            episode = {"rec_ref": title.ref, "vencimento_day": workitem["venc"]}
            self.add("escalation_work_item_exists", episode, workitem["created"][0], True)
            if title.ref in getattr(self.world, "replayed", set()):
                self.add("no_duplicate_effect", episode, workitem["created"][0],
                         {"escalation_work_items_for_episode": 1}, cases=["C-ADV-2"])
        for attempt_at in title.wrong_attempts:
            due = title.venc_at(attempt_at)
            reason = rules.escalation_reason(title.status_at(attempt_at), due, attempt_at[0],
                                             title.workitem_for(due, attempt_at) is not None)
            episode = {"rec_ref": title.ref, "vencimento_day": due}
            self.add("escalation_eligible", episode, attempt_at[0], reason is None, minute=attempt_at[1])
            self.add("http_status", episode, attempt_at[0], 409, minute=attempt_at[1])
            self.add("must_not_exist", episode, attempt_at[0], ["escalation_work_item"])

        if title.contacts:
            self.add("human_assessment_count", subject, last, len(title.contacts))
        if len(title.venc_log) > 1:
            self.add("vencimento_change_count", subject, last, len(title.venc_log) - 1)
        if title.partial is not None:
            self.add("not_representable", subject, title.partial[0][0], "partial_payment",
                     semantics="not_representable", cases=["C-AMB-3"])
        if title.ref in getattr(self.world, "restart_in_flight", set()):
            restart_day = self.cal.restart_backend[0]
            self.add("no_duplicate_effect", subject, min(restart_day + 1, last),
                     {"live_approval_requests_per_episode_max": 1,
                      "escalation_work_items_per_episode_max": 1}, cases=["C-ADV-6"])
        if title.ref in getattr(self.world, "duplicate_settled", set()):
            self.add("account_event_count", subject, last, {"status_changed_to_pago": 1})
            self.add("no_duplicate_effect", subject, last, {"observed_fact_max": 1})

        # -- Verified Effect: observed_fact ------------------------------------
        self.observed_fact_expectations(title)

    def observed_fact_expectations(self, title) -> None:
        if title.reported is None or not title.workitems:
            return
        cal = self.cal
        reported = title.reported
        due = title.venc_at(reported)
        workitem = title.workitem_for(due, reported)
        subject = {"rec_ref": title.ref, "vencimento_day": due}
        if reported < cal.evidence_floor:
            self.add("observed_fact_count", subject, cal.last_day, 0, semantics="abstain",
                     factors=None, cases=None)
            return
        if workitem is None:
            reason = "episode_changed" if any(w["created"] <= reported for w in title.workitems) \
                else "paid_before_escalation"
            self.add("observed_fact_count", subject, cal.last_day, 0, semantics="abstain")
            self.items[-1]["abstain_reason"] = reason
            return
        check_day = min(cal.last_day, reported[0] + 1)
        self.add("observed_fact_count", subject, check_day, 1, semantics="correlation_only")
        self.add("observed_fact_links_event", subject, check_day,
                 {"linked_event": "status_changed_to_pago"}, semantics="correlation_only")
        self.add("provenance_present", subject, check_day,
                 {"provenance_context": True, "producer_pass_id": True})

    # ---------------------------------------------------------------------
    def classification_expectations(self) -> None:
        cal = self.cal
        snap = self.snapshot
        by_email: dict = {}
        for customer in self.world.customers:
            by_email.setdefault(customer.email, []).append(customer)
        for customer in self.world.customers:
            shared = len(by_email[customer.email]) > 1
            email_titles = [t for c in by_email[customer.email] for t in c.titles]
            report_days = sorted({t.reported[0] for t in email_titles if t.reported is not None})
            previous = None
            labels = []
            for day in report_days + [cal.last_day]:
                at = (day, snap)
                policy_cycles, factors = self._policy_cycles(email_titles, at, customer)
                policy = rules.classify(policy_cycles)
                ideal = rules.classify(self._ideal_cycles(customer.titles, at))
                pair = (policy, ideal)
                if pair == previous and day != cal.last_day:
                    continue
                previous = pair
                labels.append((day, policy, len(policy_cycles)))
                if shared:
                    factors = set(factors) | {"shared_email"}
                semantics = "known_limit" if shared else "fact"
                self.add("classification", {"cust_ref": customer.ref, "email": customer.email}, day,
                         policy, ideal=ideal if ideal != policy else None,
                         factors=factors if ideal != policy or shared else None,
                         semantics=semantics)
            self.classification_labels[customer.ref] = labels

    def _policy_cycles(self, titles, at, customer):
        cal = self.cal
        cycles = []
        factors = set()
        for title in titles:
            if title.issue > at or title.reported is None or title.reported > at:
                continue
            due = title.venc_at(at)
            atraso = cal.utc_day(title.reported) - due
            cycles.append(atraso)
            if title.customer is not customer:
                continue
            local_late = title.reported[0] - due > 0
            if (atraso > 0) != local_late:
                factors.add("utc_date")
            ideal_late = self._ideal_late(title, at)
            if ideal_late is not None and ideal_late != (atraso > 0):
                if cal.effective_due(due) != due:
                    factors.add("calendar")
                if title.fact is not None and title.reported[0] > title.fact[0]:
                    factors.add("settlement_lag")
            if ideal_late is None:
                factors.add("window")
        return cycles, factors

    def _ideal_late(self, title, at):
        if title.fact is None or title.evidence is None or title.evidence > at:
            return None
        if title.fact[0] < at[0] - IDEAL_WINDOW_DAYS:
            return None
        due = title.venc_at(title.fact)
        return title.fact[0] > self.cal.effective_due(due)

    def _ideal_cycles(self, titles, at):
        cycles = []
        for title in titles:
            if title.issue > at:
                continue
            late = self._ideal_late(title, at)
            if late is None:
                continue
            cycles.append(1 if late else 0)
        return cycles


def _subject_key(subject: dict) -> str:
    return "|".join(f"{k}={subject[k]}" for k in sorted(subject))


def build_expectations(world, inputs) -> tuple[list, dict]:
    builder = Builder(world, inputs)
    items = builder.build()
    return items, builder.classification_labels
