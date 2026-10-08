"""
Oracle TECNICO da fixture SR-1: expectativas POR DESENHO (politica conhecida do produto, replicada em `sim.oracle.rules`),
nunca copiadas do que o produto devolve. Cada expectativa nasce de uma celula que declara seu instante efetivo e ja cai
no primeiro snapshot OBSERVAVEL (TA-1). `check_ta1_independent` reverifica isso a partir SOMENTE das operacoes da agenda.
"""

from __future__ import annotations

from sim.generator.world import Title
from sim.oracle import rules
from sim.oracle import rules_v2
from sim.oracle import v2_1
from sim.rehearsal import schedule as S

ORACLE_VERSION = "v2.2"
EFFECT_LAG_MIN = 30
CLAIM = "TECHNICAL FIXTURE ORACLE (SR-1 rehearsal); never a business result and never a claim about the Nova Horizonte"


class OracleError(ValueError):
    pass


def knowable_day(day: int, minute: int) -> int:
    """Primeiro snapshot (18:00) em que um fato de (dia, minuto) ja vale (TA-1; empate das 18:00 e inclusivo)."""
    target = day if minute <= S.SNAPSHOT else day + 1
    if target > S.LAST_DAY:
        raise OracleError(f"fato de ({day}, {S.hhmm(minute)}) sem snapshot posterior")
    return target


def effect_day(day: int, minute: int) -> int:
    """Regra de efeito verificado: execucao antes das 17:30 vale no proprio dia; senao no dia seguinte."""
    return day if minute < S.SNAPSHOT - EFFECT_LAG_MIN else min(day + 1, S.LAST_DAY)


def _subject_key(subject: dict) -> str:
    return "|".join(f"{k}={subject[k]}" for k in sorted(subject))


class Exps:
    def __init__(self) -> None:
        self.items: list = []

    def add(self, kind, day, subject, policy, *, at=None, semantics="fact", **extra) -> None:
        item = {"kind": kind, "subject": subject, "day": day, "policy": policy, "semantics": semantics, **extra}
        if at is not None:
            item["at"] = S.hhmm(at)
        self.items.append(item)

    def finish(self) -> list:
        ordered = sorted(self.items, key=lambda e: (e["day"], e.get("at", "18:00"), _subject_key(e["subject"]), e["kind"]))
        out = []
        for number, item in enumerate(ordered, start=1):
            item["exp_id"] = f"E-{number:06d}"
            out.append(v2_1.annotate(item, S.FLOOR_DAY))
        return out


def _title(a: S.Acct) -> Title:
    issue = (a.create_day, a.create_minute)
    title = Title(ref=a.ref, customer=None, order_no=1, issue=issue, valor=a.cents, method="boleto", opening=False, plan={})
    title.venc_log = [(issue, a.due0)] + [((d, m), due) for d, m, due in a.due_log]
    log = [(issue, "aberto")]
    if a.mo:
        log.append(((a.mo["day"], a.mo["exec_minute"]), "atrasado"))
    if a.pay:
        log.append(((a.pay["day"], a.pay["exec_minute"]), "pago"))
    title.status_log = sorted(log)
    return title


def _final_due(a: S.Acct) -> int:
    return a.due_at(S.LAST_DAY, 24 * 60)


