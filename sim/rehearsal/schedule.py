"""
Cronograma da fixture SR-1: converte o perfil de carga em operacoes da agenda publica (formato do Driver) e na linha do
tempo de cada conta (insumo do Oracle tecnico). 100% DETERMINISTICO: PRNG proprio (SplitMix64, so inteiros), nada de
relogio, ambiente ou ordem de dicionario. Nao le a populacao empresarial: so constantes deste modulo e o perfil.

Calendario comprimido de 21 dias (D0..D20): criacoes ate D6; escalonamentos D0..D13; pagamentos pre-floor D1..D13;
FLOOR em D14; pagamentos pos-floor D14..D19 (contas escaladas antes do floor); RESTART em D17 as 11:00 (sozinho no slot);
D20 = varredura total (sem operacoes).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

GENERATOR_VERSION = "sim-rehearsal-1.0.0"
SEED = 151001
SCENARIO_ID = "sim-harness-sr1-s151001"
D0 = "2026-01-05"
FLOOR_DAY, RESTART_DAY, LAST_DAY = 14, 17, 20
DOMAIN = "sim-harness-sr1.example.com"
MARKER = "SIM-HARNESS-SR1"
SNAPSHOT = 18 * 60
SLOT_STEP, SLOT_CAP, FIRST_SLOT, LAST_SLOT = 15, 24, 9 * 60, 17 * 60 + 15
RESTART_MINUTE = 11 * 60
PAYMENT_CODES = ("contact_made", "payment_promised", "unreachable", "payment_refused")
LATE_PAYS = ((8, 22 * 60 + 30), (9, 20 * 60 + 59), (9, 23 * 60 + 59), (10, 23 * 60 + 59), (11, 23 * 60 + 59))
MIDNIGHT_PAYS = (12, 13)                   # execucao as 00:00 do dia (aprovacao no dia anterior)

# alvos do desenho (escala 1000 permil). O perfil do NH e apenas LIMITE INFERIOR conferido nos testes.
FULL = {"creates": {0: 105, 1: 150, 2: 150, 3: 150, 4: 150, 5: 150, 6: 45}, "mp": 800, "e1": 32, "e2": 180, "e3": 20,
        "mo": 320, "consults": 200, "due_changes": 110, "second_assessment_permil": 880}
MIN_ACCOUNTS = 90


class ScheduleError(ValueError):
    """O cronograma nao e viavel com estes parametros (falha fechada; nunca gera algo parcial)."""


class Rng:
    """SplitMix64: so aritmetica inteira, identico em qualquer SO/versao do Python."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFFFFFFFFFF

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def below(self, bound: int) -> int:
        return self.next() % bound

    def between(self, low: int, high: int) -> int:
        return low + self.below(high - low + 1)

    def shuffle(self, items: list) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]

    def sample(self, items: list, k: int) -> list:
        pool = list(items)
        if k > len(pool):
            raise ScheduleError(f"amostra de {k} em {len(pool)}")
        for i in range(k):
            j = i + self.below(len(pool) - i)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]


def spread(total: int, low: int, high: int) -> dict:
    """Divide `total` em inteiros nos dias low..high (acumulado exato)."""
    days = list(range(low, high + 1))
    out, acc = {}, 0
    for index, day in enumerate(days):
        cumulative = total * (index + 1) // len(days)
        out[day] = cumulative - acc
        acc = cumulative
    return out


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


@dataclass
class Acct:
    n: int
    ref: str
    role: str                    # E1 | E2 | E3 | P | U
    create_day: int
    due0: int
    cents: int
    create_minute: int = 0
    esc_day: int | None = None
    esc_minute: int | None = None
    esc_ref: str | None = None
    consult_minute: int | None = None
    assess: list = field(default_factory=list)       # [(day, minute, k, code, key)]
    due_log: list = field(default_factory=list)      # [(day, minute, due)]
    mo: dict | None = None
    pay: dict | None = None
    special: str | None = None
    wrong: dict | None = None
    replay: dict | None = None

    @property
    def valor(self) -> str:
        return f"{self.cents // 100}.{self.cents % 100:02d}"

    def due_at(self, day: int, minute: int) -> int:
        value = self.due0
        for d, m, due in self.due_log:
            if (d, m) <= (day, minute):
                value = due
        return value


