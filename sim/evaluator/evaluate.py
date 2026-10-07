"""
Avaliacao offline (Design Freeze V1 sec. 9, V1.1 E-3/E-5).

Classes: CORRECT | CORRECT_ABSTENTION | DIVERGENCE | PRODUCT_FINDING |
KNOWN_LIMIT | NOT_REPRESENTABLE | CALIBRATION_PENDING | HARNESS_ERROR.

Regras:
* evidencia ausente de um item do plano => HARNESS_ERROR (nunca CORRECT por
  omissao); qualquer HARNESS_ERROR invalida o run;
* a classe da EVIDENCIA nunca e promovida: DRIVER_EXECUTION_EVIDENCE avalia
  so respostas do Driver; PRODUCT_FACT so o que o Collector leu;
* `provenance_present` por sujeito e DERIVACAO por exaustao (V1.1 E-5):
  sem as duas condicoes => KNOWN_LIMIT, jamais CORRECT;
* a saida e deterministica (sem relogio real, sem ids de execucao).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

CORRECT, ABSTENTION, DIVERGENCE, FINDING = "CORRECT", "CORRECT_ABSTENTION", "DIVERGENCE", "PRODUCT_FINDING"
KNOWN_LIMIT, NOT_REPRESENTABLE, PENDING, HARNESS_ERROR = (
    "KNOWN_LIMIT", "NOT_REPRESENTABLE", "CALIBRATION_PENDING", "HARNESS_ERROR")
EXPIRY_TOLERANCE_S = 120


class Evidence:
    """Indices sobre os dados de entrada do avaliador."""

    def __init__(self, oracle: dict, plan: dict, collected: list, driver: list, refs: dict, facts: dict) -> None:
        self.oracle, self.plan, self.refs, self.facts = oracle, plan, refs.get("refs", {}), facts
        self.d0 = date.fromisoformat(plan["clock"]["d0"])
        self.offset = int(plan["clock"]["utc_offset_minutes"])
        self.items = {item["item_id"]: item for item in plan["items"]}
        self.by_item = {record["item_id"]: record for record in collected if record.get("kind") == "collected"}
        self.by_exp: dict = defaultdict(list)
        for item in plan["items"]:
            for exp_id in item["satisfies"]:
                self.by_exp[exp_id].append(item["item_id"])
        self.results = [r for r in driver if r.get("kind") == "op_result"]
        self.https = [r for r in driver if r.get("kind") == "http"]
        self.accepted_changes: dict = defaultdict(int)
        for record in self.results:
            if record["op"] == "change_due_date" and record.get("accepted_change") and record.get("http_status") == 200:
                self.accepted_changes[record["rec_ref"]] += 1

    def records(self, exp_id: str) -> list | None:
        ids = self.by_exp.get(exp_id)
        if not ids:
            return None
        found = [self.by_item.get(item_id) for item_id in ids]
        return None if any(record is None for record in found) else found

    def local(self, iso_utc: str) -> tuple:
        value = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(
            timezone(timedelta(minutes=self.offset)))
        return (value.date() - self.d0).days, value.strftime("%H:%M"), value

    def account_id(self, rec_ref: str):
        return (self.refs.get(rec_ref) or {}).get("account_id")

    def driver_http(self, **match) -> list:
        """Ultima resposta http de cada op (por seq) que casa os filtros."""
        last: dict = {}
        for record in self.https:
            if all(record.get(key) == value for key, value in match.items()):
                last[record["seq"]] = record
        return [last[seq] for seq in sorted(last)]


def _finish(exp: dict, matches: bool, reason: str = "") -> tuple:
    semantics = exp.get("semantics", "fact")
    if semantics == "calibration_pending":
        return PENDING, reason or "calibration_pending"
    if semantics == "known_limit":
        return KNOWN_LIMIT, reason or "known_limit"
    if not matches:
        return DIVERGENCE, reason
    if semantics == "abstain":
        return ABSTENTION, reason
    if exp.get("finding") and exp.get("ideal") is not None and exp["ideal"] != exp["policy"]:
        return FINDING, ",".join(exp["finding"])
    return CORRECT, reason


# ------------------------------------------------------------------------ kinds
def k_account_status(exp, ev, records):
    body = records[0]["body"]
    ok = records[0]["status"] == 200 and body.get("status") == exp["policy"]
    return _finish(exp, ok, f"status={body.get('status') if isinstance(body, dict) else None}")


def k_lifecycle_state(exp, ev, records):
    body = records[0]["body"]
    state = ((body or {}).get("receivable_lifecycle") or {}).get("state") if isinstance(body, dict) else None
    return _finish(exp, records[0]["status"] == 200 and state == exp["policy"], f"state={state}")


def k_classification(exp, ev, records):
    record = records[0]
    body = record["body"] if isinstance(record["body"], dict) else {}
    label = (body.get("classification") or {}).get("label")
    ok = record["status"] == 200 and (
        label == exp["policy"] or (body.get("status") == "not_classified_yet" and exp["policy"] == "INSUFFICIENT_DATA"))
    return _finish(exp, ok, f"label={label} status={body.get('status')}")


def k_behavior(exp, ev, records):
    items = (records[0]["body"] or {}).get("items", [])
    return _finish(exp, records[0]["status"] == 200 and (len(items) > 0) == bool(exp["policy"]), f"active={len(items)}")


def k_mark_overdue_eligible(exp, ev, records):
    body = records[0]["body"] if isinstance(records[0]["body"], dict) else {}
    return _finish(exp, records[0]["status"] == 200 and body.get("system_recommendable") == bool(exp["policy"]),
                   f"recommendable={body.get('system_recommendable')}")


def k_escalation_eligible(exp, ev, records):
    body = records[0]["body"] if isinstance(records[0]["body"], dict) else {}
    return _finish(exp, records[0]["status"] == 200 and (body.get("status") == "eligible") == bool(exp["policy"]),
                   f"status={body.get('status')}")


def _approval_id(ev, exp):
    return (ev.refs.get(exp["subject"]["ref"]) or {}).get("approval_id")


def k_governed_request_exists(exp, ev, records):
    approval_id = _approval_id(ev, exp)
    items = records[0]["body"]["items"] if isinstance(records[0]["body"], dict) else []
    found = next((item for item in items if item["request_id"] == approval_id), None)
    wanted = "account.mark_paid" if exp["subject"]["kind"] == "mark_paid" else "account.mark_overdue"
    ok = (found is not None and found["skill_key"] == wanted
          and found["target_account_id"] == ev.account_id(exp["subject"]["rec_ref"]))
    return _finish(exp, ok, "found" if found else "absent")


def k_governed_decision(exp, ev, records):
    body = records[0]["body"]
    status = ((body or {}).get("request") or {}).get("status") if isinstance(body, dict) else None
    return _finish(exp, records[0]["status"] == 200 and status == exp["policy"]["decision"], f"status={status}")


def k_effective_expiry(exp, ev, records):
    body = records[0]["body"]
    expires = ((body or {}).get("request") or {}).get("expires_at") if isinstance(body, dict) else None
    if expires is None:
        return _finish(exp, False, "sem expires_at")
    day, at, instant = ev.local(expires)
    want = exp["policy"]["expires_at"]
    wanted = datetime.combine(ev.d0 + timedelta(days=want["day"]), datetime.strptime(want["at"], "%H:%M").time(),
                              tzinfo=timezone(timedelta(minutes=ev.offset)))
    delta = abs((instant - wanted).total_seconds())
    return _finish(exp, delta <= EXPIRY_TOLERANCE_S, f"delta_s={int(delta)}")


def k_escalation_work_item(exp, ev, records):
    body = records[0]["body"]
    if records[0]["route"] == "work_item":
        return _finish(exp, records[0]["status"] == 200 and bool(exp["policy"]) == isinstance(body, dict)
                       and isinstance(body, dict) and "id" in body, "work_item")
    account = ev.account_id(exp["subject"]["rec_ref"])
    due = (ev.d0 + timedelta(days=exp["subject"]["vencimento_day"])).isoformat()
    key = f"human_escalation:v1:{account}:{due}"
    present = any(item.get("work_key") == key for item in (body or {}).get("items", []))
    return _finish(exp, present == bool(exp["policy"]), f"work_key_present={present}")


def _observations(ev, exp_id) -> list:
    out: dict = {}
    for record in ev.records(exp_id) or []:
        for observation in (record["body"] or {}).get("items", []):
            out[(record["path"].split("?")[0], observation["id"], observation["observation_type"])] = observation
    return list(out.values())


def k_human_assessment_count(exp, ev, records):
    count = sum(1 for o in _observations(ev, exp["exp_id"]) if o["observation_type"] == "human_assessment")
    return _finish(exp, count == exp["policy"], f"count={count}")


def k_observed_fact_count(exp, ev, records):
    count = sum(1 for o in _observations(ev, exp["exp_id"]) if o["observation_type"] == "observed_fact")
    return _finish(exp, count == exp["policy"], f"count={count}")


def k_observed_fact_links(exp, ev, records):
    facts = [o for o in _observations(ev, exp["exp_id"]) if o["observation_type"] == "observed_fact"]
    linked = [o for o in facts if o.get("linked_account_event_id")]
    return _finish(exp, bool(facts) and len(linked) == len(facts),
                   f"facts={len(facts)} linked={len(linked)} (tipo do evento nao e publico: correlacao)")


def k_business_effect(exp, ev, records):
    body = records[0]["body"] if isinstance(records[0]["body"], dict) else {}
    result = (body.get("effect_verification") or {}).get("result")
    return _finish(exp, records[0]["status"] == 200 and result == exp["policy"], f"result={result}")


def k_account_event_count(exp, ev, records):
    body = records[0]["body"] if isinstance(records[0]["body"], dict) else {}
    events = {e["id"] for e in (body.get("payment") or {}).get("evidence", []) if e.get("source") == "account_event"}
    return _finish(exp, records[0]["status"] == 200 and len(events) == sum(exp["policy"].values()),
                   f"account_events={len(events)}")


def k_must_not_exist(exp, ev, records):
    account = ev.account_id(exp["subject"]["rec_ref"])
    due = (ev.d0 + timedelta(days=exp["subject"]["vencimento_day"])).isoformat()
    key = f"human_escalation:v1:{account}:{due}"
    present = any(item.get("work_key") == key for item in (records[0]["body"] or {}).get("items", []))
    return _finish(exp, not present, f"work_key_present={present}")


def k_no_duplicate(exp, ev, records):
    policy = exp["policy"]
    if "observed_fact_max" in policy:
        account = ev.account_id(exp["subject"]["rec_ref"])
        for record in records:
            if record["route"] == "work_items" and any(
                    (item.get("work_key") or "").startswith(f"human_escalation:v1:{account}:")
                    for item in (record["body"] or {}).get("items", [])):
                return HARNESS_ERROR, "ha work item de escalonamento nao previsto no plano de coleta"
        count = sum(1 for o in _observations(ev, exp["exp_id"]) if o["observation_type"] == "observed_fact")
        return _finish(exp, count <= policy["observed_fact_max"], f"observed_facts={count}")
    account = ev.account_id(exp["subject"]["rec_ref"])
    prefix = f"human_escalation:v1:{account}:"
    per_key: dict = defaultdict(int)
    live: dict = defaultdict(int)
    for record in records:
        body = record["body"] if isinstance(record["body"], dict) else {}
        if record["route"] == "work_items":
            for item in body.get("items", []):
                if (item.get("work_key") or "").startswith(prefix):
                    per_key[item["work_key"]] += 1
        else:
            for item in body.get("items", []):
                if item.get("target_account_id") == account and item.get("status") in ("pending", "approved"):
                    live[item["skill_key"]] += 1
    if "escalation_work_items_for_episode" in policy:
        due = (ev.d0 + timedelta(days=exp["subject"]["vencimento_day"])).isoformat()
        count = per_key.get(f"{prefix}{due}", 0)
        return _finish(exp, count == policy["escalation_work_items_for_episode"], f"work_items={count}")
    ok = (all(n <= policy["escalation_work_items_per_episode_max"] for n in per_key.values())
          and all(n <= policy["live_approval_requests_per_episode_max"] for n in live.values()))
    return _finish(exp, ok, f"work_keys={len(per_key)} live={dict(live)}")


def k_knowledge_transition(exp, ev, records):
    body = records[0]["body"]
    if records[0]["status"] != 200 or not isinstance(body, dict):
        return _finish(exp, False, f"status={records[0]['status']}")
    policy, end_day = exp["policy"], exp["day"]
    rows = []
    for row in body["items"]:
        if row.get("knowledge_type") != "receivable_lifecycle":
            continue
        day, _, instant = ev.local(row["created_at"])
        if day <= end_day:
            rows.append((instant, row["id"], day, row["severity"]))
    rows.sort()
    observed: dict = defaultdict(list)
    for _, _, day, severity in rows:
        observed[day].append(severity)
    expected: dict = defaultdict(list)
    for row in policy["rows"]:
        expected[row["day"]].append(row["severity"])
    used_tolerance = False
    for day in sorted(set(observed) | set(expected)):
        got, want = observed.get(day, []), expected.get(day, [])
        if got == want:
            continue
        allowed = set(policy["intraday_allowance"].get(str(day), []))
        # a linha esperada de fim de dia e a ULTIMA do dia; o que vem antes so pode ser transitorio permitido
        suffix_ok = len(got) >= len(want) and got[len(got) - len(want):] == want
        extras = got[: len(got) - len(want)] if suffix_ok else None
        if not allowed or extras is None or not set(extras) <= allowed:
            return _finish(exp, False, f"day={day} observed={got} expected={want}")
        used_tolerance = True
    if used_tolerance:
        return KNOWN_LIMIT, "intraday_sampling: linhas transitorias dentro da tolerancia do Oracle"
    return _finish(exp, True, f"rows={len(rows)}")


_KINDS = {
    "account_status": k_account_status, "lifecycle_state": k_lifecycle_state, "classification": k_classification,
    "behavior_pattern_present": k_behavior, "mark_overdue_eligible": k_mark_overdue_eligible,
    "escalation_eligible": k_escalation_eligible, "governed_request_exists": k_governed_request_exists,
    "governed_decision": k_governed_decision, "effective_expiry": k_effective_expiry,
    "escalation_work_item_exists": k_escalation_work_item, "human_assessment_count": k_human_assessment_count,
    "observed_fact_count": k_observed_fact_count, "observed_fact_links_event": k_observed_fact_links,
    "business_effect_verified": k_business_effect, "account_event_count": k_account_event_count,
    "must_not_exist": k_must_not_exist, "no_duplicate_effect": k_no_duplicate,
    "knowledge_transition": k_knowledge_transition,
}


# ---------------------------------------------------------- driver evidence kinds
def d_nba(exp, ev):
    rows = [r for r in ev.driver_http(op="consult_nba", rec_ref=exp["subject"]["rec_ref"], day=exp["day"],
                                      at=exp["at"]) if r["status"] == 200]
    if not rows:
        return HARNESS_ERROR, "sem resposta do consult_nba no slot"
    body = rows[-1]["response"] or {}
    if exp["kind"] == "nba_applied_rules":
        return _finish(exp, body.get("applied_rules") == exp["policy"], f"rules={body.get('applied_rules')}")
    decision = body.get("decision") or {}
    ok = (decision.get("decision_type") == exp["policy"]["decision_type"]
          and decision.get("selected_actions") == exp["policy"]["selected_actions"])
    return _finish(exp, ok, f"decision={decision.get('decision_type')}")


def d_governed_execution(exp, ev):
    subject = exp["subject"]
    op = "execute_mark_paid" if subject["kind"] == "mark_paid" else "execute_mark_overdue"
    rows = ev.driver_http(op=op, ref=subject["ref"], day=exp["day"], at=exp["at"])
    if not rows:
        return HARNESS_ERROR, "sem execucao do Driver no slot"
    want = exp["policy"]["result"]
    for row in rows:
        body = row["response"] if isinstance(row["response"], dict) else {}
        if want == "succeeded" and row["status"] == 200 and body.get("invocation_status") == "succeeded":
            return _finish(exp, True, "succeeded")
        if want == "failed" and (row["status"] >= 400 or body.get("invocation_status") == "failed"):
            return _finish(exp, True, f"failed status={row['status']}")
    return _finish(exp, False, f"statuses={[r['status'] for r in rows]}")


def d_authority_violation(exp, ev):
    subject = exp["subject"]
    attempt = exp["policy"]["attempt"]
    ops = ("decide_mark_paid", "decide_mark_overdue") if attempt == "decider_is_requester" else (
        "execute_mark_paid", "execute_mark_overdue")
    rows = [r for op in ops for r in ev.driver_http(op=op, ref=subject["ref"], day=exp["day"], at=exp["at"],
                                                    actor=exp["policy"]["actor"])]
    if not rows:
        return HARNESS_ERROR, "sem tentativa de violacao no slot"
    return _finish(exp, any(r["status"] == 403 for r in rows), f"statuses={[r['status'] for r in rows]}")


def d_http_status(exp, ev):
    rows = ev.driver_http(rec_ref=exp["subject"]["rec_ref"], day=exp["day"], at=exp["at"])
    if not rows:
        return HARNESS_ERROR, "sem resposta do Driver no slot"
    return _finish(exp, any(r["status"] == exp["policy"] for r in rows), f"statuses={[r['status'] for r in rows]}")


def d_due_changes(exp, ev):
    count = ev.accepted_changes.get(exp["subject"]["rec_ref"], 0)
    return _finish(exp, count == exp["policy"], f"accepted_puts={count} (evidencia do Driver, nao contagem do produto)")


_DRIVER = {
    "nba_decision": d_nba, "nba_applied_rules": d_nba, "governed_execution": d_governed_execution,
    "authority_violation_rejected": d_authority_violation, "http_status": d_http_status,
    "driver_due_date_change_accepted_count": d_due_changes,
}


# ------------------------------------------------------------------ proveniencia
def provenance_state(ev: Evidence) -> dict:
    """Agregado (PRODUCT FACT via CLI) + condicoes (i)/(ii) da exaustao."""
    report = ev.facts.get("provenance_report")
    if not isinstance(report, dict) or ev.facts.get("provenance_exit") not in (0, 2):
        return {"available": False, "reason": "relatorio de proveniencia ausente ou CLI indisponivel"}
    counts = report.get("observations", {})
    collected = set()
    for exp_id, ids in ev.by_exp.items():
        for item_id in ids:
            record = ev.by_item.get(item_id)
            if record and record["route"] == "observations":
                for observation in (record["body"] or {}).get("items", []):
                    if observation["observation_type"] == "observed_fact":
                        collected.add((record["path"].split("?")[0], observation["id"]))
    failures = report.get("integrity_failures") or []
    return {
        "available": True, "exit": ev.facts.get("provenance_exit"), "integrity_failures": len(failures) if
        isinstance(failures, list) else int(failures),
        "legacy": counts.get("observed_fact_legacy_without_provenance"),
        "with_provenance": counts.get("observed_fact_with_provenance"), "collected_observed_facts": len(collected),
        "exhaustion": (counts.get("observed_fact_legacy_without_provenance") == 0
                       and counts.get("observed_fact_with_provenance") == len(collected)),
    }


def e_provenance_aggregate(exp, ev):
    state = provenance_state(ev)
    if not state["available"]:
        return HARNESS_ERROR, state["reason"]
    ok = state["legacy"] == 0 and state["integrity_failures"] == 0 and state["exhaustion"]
    if state["exit"] == 2:
        return FINDING, "integridade de proveniencia violada (exit 2 do CLI do produto)"
    return _finish(exp, ok, f"legacy={state['legacy']} with={state['with_provenance']} "
                            f"collected={state['collected_observed_facts']}")


def e_provenance_present(exp, ev):
    state = provenance_state(ev)
    if not state["available"] or not state["exhaustion"] or state["integrity_failures"]:
        return KNOWN_LIMIT, "exaustao nao estabelecida: sem promocao a CORRECT (D-1.5.2-4)"
    rec = exp["subject"]["rec_ref"]
    has_fact = False
    for record in ev.by_item.values():
        if record["route"] == "observations" and rec in _rec_of(ev, record):
            has_fact = has_fact or any(o["observation_type"] == "observed_fact"
                                       for o in (record["body"] or {}).get("items", []))
    if has_fact:
        return CORRECT, "derivado por exaustao: toda observacao do run tem proveniencia"
    return ABSTENTION, "sem observacao coletada para o sujeito"


def _rec_of(ev, record) -> set:
    work_item = int(record["path"].split("/")[2])
    return {ref["rec_ref"] for ref in ev.refs.values() if ref.get("work_item_id") == work_item and "rec_ref" in ref}


# --------------------------------------------------------------------- avaliacao
def evaluate(oracle: dict, plan: dict, collected: list, driver: list, refs: dict, facts: dict) -> dict:
    ev = Evidence(oracle, plan, collected, driver, refs, facts)
    results = []
    for exp in oracle["expectations"]:
        kind, exp_id = exp["kind"], exp["exp_id"]
        if exp.get("superseded_same_day"):
            klass, reason = KNOWN_LIMIT, "superseded_same_day: so o ultimo status do dia e verificavel"
        elif exp["semantics"] == "not_representable":
            klass, reason = NOT_REPRESENTABLE, "fato so do mundo"
        elif kind in _DRIVER:
            klass, reason = _DRIVER[kind](exp, ev)
        elif kind == "provenance_aggregate":
            klass, reason = e_provenance_aggregate(exp, ev)
        elif kind == "provenance_present":
            klass, reason = e_provenance_present(exp, ev)
        elif kind in _KINDS:
            records = ev.records(exp_id)
            if records is None:
                klass, reason = HARNESS_ERROR, "evidencia do plano ausente (item nao coletado)"
            else:
                klass, reason = _KINDS[kind](exp, ev, records)
        else:
            klass, reason = HARNESS_ERROR, f"kind sem avaliador: {kind}"
        results.append({"exp_id": exp_id, "kind": kind, "class": klass, "reason": reason,
                        "evidence_class": exp["evidence_class"]})
    counts: dict = defaultdict(int)
    for result in results:
        counts[result["class"]] += 1
    document = {
        "artifact": "evaluation", "oracle_version": oracle["oracle_version"],
        "execution_scenario_id": oracle["execution_scenario_id"], "results": results,
        "summary": dict(sorted(counts.items())), "run_valid": counts.get(HARNESS_ERROR, 0) == 0,
        "provenance": provenance_state(ev),
    }
    document["evaluation_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in document.items()}, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()
    return document
