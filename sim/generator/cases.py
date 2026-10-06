"""
Catalogo de casos (Design Freeze v2, secao 7): injecao deterministica e
rotulagem.

* `Injector` -- decide, NA EMISSAO, o roteiro forcado de titulos escolhidos
  (cota por caso + espacamento no tempo + clientes distintos); monta a
  carteira de abertura (D0); aplica correcoes de vencimento no mesmo dia.
* `inject_agenda` -- depois da simulacao, acrescenta operacoes de persona
  que NAO mudam o mundo (duplicidade, concorrencia, replay, tentativa
  indevida).
* `tag_cases` -- rotula instancias naturais e forcadas por `case_id`.
"""

from __future__ import annotations

from sim.generator.rng import substream
from sim.generator.timeline import to_minutes
from sim.oracle import rules

# Casos injetados na emissao e margem de seguranca sobre o minimo (alguns
# dependem de escalonamento/contato para se concretizarem).
ISSUE_CASES = {
    "C-TZ-1": 1,
    "C-TZ-2": 1,
    "C-ADV-7": 2,  # cada instancia = 2 titulos (23:59 e 00:00)
    "C-ADV-5": 1,
    "C-ADV-10": 1,
    "C-AMB-9": 1,
    "C-AMB-5": 4,
    "C-AMB-3": 1,
    "C-AMB-4": 3,
    "C-AMB-8": 3,
    "C-AMB-2": 3,
}
FIRST_INJECTION_DAY = 15  # depois do floor sintetico (D14)
LAST_DUE_DAY = 160


