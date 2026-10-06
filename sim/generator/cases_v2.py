"""
Catalogo da v2 (SIM-1.3 Design Freeze V1, secao 8): injecoes so de agenda
e rotulagem. O modulo v1 (`cases.py`) nao e alterado.

Injecoes de agenda (nao mudam o mundo):
* C-ADV-1 execucao duplicada da mesma baixa (mesmo ref);
* C-ADV-3 execucoes concorrentes da mesma baixa (grupo);
* C-ADV-4 N execucoes de baixas diferentes no mesmo instante (grupo);
* C-ADV-2 replay do materialize de escalonamento;
* C-ADV-9 / C-NEG-4 escalonar titulo ja pago.
"""

from __future__ import annotations

from sim.generator.cases import Injector
from sim.oracle import rules


class InjectorV2(Injector):
    """Injetor da v2: tudo do injetor v1 (inalterado) + C-DEAD-1 explicito.

    C-DEAD-1: titulos de pagadores tardios com vencimento ajustado para o
    dia anterior a ausencia NAO planejada do coordenador; o atraso e pedido
    no 1o dia da ausencia (o gerente ainda nao sabe), ninguem decide, a
    aprovacao expira e o episodio fica sem saida (V2-4).
    """

    LATE_PROFILES = ("P2", "P3", "P9")

    def __init__(self, inputs, variant: str, seed: int) -> None:
        super().__init__(inputs, variant, seed)
        coordinator = next(p for p in inputs.company["personas"] if p["user"] == "sim-coordenador-fin")
        unplanned = [a for a in coordinator.get("absences", []) if not a.get("planned", True)]
        self.dead_day = unplanned[0]["from_day"] if unplanned else None
        self.dead_quota = self.minimum + 1
        self.dead_used: list = []

    def on_issue(self, sim, title, rng) -> None:
        if self.forced_next is not None or title.opening or self.dead_day is None:
            super().on_issue(sim, title, rng)
            return
        due = self.dead_day - 1
        issue_day = title.issue[0]
        eligible = (
            len(self.dead_used) < self.dead_quota
            and title.profile_at_issue in self.LATE_PROFILES
            and due - 30 <= issue_day <= due - 5
            and self.cal.is_business(due)
            and all(other.customer is not title.customer for other in self.dead_used)
        )
        if not eligible:
            super().on_issue(sim, title, rng)
            return
        title.venc_log[0] = (title.issue, due)
        title.plan.update({"kind": "late_fixed", "pay_day": due + rng.randint(12, 20)})
        title.injected.append("C-DEAD-1")
        self.dead_used.append(title)


def restart_in_flight_v2(world, inputs) -> set:
    restart = inputs.calendar.restart_backend
    refs = set()
    for request in world.governed:
        expires = request["expires"]
        closed = request["decided_at"] if request["decision"] else None
        executed = request["executions"][-1]["at"] if request["executions"] else None
        end = min(x for x in (expires, closed, executed) if x is not None)
        if request["created"] <= restart < end:
            refs.add(request["rec_ref"])
    for title in world.titles:
        if title.issue > restart:
            continue
        unpaid = title.reported is None or title.reported > restart
        if unpaid and any(w["created"] <= restart for w in title.workitems):
            refs.add(title.ref)
    return refs


