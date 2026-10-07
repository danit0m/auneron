"""
Cenarios TECNICOS do harness live (fixtures `[SIM-HARNESS]`; nada da Nova Horizonte): agenda publica no formato do
Driver + Oracle tecnico minimo no formato v2.1. Cada rodada usa uma `tag` propria, entao nomes, refs e chaves de
idempotencia nao colidem entre rodadas. As expectativas sao POR DESENHO (politica conhecida do produto), nunca
copiadas do que o produto devolveu.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

D0 = "2026-01-05"
DOMAIN = "nova-horizonte.example.com"
P, D = "PRODUCT_FACT", "DRIVER_EXECUTION_EVIDENCE"
DERIVATION = "EVALUATOR_DERIVATION"
AFTER = "after_daily_barrier"
FLOOR_DAY = 14

# Dias virtuais por gate (sempre para frente: o clock nunca retrocede dentro de um stack)
DAYS = {"c0": 1, "dr3": 5, "dr4": 6, "dr5": 8, "dr6": 9, "dr7": 10, "dr8": 12, "c0b": FLOOR_DAY - 1}


def scenario_id(tag: str) -> str:
    return f"sim15-tech-{tag}"


def expected_lifecycle(days_to_due: int, paid: bool = False) -> str:
    """Regra canonica de `core/receivable_lifecycle.py` (design freeze F3), replicada do lado do Oracle."""
    if paid:
        return "paid"
    if days_to_due > 14:
        return "open"
    if days_to_due >= 1:
        return "due_soon"
    if days_to_due == 0:
        return "due_today"
    return "overdue" if -days_to_due <= 5 else "overdue_alert"


class Builder:
    def __init__(self, instance: str, tag: str) -> None:
        self.instance, self.tag = instance, tag
        self.sid = scenario_id(tag)
        self.raw: list = []

    def rec(self, n: int) -> str:
        return f"{self.tag.upper()}-{n:02d}"

    def key(self, ref: str, step: str) -> str:
        return f"sim:{self.sid}:{ref}:{step}"

    def add(self, day: int, at: str, actor: str, op: str, **kw) -> None:
        self.raw.append({"day": day, "at": at, "actor": actor, "op": op, **kw})

    def create(self, day, at, n, valor, due, actor="sim-faturamento"):
        self.add(day, at, actor, "create_receivable", rec_ref=self.rec(n),
                 cliente=f"[SIM-HARNESS] {self.tag}-{n} {self.instance}",
                 email=f"sim-harness-{self.tag}-{n}@{DOMAIN}", whatsapp=f"+55 00 0000-{n:04d}", valor=valor,
                 vencimento_day=due)
        return self.rec(n)

    def escalate(self, day, n, due, at="11:00", assess_at="11:15"):
        ref = f"ESC-{self.tag}{n}"
        self.add(day, at, "sim-coordenador-fin", "materialize_escalation", rec_ref=self.rec(n), ref=ref,
                 vencimento_day=due, idempotency_key=self.key(ref, "materialize"))
        self.add(day, assess_at, "sim-cobranca-1", "record_assessment", rec_ref=self.rec(n), ref=ref,
                 code="payment_promised", idempotency_key=self.key(ref, "assessment-1"))
        return ref

    def pay_chain(self, day, n, request_at="15:00", decide_at="15:30", execute_at="16:00", expected="aberto", ref=None):
        ref = ref or f"MP-{self.tag}{n}"
        self.add(day, request_at, "sim-gerente-fin", "request_mark_paid", rec_ref=self.rec(n), ref=ref,
                 expected_status=expected, idempotency_key=self.key(ref, "request"))
        self.add(day, decide_at, "sim-coordenador-fin", "decide_mark_paid", ref=ref, decision="approve")
        self.add(day, execute_at, "sim-gerente-fin", "execute_mark_paid", rec_ref=self.rec(n), ref=ref,
                 expected_status=expected)
        return ref

    def ops(self) -> list:
        ordered = sorted(enumerate(self.raw), key=lambda p: (p[1]["day"], p[1]["at"], p[0]))
        return [{"seq": i, **op} for i, (_, op) in enumerate(ordered, start=1)]

    def write(self, out: Path, version_id: int) -> dict:
        out.mkdir(parents=True, exist_ok=True)
        ops = self.ops()
        agenda = {"artifact": "public_agenda", "scenario_id": self.sid, "ops": ops}
        data = json.dumps(agenda, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8")
        (out / "public_agenda.json").write_bytes(data)
        personas = sorted({op["actor"] for op in ops})
        manifest = {"schema": "sim.driver.manifest.v1", "scenario_id": self.sid, "d0": D0,
                    "days": max(op["day"] for op in ops) + 1,
                    "public_agenda_sha256": hashlib.sha256(data).hexdigest(),
                    "mark_paid_version_id": version_id, "personas": personas}
        (out / "driver_manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return {"ops": ops, "personas": personas, "manifest": manifest}


class OracleBuilder:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.exps: list = []

    def add(self, kind, ev, safe_at, day, subject, policy, at=None, semantics="fact", **extra):
        exp = {"exp_id": f"X-{len(self.exps) + 1:04d}", "kind": kind, "evidence_class": ev, "safe_at": safe_at,
               "day": day, "subject": subject, "policy": policy, "semantics": semantics, **extra}
        if at is not None:
            exp["at"] = at
        self.exps.append(exp)

    def document(self) -> dict:
        return {"artifact": "oracle", "oracle_version": "v2.1", "schema": "sim.oracle.v2.1",
                "execution_scenario_id": scenario_id(self.tag), "scenario_id": scenario_id(self.tag),
                "expectations": self.exps,
                "claim": "TECHNICAL FIXTURE ORACLE (gate live); nunca um resultado de cenario"}


# ---------------------------------------------------------------------------------------- C0 (2 dias)
C0_ACCOUNTS = {1: ("19295.85", -7), 2: ("150.00", 9), 3: ("800.00", 30), 4: ("420.00", -3),
               5: ("300.00", -2), 6: ("310.00", -1)}


def scen_c0(instance: str, tag: str = "c", day: int = DAYS["c0"]) -> Builder:
    """Dia D: cria 6 contas, escalonamento, mark_overdue, mark_paid e violacao de autoridade. Dia D+1: paga as
    contas ja DETECTADAS (o Outcome so correlaciona pagamento depois da deteccao), com grupos concorrentes."""
    b = Builder(instance, tag)
    for n, (valor, due) in C0_ACCOUNTS.items():
        b.create(day, "09:00", n, valor, due)
    b.add(day, "11:00", "sim-coordenador-fin", "consult_nba", rec_ref=b.rec(1), vencimento_day=-7)
    b.escalate(day, 1, -7)
    b.add(day, "13:30", "sim-gerente-fin", "materialize_mark_overdue", rec_ref=b.rec(1), ref=f"MO-{tag}1",
          vencimento_day=-7, idempotency_key=b.key(f"MO-{tag}1", "materialize"))
    b.add(day, "14:00", "sim-coordenador-fin", "decide_mark_overdue", ref=f"MO-{tag}1", decision="approve")
    b.add(day, "14:15", "sim-gerente-fin", "execute_mark_overdue", rec_ref=b.rec(1), ref=f"MO-{tag}1")
    b.pay_chain(day, 2, "15:00", "15:30", "16:00", ref=f"MP-{tag}1")      # MP-c2 e a solicitacao da violacao (conta 4)
    b.add(day, "16:30", "sim-gerente-fin", "request_mark_paid", rec_ref=b.rec(4), ref=f"MP-{tag}2",
          expected_status="aberto", idempotency_key=b.key(f"MP-{tag}2", "request"))
    b.add(day, "16:45", "sim-gerente-fin", "decide_mark_paid", ref=f"MP-{tag}2", decision="approve")   # violacao (403)
    b.add(day, "17:00", "sim-receber", "change_due_date", rec_ref=b.rec(3), vencimento_day=40)
    nxt = day + 1
    b.add(nxt, "10:00", "sim-coordenador-fin", "decide_mark_paid", ref=f"MP-{tag}2", decision="approve")
    b.add(nxt, "10:30", "sim-gerente-fin", "execute_mark_paid", rec_ref=b.rec(4), ref=f"MP-{tag}2",
          expected_status="aberto")
    for n in (5, 6):
        ref = f"MP-{tag}{n - 2}"
        b.add(nxt, "11:00", "sim-gerente-fin", "request_mark_paid", rec_ref=b.rec(n), ref=ref, expected_status="aberto",
              idempotency_key=b.key(ref, "request"))
        b.add(nxt, "11:30", "sim-coordenador-fin", "decide_mark_paid", ref=ref, decision="approve")
    for n, copies in ((5, 2), (6, 3)):
        for _ in range(copies):
            b.add(nxt, "12:00", "sim-gerente-fin", "execute_mark_paid", rec_ref=b.rec(n), ref=f"MP-{tag}{n - 2}",
                  expected_status="aberto", group=f"G-CONC-{tag}-{n}", mode="concurrent")
    return b


def oracle_c0(tag: str = "c", day: int = DAYS["c0"]) -> dict:
    o = OracleBuilder(tag)
    rec = lambda n: f"{tag.upper()}-{n:02d}"  # noqa: E731
    email = f"sim-harness-{tag}-1@{DOMAIN}"
    status = {1: "atrasado", 2: "pago", 3: "aberto", 4: "aberto"}
    for n in range(1, 5):
        due = 40 if n == 3 else C0_ACCOUNTS[n][1]         # n=3: change_due_date (17:00) move o vencimento para +40
        o.add("account_status", P, AFTER, day, {"rec_ref": rec(n)}, status[n])
        o.add("lifecycle_state", P, AFTER, day, {"rec_ref": rec(n)}, expected_lifecycle(due - day, paid=n == 2))
    o.add("classification", P, AFTER, day, {"cust_ref": "C-0001", "email": email}, "INSUFFICIENT_DATA")
    o.add("behavior_pattern_present", P, AFTER, day, {"anchor_rec_ref": rec(1), "email": email}, False)
    o.add("mark_overdue_eligible", P, "pre_slot", day, {"rec_ref": rec(1), "vencimento_day": -7}, True, at="11:00")
    o.add("escalation_eligible", P, "pre_slot", day, {"rec_ref": rec(1), "vencimento_day": -7}, True, at="11:00")
    o.add("nba_decision", D, "in_slot", day, {"rec_ref": rec(1), "vencimento_day": -7},
          {"decision_type": "action_bundle", "selected_actions": ["account.mark_overdue", "escalate_to_human"],
           "trigger_rules": ["high_exposure_early_escalation"]}, at="11:00")
    o.add("nba_applied_rules", D, "in_slot", day, {"rec_ref": rec(1), "vencimento_day": -7},
          ["high_exposure_early_escalation"], at="11:00")
    for ref, kind, n, at, due in ((f"MO-{tag}1", "mark_overdue", 1, "13:30", -7), (f"MP-{tag}1", "mark_paid", 2, "15:00", 9)):
        subj = {"kind": kind, "rec_ref": rec(n), "ref": ref}
        o.add("governed_request_exists", P, AFTER, day, subj, {"requester": "sim-gerente-fin", "vencimento_day": due}, at=at)
        o.add("governed_decision", P, AFTER, day, subj, {"decider": "sim-coordenador-fin", "decision": "approved"})
    o.add("governed_execution", D, "in_slot", day, {"kind": "mark_overdue", "rec_ref": rec(1), "ref": f"MO-{tag}1"},
          {"executor": "sim-gerente-fin", "result": "succeeded"}, at="14:15")
    o.add("governed_execution", D, "in_slot", day, {"kind": "mark_paid", "rec_ref": rec(2), "ref": f"MP-{tag}1"},
          {"executor": "sim-gerente-fin", "result": "succeeded"}, at="16:00")
    o.add("authority_violation_rejected", D, "in_slot", day, {"kind": "mark_paid", "rec_ref": rec(4), "ref": f"MP-{tag}2"},
          {"actor": "sim-gerente-fin", "attempt": "decider_is_requester", "effect": "none"}, at="16:45")
    o.add("http_status", D, "in_slot", day, {"rec_ref": rec(2), "vencimento_day": 9}, 200, at="16:00")
    o.add("effective_expiry", P, AFTER, day, {"kind": "mark_paid", "rec_ref": rec(2), "ref": f"MP-{tag}1"},
          {"expires_at": {"day": day + 1, "at": "15:00"}, "retry": "persona_rerequest", "status_may_remain": "pending"})
    o.add("escalation_work_item_exists", P, AFTER, day, {"rec_ref": rec(1), "vencimento_day": -7}, True)
    o.add("human_assessment_count", P, AFTER, day, {"rec_ref": rec(1)}, 1)
    o.add("business_effect_verified", P, AFTER, day, {"kind": "mark_overdue", "rec_ref": rec(1), "ref": f"MO-{tag}1"}, "verified")
    o.add("driver_due_date_change_accepted_count", D, "end_of_run", day, {"rec_ref": rec(3)}, 1)
    o.add("must_not_exist", P, "end_of_run", day, {"rec_ref": rec(3), "vencimento_day": 30}, ["escalation_work_item"])
    o.add("no_duplicate_effect", P, "end_of_run", day, {"rec_ref": rec(1)},
          {"escalation_work_items_per_episode_max": 1, "live_approval_requests_per_episode_max": 1})
    o.add("knowledge_transition", P, AFTER, day, {"rec_ref": rec(1)},
          {"closure": "GET /accounts/{id}", "intraday_allowance": {}, "observation_model": "end_of_day",
           "rows": [{"day": day, "severity": "critical", "state": "overdue_alert"}], "severities": ["critical"]})
    day2 = day + 1
    for n, ref, at in ((4, f"MP-{tag}2", "10:30"), (5, f"MP-{tag}3", "12:00"), (6, f"MP-{tag}4", "12:00")):
        o.add("account_status", P, AFTER, day2, {"rec_ref": rec(n)}, "pago")
        o.add("lifecycle_state", P, AFTER, day2, {"rec_ref": rec(n)}, "paid")
        o.add("account_event_count", P, AFTER, day2, {"rec_ref": rec(n)}, {"status_changed_to_pago": 1})
        subj = {"kind": "mark_paid", "rec_ref": rec(n), "ref": ref}
        o.add("governed_decision", P, AFTER, day2, subj, {"decider": "sim-coordenador-fin", "decision": "approved"})
        o.add("governed_execution", D, "in_slot", day2, subj, {"executor": "sim-gerente-fin", "result": "succeeded"}, at=at)
    return o.document()


# ---------------------------------------------------------------------------------------- mini-cenarios dos DRs
def scen_dr3(instance, tag="h", day=DAYS["dr3"]):
    b = Builder(instance, tag)
    b.create(day, "09:00", 1, "210.00", -2)
    b.add(day, "09:30", "sim-gerente-fin", "request_mark_paid", rec_ref=b.rec(1), ref=f"MP-{tag}1",
          expected_status="aberto", idempotency_key=b.key(f"MP-{tag}1", "request"))
    return b


def scen_dr4(instance, tag="e", day=DAYS["dr4"]):
    b = Builder(instance, tag)
    rec = b.create(day, "09:00", 1, "120.00", 20)
    b.add(day, "16:40", "sim-faturamento", "change_due_date", rec_ref=rec, vencimento_day=21)
    b.add(day, "16:50", "sim-faturamento", "change_due_date", rec_ref=rec, vencimento_day=22)
    b.add(day + 1, "09:00", "sim-faturamento", "change_due_date", rec_ref=rec, vencimento_day=23)
    return b


def scen_dr5(instance, tag="f", day=DAYS["dr5"]):
    b = Builder(instance, tag)
    for n in (1, 2, 3, 4):
        b.create(day, "09:00", n, f"{100 + n}.00", 15)
    return b


def scen_dr6(instance, tag="k", day=DAYS["dr6"]):
    """2 grupos concorrentes (2 e 3 execucoes) + uma execucao `failed` (status da conta muda entre aprovacao e execucao)."""
    b = Builder(instance, tag)
    r1 = b.create(day, "09:00", 1, "301.00", -2)
    r2 = b.create(day, "09:00", 2, "302.00", -1)
    for n, rec in ((1, r1), (2, r2)):
        b.add(day, "10:00", "sim-gerente-fin", "request_mark_paid", rec_ref=rec, ref=f"MP-{tag}{n}",
              expected_status="aberto", idempotency_key=b.key(f"MP-{tag}{n}", "request"))
    for n in (1, 2):
        b.add(day, "10:30", "sim-coordenador-fin", "decide_mark_paid", ref=f"MP-{tag}{n}", decision="approve")
    for n, rec, copies in ((1, r1, 2), (2, r2, 3)):
        for _ in range(copies):
            b.add(day, "11:00", "sim-gerente-fin", "execute_mark_paid", rec_ref=rec, ref=f"MP-{tag}{n}",
                  expected_status="aberto", group=f"G-CONC-{tag}-{n}", mode="concurrent")
    r3 = b.create(day, "09:00", 3, "303.00", -5)
    b.add(day, "10:00", "sim-gerente-fin", "request_mark_paid", rec_ref=r3, ref=f"MP-{tag}3", expected_status="aberto",
          idempotency_key=b.key(f"MP-{tag}3", "request"))
    b.add(day, "10:30", "sim-coordenador-fin", "decide_mark_paid", ref=f"MP-{tag}3", decision="approve")
    b.add(day, "10:45", "sim-gerente-fin", "materialize_mark_overdue", rec_ref=r3, ref=f"MO-{tag}3", vencimento_day=-5,
          idempotency_key=b.key(f"MO-{tag}3", "materialize"))
    b.add(day, "10:50", "sim-coordenador-fin", "decide_mark_overdue", ref=f"MO-{tag}3", decision="approve")
    b.add(day, "10:55", "sim-gerente-fin", "execute_mark_overdue", rec_ref=r3, ref=f"MO-{tag}3")
    b.add(day, "11:15", "sim-gerente-fin", "execute_mark_paid", rec_ref=r3, ref=f"MP-{tag}3", expected_status="aberto")
    return b


def oracle_dr6(tag="k", day=DAYS["dr6"]) -> dict:
    o = OracleBuilder(tag)
    rec = lambda n: f"{tag.upper()}-{n:02d}"  # noqa: E731
    for n in (1, 2):
        o.add("account_status", P, AFTER, day, {"rec_ref": rec(n)}, "pago")
        o.add("lifecycle_state", P, AFTER, day, {"rec_ref": rec(n)}, "paid")
        subj = {"kind": "mark_paid", "rec_ref": rec(n), "ref": f"MP-{tag}{n}"}
        o.add("governed_decision", P, AFTER, day, subj, {"decider": "sim-coordenador-fin", "decision": "approved"})
        o.add("governed_execution", D, "in_slot", day, subj, {"executor": "sim-gerente-fin", "result": "succeeded"}, at="11:00")
    subj = {"kind": "mark_paid", "rec_ref": rec(3), "ref": f"MP-{tag}3"}
    o.add("account_status", P, AFTER, day, {"rec_ref": rec(3)}, "atrasado")
    o.add("governed_decision", P, AFTER, day, subj, {"decider": "sim-coordenador-fin", "decision": "approved"})
    o.add("governed_execution", D, "in_slot", day, subj,
          {"executor": "sim-gerente-fin", "reason": "expected_status_mismatch", "result": "failed"}, at="11:15")
    return o.document()


def scen_dr8(instance, tag="g", day=DAYS["dr8"]):
    b = Builder(instance, tag)
    r1 = b.create(day, "09:00", 1, "401.00", 12)
    b.create(day, "09:00", 2, "402.00", 12)
    b.add(day, "09:30", "sim-gerente-fin", "request_mark_paid", rec_ref=r1, ref=f"MP-{tag}1", expected_status="aberto",
          idempotency_key=b.key(f"MP-{tag}1", "request"))
    b.add(day, "09:45", "sim-coordenador-fin", "decide_mark_paid", ref=f"MP-{tag}1", decision="approve")
    b.add(day, "10:00", "sim-gerente-fin", "execute_mark_paid", rec_ref=r1, ref=f"MP-{tag}1", expected_status="aberto")
    return b


# ---------------------------------------------------------------------------------------- C0b (floor D14)
def scen_c0b(instance: str, tag: str = "m", floor_day: int = FLOOR_DAY) -> Builder:
    """Floor -> observed facts -> leitura publica -> proveniencia -> avaliacao.
    m-1: escalada e PAGA ANTES do floor        -> nenhum observed_fact (safe abstention)
    m-2: escalada e PAGA DEPOIS do floor       -> 1 observed_fact, ligado ao `account_event`, com proveniencia
    m-3: escalada e NUNCA paga                 -> nenhum observed_fact"""
    b = Builder(instance, tag)
    pre = floor_day - 1
    b.create(pre, "09:00", 1, "310.00", -5)
    b.escalate(pre, 1, -5)
    b.pay_chain(pre, 1)
    b.create(floor_day, "09:00", 2, "320.00", -6)
    b.create(floor_day, "09:00", 3, "330.00", -6)
    b.escalate(floor_day, 2, -6)
    b.escalate(floor_day, 3, -6)
    b.pay_chain(floor_day, 2)
    return b


def oracle_c0b(tag: str = "m", floor_day: int = FLOOR_DAY) -> dict:
    o = OracleBuilder(tag)
    rec = lambda n: f"{tag.upper()}-{n:02d}"  # noqa: E731
    pre, req = floor_day - 1, ["after_floor_barrier"]
    o.add("account_status", P, AFTER, pre, {"rec_ref": rec(1)}, "pago")
    o.add("lifecycle_state", P, AFTER, pre, {"rec_ref": rec(1)}, "paid")
    for n, due, paid in ((1, -5, True), (2, -6, True), (3, -6, False)):
        subj = {"rec_ref": rec(n), "vencimento_day": due}
        o.add("escalation_work_item_exists", P, AFTER, floor_day, subj, True)
        o.add("human_assessment_count", P, AFTER, floor_day, {"rec_ref": rec(n)}, 1, requires=req)
        o.add("observed_fact_count", P, AFTER, floor_day, subj, 1 if n == 2 else 0, requires=req,
              semantics="correlation_only")
    o.add("observed_fact_links_event", P, AFTER, floor_day, {"rec_ref": rec(2), "vencimento_day": -6},
          {"linked_event": "status_changed_to_pago"}, requires=req, semantics="correlation_only")
    o.add("account_status", P, AFTER, floor_day, {"rec_ref": rec(2)}, "pago")
    o.add("account_status", P, AFTER, floor_day, {"rec_ref": rec(3)}, "aberto")
    o.add("provenance_aggregate", P, "end_of_run", floor_day, {"scope": "run"},
          {"integrity_failures": 0, "legacy_without_provenance": 0,
           "with_provenance_equals_collected_observed_facts": True})
    for n, due in ((2, -6), (3, -6)):
        o.add("provenance_present", DERIVATION, "end_of_run", floor_day, {"rec_ref": rec(n), "vencimento_day": due},
              {"producer_pass_id": True, "provenance_context": True}, requires=["provenance_aggregate", "after_floor_barrier"],
              derivation="exhaustion")
    return o.document()