def build_expectations(plan: S.Plan) -> list:
    ex = Exps()
    last = S.LAST_DAY
    for a in plan.accounts:
        subject = {"rec_ref": a.ref}
        paid = a.pay is not None
        # -- estado final e Knowledge (varredura do D20) --------------------------------------------------------------
        final_status = "pago" if paid else "aberto"
        ex.add("account_status", last, subject, final_status)
        ex.add("lifecycle_state", last, subject, rules.lifecycle_state(final_status, _final_due(a), last))
        projection = v2_1.knowledge_projection(_title(a), last, S.SNAPSHOT)
        ex.add("knowledge_transition", last, subject, projection)
        # -- mark_overdue (ciclo governado) -------------------------------------------------------------------------------
        if a.mo:
            m = a.mo
            ms = {"kind": "mark_overdue", "rec_ref": a.ref, "ref": m["ref"]}
            ex.add("governed_request_exists", m["day"], ms, {"requester": "sim-gerente-fin", "vencimento_day": a.due0},
                   at=m["req_minute"])
            ex.add("governed_decision", m["day"], ms, {"decider": "sim-coordenador-fin", "decision": "approved"}, at=m["dec_minute"])
            ex.add("governed_execution", m["day"], ms, {"executor": "sim-gerente-fin", "result": "succeeded"}, at=m["exec_minute"])
            ex.add("business_effect_verified", effect_day(m["day"], m["exec_minute"]), ms, "verified")
            day_k = knowable_day(m["day"], m["exec_minute"])
            ex.add("account_status", day_k, subject, "atrasado")
            ex.add("lifecycle_state", day_k, subject, rules.lifecycle_state("atrasado", a.due0, day_k))
        # -- mark_paid -------------------------------------------------------------------------------------------------------
        if paid:
            p = a.pay
            ps = {"kind": "mark_paid", "rec_ref": a.ref, "ref": p["ref"]}
            req_day = p.get("req_day", p["day"])
            ex.add("governed_request_exists", req_day, ps, {"requester": "sim-gerente-fin", "expected_status": p["expected"]},
                   at=p["req_minute"])
            ex.add("governed_decision", req_day, ps, {"decider": "sim-coordenador-fin", "decision": "approved"}, at=p["dec_minute"])
            ex.add("governed_execution", p["day"], ps, {"executor": "sim-gerente-fin", "result": "succeeded"}, at=p["exec_minute"])
            ex.add("business_effect_verified", effect_day(p["day"], p["exec_minute"]), ps, "verified")
            day_k = knowable_day(p["day"], p["exec_minute"])
            ex.add("account_status", day_k, subject, "pago")
            ex.add("lifecycle_state", day_k, subject, "paid")
            if p.get("violation"):
                attempt = "decider_is_requester" if p["violation"] == "decide" else "executor_is_decider"
                ex.add("authority_violation_rejected", p["day"] if p["violation"] == "execute" else req_day, ps,
                       {"actor": "sim-gerente-fin" if p["violation"] == "decide" else "sim-coordenador-fin",
                        "attempt": attempt, "effect": "none"}, at=p["attempt_minute"])
            if p["kind"] == "conc":
                ex.add("account_event_count", day_k, subject, {"status_changed_to_pago": 1})
                ex.add("no_duplicate_effect", last, subject, {"observed_fact_max": 1})
            if p["kind"] != "late" and p["day"] == S.RESTART_DAY:
                ex.add("no_duplicate_effect", min(S.RESTART_DAY + 1, last), subject,
                       {"live_approval_requests_per_episode_max": 1, "escalation_work_items_per_episode_max": 1})
        # -- escalonamento, avaliacoes e fatos observados ---------------------------------------------------------------------
        if a.esc_day is not None:
            episode = {"rec_ref": a.ref, "vencimento_day": a.due0}
            ex.add("escalation_work_item_exists", a.esc_day, episode, True)
            ex.add("human_assessment_count", last, subject, len(a.assess))
            if a.consult_minute is not None and a.consult_minute >= 0:
                overdue = rules.days_overdue(a.due0, a.esc_day)
                mo_ok = rules.mark_overdue_reason("aberto", a.due0, a.esc_day) is None
                esc_ok = rules.escalation_reason("aberto", a.due0, a.esc_day, False) is None
                ex.add("mark_overdue_eligible", a.esc_day, episode, mo_ok, at=a.consult_minute)
                ex.add("escalation_eligible", a.esc_day, episode, esc_ok, at=a.consult_minute)
                decision = rules.nba_decision(mo_ok, esc_ok, overdue, a.cents)
                value = {"decision_type": decision.decision_type, "selected_actions": list(decision.selected_actions)}
                if decision.trigger_rules:
                    value["trigger_rules"] = list(decision.trigger_rules)
                ex.add("nba_decision", a.esc_day, episode, value, at=a.consult_minute)
                ex.add("nba_applied_rules", a.esc_day, episode, list(rules_v2.nba_applied_rules(mo_ok, esc_ok, overdue, a.cents)),
                       at=a.consult_minute)
            if a.role == "E2":
                check = min(last, a.pay["day"] + 1)
                ex.add("observed_fact_count", check, episode, 1, semantics="correlation_only")
                ex.add("observed_fact_links_event", check, episode, {"linked_event": "status_changed_to_pago"},
                       semantics="correlation_only")
                ex.add("provenance_present", last, episode, {"provenance_context": True, "producer_pass_id": True})
            elif a.role == "E1":
                ex.add("observed_fact_count", last, episode, 0, semantics="abstain")
            if a.replay:
                ex.add("no_duplicate_effect", a.replay["day"], episode, {"escalation_work_items_for_episode": 1})
        if a.wrong:
            episode = {"rec_ref": a.ref, "vencimento_day": a.due0}
            ex.add("escalation_eligible", a.wrong["day"], episode, False, at=a.wrong["minute"])
            ex.add("http_status", a.wrong["day"], episode, 409, at=a.wrong["minute"])
            ex.add("must_not_exist", a.wrong["day"], episode, ["escalation_work_item"])
    ex.add("provenance_aggregate", last, {"scope": "run"},
           {"legacy_without_provenance": 0, "integrity_failures": 0, "with_provenance_equals_collected_observed_facts": True})
    return ex.finish()