def inject_agenda_v2(world, inputs, minimum: int) -> dict:
    cal = inputs.calendar
    lo, hi = cal.stress_week
    count = minimum + 1
    injected: dict[str, list] = {}
    used: set = set()
    titles = {t.ref: t for t in world.titles}
    execute_ops = {}
    for day, minute, counter, op in world.ops:
        if op["op"] == "execute_mark_paid":
            execute_ops.setdefault(op["ref"], []).append((day, minute, counter, op))

    def take(case, ref):
        used.add(ref)
        injected.setdefault(case, []).append(ref)

    succeeded = []
    for request in world.governed:
        if request["kind"] != "mark_paid" or request["state"] != "done":
            continue
        if request.get("cases") or request["violations"]:
            continue
        at = request["executions"][-1]["at"]
        title = titles[request["rec_ref"]]
        if lo <= at[0] <= hi and title.settle_override is None and title.ref not in used:
            succeeded.append((at, request))
    succeeded.sort(key=lambda item: (item[0], item[1]["ref"]))

    # C-ADV-1: execucao duplicada da mesma aprovacao
    for at, request in succeeded[:count]:
        world.op(at, request["planned_executor"], "execute_mark_paid", rec_ref=request["rec_ref"],
                 ref=request["ref"], expected_status=request["expected_status"], mode="duplicate")
        take("C-ADV-1", request["rec_ref"])
    # C-ADV-3: duas execucoes concorrentes da mesma aprovacao
    rest = [item for item in succeeded if item[1]["rec_ref"] not in used]
    for number, (at, request) in enumerate(rest[:count], start=1):
        group = f"G-CONC-{number}"
        original = execute_ops[request["ref"]][-1][3]
        original.update({"group": group, "mode": "concurrent"})
        world.op(at, request["planned_executor"], "execute_mark_paid", rec_ref=request["rec_ref"],
                 ref=request["ref"], expected_status=request["expected_status"],
                 group=group, mode="concurrent")
        take("C-ADV-3", request["rec_ref"])
    # C-ADV-4: grupos de 5 execucoes de baixas diferentes no mesmo instante
    by_instant: dict = {}
    for at, request in succeeded:
        if request["rec_ref"] not in used:
            by_instant.setdefault(at, []).append(request)
    groups = 0
    for at in sorted(by_instant):
        members = sorted(by_instant[at], key=lambda r: r["ref"])[:5]
        if len(members) < 5 or groups >= count:
            continue
        groups += 1
        group = f"G-SAME-{groups}"
        for request in members:
            execute_ops[request["ref"]][-1][3].update({"group": group, "mode": "same_instant"})
            take("C-ADV-4", request["rec_ref"])
    # C-ADV-2: replay do materialize de escalonamento
    materialized = [(d, m, op) for d, m, _, op in world.ops if op["op"] == "materialize_escalation"]
    ordered = sorted(materialized, key=lambda item: (not (lo <= item[0] <= hi), item[0], item[1]))
    replays = 0
    for day, minute, op in ordered:
        if replays >= count or op["rec_ref"] in used or op.get("mode"):
            continue
        world.op((day, minute), op["actor"], "materialize_escalation", rec_ref=op["rec_ref"],
                 ref=op["ref"], vencimento_day=op["vencimento_day"],
                 idempotency_key=op["idempotency_key"], mode="replay")
        take("C-ADV-2", op["rec_ref"])
        replays += 1
    # C-ADV-9 (semana de stress) e C-NEG-4 (fora): escalonar titulo ja pago
    def paid_late_never_escalated(title) -> bool:
        if title.reported is None or title.workitems:
            return False
        if title.venc_at(title.reported) >= title.reported[0]:
            return False
        return cal.next_business_day(title.reported[0]) <= cal.last_day
    pool = sorted((t for t in world.titles if paid_late_never_escalated(t) and t.ref not in used),
                  key=lambda t: (t.reported, t.ref))
    coordinator = "sim-coordenador-fin"
    absences = next(p for p in inputs.company["personas"] if p["user"] == coordinator).get("absences", [])
    for case, inside in (("C-ADV-9", True), ("C-NEG-4", False)):
        chosen = []
        for title in pool:
            if len(chosen) >= count or title.ref in used:
                continue
            if (lo <= title.reported[0] <= hi) != inside:
                continue
            day = cal.next_business_day(title.reported[0])
            if any(a["from_day"] <= day <= a["to_day"] for a in absences):
                continue
            chosen.append((title, day))
        for title, day in chosen:
            at = (day, cal.slot("collections_1"))
            ref = f"ESC-X{len(title.wrong_attempts) + 1}-{title.ref}"
            world.op(at, coordinator, "materialize_escalation", rec_ref=title.ref, ref=ref,
                     vencimento_day=title.venc_at(at), idempotency_key=f"sim:{world.scenario_id}:{ref}:materialize")
            title.wrong_attempts.append(at)
            take(case, title.ref)
    return injected


