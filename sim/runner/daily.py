"""
Registro diario (`days/D###.json`, SIM-1.5E B.5) e seu julgamento. Um checkpoint NAO e ponto de resume (R0): e ponto
de VERIFICACAO. O julgamento e PURO e independe do Oracle: usa so contagens do produto, da agenda PUBLICA, do Driver e
do Collector (balanco de massa), alem de relogio, barreira, O-1, topologia, identidade, disco e autoridade.

Fato ausente = falha (fail-closed). Cada violacao carrega uma CLASSE da taxonomia; o runner escolhe o estado terminal
(INVALID prevalece sobre ABORTED).
"""

from __future__ import annotations

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import disk as disk_mod
from sim.runner.errors import RunnerError

DAY_SCHEMA = "sim.run.day.v1"
GENESIS = "0" * 64
REQUIRED = {
    "time": ("real_open", "real_close", "virtual_open", "virtual_close", "clock_skew_ms", "barrier_total_ms",
             "barrier_events_ok", "barrier_hard_ms", "o1_ms"),
    "driver": ("counts", "in_flight", "agenda_ops_through_day"),
    "collector": ("expected_items", "collected_items", "chain_ok", "missing", "problems"),
    "balance": (),                     # pares {nome: [observado_no_produto, esperado_pela_agenda_publica]}
    "infra": ("containers_ok", "unexpected_restarts", "topology_ok", "run_id_ok"),
    "disk": ("free_bytes",),
    "authority": ("status_lines", "chain_lines", "tripwire"),
    "logs": (),
}


class DayRecordError(RunnerError):
    pass


def record_hash(record: dict) -> str:
    return sha256_bytes(canonical_bytes({k: v for k, v in record.items() if k != "hash"}))


def missing_facts(facts: dict) -> list:
    out = []
    for group, keys in REQUIRED.items():
        if group not in facts:
            out.append(group)
            continue
        out += [f"{group}.{key}" for key in keys if key not in facts[group]]
    return out


def build_day_record(*, run_id: str, day: int, facts: dict, prev_hash: str, violations: list, warnings: list) -> dict:
    record = {"schema": DAY_SCHEMA, "run_id": run_id, "day": day, "prev": prev_hash, "facts": facts,
              "violations": violations, "warnings": warnings}
    record["hash"] = record_hash(record)
    return record


def verify_day_chain(records: list) -> None:
    tail = GENESIS
    for index, record in enumerate(records):
        if record.get("day") != index:
            raise DayRecordError(f"registros diarios fora de ordem ou faltando no indice {index}")
        if record.get("prev") != tail or record.get("hash") != record_hash(record):
            raise DayRecordError(f"cadeia diaria quebrada no dia {index}")
        tail = record["hash"]


def judge_day(facts: dict, *, disk_policy: dict, clock_skew_limit_ms: int, o1_limit_ms: int) -> tuple:
    """-> (violacoes [{class, detail}], avisos [str]). Vazio de violacoes = o dia pode ser fechado."""
    absent = missing_facts(facts)
    if absent:
        return [{"class": "HARNESS_ERROR", "detail": f"fatos do dia ausentes: {absent}"}], []
    violations: list = []
    warnings: list = []

    def fail(cls: str, detail: str) -> None:
        violations.append({"class": cls, "detail": detail})

    t = facts["time"]
    if t["barrier_events_ok"] is not True or t["barrier_total_ms"] is None or t["barrier_total_ms"] > t["barrier_hard_ms"]:
        fail("BARRIER_FAILURE", f"barreira: eventos_ok={t['barrier_events_ok']} total_ms={t['barrier_total_ms']} teto={t['barrier_hard_ms']}")
    if abs(t["clock_skew_ms"]) > clock_skew_limit_ms:
        fail("CLOCK_VIOLATION", f"skew {t['clock_skew_ms']} ms > {clock_skew_limit_ms} ms")
    if t["o1_ms"] > o1_limit_ms:
        fail("O1_THRESHOLD_REVIEW_REQUIRED", f"O-1 {t['o1_ms']} ms > {o1_limit_ms} ms (o limite nunca e relaxado)")
    d = facts["driver"]
    counts = d["counts"]
    if counts.get("failed", 0) or counts.get("sent", 0) or d["in_flight"]:
        fail("HARNESS_ERROR", f"Driver com ops failed/sent/em voo: {counts} em_voo={d['in_flight']}")
    terminal = counts.get("done", 0) + counts.get("reconciled", 0)
    if terminal != d["agenda_ops_through_day"]:
        fail("MASS_BALANCE_MISMATCH", f"ops terminais do Driver {terminal} != ops da agenda ate o dia {d['agenda_ops_through_day']}")
    c = facts["collector"]
    if (not c["chain_ok"] or c["missing"] or c["problems"] or c["collected_items"] != c["expected_items"]):
        fail("HARNESS_ERROR", f"Collector: cadeia_ok={c['chain_ok']} faltando={c['missing']} problemas={c['problems']} "
                              f"coletados={c['collected_items']}/{c['expected_items']}")
    for name, pair in sorted(facts["balance"].items()):
        observed, expected = pair
        if observed != expected:
            fail("MASS_BALANCE_MISMATCH", f"balanco `{name}`: produto={observed} agenda={expected}")
    i = facts["infra"]
    if not i["containers_ok"] or i["unexpected_restarts"]:
        fail("INFRA_FAILURE", f"containers: ok={i['containers_ok']} reinicios_nao_previstos={i['unexpected_restarts']}")
    if not i["topology_ok"]:
        fail("TOPOLOGY_VIOLATION", "topologia de rede fora do congelado")
    if not i["run_id_ok"]:
        fail("IDENTITY_DRIFT", "run_id do ledger != /state/RUN_ID dos containers ou run_id.json")
    a = facts["authority"]
    if a["tripwire"]:
        fail("AUTHORITY_OUTSIDE_C_AUTH_2", f"{len(a['tripwire'])} violacao(oes) publica(s) de autoridade: {a['tripwire'][:3]}")
    verdict = disk_mod.judge_day(facts["disk"]["free_bytes"], disk_policy)
    if verdict.level == disk_mod.ABORT:
        fail("DISK_LOW", "; ".join(verdict.reasons))
    elif verdict.level == disk_mod.WARN:
        warnings.append("DISK_WARNING: " + "; ".join(verdict.reasons))
    return violations, warnings
