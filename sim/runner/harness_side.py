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
    flagged = lambda i: bool(i.get("superseded_same_day")) or (i.get("ta1") or {}).get("disposition") == "NOT_COLLECTED"   # noqa: E731
    return {"schema": PREREG_SCHEMA, "oracle_version": oracle["oracle_version"], "expectations": len(items),
            "by_evidence_class": count("evidence_class"), "by_semantics": count("semantics"),
            "by_safe_at": count("safe_at"), "expected_findings": dict(sorted(findings.items())),
            "finding_expectations": sum(1 for i in items if i.get("finding")),
            "known_limit_expectations": sum(1 for i in items if flagged(i) or i["semantics"] == "known_limit"),
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


def _dump(doc) -> bytes:
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def compute_surprises(evaluation: dict, prereg: dict, *, balance_divergences: int, collection_problems: list) -> dict:
    """Compara o resultado com o PRE-REGISTRO. Surpresa nunca e absorvida: vai ao relatorio."""
    summary = evaluation.get("summary", {})
    semantics = prereg["by_semantics"]
    expected = {"KNOWN_LIMIT": prereg["known_limit_expectations"], "CALIBRATION_PENDING": semantics.get("calibration_pending", 0),
                "NOT_REPRESENTABLE": semantics.get("not_representable", 0), "CORRECT_ABSTENTION": semantics.get("abstain", 0)}
    out = []
    for klass in ("HARNESS_ERROR", "DIVERGENCE"):
        if summary.get(klass, 0):
            out.append({"class": klass, "expected": 0, "actual": summary[klass]})
    if summary.get("PRODUCT_FINDING", 0) != prereg["finding_expectations"]:
        out.append({"class": "PRODUCT_FINDING", "expected": prereg["finding_expectations"], "actual": summary.get("PRODUCT_FINDING", 0)})
    for klass, count in sorted(expected.items()):
        if summary.get(klass, 0) != count:
            out.append({"class": klass, "expected": count, "actual": summary.get(klass, 0)})
    if balance_divergences:
        out.append({"class": "BALANCE_DIVERGENCE", "expected": 0, "actual": balance_divergences})
    if collection_problems:
        out.append({"class": "COLLECTION_PROBLEM", "expected": 0, "actual": len(collection_problems)})
    return {"schema": "sim.run.surprises.v1", "clean": not out, "surprises": out, "summary": dict(sorted(summary.items()))}


GROWTH_MODEL_PATH = "inputs/growth_model.json"          # unica linhagem autorizada fixture -> NH (medicao, nao dados)


def contamination_problems(run_dir, kind: str) -> list:
    """C-7: o pacote de um `RUN` nao pode conter nenhum marcador da fixture (nem o `run_id` de ensaio), salvo o modelo de
    crescimento. Ensaio (`REHEARSAL`) carrega os marcadores por definicao: nada a varrer."""
    if kind != "RUN":
        return []
    from sim.rehearsal import contamination

    report = contamination.scan_tree(run_dir, allow_paths=(GROWTH_MODEL_PATH,))
    return [f"{hit['path']}: {hit['markers']}" for hit in report["hits"]]


class EvaluateRefused(RuntimeError):
    """Pre-condicao do `evaluate` nao atendida: nada foi gravado."""


def _load_json(path: Path):
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def evaluate_package(run_dir, *, registry=None, evaluate_fn=None, verify_only: bool = False) -> dict:
    """Evaluator OFFLINE sobre o pacote selado (emenda B): pre-condicoes fail-closed, resultados fora do digest do pacote
    mas cobertos por um digest FINAL vinculado ao ledger (`results.seal_results`)."""
    import hashlib

    from sim.collector.collect import missing_items
    from sim.collector.collect import verify_records
    from sim.runner import interlock
    from sim.runner import package
    from sim.runner import results

    if evaluate_fn is None:
        from sim.evaluator.evaluate import evaluate as evaluate_fn
    run_dir = Path(run_dir)
    ledger = RunLedger(run_dir)
    ledger.verify()
    interlock.verify_ledger_authorization(ledger, run_dir)
    if ledger.state != "COMPLETED":
        raise EvaluateRefused(f"so um run COMPLETED e avaliado (estado: {ledger.state})")
    evaluated = "EVALUATED" in ledger.replay.seen
    if evaluated != verify_only:
        raise EvaluateRefused("o run ja foi avaliado (use --verify-only)" if evaluated else "--verify-only exige um run avaliado")
    run_id = ledger.records[0]["payload"]["run_id"]
    registry_digest = None
    if registry is not None:
        terminal = registry.runs()[run_id]["terminal"]
        registry_digest = terminal["package_digest"] if terminal else None
    verdict = package.verify(run_dir, expected_digest=registry_digest)
    if not verdict["ok"]:
        raise EvaluateRefused("pacote nao verifica: " + "; ".join(verdict["problems"]))
    rim = _load_json(run_dir / "rim.json")
    declared = rim["declared"]
    contaminated = contamination_problems(run_dir, rim["kind"])
    if contaminated:
        raise EvaluateRefused("pacote de RUN contaminado por marcadores da fixture: " + "; ".join(contaminated[:3]))
    inputs = run_dir / "inputs"
    for name, key in (("oracle.json", "oracle_sha256"), ("collection_plan.json", "plan_sha256"),
                      ("preregistration.json", "preregistration_sha256")):
        path = inputs / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != declared[key]:
            raise EvaluateRefused(f"inputs/{name} ausente ou diferente do RIM ({key})")
    oracle, plan, prereg = _load_json(inputs / "oracle.json"), _load_json(inputs / "collection_plan.json"), _load_json(inputs / "preregistration.json")
    collected = read_jsonl(run_dir / "collector" / "collected.jsonl")
    driver_lines = read_jsonl(run_dir / "driver" / "driver_evidence.jsonl")
    refs, facts = _load_json(run_dir / "harness" / "refs.json"), _load_json(run_dir / "harness" / "facts.json")
    problems = verify_records(plan, collected) + [f"faltando {i}" for i in missing_items(plan, collected)]
    evaluation = {**evaluate_fn(oracle, plan, collected, driver_lines, refs, facts), "collection_problems": problems}
    divergences = sum(len(r["payload"]["divergences"]) for r in ledger.events() if r["event"] == "BALANCE_DIVERGENCE")
    surprises = compute_surprises(evaluation, prereg, balance_divergences=divergences, collection_problems=problems)
    claim = claim_mod.claim_for(kind=rim["kind"], state=ledger.state, run_id=run_id, oracle_version=oracle["oracle_version"],
                                evaluated=True, balance_divergences=divergences)
    new = {"evaluation.json": _dump(evaluation), "surprises.json": _dump(surprises), "claim.json": _dump(claim)}
    if verify_only:
        recorded = [r for r in ledger.events() if r["event"] == "EVALUATED"][0]["payload"]["files"]
        same = {name: hashlib.sha256(data).hexdigest() == recorded[name] for name, data in new.items()}
        final = results.verify_results(run_dir, registry_digest=None)
        return {"verified": all(same.values()) and final["ok"], "deterministic": same, "results": final}
    for name, data in new.items():
        (run_dir / name).write_bytes(data)
    sealed = results.seal_results(run_dir, ledger=ledger, extra_payload={
        "summary": evaluation.get("summary"), "run_valid": evaluation.get("run_valid"), "surprises_clean": surprises["clean"]})
    if registry is not None:
        registry.record_annotation(run_id, "EVALUATED", {"results_digest": sealed["digest"]})
    return {"evaluation": evaluation, "surprises": surprises, "claim": claim, "results": sealed}