def tag_cases_v2(world, inputs, agenda_cases: dict, classification_labels: dict) -> dict:
    cal = inputs.calendar
    floor = cal.evidence_floor
    tags: dict[str, set] = {}

    def add(case, ref):
        tags.setdefault(case, set()).add(ref)

    for case, refs in agenda_cases.items():
        for ref in refs:
            add(case, ref)
    by_title: dict[str, list] = {}
    for request in world.governed:
        by_title.setdefault(request["rec_ref"], []).append(request)
    voided = getattr(world, "voided_overrides", set())

    for title in world.titles:
        requests = by_title.get(title.ref, [])
        reported = title.reported
        due_at_report = title.venc_at(reported) if reported else None
        workitem = title.workitem_for(due_at_report, reported) if reported else None
        mo = [r for r in requests if r["kind"] == "mark_overdue"]
        for case in title.injected:
            if case in ("C-AMB-5", "C-AMB-2", "C-AMB-4", "C-AMB-8"):
                continue
            if case in ("C-TZ-1", "C-TZ-2", "C-ADV-7") and title.ref in voided:
                continue
            add(case, title.ref)
        if reported and title.profile_at_issue == "P1" and reported[0] <= due_at_report \
                and not mo and not title.workitems:
            add("C-N-1", title.ref)
        if reported and title.profile_at_issue == "P7" and reported[0] <= due_at_report - 3:
            add("C-N-2", title.ref)
        if reported and due_at_report + 1 <= reported[0] <= due_at_report + 4 and not title.workitems:
            add("C-N-3", title.ref)
        if reported and workitem is not None:
            add("C-N-4", title.ref)
        if reported and len(title.venc_log) > 1 and title.venc_log[1][0][0] < title.venc_log[0][1] \
                and "C-ADV-10" not in title.injected:
            add("C-N-6", title.ref)
        for consult in title.consults:
            decision = rules.nba_decision(consult["mark_overdue_available"], consult["escalate_available"],
                                          consult["days_overdue"], title.valor)
            if "high_exposure_early_escalation" in decision.trigger_rules:
                add("C-N-7", title.ref)
            if "prolonged_overdue_escalation" in decision.trigger_rules:
                add("C-N-8", title.ref)
        broken = 0
        for contact in title.contacts:
            if contact["code"] != "payment_promised":
                continue
            day = contact["at"][0]
            if title.fact is not None and day < title.fact[0] <= day + 5:
                add("C-PRM-1", title.ref)
            else:
                broken += 1
                add("C-PRM-2", title.ref)
        if broken >= 2:
            add("C-PRM-3", title.ref)
        if reported and reported < floor:
            add("C-NEG-2", title.ref)
        done_mo = [r for r in mo if r["state"] == "done"]
        if done_mo and len(mo) == 1:
            executed = done_mo[0]["executions"][-1]["at"][0]
            if reported is None or reported[0] >= executed + 3:
                add("C-NEG-5", title.ref)
        if reported and title.workitems:
            first_wi = title.workitems[0]
            changed_after = any(at > first_wi["created"] for at, _ in title.venc_log[1:])
            if changed_after and title.workitem_for(due_at_report, reported) is None:
                add("C-AMB-1", title.ref)
        if "C-AMB-5" in title.injected and workitem is not None:
            add("C-AMB-5", title.ref)
        if "C-AMB-4" in title.injected and title.contacts and title.fact is None:
            add("C-AMB-4", title.ref)
        if "C-AMB-8" in title.injected and title.contacts \
                and title.contacts[0]["code"] == "payment_refused" and title.fact is not None:
            add("C-AMB-8", title.ref)
        if title.workitems and reported and title.workitems[0]["created"] < floor and reported < floor:
            add("C-ADV-8", title.ref)
        if title.ref in world.restart_in_flight:
            add("C-ADV-6", title.ref)
        for request in requests:
            if request["decision"] == "approved" and request["decided_at"][0] == request["created"][0] \
                    and request["kind"] == "mark_overdue":
                add("C-HUM-1", title.ref)
            if request["kind"] == "mark_overdue" and request["state"] == "expired":
                add("C-DEAD-1", title.ref)
            if "C-RACE-1" in request.get("cases", []) and request["kind"] == "mark_paid":
                if request["state"] == "failed" and reported is not None:
                    add("C-RACE-1", title.ref)
            if "C-AUTH-1" in request.get("cases", []) and request["state"] == "done":
                add("C-AUTH-1", title.ref)
            if "C-AUTH-2" in request.get("cases", []) and request["violations"]:
                add("C-AUTH-2", title.ref)
        # C-HUM-2: pagamento conciliado durante a ausencia do gerente e
        # baixa executada so depois dela (backlog)
        manager = next(p for p in inputs.company["personas"] if p["user"] == "sim-gerente-fin")
        for absence in manager.get("absences", []):
            recon = world.reconciled.get(title.ref)
            if recon and absence["from_day"] <= recon[0] <= absence["to_day"] \
                    and reported is not None and reported[0] > absence["to_day"]:
                add("C-HUM-2", title.ref)

    by_customer: dict = {}
    for title in world.titles:
        if "C-AMB-2" in title.injected:
            by_customer.setdefault(title.customer.ref, []).append(title)
    for cust_ref, pair in by_customer.items():
        if len(pair) == 2 and all(t.workitems for t in pair) \
                and sum(1 for t in pair if t.reported) == 1:
            add("C-AMB-2", "+".join(t.ref for t in pair))

    for customer in world.customers:
        for title in customer.titles:
            check_day = title.venc + 1
            if not 0 <= check_day <= cal.last_day:
                continue
            at = (check_day, cal.slot("snapshot"))
            if title.reported is not None and title.reported <= at:
                continue
            others = [
                o for o in customer.titles
                if o is not title and o.issue <= at and (o.reported is None or o.reported > at)
                and o.venc_at(at) >= check_day
            ]
            if others:
                add("C-N-9", title.ref)
    for cust_ref, labels in classification_labels.items():
        values = [label for _, label, _ in labels]
        if "ATRASO_RECORRENTE" in values:
            add("C-N-5", cust_ref)
        if any(label == "INSUFFICIENT_DATA" and resolved in (1, 2) for _, label, resolved in labels):
            add("C-AMB-7", cust_ref)
    for customer in world.customers:
        if (customer.role or "").startswith("shared_email"):
            add("C-AMB-6", customer.ref)
    for customer in world.customers:
        if customer.profile != "P1" or customer.drift_to is not None:
            continue
        for start in (30, 75, 120):
            window = (start, start + 29)
            in_window = [t for t in customer.titles if window[0] <= t.issue[0] <= window[1]]
            if not in_window:
                continue
            clean = all(
                not any(r["kind"] == "mark_overdue" for r in by_title.get(t.ref, []))
                and not t.workitems
                and t.reported is not None and t.reported[0] <= t.venc_at(t.reported)
                for t in in_window
            )
            if clean:
                add("C-NEG-1", f"{customer.ref}@{window[0]}-{window[1]}")
                break
    return {case: sorted(refs) for case, refs in sorted(tags.items())}