@dataclass
class Plan:
    ops: list
    accounts: list
    meta: dict
    scale_permille: int
    seed: int


class DayAllocator:
    """Aloca operacoes em slots de 15 min (<= 25 ops), nunca em 17:30..18:30 e nunca no slot reservado."""

    def __init__(self, day: int, reserved: tuple = ()) -> None:
        self.day, self.reserved, self.cursor = day, set(reserved), FIRST_SLOT
        self.ops: list = []

    def _slot(self) -> int:
        while self.cursor in self.reserved:
            self.cursor += SLOT_STEP
        if self.cursor > LAST_SLOT:
            raise ScheduleError(f"dia {self.day} estourou a janela de slots (cursor {hhmm(self.cursor)})")
        minute = self.cursor
        self.cursor += SLOT_STEP
        return minute

    def phase(self, items: list, *, cap: int = SLOT_CAP, start: int | None = None, single_slot: bool = False) -> list:
        """items: [(callable(minute) -> op dict ou lista de ops)] ; devolve os minutos usados por item."""
        if start is not None and self.cursor < start:
            self.cursor = start
        minutes = []
        step = len(items) if single_slot else cap
        for offset in range(0, len(items), max(step, 1)):
            minute = self._slot()
            for item in items[offset:offset + step]:
                built = item(minute)
                self.ops.extend(built if isinstance(built, list) else [built])
                minutes.append(minute)
        return minutes

    def fixed(self, minute: int, op_or_ops) -> None:
        self.ops.extend(op_or_ops if isinstance(op_or_ops, list) else [op_or_ops])