def oracle_document(plan: S.Plan) -> dict:
    expectations = build_expectations(plan)
    return {"artifact": "oracle", "oracle_version": ORACLE_VERSION, "schema": "sim.oracle.v2.2",
            "execution_scenario_id": S.SCENARIO_ID, "scenario_id": S.SCENARIO_ID, "generator_version": S.GENERATOR_VERSION,
            "claim": CLAIM, "expectations": expectations,
            "claim_rules": ["technical fixture: never promoted to a scenario result",
                            "an expectation may only require facts effective at or before its observation instant (TA-1)"]}


# ---------------------------------------------------------------------------------------------------------------------
# verificador TA-1 INDEPENDENTE: so a agenda (operacoes) e as expectativas
# ---------------------------------------------------------------------------------------------------------------------
def _minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def check_ta1_independent(ops: list, expectations: list) -> list:
    """Violacoes de TA-1 reconstruidas SO da agenda. Lista vazia = admissivel."""
    first_exec_ok: dict = {}
    for op in ops:
        if op["op"] == "execute_mark_paid" and op["actor"] == "sim-gerente-fin":
            first_exec_ok.setdefault(op["rec_ref"], (op["day"], _minutes(op["at"])))
        elif op["op"] == "execute_mark_overdue":
            first_exec_ok.setdefault(("MO", op["rec_ref"]), (op["day"], _minutes(op["at"])))
    esc_at = {op["rec_ref"]: (op["day"], _minutes(op["at"])) for op in reversed(ops)
              if op["op"] == "materialize_escalation" and op.get("mode") != "replay"}
    violations = []
    for exp in expectations:
        day, kind = exp["day"], exp["kind"]
        if exp["safe_at"] != "after_daily_barrier":
            continue
        observation = day * 1440 + S.SNAPSHOT
        rec = exp["subject"].get("rec_ref")
        effective = None
        if kind in ("account_status", "lifecycle_state") and exp["policy"] in ("pago", "paid") and rec in first_exec_ok:
            effective = first_exec_ok[rec][0] * 1440 + first_exec_ok[rec][1]
        elif kind in ("account_status", "lifecycle_state") and exp["policy"] in ("atrasado",) and ("MO", rec) in first_exec_ok:
            effective = first_exec_ok[("MO", rec)][0] * 1440 + first_exec_ok[("MO", rec)][1]
        elif kind == "business_effect_verified":
            key = rec if exp["subject"]["kind"] == "mark_paid" else ("MO", rec)
            effective = first_exec_ok[key][0] * 1440 + first_exec_ok[key][1] + EFFECT_LAG_MIN
        elif kind == "escalation_work_item_exists" and rec in esc_at:
            effective = esc_at[rec][0] * 1440 + esc_at[rec][1]
        if effective is not None and effective > observation:
            violations.append({"exp_id": exp["exp_id"], "kind": kind, "day": day, "effective": effective, "observation": observation})
    return violations
