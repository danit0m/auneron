"""
Simulacao deterministica do mundo da Nova Horizonte (Design Freeze v2).

Um laco dia a dia (D0..D179) com os slots intradiarios do calendario
normativo. Modela tres coisas SEPARADAS:

* o MUNDO      -- clientes pagando (fact_at), banco tornando o pagamento
                  conhecivel (evidence_at), ERP conciliando;
* as PERSONAS  -- faturamento, baixas (reported_at), cobranca com
                  capacidade, gerente com ausencias;
* o AUNERON (replica) -- status do titulo, deteccao de atraso/pedidos de
  aprovacao, WorkItems de escalonamento, segundo as regras replicadas em
  `sim.oracle.rules` (nunca importa o produto).

Toda aleatoriedade vem de sub-streams (`sim.generator.rng`).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from sim.generator.config import Inputs
from sim.generator.config import cents
from sim.generator.rng import chance_bps
from sim.generator.rng import pick_weighted
from sim.generator.rng import substream
from sim.generator.timeline import Instant
from sim.generator.timeline import to_minutes
from sim.oracle import rules

NEVER: Instant = (10**6, 0)
LATE_OPENING_PROFILES = ("P2", "P3", "P4", "P6", "P9")


# ---------------------------------------------------------------------------
# entidades
# ---------------------------------------------------------------------------
@dataclass
class Customer:
    index: int
    ref: str
    name: str
    email: str
    whatsapp: str
    segment: str
    profile: str
    route: str
    term_days: int
    every_days: int
    phase: int
    ticket_lo: int
    ticket_hi: int
    pay_mix: tuple
    drift_to: str | None = None
    drift_from_day: int | None = None
    role: str | None = None  # papel de caso (clientes P10 / pares de email)
    order_count: int = 0
    titles: list = field(default_factory=list)
    order_days: list | None = None

    def profile_at(self, day: int) -> str:
        if self.drift_to is not None and day >= int(self.drift_from_day):
            return self.drift_to
        return self.profile


@dataclass
class Title:
    ref: str
    customer: Customer
    order_no: int
    issue: Instant
    valor: int
    method: str
    opening: bool
    plan: dict
    venc_log: list = field(default_factory=list)  # [(instant, venc_day)]
    status_log: list = field(default_factory=list)  # [(instant, status)]
    fact: Instant | None = None
    partial: tuple | None = None  # (instant, cents)
    evidence: Instant | None = None
    reported: Instant | None = None
    settle_override: Instant | None = None
    hold_until: Instant | None = None
    queue_delay: bool = False
    queue_skipped: bool = False
    pending_changes: list = field(default_factory=list)  # [(day, minute, new_venc)]
    requests: list = field(default_factory=list)
    workitems: list = field(default_factory=list)
    contacts: list = field(default_factory=list)
    consults: list = field(default_factory=list)
    injected: list = field(default_factory=list)  # case ids forcados na emissao
    wrong_attempts: list = field(default_factory=list)  # materialize apos pagamento
    profile_at_issue: str = ""

    # -- consultas de estado (replica do Auneron) --
    @property
    def status(self) -> str:
        return self.status_log[-1][1]

    @property
    def venc(self) -> int:
        return self.venc_log[-1][1]

    def status_at(self, instant: Instant) -> str:
        value = self.status_log[0][1]
        for at, status in self.status_log:
            if at <= instant:
                value = status
        return value

    def venc_at(self, instant: Instant) -> int:
        value = self.venc_log[0][1]
        for at, venc in self.venc_log:
            if at <= instant:
                value = venc
        return value

    def workitem_for(self, venc: int, instant: Instant = NEVER):
        for item in self.workitems:
            if item["venc"] == venc and item["created"] <= instant:
                return item
        return None


@dataclass
class World:
    inputs: Inputs
    variant: str
    seed: int
    customers: list = field(default_factory=list)
    titles: list = field(default_factory=list)
    ops: list = field(default_factory=list)  # [(day, minute, counter, op)]
    events: list = field(default_factory=list)  # [(day, minute, counter, event)]
    counter: int = 0
    replayed: set = field(default_factory=set)
    duplicate_settled: set = field(default_factory=set)
    restart_in_flight: set = field(default_factory=set)

    # -- registro --------------------------------------------------------
    def _next(self) -> int:
        self.counter += 1
        return self.counter

    def op(self, at: Instant, actor: str, op: str, **payload) -> dict:
        record = {"actor": actor, "op": op, **payload}
        self.ops.append((at[0], at[1], self._next(), record))
        return record

    def event(self, at: Instant, kind: str, source: str, subject: dict, **data) -> None:
        record = {
            "type": kind,
            "source": source,
            "source_kind": "simulated",
            "subject": subject,
            "data": data,
        }
        self.events.append((at[0], at[1], self._next(), record))


# ---------------------------------------------------------------------------
# construcao da populacao
# ---------------------------------------------------------------------------
def build_customer(inputs: Inputs, seed: int, index: int, profile: str, segment: str) -> Customer:
    """Pura por indice: o cliente i nao depende de N (T-2)."""
    company = inputs.company["company"]
    seg = inputs.population["segments"][segment]
    names = inputs.names
    rng = substream(seed, "customer", index)
    types = names["segment_types"][segment]
    cores = names["cores"]
    qualifiers = names["qualifiers"]
    combos = len(cores) * len(qualifiers)
    slot = (index * 7 + 3) % combos
    name = " ".join(
        (
            company["name_marker"],
            types[rng.randrange(len(types))],
            cores[slot // len(qualifiers)],
            qualifiers[slot % len(qualifiers)],
        )
    )
    number = index + 1
    lo, hi = seg["ticket"]
    return Customer(
        index=index,
        ref=f"C-{number:04d}",
        name=name,
        email=f"c-{number:04d}@{company['email_domain']}",
        whatsapp=f"{company['whatsapp_prefix']}{number:04d}",
        segment=segment,
        profile=profile,
        route=company["routes"][rng.randrange(len(company["routes"]))],
        term_days=int(seg["term_days"]),
        every_days=int(seg["purchase_every_days"]),
        phase=rng.randrange(int(seg["purchase_every_days"])),
        ticket_lo=cents(lo),
        ticket_hi=cents(hi),
        pay_mix=tuple(
            (int(weight), method) for method, weight in seg["payment_mix"].items()
        ),
    )


def _expand(counts: dict) -> list:
    items: list = []
    for key, count in counts.items():
        items.extend([key] * int(count))
    return items


def build_population(inputs: Inputs, variant: str, seed: int) -> list:
    spec = inputs.variant(variant)
    total = int(spec["customers"])
    profiles = _expand(spec["profiles"])
    segments = _expand(spec["segments"])
    if len(profiles) != total or len(segments) != total:
        raise ValueError("contagens de perfis/segmentos != clientes")
    substream(seed, "profile-assignment").shuffle(profiles)
    substream(seed, "segment-assignment").shuffle(segments)
    customers = [
        build_customer(inputs, seed, index, profiles[index], segments[index])
        for index in range(total)
    ]
    # deriva comportamental (secao 5.3)
    for rule_index, rule in enumerate(spec["drift"]):
        pool = [c for c in customers if c.profile == rule["from"] and c.drift_to is None]
        substream(seed, "drift", rule_index).shuffle(pool)
        for customer in sorted(pool[: int(rule["count"])], key=lambda c: c.index):
            customer.drift_to = rule["to"]
            customer.drift_from_day = int(rule["from_day"])
    # papeis dos clientes ambiguos (P10)
    ambiguous = sorted((c for c in customers if c.profile == "P10"), key=lambda c: c.index)
    pairs = len(ambiguous) // 2 if len(ambiguous) < 6 else 3
    for pair in range(pairs):
        first, second = ambiguous[2 * pair], ambiguous[2 * pair + 1]
        first.role = f"shared_email_{pair + 1}:a"
        second.role = f"shared_email_{pair + 1}:b"
        second.email = first.email  # contabilidade comum (C-AMB-6)
    return customers


# ---------------------------------------------------------------------------
# simulador
# ---------------------------------------------------------------------------
class Simulator:
    def __init__(self, inputs: Inputs, variant: str, seed: int, injector) -> None:
        self.inputs = inputs
        self.cal = inputs.calendar
        self.variant = variant
        self.seed = seed
        self.spec = inputs.variant(variant)
        self.policy = inputs.company["collections_policy"]
        self.methods = inputs.company["payment_methods"]
        self.profiles = inputs.population["profiles"]
        personas = {p["user"]: p for p in inputs.company["personas"]}
        self.receber = personas["sim-receber"]
        self.gerente = personas["sim-gerente-fin"]
        self.collectors = [
            p["user"] for p in inputs.company["personas"] if p["function"] == "collections"
        ]
        self.contacts_per_round = {
            p["user"]: int(p["contacts_per_day"]) // 2
            for p in inputs.company["personas"]
            if p["function"] == "collections"
        }
        self.world = World(inputs=inputs, variant=variant, seed=seed)
        self.world.customers = build_population(inputs, variant, seed)
        self.injector = injector
        self.title_count = 0

    # -- utilitarios ---------------------------------------------------------
    def present(self, persona: dict, day: int) -> bool:
        if not self.cal.is_business(day):
            return False
        for absence in persona.get("absences", []):
            if absence["from_day"] <= day <= absence["to_day"]:
                return False
        return True

    def method_window(self, method: str) -> tuple[int, int]:
        lo, hi = self.methods[method]["window"]
        return to_minutes(lo), to_minutes(hi)

    def fact_instant(self, title: Title, payday: int, rng) -> Instant | None:
        cfg = self.methods[title.method]
        if not cfg["any_day"]:
            payday = self.cal.business_on_or_after(payday)
        lo, hi = self.method_window(title.method)
        minute = rng.randint(lo, hi)
        if (payday, minute) <= title.issue:
            payday = title.issue[0]
            minute = title.issue[1] + 60
            if minute > hi:
                payday += 1
                if not cfg["any_day"]:
                    payday = self.cal.business_on_or_after(payday)
                minute = lo
        if payday > self.cal.last_day:
            return None
        return (payday, minute)

    def evidence_for(self, title: Title, fact: Instant) -> Instant | None:
        cfg = self.methods[title.method]
        if cfg["evidence"] == "same_instant":
            evidence = fact
        else:
            evidence = (
                self.cal.next_business_day(fact[0]),
                to_minutes(cfg["evidence_at"]),
            )
        return evidence if evidence[0] <= self.cal.last_day else None

    def set_fact(self, title: Title, fact: Instant | None) -> None:
        title.fact = fact
        if fact is None:
            return
        title.evidence = self.evidence_for(title, fact)
        subject = {"rec_ref": title.ref, "cust_ref": title.customer.ref}
        self.world.event(
            fact, "payment_received", f"bank-sim", subject,
            amount=_money(title.valor), method=title.method,
        )
        if title.evidence is not None:
            self.world.event(
                title.evidence, "bank_evidence_available", "bank-sim", subject,
                amount=_money(title.valor), method=title.method,
            )

    def payday_from_offset(self, due: int, offset: int) -> int:
        payday = due + offset
        if offset == 0 and not self.cal.is_business(due):
            payday = self.cal.effective_due(due)
        return payday

    # -- emissao ------------------------------------------------------------
    def issue_title(self, customer: Customer, at: Instant, *, opening: bool,
                    venc: int | None = None, valor: int | None = None) -> Title:
        customer.order_count += 1
        order_no = customer.order_count
        rng = substream(self.seed, "title", customer.index, order_no)
        self.title_count += 1
        title = Title(
            ref=f"R-{self.title_count:05d}",
            customer=customer,
            order_no=order_no,
            issue=at,
            valor=valor if valor is not None else rng.randint(customer.ticket_lo, customer.ticket_hi),
            method=pick_weighted(rng, customer.pay_mix),
            opening=opening,
            plan={},
        )
        title.profile_at_issue = customer.profile_at(at[0])
        title.queue_delay = chance_bps(rng, int(self.receber.get("queue_delay_bps", 0)))
        due = venc if venc is not None else at[0] + customer.term_days
        title.venc_log.append((at, due))
        title.status_log.append((at, "aberto"))
        self.world.titles.append(title)
        customer.titles.append(title)
        self.injector.on_issue(self, title, rng)
        self.plan_payment(title, rng)
        self.injector.after_plan(self, title, rng)
        self.world.op(
            at, "sim-faturamento", "create_receivable",
            rec_ref=title.ref, cliente=customer.name, email=customer.email,
            whatsapp=customer.whatsapp, valor=_money(title.valor),
            vencimento_day=title.venc_log[0][1],
        )
        subject = {"rec_ref": title.ref, "cust_ref": customer.ref}
        self.world.event(at, "invoice_issued", "erp-sim", subject,
                         amount=_money(title.valor), due_day=title.venc_log[0][1])
        reminder_day = title.plan.get("final_due", due) - int(self.policy["reminder_days_before_due"])
        if at[0] < reminder_day <= self.cal.last_day:
            self.world.event((reminder_day, to_minutes("09:00")), "reminder_sent",
                             "erp-sim", subject)
        return title

    def plan_payment(self, title: Title, rng) -> None:
        plan = title.plan
        due = title.venc_log[-1][1]
        for change in plan.get("changes", []):
            due = change[2]
        plan["final_due"] = due
        kind = plan.get("kind")
        if kind is None and title.opening and due < 0:
            self._plan_opening_overdue(title, rng)
        elif kind is None:
            profile = title.profile_at_issue
            spec = self.profiles[profile]
            kind = spec["kind"]
            plan["kind"] = kind
            if kind == "offset":
                plan["offset"] = rng.randint(*pick_weighted(rng, _branches(spec["branches"])))
            elif kind == "seasonal":
                branches = spec["seasonal_branches"] if self.cal.in_seasonal_window(due) else spec["branches"]
                plan["offset"] = rng.randint(*pick_weighted(rng, _branches(branches)))
            elif kind == "renegotiator":
                self._plan_renegotiation(title, rng, spec)
                due = plan["final_due"]
                plan["offset"] = rng.randint(*pick_weighted(rng, _branches(spec["branches"])))
            elif kind == "prolonged_default":
                if chance_bps(rng, int(spec["late_payer_pct"]) * 100):
                    plan["offset"] = rng.randint(*spec["late_range"])
                else:
                    plan["kind"] = "never"
            elif kind == "after_contact":
                plan.setdefault("keep_pct", int(spec["promise_kept_pct"]))
                plan.setdefault("pay_after", list(spec["pay_after_contact_days"]))
        if title.fact is None and plan.get("pay_day") is not None:
            self.set_fact(title, self.fact_instant(title, int(plan["pay_day"]), rng))
        elif title.fact is None and "offset" in plan and plan["kind"] not in ("never", "after_contact"):
            payday = self.payday_from_offset(plan["final_due"], int(plan["offset"]))
            self.set_fact(title, self.fact_instant(title, payday, rng))
        if plan.get("partial"):
            self._apply_partial(title, rng)

    def _plan_opening_overdue(self, title: Title, rng) -> None:
        plan = title.plan
        profile = title.profile_at_issue
        if profile == "P4":
            plan["kind"] = "after_contact"
            plan["keep_pct"] = int(self.profiles["P4"]["promise_kept_pct"])
            plan["pay_after"] = list(self.profiles["P4"]["pay_after_contact_days"])
            return
        if profile == "P6":
            if chance_bps(rng, int(self.profiles["P6"]["late_payer_pct"]) * 100):
                payday = max(1, title.venc + rng.randint(*self.profiles["P6"]["late_range"]))
                plan["kind"] = "late_fixed"
                self.set_fact(title, self.fact_instant(title, payday, rng))
            else:
                plan["kind"] = "never"
            return
        horizon = 30 if profile == "P9" else 10
        plan["kind"] = "late_fixed"
        self.set_fact(title, self.fact_instant(title, rng.randint(1, horizon), rng))

    def _plan_renegotiation(self, title: Title, rng, spec: dict) -> None:
        plan = title.plan
        issue_day = title.issue[0]
        due = title.venc_log[-1][1]
        changes = []
        count = rng.randint(*spec["changes"])
        for number in range(count):
            before = number > 0 or chance_bps(rng, int(spec["before_due_pct"]) * 100)
            if before:
                request = due - rng.randint(*spec["request_before_due_days"])
                new_due = due + rng.randint(*spec["extend_days"])
            else:
                request = due + int(self.policy["renegotiation_offer_day"])
                new_due = request + rng.randint(*spec["extend_days"])
            floor_day = changes[-1][0] + 1 if changes else issue_day + 1
            request = self.cal.business_on_or_after(max(request, floor_day))
            if request > self.cal.last_day:
                break
            if new_due <= request:
                new_due = request + 1
            changes.append((request, to_minutes(self.cal.intraday["renegotiation"]), new_due))
            due = new_due
        plan["changes"] = changes
        plan["final_due"] = due
        title.pending_changes = list(changes)

    def _apply_partial(self, title: Title, rng) -> None:
        due = title.plan["final_due"]
        part_day = due + rng.randint(1, 3)
        full_day = due + rng.randint(10, 20)
        if full_day > self.cal.last_day:
            return
        part = self.fact_instant(title, part_day, rng)
        if part is None:
            return
        amount = title.valor * rng.randint(40, 60) // 100
        title.partial = (part, amount)
        subject = {"rec_ref": title.ref, "cust_ref": title.customer.ref}
        self.world.event(part, "partial_payment", "bank-sim", subject,
                         amount=_money(amount), method=title.method)
        self.world.event((part[0], to_minutes("18:00")), "reconciliation_result",
                         "erp-sim", subject, result="amount_mismatch")
        self.set_fact(title, self.fact_instant(title, full_day, rng))

    # -- slots ---------------------------------------------------------------
    def detection(self, day: int, minute: int) -> None:
        now = (day, minute)
        for title in self.world.titles:
            if title.issue > now:
                continue
            for request in title.requests:
                if request["outcome"] is None and request["expires"] <= now:
                    request["outcome"] = "expired"
                    request["closed"] = request["expires"]
            if title.status != "aberto" or not title.venc < day:
                continue
            live = [r for r in title.requests if r["outcome"] is None and r["venc"] == title.venc]
            if live:
                continue
            attempt = 1 + sum(1 for r in title.requests if r["venc"] == title.venc)
            title.requests.append({
                "venc": title.venc,
                "attempt": attempt,
                "created": now,
                "expires": (day + 1, minute),
                "outcome": None,
                "closed": None,
            })

    def billing(self, day: int, slot_name: str) -> None:
        minute = self.cal.slot(slot_name)
        if not self.cal.is_business(day):
            return
        for customer in self.world.customers:
            for order_day in self._order_days(customer):
                if order_day != day:
                    continue
                rng = substream(self.seed, "order-slot", customer.index, day)
                chosen = "billing_1" if rng.randrange(2) == 0 else "billing_2"
                if chosen != slot_name:
                    continue
                if self._blocked(customer, day):
                    self.world.event((day, minute), "order_blocked", "erp-sim",
                                     {"cust_ref": customer.ref}, reason="overdue_over_30_days")
                    continue
                self.issue_title(customer, (day, minute), opening=False)

    def _order_days(self, customer: Customer) -> list:
        if customer.order_days is not None:
            return customer.order_days
        days = []
        last = 0
        current = customer.phase + 1
        while current <= self.cal.last_day:
            adjusted = self.cal.business_on_or_after(current)
            if adjusted > last and adjusted <= self.cal.last_day:
                days.append(adjusted)
                last = adjusted
            current += customer.every_days
        customer.order_days = days
        return days

    def _blocked(self, customer: Customer, day: int) -> bool:
        limit = int(self.policy["block_new_orders_after_days_overdue"])
        return any(
            title.reported is None and day - title.venc > limit
            for title in customer.titles
        )

    def settle(self, title: Title, at: Instant, actor: str = "sim-receber") -> None:
        title.reported = at
        title.status_log.append((at, "pago"))
        self.world.op(at, actor, "settle_receivable", rec_ref=title.ref)
        subject = {"rec_ref": title.ref, "cust_ref": title.customer.ref}
        self.world.event(at, "receivable_settled", "persona", subject, actor=actor)
        self.world.event((at[0], to_minutes("18:00")) if at[1] <= to_minutes("18:00") else at,
                         "reconciliation_result", "erp-sim", subject, result="matched")

    def settlement_slot(self, day: int, slot_name: str) -> None:
        if not self.present(self.receber, day):
            return
        now = (day, self.cal.slot(slot_name))
        ready = []
        for title in self.world.titles:
            if title.reported is not None or title.evidence is None:
                continue
            if title.settle_override is not None:
                continue
            if title.evidence > now:
                continue
            if title.hold_until is not None and title.hold_until > now:
                continue
            if title.queue_delay and not title.queue_skipped:
                title.queue_skipped = True
                continue
            ready.append(title)
        ready.sort(key=lambda t: (t.evidence, t.ref))
        for title in ready[: int(self.receber["capacity_per_slot"])]:
            self.settle(title, now)

    def renegotiation(self, day: int) -> None:
        if not self.present(self.receber, day):
            return
        minute = self.cal.slot("renegotiation")
        for title in self.world.titles:
            if not title.pending_changes or title.reported is not None:
                continue
            change_day, change_minute, new_due = title.pending_changes[0]
            if change_day != day:
                continue
            title.pending_changes.pop(0)
            at = (day, change_minute)
            self._change_due(title, at, new_due, actor="sim-receber")

    def _change_due(self, title: Title, at: Instant, new_due: int, actor: str) -> None:
        for request in title.requests:
            if request["outcome"] is None and request["venc"] == title.venc:
                request["outcome"] = "superseded"
                request["closed"] = at
        title.venc_log.append((at, new_due))
        self.world.op(at, actor, "change_due_date", rec_ref=title.ref, vencimento_day=new_due)
        self.world.event(at, "due_date_renegotiated", "persona",
                         {"rec_ref": title.ref, "cust_ref": title.customer.ref},
                         new_due_day=new_due)

    def approvals(self, day: int) -> None:
        if not self.present(self.gerente, day):
            return
        now = (day, self.cal.slot("approvals"))
        for title in self.world.titles:
            for request in title.requests:
                if request["outcome"] is not None or request["created"] > now:
                    continue
                if request["expires"] <= now:
                    continue
                approve = title.reported is None
                request["outcome"] = "approved" if approve else "rejected"
                request["closed"] = now
                self.world.op(now, self.gerente["user"], "decide_mark_overdue",
                              rec_ref=title.ref, decision="approve" if approve else "reject")
                if approve and title.status == "aberto":
                    title.status_log.append((now, "atrasado"))

    def collections(self, day: int, slot_name: str) -> None:
        if not self.cal.is_business(day):
            return
        now = (day, self.cal.slot(slot_name))
        threshold = int(self.policy["escalate_at_days_overdue"])
        max_contacts = int(self.policy["max_contacts_per_episode"])
        recontact_gap = int(self.policy["recontact_after_business_days"])
        candidates = []
        for title in self.world.titles:
            if title.issue > now or title.reported is not None or title.status == "pago":
                continue
            overdue = rules.days_overdue(title.venc, day)
            if overdue < threshold:
                continue
            workitem = title.workitem_for(title.venc)
            if workitem is None:
                candidates.append((0, title))
                continue
            episode_contacts = [c for c in title.contacts if c["venc"] == title.venc]
            if len(episode_contacts) >= max_contacts:
                continue
            last = episode_contacts[-1]["at"][0] if episode_contacts else workitem["created"][0]
            if self.cal.add_business_days(last, recontact_gap) <= day:
                candidates.append((1, title))
        candidates.sort(key=lambda item: (item[0], -item[1].valor,
                                          -rules.days_overdue(item[1].venc, day), item[1].ref))
        # consulta read-only do NBA para todos os candidatos de escalonamento
        for kind, title in candidates:
            if kind == 0:
                self._consult(title, now, self.collectors[0])
        capacity = dict(self.contacts_per_round)
        position = 0
        for kind, title in candidates:
            assigned = None
            for step in range(len(self.collectors)):
                persona = self.collectors[(position + step) % len(self.collectors)]
                if capacity[persona] > 0:
                    assigned = persona
                    position = (position + step + 1) % len(self.collectors)
                    break
            if assigned is None:
                break
            capacity[assigned] -= 1
            if kind == 0:
                title.workitems.append({"venc": title.venc, "created": now})
                self.world.op(now, assigned, "materialize_escalation", rec_ref=title.ref)
            self._contact(title, now, assigned)

    def _consult(self, title: Title, now: Instant, actor: str) -> None:
        day = now[0]
        status = title.status
        active = title.workitem_for(title.venc, now) is not None
        title.consults.append({
            "at": now,
            "actor": actor,
            "status": status,
            "venc": title.venc,
            "active_escalation": active,
            "mark_overdue_available": rules.mark_overdue_reason(status, title.venc, day) is None,
            "escalate_available": rules.escalation_reason(status, title.venc, day, active) is None,
            "days_overdue": rules.days_overdue(title.venc, day),
        })
        self.world.op(now, actor, "consult_nba", rec_ref=title.ref)

    def _contact(self, title: Title, now: Instant, actor: str) -> None:
        plan = title.plan
        number = len(title.contacts) + 1
        rng = substream(self.seed, "contact", title.ref, number)
        outcomes = self.inputs.population["contact_outcomes"]
        forced = plan.get("forced_codes") or []
        already_paid = title.fact is not None and title.fact <= now
        if already_paid:
            code = "contact_made"
        elif number <= len(forced):
            code = forced[number - 1]
        else:
            table = outcomes.get(title.profile_at_issue, outcomes["default"])
            if plan.get("kind") == "after_contact" and title.profile_at_issue not in outcomes:
                table = outcomes["P4"]
            code = pick_weighted(rng, [(int(w), k) for k, w in table.items()])
        title.contacts.append({"at": now, "actor": actor, "code": code, "venc": title.venc})
        self.world.op(now, actor, "record_assessment", rec_ref=title.ref, code=code)
        self.world.event(now, "customer_contact_outcome", "persona",
                         {"rec_ref": title.ref, "cust_ref": title.customer.ref}, code=code)
        if code == "payment_promised":
            self.world.event(now, "promise_made", "customer",
                             {"rec_ref": title.ref, "cust_ref": title.customer.ref})
        if already_paid or title.fact is not None:
            return
        if plan.get("kind") == "after_contact":
            if plan.get("never_pay"):
                return
            if plan.get("pay_after_refusal") and code == "payment_refused":
                self.set_fact(title, self.fact_instant(title, now[0] + rng.randint(2, 4), rng))
                return
            if chance_bps(rng, int(plan.get("keep_pct", 70)) * 100):
                lo, hi = plan.get("pay_after", [1, 5])
                self.set_fact(title, self.fact_instant(title, now[0] + rng.randint(lo, hi), rng))

    # -- laco principal -----------------------------------------------------------
    def run(self) -> World:
        cal = self.cal
        for day in range(cal.days):
            self.overrides_at(day, 0, 1)
            self.detection(day, cal.slot("clock"))
            if day == 0:
                self.injector.opening(self)
                self.detection(day, cal.slot("billing_1"))
            self.billing(day, "billing_1")
            self.settlement_slot(day, "settlement_bank_return")
            self.renegotiation(day)
            if (day, cal.slot("collections_1")) == cal.restart_backend:
                self.world.op(cal.restart_backend, "harness", "restart_backend")
            self.collections(day, "collections_1")
            self.approvals(day)
            self.collections(day, "collections_2")
            self.injector.same_day_corrections(self, day)
            self.billing(day, "billing_2")
            self.settlement_slot(day, "settlement_day")
            self.overrides_at(day, cal.slot("settlement_day") + 1, 1440)
        return self.world

    def overrides_at(self, day: int, lo: int, hi: int) -> None:
        due_now = [t for t in self.world.titles
                   if t.settle_override is not None and t.reported is None
                   and t.settle_override[0] == day and lo <= t.settle_override[1] < hi]
        for title in sorted(due_now, key=lambda t: (t.settle_override, t.ref)):
            self.settle(title, title.settle_override)


def _branches(raw: list) -> list:
    return [(int(branch["weight"]), tuple(branch["range"])) for branch in raw]


def _money(value_cents: int) -> str:
    return f"{value_cents // 100}.{value_cents % 100:02d}"