def build_plan(seed: int = SEED, scale_permille: int = 1000) -> Plan:
    if not 100 <= scale_permille <= 1000:
        raise ScheduleError("scale_permille fora de 100..1000")
    rng = Rng(seed)

    def sc(value: int) -> int:
        return value if scale_permille == 1000 else max(1, value * scale_permille // 1000)

    creates = {d: sc(v) for d, v in FULL["creates"].items()}
    n_accounts = sum(creates.values())
    if n_accounts < MIN_ACCOUNTS:
        raise ScheduleError(f"escala insuficiente: {n_accounts} contas < {MIN_ACCOUNTS}")
    mp_total, e1, e2, e3 = sc(FULL["mp"]), sc(FULL["e1"]), sc(FULL["e2"]), sc(FULL["e3"])
    p_total = mp_total - e1 - e2
    u_total = n_accounts - mp_total - e3
    mo_total = sc(FULL["mo"])
    if p_total < 40 or u_total < 5 or mo_total > p_total - 30:
        raise ScheduleError("composicao de papeis inviavel")

    # --- contas, papeis, vencimentos, valores --------------------------------------------------------------------
    accounts: list = []
    for day in sorted(creates):
        for _ in range(creates[day]):
            n = len(accounts) + 1
            accounts.append(Acct(n=n, ref=f"SH-{n:04d}", role="", create_day=day, due0=0, cents=0))
    roles = ["E1"] * e1 + ["E2"] * e2 + ["E3"] * e3 + ["P"] * p_total + ["U"] * u_total
    rng.shuffle(roles)
    for acct, role in zip(accounts, roles):
        acct.role = role
        acct.cents = 5000 + rng.below(1_500_000) if rng.below(100) >= 3 else 1_500_000 + rng.below(500_000)
        acct.due0 = acct.create_day + (rng.between(-12, 40) if role == "U" else -rng.between(1, 30))
    by_role = lambda role: [a for a in accounts if a.role == role]            # noqa: E731

    # --- escalonamentos D0..D13 (E1/E2/E3) -----------------------------------------------------------------------
    esc_total = e1 + e2 + e3
    esc_quota = spread(esc_total, 0, 13)
    consult_quota = spread(sc(FULL["consults"]), 0, 13)
    pending = [a for a in accounts if a.role in ("E1", "E2", "E3")]
    escalated: list = []
    for day in range(0, 14):
        pool = [a for a in pending if a.create_day <= day and not (a.role == "E1" and day >= 13)]
        pool.sort(key=lambda a: (a.role != "E1", a.n))
        take = min(esc_quota[day], len(pool))
        early = [a for a in pool if a.role == "E1"][:take]
        rest = rng.sample([a for a in pool if a not in early], take - len(early)) if take > len(early) else []
        for a in sorted(early + rest, key=lambda a: a.n):
            a.esc_day = day
            pending.remove(a)
            escalated.append(a)
        consults = [a for a in escalated if a.esc_day == day][:consult_quota[day]]
        for a in consults:
            a.consult_minute = -1                                              # marcado; minuto vem da alocacao
    if pending:
        raise ScheduleError(f"{len(pending)} contas escaladas sem dia viavel (escala/cronograma)")
    second = FULL["second_assessment_permil"]
    for index, a in enumerate(sorted(escalated, key=lambda x: x.n)):
        a.assess = [(a.esc_day, -1, 1, PAYMENT_CODES[(a.n + index) % 4], f"{a.ref}-a1")]
        if (index + 1) * second // 1000 > index * second // 1000:                  # 88% recebem a 2a avaliacao
            a.assess.append((min(a.esc_day + 1, LAST_DAY), -1, 2, PAYMENT_CODES[(a.n + index + 1) % 4], f"{a.ref}-a2"))

    # --- pagamentos ---------------------------------------------------------------------------------------------------
    pre_quota = spread(p_total + e1, 1, 13)
    post_quota = spread(e2, 14, 19)
    payers_pre = [a for a in accounts if a.role in ("P", "E1")]
    unpaid_pre = list(payers_pre)
    for day in range(1, 14):
        pool = [a for a in unpaid_pre if a.create_day <= day and (a.role != "E1" or a.esc_day < day)]
        take = min(pre_quota[day], len(pool))
        e1_pool = [a for a in pool if a.role == "E1"]
        forced = [a for a in e1_pool if day >= 12]                              # E1 precisam pagar antes do floor
        chosen = list(forced)
        rest_pool = [a for a in pool if a not in chosen]
        chosen += rng.sample(rest_pool, max(take - len(chosen), 0)) if take > len(chosen) else []
        for a in chosen:
            a.pay = {"day": day, "kind": "std"}
            unpaid_pre.remove(a)
    if unpaid_pre:
        # sobras (quotas inteiras arredondadas): pagam no ultimo dia pre-floor
        for a in unpaid_pre:
            a.pay = {"day": 13, "kind": "std"}
    e2_accounts = by_role("E2")
    post_days = [d for d in range(14, 20) for _ in range(post_quota[d])]
    rng.shuffle(post_days)
    for a, day in zip(e2_accounts, post_days):
        a.pay = {"day": day, "kind": "std"}
    if any(a.pay is None for a in accounts if a.role in ("P", "E1", "E2")):
        raise ScheduleError("conta pagadora sem dia de pagamento")

    # --- celulas ESPECIAIS (conjuntos disjuntos de contas P sem MO) ------------------------------------------------------
    def free(day: int | None = None) -> list:
        return [a for a in by_role("P") if a.special is None and (day is None or a.pay["day"] == day)]

    for day, minute in LATE_PAYS:
        pool = [a for a in free(day)]
        if not pool:
            raise ScheduleError(f"sem conta para pagamento tardio no dia {day}")
        a = rng.sample(pool, 1)[0]
        a.special, a.pay["kind"], a.pay["minute"] = "late", "late", minute
    for day in MIDNIGHT_PAYS:
        pool = [a for a in free(day) if a.create_day <= day - 1]
        if not pool:
            raise ScheduleError(f"sem conta para pagamento a meia-noite no dia {day}")
        a = rng.sample(pool, 1)[0]
        a.special, a.pay["kind"], a.pay["minute"], a.pay["req_day"] = "midnight", "midnight", 0, day - 1
    def pick(count: int, day: int, special: str) -> list:
        pool = free(day)
        if len(pool) < count:
            raise ScheduleError(f"sem {count} contas para {special} no dia {day}")
        chosen = rng.sample(pool, count)
        for a in chosen:
            a.special = special
        return chosen
    for gid, day in ((1, 5), (2, 6)):
        for a in pick(5, day, f"same{gid}"):
            a.pay["kind"], a.pay["group"] = "same", f"G-SAME-SR1-{gid}"
    for gid, (day, copies) in enumerate(((7, 2), (8, 3)), start=1):
        a = pick(1, day, "conc")[0]
        a.pay["kind"], a.pay["group"], a.pay["copies"] = "conc", f"G-CONC-SR1-{gid}", copies
    for index, day in enumerate((8, 9, 10, 11, 9, 10, 11)):
        a = pick(1, day, "violation")[0]
        a.pay["violation"] = "decide" if index < 4 else "execute"

    # --- MO (somente contas P sem especiais; antes do pagamento) -------------------------------------------------------
    mo_quota = spread(mo_total, 0, 12)
    for day in range(12, -1, -1):
        pool = [a for a in by_role("P") if a.special is None and a.mo is None and a.create_day <= day
                and a.pay["day"] > day]
        if len(pool) < mo_quota[day]:
            raise ScheduleError(f"MO inviavel no dia {day}: {len(pool)} < {mo_quota[day]}")
        for a in rng.sample(pool, mo_quota[day]):
            a.mo = {"day": day}
    for a in by_role("P"):
        if a.mo is not None:
            a.pay["expected"] = "atrasado"
    for a in accounts:
        if a.pay is not None:
            a.pay.setdefault("expected", "aberto")

    # --- mudancas de vencimento (somente U) -----------------------------------------------------------------------------
    due_total = sc(FULL["due_changes"])
    due_quota = spread(due_total, 1, 12)
    u_accounts = by_role("U")
    due_plan: dict = {d: [] for d in range(1, 13)}
    changes = 0
    for day in range(1, 13):
        pool = [a for a in u_accounts if a.create_day <= day and len(a.due_log) < (2 if changes >= len(u_accounts) else 1)]
        pool.sort(key=lambda a: (len(a.due_log), a.n))
        for a in pool[:due_quota[day]]:
            a.due_log.append((day, -1, a.due_at(day, 24 * 60) + rng.between(3, 25)))
            due_plan[day].append(a)
            changes += 1
    # --- tentativas erradas de escalonamento (4) e replays (2) -------------------------------------------------------------
    paid_plain = [a for a in by_role("P") if a.special is None and a.mo is None and a.esc_day is None]
    for index, day in enumerate((9, 10, 11, 12)):
        pool = [a for a in paid_plain if a.pay["day"] < day and a.wrong is None]
        a = rng.sample(pool, 1)[0]
        a.wrong = {"day": day, "ref": f"SH-ESC-W{index + 1}"}
    for index, a in enumerate(rng.sample([x for x in escalated if x.role != "E1"][:50], 2)):
        a.replay = {"day": 13}

    # --- operacoes, dia a dia ----------------------------------------------------------------------------------------------
    sid = SCENARIO_ID
    key = lambda ref, step: f"sim:{sid}:{ref}:{step}"                           # noqa: E731
    counters = {"MP": 0, "MO": 0, "ESC": 0}
    ops: list = []

    def op(day, minute, actor, name, **kw):
        return {"day": day, "at": hhmm(minute), "actor": actor, "op": name, **kw}

    for a in accounts:
        if a.esc_day is not None:
            counters["ESC"] += 1
            a.esc_ref = f"SH-ESC-{counters['ESC']:04d}"
    for a in sorted([a for a in accounts if a.pay is not None], key=lambda x: (x.pay["day"], x.n)):
        counters["MP"] += 1
        a.pay["ref"] = f"SH-MP-{counters['MP']:04d}"
    for a in sorted([a for a in accounts if a.mo is not None], key=lambda x: (x.mo["day"], x.n)):
        counters["MO"] += 1
        a.mo["ref"] = f"SH-MO-{counters['MO']:04d}"
    chain_no = counters

    for day in range(0, LAST_DAY + 1):
        alloc = DayAllocator(day, reserved=(RESTART_MINUTE,) if day == RESTART_DAY else ())
        # 00:00: execucoes de meia-noite (aprovadas no dia anterior)
        for a in sorted([x for x in accounts if x.pay and x.pay.get("kind") == "midnight" and x.pay["day"] == day], key=lambda x: x.n):
            a.pay["exec_minute"] = 0
            alloc.fixed(0, op(day, 0, "sim-gerente-fin", "execute_mark_paid", rec_ref=a.ref, ref=a.pay["ref"],
                              expected_status=a.pay["expected"]))
        # A: criacoes (D0: UM slot de rajada)
        today = [a for a in accounts if a.create_day == day]
        if today:
            def make_create(a):
                def build(minute):
                    a.create_minute = minute
                    return op(day, minute, "sim-faturamento", "create_receivable", rec_ref=a.ref,
                              cliente=f"[SIM-HARNESS-SR1] {a.n:04d}", email=f"sr1-{a.n:04d}@{DOMAIN}",
                              whatsapp=f"+55 00 9999-{a.n:04d}", valor=a.valor, vencimento_day=a.due0)
                return build
            alloc.phase([make_create(a) for a in today], cap=len(today) if day == 0 else SLOT_CAP)
        # B/C/D: consulta -> escalonamento -> avaliacoes
        esc_today = [a for a in accounts if a.esc_day == day]
        cons = [a for a in esc_today if a.consult_minute == -1]
        def make_consult(a):
            def build(minute):
                a.consult_minute = minute
                return op(day, minute, "sim-coordenador-fin", "consult_nba", rec_ref=a.ref, vencimento_day=a.due0)
            return build
        alloc.phase([make_consult(a) for a in cons])
        def make_esc(a):
            def build(minute):
                a.esc_minute = minute
                return op(day, minute, "sim-coordenador-fin", "materialize_escalation", rec_ref=a.ref, ref=a.esc_ref,
                          vencimento_day=a.due0, idempotency_key=key(a.esc_ref, "materialize"))
            return build
        alloc.phase([make_esc(a) for a in esc_today])
        due_assess = [(a, i) for a in accounts for i, t in enumerate(a.assess) if t[0] == day]
        def make_assess(a, i):
            def build(minute):
                d, _, k, code, akey = a.assess[i]
                a.assess[i] = (d, minute, k, code, akey)
                return op(day, minute, ["sim-cobranca-1", "sim-cobranca-2", "sim-cobranca-3"][a.n % 3], "record_assessment",
                          rec_ref=a.ref, ref=a.esc_ref, code=code, idempotency_key=key(a.esc_ref, f"assessment-{k}"))
            return build
        alloc.phase([make_assess(a, i) for a, i in sorted(due_assess, key=lambda t: (t[0].n, t[1]))])
        # E: mudancas de vencimento
        def make_due(a):
            def build(minute):
                for index, (d, m, due) in enumerate(a.due_log):
                    if d == day and m == -1:
                        a.due_log[index] = (d, minute, due)
                        return op(day, minute, "sim-receber", "change_due_date", rec_ref=a.ref, vencimento_day=due)
                raise ScheduleError("mudanca de vencimento sem item pendente")
            return build
        alloc.phase([make_due(a) for a in due_plan.get(day, [])])
        # F/G/H: mark_overdue (materializa -> decide -> executa)
        mo_today = sorted([a for a in accounts if a.mo and a.mo["day"] == day], key=lambda a: a.n)
        def build_mo(a):
            def b1(minute):
                a.mo["req_minute"] = minute
                return op(day, minute, "sim-gerente-fin", "materialize_mark_overdue", rec_ref=a.ref, ref=a.mo["ref"],
                          vencimento_day=a.due0, idempotency_key=key(a.mo["ref"], "materialize"))
            def b2(minute):
                a.mo["dec_minute"] = minute
                return op(day, minute, "sim-coordenador-fin", "decide_mark_overdue", ref=a.mo["ref"], decision="approve")
            def b3(minute):
                a.mo["exec_minute"] = minute
                return op(day, minute, "sim-gerente-fin", "execute_mark_overdue", rec_ref=a.ref, ref=a.mo["ref"])
            return b1, b2, b3
        mo_builders = [build_mo(a) for a in mo_today]
        for stage in range(3):
            alloc.phase([b[stage] for b in mo_builders])
        # I/K/L: mark_paid (solicita -> decide [+ tentativa] -> executa [+ tentativa/duplicatas])
        chains = sorted([a for a in accounts if a.pay and a.pay.get("req_day", a.pay["day"]) == day], key=lambda a: a.n)
        def build_pay(a):
            pay = a.pay

            def req(minute):
                pay["req_minute"] = minute
                return op(day, minute, "sim-gerente-fin", "request_mark_paid", rec_ref=a.ref, ref=pay["ref"],
                          expected_status=pay["expected"], idempotency_key=key(pay["ref"], "request"))

            def dec(minute):
                pay["dec_minute"] = minute
                ops_ = []
                if pay.get("violation") == "decide":
                    pay["attempt_minute"] = minute
                    ops_.append(op(day, minute, "sim-gerente-fin", "decide_mark_paid", ref=pay["ref"], decision="approve"))
                ops_.append(op(day, minute, "sim-coordenador-fin", "decide_mark_paid", ref=pay["ref"], decision="approve"))
                return ops_

            def exe(minute):
                pay["exec_minute"] = minute
                ops_ = []
                if pay.get("violation") == "execute":
                    pay["attempt_minute"] = minute
                    ops_.append(op(day, minute, "sim-coordenador-fin", "execute_mark_paid", rec_ref=a.ref, ref=pay["ref"],
                                   expected_status=pay["expected"]))
                extra = {}
                if pay.get("group") and pay["kind"] == "same":
                    extra = {"group": pay["group"], "mode": "same_instant"}
                if pay.get("group") and pay["kind"] == "conc":
                    extra = {"group": pay["group"], "mode": "concurrent"}
                for _ in range(pay.get("copies", 1)):
                    ops_.append(op(day, minute, "sim-gerente-fin", "execute_mark_paid", rec_ref=a.ref, ref=pay["ref"],
                                   expected_status=pay["expected"], **extra))
                return ops_
            return req, dec, exe
        pay_builders = [(a, build_pay(a)) for a in chains]
        alloc.phase([b[0] for _, b in pay_builders])
        alloc.phase([b[1] for _, b in pay_builders])
        exec_now = [(a, b) for a, b in pay_builders if a.pay["kind"] not in ("late", "midnight") and a.pay["day"] == day]
        exec_now.sort(key=lambda t: (t[0].pay["kind"] not in ("same", "conc"), t[0].n))        # grupos juntos, no 1o slot
        alloc.phase([b[2] for _, b in exec_now], start=(RESTART_MINUTE + SLOT_STEP) if day == RESTART_DAY else None)
        for a, b in pay_builders:
            if a.pay["kind"] == "late" and a.pay["day"] == day:
                alloc.fixed(a.pay["minute"], b[2](a.pay["minute"]))
        # M: tentativas erradas e replays
        wrong_today = [a for a in accounts if a.wrong and a.wrong["day"] == day]
        def make_wrong(a):
            def build(minute):
                a.wrong["minute"] = minute
                return op(day, minute, "sim-coordenador-fin", "materialize_escalation", rec_ref=a.ref, ref=a.wrong["ref"],
                          vencimento_day=a.due0, idempotency_key=key(a.wrong["ref"], "materialize"))
            return build
        alloc.phase([make_wrong(a) for a in wrong_today])
        replay_today = [a for a in accounts if a.replay and a.replay["day"] == day]
        def make_replay(a):
            def build(minute):
                a.replay["minute"] = minute
                return op(day, minute, "sim-coordenador-fin", "materialize_escalation", rec_ref=a.ref, ref=a.esc_ref,
                          vencimento_day=a.due0, idempotency_key=key(a.esc_ref, "materialize"), mode="replay")
            return build
        alloc.phase([make_replay(a) for a in replay_today])
        if day == RESTART_DAY:
            alloc.fixed(RESTART_MINUTE, op(day, RESTART_MINUTE, "harness", "restart_backend"))
        ops.extend(alloc.ops)

    ordered = sorted(enumerate(ops), key=lambda p: (p[1]["day"], p[1]["at"], p[0]))
    final_ops = [{"seq": i, **o} for i, (_, o) in enumerate(ordered, start=1)]
    meta = {"accounts": n_accounts, "ops": len(final_ops), "roles": {"E1": e1, "E2": e2, "E3": e3, "P": p_total, "U": u_total},
            "mp": mp_total, "mo": mo_total, "escalations": esc_total, "chain_numbers": chain_no}
    return Plan(ops=final_ops, accounts=accounts, meta=meta, scale_permille=scale_permille, seed=seed)
