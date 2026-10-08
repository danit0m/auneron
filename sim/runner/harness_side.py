"""
Lado do HARNESS no host (mesmo nivel de confianca de `sim.live`): e o UNICO modulo de `sim/runner` que le o Oracle.
Compila o plano de coleta (sem valores), extrai o pre-registro e os status esperados e roda a avaliacao OFFLINE.
Nada que sai daqui vai para um container: o plano (so roteamento) e entregue pelo `nh-stage`; o resto fica no host e
seus hashes entram no conjunto proibido da sonda DR-1.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import authority
from sim.runner import claim as claim_mod
from sim.runner import offline
from sim.runner.ledger import RunLedger

PREREG_SCHEMA = "sim.run.preregistration.v1"
AUTH_VIOLATION_STATUSES = (403, 409)


def authority_invariants_sha256() -> str:
    doc = {"personas": {k: sorted(v) for k, v in sorted(authority.PERSONA_AUTHORITY.items())},
           "chains": list(authority.CHAINS), "steps": dict(sorted(authority.STEP_BY_OP.items()))}
    return sha256_bytes(canonical_bytes(doc))


def compile_plan_for(oracle: dict, agenda_ops: list, *, d0: str, utc_offset_minutes: int, observer_email: str) -> dict:
    from sim.oracle.collection_plan import compile_plan

    return compile_plan(oracle, agenda_ops, d0, utc_offset_minutes, observer_email)


def preregistration(oracle: dict) -> dict:
    """Classes esperadas DERIVADAS do Oracle e congeladas (por hash) no RIM: o que sair disto e surpresa, nao ajuste."""
    items = oracle["expectations"]
    count = lambda key: dict(sorted(_tally(i.get(key) for i in items).items()))      # noqa: E731
    findings: dict = {}
    for item in items:
        for code in item.get("finding", []):
            findings[code] = findings.get(code, 0) + 1
    return {"schema": PREREG_SCHEMA, "oracle_version": oracle["oracle_version"], "expectations": len(items),
            "by_evidence_class": count("evidence_class"), "by_semantics": count("semantics"),
            "by_safe_at": count("safe_at"), "expected_findings": dict(sorted(findings.items())),
            "retimed_expected": sorted(i["exp_id"] for i in items if "retimed" in i),
            "not_collected": sum(1 for i in items if i["safe_at"] == "not_collected")}


def _tally(values) -> dict:
    out: dict = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def expected_statuses(oracle: dict) -> list:
    """Status HTTP nao-2xx que o Oracle ESPERA (rejeicoes de C-AUTH-2 e `http_status`). Uso: adjudicacao offline."""
    out = []
    for item in oracle["expectations"]:
        if item["kind"] == "authority_violation_rejected":
            out.append({"day": item["day"], "at": item["at"], "statuses": list(AUTH_VIOLATION_STATUSES)})
        elif item["kind"] == "http_status":
            out.append({"day": item["day"], "at": item["at"], "statuses": [int(item["policy"])]})
    return out


def read_jsonl(path: Path) -> list:
    if not Path(path).exists():
        return []
    return [json.loads(raw.decode("utf-8")) for raw in Path(path).read_bytes().splitlines() if raw.strip()]


def verify_authority_offline(run_dir, oracle: dict) -> dict:
    """Adjudicacao completa de status/autoridade sobre o que o run gravou. -> {violations:[{class,detail}], report}."""
    run_dir = Path(run_dir)
    status_ledger = read_jsonl(run_dir / "authority" / "status_ledger.jsonl")
    chain = read_jsonl(run_dir / "authority" / "authority_chain.jsonl")
    tripwire = read_jsonl(run_dir / "authority" / "tripwire.jsonl")
    report = offline.adjudicate_status(status_ledger, chain, expected_statuses(oracle))
    report["tripwire_violations"] = len(tripwire)
    (run_dir / "authority").mkdir(exist_ok=True)
    (run_dir / "authority" / "verify_authority.json").write_bytes(canonical_bytes(report))
    violations = []
    if report["unexplained"]:
        violations.append({"class": "UNEXPECTED_STATUS_UNEXPLAINED",
                           "detail": f"{len(report['unexplained'])} status HTTP nao-2xx sem explicacao (ex.: {report['unexplained'][:2]})"})
    if tripwire:
        violations.append({"class": "AUTHORITY_OUTSIDE_C_AUTH_2", "detail": f"{len(tripwire)} violacao(oes) publica(s) de autoridade"})
    return {"violations": violations, "report": report}


def evaluate_run(run_dir, oracle: dict, plan: dict, *, registry=None, evaluate_fn=None) -> dict:
    """Evaluator OFFLINE sobre o pacote selado. Grava `evaluation.json`, a anotacao EVALUATED e a claim gerada."""
    from sim.collector.collect import missing_items
    from sim.collector.collect import verify_records

    if evaluate_fn is None:
        from sim.evaluator.evaluate import evaluate as evaluate_fn
    run_dir = Path(run_dir)
    ledger = RunLedger(run_dir)
    if ledger.state != "COMPLETED":
        raise RuntimeError(f"so um run COMPLETED e avaliado (estado: {ledger.state})")
    collected = read_jsonl(run_dir / "collector" / "collected.jsonl")
    driver_lines = read_jsonl(run_dir / "driver" / "driver_evidence.jsonl")
    refs = json.loads((run_dir / "harness" / "refs.json").read_bytes().decode("utf-8"))
    facts = json.loads((run_dir / "harness" / "facts.json").read_bytes().decode("utf-8"))
    problems = verify_records(plan, collected) + [f"faltando {i}" for i in missing_items(plan, collected)]
    evaluation = evaluate_fn(oracle, plan, collected, driver_lines, refs, facts)
    evaluation = {**evaluation, "collection_problems": problems}
    data = canonical_bytes(evaluation)
    (run_dir / "evaluation.json").write_bytes(data)
    ledger.append("EVALUATED", {"evaluation_sha256": sha256_bytes(data), "summary": evaluation.get("summary"),
                                "run_valid": evaluation.get("run_valid")})
    rim = json.loads((run_dir / "rim.json").read_bytes().decode("utf-8"))
    claim = claim_mod.claim_for(kind=rim["kind"], state=ledger.state, run_id=ledger.records[0]["payload"]["run_id"],
                                oracle_version=oracle["oracle_version"], evaluated=True)
    (run_dir / "claim.json").write_bytes(canonical_bytes(claim))
    if registry is not None:
        registry.record_annotation(claim["run_id"], "EVALUATED", {"evaluation_sha256": sha256_bytes(data)})
    return evaluation