class Injector:
    def __init__(self, inputs, variant: str, seed: int) -> None:
        self.inputs = inputs
        self.cal = inputs.calendar
        self.seed = seed
        self.minimum = int(inputs.variant(variant)["min_case_instances"])
        self.quota = {}
        for case, margin in ISSUE_CASES.items():
            per_instance = 2 if case == "C-ADV-7" else 1
            self.quota[case] = (self.minimum + margin) * per_instance
        self.used: dict[str, list] = {case: [] for case in ISSUE_CASES}
        self.forced_next: dict | None = None
        self.corrections: list = []  # (day, minute, title, correct_due)
        self.pair_second: set = set()  # clientes com o 2o titulo do C-AMB-2 pendente

    # -- cota e espacamento ---------------------------------------------------
    def _allowed(self, case: str, title) -> bool:
        used = self.used[case]
        if len(used) >= self.quota[case]:
            return False
        if any(other.customer is title.customer for other in used) and case != "C-ADV-7":
            return False
        spacing = max(6, 130 // max(self.quota[case], 1))
        return title.issue[0] >= FIRST_INJECTION_DAY + spacing * len(used)

    def _take(self, case: str, title) -> None:
        self.used[case].append(title)
        title.injected.append(case)

    # -- hooks da simulacao ---------------------------------------------------
    def on_issue(self, sim, title, rng) -> None:
        if self.forced_next is not None:
            forced, self.forced_next = self.forced_next, None
            title.plan.update(forced.get("plan", {}))
            title.injected.extend(forced.get("cases", []))
            return
        if title.opening:
            return
        customer = title.customer
        profile = title.profile_at_issue
        due = title.venc
        issue_day, issue_minute = title.issue
        regular_due = FIRST_INJECTION_DAY <= issue_day and due <= LAST_DUE_DAY

        if customer.index in self.pair_second:
            self.pair_second.discard(customer.index)
            title.plan.update({"kind": "never"})
            self._take("C-AMB-2", title)
            return

        ambiguous_pool = profile == "P10" and not (customer.role or "").startswith("shared")
        fallback_pool = profile == "P2"
        if regular_due and (ambiguous_pool or fallback_pool):
            for case in ("C-AMB-4", "C-AMB-8", "C-AMB-3", "C-AMB-2"):
                if not self._allowed(case, title):
                    continue
                if case == "C-AMB-2" and any(
                    t.plan.get("never_pay") or t.plan.get("kind") == "never" for t in customer.titles
                ):
                    continue  # o bloqueio D+30 impediria o 2o titulo do par
                if case == "C-AMB-4":
                    title.plan.update({"kind": "after_contact", "never_pay": True,
                                       "forced_codes": ["payment_promised"] * 4})
                elif case == "C-AMB-8":
                    title.plan.update({"kind": "after_contact", "pay_after_refusal": True,
                                       "forced_codes": ["payment_refused"]})
                elif case == "C-AMB-3":
                    title.plan.update({"kind": "partial", "partial": True})
                elif case == "C-AMB-2":
                    title.plan.update({"kind": "late_fixed", "pay_day": due + 16})
                    self.pair_second.add(customer.index)
                self._take(case, title)
                return

        pix_p1 = (
            profile == "P1" and title.method == "pix" and regular_due
            and self.cal.is_business(due) and due > issue_day
        )
        if pix_p1:
            for case, at in (("C-TZ-1", "22:30"), ("C-TZ-2", "20:59")):
                if self._allowed(case, title):
                    fact_minute = rng.randint(to_minutes("09:00"), to_minutes("12:00"))
                    title.plan.update({"kind": "override", "fact_at": [due, fact_minute]})
                    title.settle_override = (due, to_minutes(at))
                    self._take(case, title)
                    return
            if self.cal.is_business(due + 1) and self._allowed("C-ADV-7", title):
                fact_minute = rng.randint(to_minutes("09:00"), to_minutes("12:00"))
                title.plan.update({"kind": "override", "fact_at": [due, fact_minute]})
                first_of_pair = len(self.used["C-ADV-7"]) % 2 == 0
                title.settle_override = (due, to_minutes("23:59")) if first_of_pair else (due + 1, 0)
                self._take("C-ADV-7", title)
                return

        if profile in ("P1", "P2") and regular_due and profile != "P5":
            if self._allowed("C-ADV-5", title):
                title.plan.update({"kind": "late_fixed", "pay_day": due + 3})
                title.hold_until = (self.cal.business_on_or_after(due + 6),
                                    self.cal.slot("settlement_bank_return"))
                self._take("C-ADV-5", title)
                return
            if self._allowed("C-AMB-5", title):
                title.plan.update({"kind": "late_fixed", "pay_day": due + rng.randint(7, 10)})
                self._take("C-AMB-5", title)
                return

        if (
            profile not in ("P5", "P10")
            and regular_due
            and issue_minute == self.cal.slot("billing_1")
            and self._allowed("C-ADV-10", title)
        ):
            wrong = due + 10
            title.venc_log[0] = (title.issue, wrong)
            title.plan.update({"changes": [(issue_day, self.cal.slot("billing_2"), due)]})
            self.corrections.append((issue_day, title, due))
            self._take("C-ADV-10", title)
            return

        if profile in ("P1", "P7") and regular_due and self._allowed("C-AMB-9", title):
            title.plan["missing_settlement"] = True
            self._take("C-AMB-9", title)

    def after_plan(self, sim, title, rng) -> None:
        plan = title.plan
        if plan.get("kind") == "override" and title.fact is None:
            day, minute = plan["fact_at"]
            sim.set_fact(title, (int(day), int(minute)))
        if plan.get("missing_settlement") and title.evidence is not None:
            delay = rng.randint(5, 10)
            hold_day = self.cal.add_business_days(title.evidence[0], delay)
            title.hold_until = (hold_day, self.cal.slot("settlement_bank_return"))
            sim.world.event(
                (title.evidence[0], to_minutes("18:00")), "reconciliation_result",
                "erp-sim", {"rec_ref": title.ref, "cust_ref": title.customer.ref},
                result="missing_settlement",
            )

    def same_day_corrections(self, sim, day: int) -> None:
        for correction_day, title, correct_due in self.corrections:
            if correction_day == day and title.venc != correct_due:
                sim._change_due(title, (day, self.cal.slot("billing_2")), correct_due,
                                actor="sim-faturamento")

    # -- carteira de abertura (D0) --------------------------------------------
    def opening(self, sim) -> None:
        spec = sim.spec["opening"]
        to_due = int(spec["to_due"])
        overdue = int(spec["overdue"])
        at = (0, self.cal.slot("billing_1"))
        customers = list(sim.world.customers)
        rng = substream(self.seed, "opening")

        late_pool = [c for c in customers if c.profile in ("P2", "P3", "P4", "P6", "P9")]
        rng.shuffle(late_pool)
        resellers = [c for c in customers if c.segment == "revenda_atacarejo"]
        rng.shuffle(resellers)
        forced_high = self.minimum + 1
        forced_long = self.minimum + 1

        overdue_specs = []
        for i in range(forced_high):
            customer = resellers[i % len(resellers)]
            valor = rng.randint(max(customer.ticket_lo, rules.ABSOLUTE_HIGH_VALUE_REFERENCE_CENTS),
                                customer.ticket_hi)
            overdue_specs.append((customer, -rng.randint(5, 20), valor,
                                  {"plan": {"kind": "after_contact", "keep_pct": 100,
                                            "pay_after": [3, 8]}, "cases": ["C-N-7"]}))
        for i in range(forced_long):
            customer = late_pool[i % len(late_pool)]
            overdue_specs.append((customer, -rng.randint(46, 60), None,
                                  {"plan": {"kind": "late_fixed", "pay_day": rng.randint(20, 40)},
                                   "cases": ["C-N-8"]}))
        index = forced_long
        while len(overdue_specs) < overdue:
            customer = late_pool[index % len(late_pool)]
            index += 1
            overdue_specs.append((customer, -rng.randint(1, 60), None, None))

        punctual = [c for c in customers if c.profile == "P1"]
        rng.shuffle(punctual)
        forced_holiday = self.minimum + 1
        holiday_due = min(d for d in self.cal.non_business_days if d > 0)
        due_specs = []
        for i in range(forced_holiday):
            due_specs.append((punctual[i % len(punctual)], holiday_due, None,
                              {"plan": {"kind": "offset", "offset": 0}, "cases": ["C-NEG-3"]}))
        ordered = list(customers)
        rng.shuffle(ordered)
        index = 0
        while len(due_specs) < to_due:
            customer = ordered[index % len(ordered)]
            index += 1
            due_specs.append((customer, rng.randint(1, 42), None, None))

        for customer, due, valor, forced in overdue_specs + due_specs:
            self.forced_next = forced
            sim.issue_title(customer, at, opening=True, venc=due, valor=valor)
            self.forced_next = None


# ---------------------------------------------------------------------------
# injecoes so de agenda (pos-simulacao)
# ---------------------------------------------------------------------------
def restart_in_flight(world, inputs) -> set:
    """Titulos com estado do Auneron ativo no instante do restart: pedido de
    aprovacao vivo ou WorkItem ativo de titulo ainda nao pago."""
    restart = inputs.calendar.restart_backend
    refs = set()
    for title in world.titles:
        if title.issue > restart:
            continue
        live_request = any(
            r["created"] <= restart and (r["closed"] is None or r["closed"] > restart)
            and r["expires"] > restart
            for r in title.requests
        )
        active_wi = (title.reported is None or title.reported > restart) and any(
            w["created"] <= restart for w in title.workitems
        )
        if live_request or active_wi:
            refs.add(title.ref)
    return refs


def inject_agenda(world, inputs, minimum: int) -> dict:
    cal = inputs.calendar
    lo, hi = cal.stress_week
    count = minimum + 1
    injected: dict[str, list] = {}
    settle_ops = {}
    for day, minute, counter, op in world.ops:
        if op["op"] == "settle_receivable":
            settle_ops.setdefault(op["rec_ref"], (day, minute, counter, op))
    titles = {t.ref: t for t in world.titles}

    def regular_settled_in_stress(title) -> bool:
        return (
            title.reported is not None
            and lo <= title.reported[0] <= hi
            and title.settle_override is None
            and title.reported[1] in (cal.slot("settlement_bank_return"), cal.slot("settlement_day"))
        )

    stress = sorted((t for t in world.titles if regular_settled_in_stress(t)),
                    key=lambda t: (t.reported, t.ref))
    used: set = set()

    def take(case, title):
        used.add(title.ref)
        injected.setdefault(case, []).append(title.ref)

    # C-ADV-1: baixa duplicada
    for title in [t for t in stress if t.ref not in used][:count]:
        world.op(title.reported, "sim-receber", "settle_receivable", rec_ref=title.ref, mode="duplicate")
        take("C-ADV-1", title)
    # C-ADV-3: duas personas no mesmo instante
    for number, title in enumerate([t for t in stress if t.ref not in used][:count], start=1):
        group = f"G-CONC-{number}"
        settle_ops[title.ref][3].update({"group": group, "mode": "concurrent"})
        world.op(title.reported, "sim-faturamento", "settle_receivable",
                 rec_ref=title.ref, group=group, mode="concurrent")
        take("C-ADV-3", title)
    # C-ADV-4: grupos de 5 baixas no mesmo instante
    by_slot: dict = {}
    for title in stress:
        if title.ref not in used:
            by_slot.setdefault(title.reported, []).append(title)
    groups = 0
    for slot_at in sorted(by_slot):
        members = sorted(by_slot[slot_at], key=lambda t: t.ref)[:5]
        if len(members) < 5 or groups >= count:
            continue
        groups += 1
        group = f"G-SAME-{groups}"
        for title in members:
            settle_ops[title.ref][3].update({"group": group, "mode": "same_instant"})
            take("C-ADV-4", title)
    # C-ADV-2: replay do materialize
    materialized = [
        (day, minute, op) for day, minute, _, op in world.ops if op["op"] == "materialize_escalation"
    ]
    stress_first = sorted(materialized, key=lambda item: (not (lo <= item[0] <= hi), item[0], item[1]))
    replays = 0
    for day, minute, op in stress_first:
        if replays >= count or op["rec_ref"] in used:
            continue
        world.op((day, minute), op["actor"], "materialize_escalation", rec_ref=op["rec_ref"], mode="replay")
        take("C-ADV-2", titles[op["rec_ref"]])
        replays += 1
    # C-ADV-9 (semana de stress) e C-NEG-4 (fora dela): materializar titulo ja pago
    def paid_late_never_escalated(title) -> bool:
        if title.reported is None or title.workitems:
            return False
        if title.venc_at(title.reported) >= title.reported[0]:
            return False
        nxt = cal.next_business_day(title.reported[0])
        return nxt <= cal.last_day
    pool = sorted((t for t in world.titles if paid_late_never_escalated(t) and t.ref not in used),
                  key=lambda t: (t.reported, t.ref))
    for case, inside in (("C-ADV-9", True), ("C-NEG-4", False)):
        chosen = [t for t in pool if (lo <= t.reported[0] <= hi) == inside and t.ref not in used][:count]
        for title in chosen:
            at = (cal.next_business_day(title.reported[0]), cal.slot("collections_1"))
            world.op(at, "sim-cobranca-1", "materialize_escalation", rec_ref=title.ref)
            title.wrong_attempts.append(at)
            take(case, title)
    return injected


# ---------------------------------------------------------------------------
# rotulagem (natural + forcado)
# ---------------------------------------------------------------------------
def tag_cases(world, inputs, agenda_cases: dict, classification_labels: dict) -> dict:
    cal = inputs.calendar
    floor = cal.evidence_floor
    absences = []
    for persona in inputs.company["personas"]:
        absences.extend(persona.get("absences", []))
    tags: dict[str, set] = {}

    def add(case, ref):
        tags.setdefault(case, set()).add(ref)

    for case, refs in agenda_cases.items():
        for ref in refs:
            add(case, ref)

    for title in world.titles:
        for case in title.injected:
            if case in ("C-AMB-5", "C-AMB-2", "C-AMB-4", "C-AMB-8"):
                continue  # dependem do que aconteceu; avaliados abaixo
            add(case, title.ref)
        reported = title.reported
        due_at_report = title.venc_at(reported) if reported else None
        workitem = title.workitem_for(due_at_report, reported) if reported else None

        if reported and title.profile_at_issue == "P1" and reported[0] <= due_at_report \
                and not title.requests and not title.workitems:
            add("C-N-1", title.ref)
        if reported and title.profile_at_issue == "P7" and reported[0] <= due_at_report - 3:
            add("C-N-2", title.ref)
        if reported and due_at_report + 1 <= reported[0] <= due_at_report + 4 and not title.workitems:
            add("C-N-3", title.ref)
        if reported and workitem is not None:
            add("C-N-4", title.ref)
        first_due = title.venc_log[0][1]
        if reported and len(title.venc_log) > 1 and title.venc_log[1][0][0] < first_due \
                and "C-ADV-10" not in title.injected:
            add("C-N-6", title.ref)
        for consult in title.consults:
            decision = rules.nba_decision(consult["mark_overdue_available"], consult["escalate_available"],
                                          consult["days_overdue"], title.valor)
            if "high_exposure_early_escalation" in decision.trigger_rules:
                add("C-N-7", title.ref)
            if "prolonged_overdue_escalation" in decision.trigger_rules:
                add("C-N-8", title.ref)
        # promessas
        broken = 0
        for contact in title.contacts:
            if contact["code"] != "payment_promised":
                continue
            day = contact["at"][0]
            kept = title.fact is not None and day < title.fact[0] <= day + 5
            if kept:
                add("C-PRM-1", title.ref)
            else:
                broken += 1
                add("C-PRM-2", title.ref)
        if broken >= 2:
            add("C-PRM-3", title.ref)
        # negativos
        if reported and reported < floor:
            add("C-NEG-2", title.ref)
        approved = [r for r in title.requests if r["outcome"] == "approved" and r["attempt"] == 1]
        if approved and len(title.requests) == 1:
            decided_day = approved[0]["closed"][0]
            if reported is None or reported[0] >= decided_day + 3:
                add("C-NEG-5", title.ref)
        # ambiguos
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
        # humano
        for request in title.requests:
            if request["outcome"] == "approved" and request["closed"][0] == request["created"][0]:
                add("C-HUM-1", title.ref)
            if request["outcome"] == "expired" and any(
                a["from_day"] <= request["created"][0] <= a["to_day"] for a in absences
            ):
                add("C-HUM-2", title.ref)
        # restart (D90 11:00): estado do Auneron "em voo" no instante do restart
        if title.ref in world.restart_in_flight:
            add("C-ADV-6", title.ref)

    # C-AMB-2: dois titulos do mesmo cliente escalonados, so um pago
    by_customer: dict = {}
    for title in world.titles:
        if "C-AMB-2" in title.injected:
            by_customer.setdefault(title.customer.ref, []).append(title)
    for cust_ref, pair in by_customer.items():
        if len(pair) == 2 and all(t.workitems for t in pair) \
                and sum(1 for t in pair if t.reported) == 1:
            add("C-AMB-2", "+".join(t.ref for t in pair))

    # C-N-9: varios titulos abertos, um atrasado
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

    # nivel de cliente (classificacao)
    for cust_ref, labels in classification_labels.items():
        values = [label for _, label, _ in labels]
        if "ATRASO_RECORRENTE" in values:
            add("C-N-5", cust_ref)
        if any(label == "INSUFFICIENT_DATA" and resolved in (1, 2) for _, label, resolved in labels):
            add("C-AMB-7", cust_ref)
    for customer in world.customers:
        if (customer.role or "").startswith("shared_email"):
            add("C-AMB-6", customer.ref)

    # C-NEG-1: clientes pontuais com janela de 30 dias sem nenhum efeito
    for customer in world.customers:
        if customer.profile != "P1" or customer.drift_to is not None:
            continue
        for start in (30, 75, 120):
            window = (start, start + 29)
            in_window = [t for t in customer.titles if window[0] <= t.issue[0] <= window[1]]
            if not in_window:
                continue
            clean = all(
                not t.requests and not t.workitems
                and t.reported is not None and t.reported[0] <= t.venc_at(t.reported)
                for t in in_window
            )
            if clean:
                add("C-NEG-1", f"{customer.ref}@{window[0]}-{window[1]}")
                break

    return {case: sorted(refs) for case, refs in sorted(tags.items())}
