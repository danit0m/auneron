"""
Oracle v2.2 (SIM-1.5E, D-1.5E-1): correcao de ADMISSIBILIDADE TEMPORAL (TA-1).

REVISAO SOMENTE DO ORACLE, derivada do Oracle v2.1 (que permanece IMUTAVEL). A agenda publica e o mundo v2 sao
reutilizados POR REFERENCIA (mesma prova de sha256 do v2.1); `scenario_id` e seed nao mudam.

TA-1: uma expectativa so pode exigir fatos cujo INSTANTE EFETIVO seja menor ou igual ao INSTANTE DE OBSERVACAO da sua
coleta. O instante efetivo vem de um REGISTRO por tipo de expectativa (`RULES`): tipo sem regra => erro de geracao.
A regra e GERAL: nenhum `exp_id` aparece neste modulo (os casos conhecidos sao regressoes nos testes).

Para cada violacao, o gerador precisa PROVAR mecanicamente uma de tres coisas (nunca silencia):
  1. observavel no proximo `safe_at`           -> expectativa RETEMPORIZADA (valor recalculado no novo snapshot);
  2. semanticamente superada antes dele         -> `not_collected` + `reason_code` deterministico;
  3. nenhuma das duas demonstravel              -> geracao FAIL-CLOSED.
O `exp_id` e HERDADO do v2.1; as 13.074 expectativas se conservam (relatorio `conservation`).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import read_json
from sim.generator.canonical import sha256_bytes
from sim.generator.timeline import to_hhmm
from sim.generator.timeline import to_minutes
from sim.oracle import rules
from sim.oracle import v2_1

ORACLE_VERSION = "v2.2"
ORACLE_SCHEMA = "sim.oracle.v2.2"
GENERATOR_VERSION = "sim-1.5.1"
TA1_RULE = "temporal_admissibility_v1"
CLAIM_V2_2 = (
    "SIMULATION RESULT over a known synthetic population, valid only after an evaluated run. "
    "Never OPERATIONALLY OBSERVED."
)
DAY = 1440
EFFECT_LAG_MIN = 30           # regra ja existente em BuilderV2._effect_day (minute < snapshot - 30)

# razoes deterministicas
REASON_SUPERSEDED_BEFORE_NEXT = "SUPERSEDED_BEFORE_NEXT_SAFE_AT"
REASON_SUPERSEDED_SAME_DAY = "SUPERSEDED_SAME_DAY"
REASON_NOT_REPRESENTABLE = "NOT_REPRESENTABLE"

# disposicoes da conservacao
D_UNCHANGED = "UNCHANGED"
D_RETIMED = "RETIMED"
D_NOT_COLLECTED_TA1 = "NOT_COLLECTED_TA1"
D_RESTORED = "RESTORED_COLLECTIBLE"
D_PRE_EXISTING = "PRE_EXISTING_NOT_COLLECTED"


class TemporalAdmissibilityError(ValueError):
    """Geracao FAIL-CLOSED: violacao de TA-1 que nao se consegue classificar mecanicamente."""


def absolute(instant) -> int:
    """(dia, minuto) -> minutos absolutos desde D0 00:00."""
    return int(instant[0]) * DAY + int(instant[1])


def from_absolute(value: int) -> tuple:
    return value // DAY, value % DAY


def instant_doc(value: int) -> dict:
    day, minute = from_absolute(value)
    return {"day": day, "at": to_hhmm(minute)}


# ---------------------------------------------------------------------------------------------------------------------
# contexto e registro de regras
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Context:
    world: object
    snapshot: int
    last_day: int

    def __post_init__(self) -> None:
        self.titles = {title.ref: title for title in self.world.titles}
        self.governed = {request["ref"]: request for request in self.world.governed}

    def title(self, item: dict):
        ref = item["subject"].get("rec_ref")
        if ref not in self.titles:
            raise TemporalAdmissibilityError(f"{item['exp_id']}: titulo ausente no mundo ({ref})")
        return self.titles[ref]

    def request(self, item: dict) -> dict:
        ref = item["subject"].get("ref")
        if ref not in self.governed:
            raise TemporalAdmissibilityError(f"{item['exp_id']}: pedido governado ausente no mundo ({ref})")
        return self.governed[ref]


@dataclass(frozen=True)
class Rule:
    name: str                                                    # nome estavel (entra no sha256 do registro)
    effective: Callable | None = None                            # (ctx, item) -> minutos absolutos | None (sem ancora)
    value_at: Callable | None = None                             # (ctx, item, day) -> valor esperado no snapshot de `day`
    superseded_by: Callable | None = None                        # (ctx, item, effective, next_obs) -> instante | None


def _item_instant(ctx: Context, item: dict) -> int:
    return item["day"] * DAY + (to_minutes(item["at"]) if "at" in item else ctx.snapshot)


def _status_entries(title, item: dict, ctx: Context) -> list:
    return [at for at, status in title.status_log if status == item["policy"] and absolute(at) <= item["day"] * DAY + DAY - 1]


def _account_status_effective(ctx: Context, item: dict) -> int:
    entries = _status_entries(ctx.title(item), item, ctx)
    if not entries:
        raise TemporalAdmissibilityError(f"{item['exp_id']}: nenhuma transicao do mundo produz {item['policy']!r}")
    return max(absolute(at) for at in entries)


def _account_status_value(ctx: Context, item: dict, day: int):
    return ctx.title(item).status_at((day, ctx.snapshot))


def _account_status_superseded(ctx: Context, item: dict, effective: int, next_obs: int):
    for at, status in ctx.title(item).status_log:
        if effective < absolute(at) <= next_obs and status != item["policy"]:
            return absolute(at), status
    return None


def _request_instant(field: str) -> Callable:
    def effective(ctx: Context, item: dict) -> int:
        return absolute(ctx.request(item)[field])
    return effective


def _expiry_effective(ctx: Context, item: dict) -> int:
    return absolute(ctx.request(item)["expires"])


def _work_item_effective(ctx: Context, item: dict) -> int:
    title = ctx.title(item)
    matches = [w["created"] for w in title.workitems
               if w["venc"] == item["subject"]["vencimento_day"] and w["created"][0] == item["day"]]
    if not matches:
        raise TemporalAdmissibilityError(f"{item['exp_id']}: escalonamento ausente no mundo")
    return min(absolute(at) for at in matches)


def _effect_effective(ctx: Context, item: dict) -> int:
    request = ctx.request(item)
    candidates = []
    for execution in request["executions"]:
        if execution["result"] != "succeeded":
            continue
        day, minute = execution["at"]
        effect_day = day if minute < ctx.snapshot - EFFECT_LAG_MIN else min(day + 1, ctx.last_day)
        if effect_day == item["day"]:
            candidates.append(absolute(execution["at"]) + EFFECT_LAG_MIN)
    if not candidates:
        raise TemporalAdmissibilityError(f"{item['exp_id']}: nenhuma execucao bem-sucedida explica o efeito")
    return max(candidates)


def _reported_effective(ctx: Context, item: dict) -> int | None:
    title = ctx.title(item)
    return absolute(title.reported) if title.reported is not None else None


def _contacts_effective(ctx: Context, item: dict) -> int | None:
    contacts = ctx.title(item).contacts
    return max(absolute(c["at"]) for c in contacts) if contacts else None


def _paid_effective(ctx: Context, item: dict) -> int:
    paid = [at for at, status in ctx.title(item).status_log if status == "pago"]
    if not paid:
        raise TemporalAdmissibilityError(f"{item['exp_id']}: titulo nunca ficou pago no mundo")
    return max(absolute(at) for at in paid)


def _at_item(ctx: Context, item: dict) -> int:
    if "at" not in item:
        raise TemporalAdmissibilityError(f"{item['exp_id']}: expectativa de slot sem instante")
    return _item_instant(ctx, item)


def _aggregate(ctx: Context, item: dict) -> int:
    return item["day"] * DAY                                   # agregado de fim de run: ancora = inicio do dia declarado


SNAPSHOT_DERIVED = Rule("snapshot_derived")                      # calculada em (dia, snapshot) por construcao
EXEMPT = Rule("exempt")

RULES: dict = {
    "account_status": Rule("transition_instant", _account_status_effective, _account_status_value,
                           _account_status_superseded),
    "lifecycle_state": SNAPSHOT_DERIVED,
    "classification": SNAPSHOT_DERIVED,
    "behavior_pattern_present": SNAPSHOT_DERIVED,
    "knowledge_transition": SNAPSHOT_DERIVED,
    "governed_request_exists": Rule("request_created", _request_instant("created")),
    "governed_decision": Rule("request_decided", _request_instant("decided_at")),
    "effective_expiry": Rule("request_expires", _expiry_effective),
    "escalation_work_item_exists": Rule("work_item_created", _work_item_effective),
    "business_effect_verified": Rule("execution_plus_effect_lag", _effect_effective),
    "observed_fact_count": Rule("title_reported", _reported_effective),
    "observed_fact_links_event": Rule("title_reported", _reported_effective),
    "human_assessment_count": Rule("last_contact", _contacts_effective),
    "account_event_count": Rule("paid_instant", _paid_effective),
    "mark_overdue_eligible": Rule("at_item", _at_item),
    "escalation_eligible": Rule("at_item", _at_item),
    "nba_decision": Rule("at_item", _at_item),
    "nba_applied_rules": Rule("at_item", _at_item),
    "governed_execution": Rule("at_item", _at_item),
    "authority_violation_rejected": Rule("at_item", _at_item),
    "http_status": Rule("at_item", _at_item),
    "must_not_exist": Rule("end_of_run_aggregate", _aggregate),
    "no_duplicate_effect": Rule("end_of_run_aggregate", _aggregate),
    "driver_due_date_change_accepted_count": Rule("end_of_run_aggregate", _aggregate),
    "provenance_aggregate": Rule("end_of_run_aggregate", _aggregate),
    "provenance_present": Rule("end_of_run_aggregate", _aggregate),
    "not_representable": EXEMPT,
}


def registry_sha256() -> str:
    """Impressao digital do REGISTRO (kind -> nome da regra, e quais tem retemporizacao)."""
    doc = {kind: {"rule": rule.name, "retimes": rule.value_at is not None} for kind, rule in sorted(RULES.items())}
    return sha256_bytes(canonical_bytes({"ta1": TA1_RULE, "registry": doc, "effect_lag_min": EFFECT_LAG_MIN}))


def check_registry_complete() -> None:
    missing = sorted(set(v2_1._BY_KIND) - set(RULES))
    extra = sorted(set(RULES) - set(v2_1._BY_KIND))
    if missing or extra:
        raise TemporalAdmissibilityError(f"registro TA-1 incompleto: sem regra {missing}; regra sem kind {extra}")


# ---------------------------------------------------------------------------------------------------------------------
# instante de observacao e verificador (INDEPENDENTE da transformacao)
# ---------------------------------------------------------------------------------------------------------------------
def observation_instant(ctx: Context, item: dict) -> int | None:
    safe = item["safe_at"]
    if safe == "not_collected":
        return None
    if safe == "after_daily_barrier":
        return item["day"] * DAY + ctx.snapshot
    if safe in ("pre_slot", "in_slot", "post_slot"):
        return _at_item(ctx, item)
    if safe == "end_of_run":
        return ctx.last_day * DAY + DAY - 1
    raise TemporalAdmissibilityError(f"{item['exp_id']}: safe_at sem instante de observacao: {safe!r}")


def violations(items: list, ctx: Context) -> list:
    """Itens que violam TA-1 (efetivo > observacao). Lista vazia = admissivel. Nao altera nada."""
    check_registry_complete()
    found = []
    for item in items:
        rule = RULES.get(item["kind"])
        if rule is None:
            raise TemporalAdmissibilityError(f"{item['exp_id']}: kind sem regra TA-1: {item['kind']}")
        observation = observation_instant(ctx, item)
        if observation is None or rule.effective is None:
            continue
        effective = rule.effective(ctx, item)
        if effective is None:
            continue
        if effective > observation:
            found.append({"exp_id": item["exp_id"], "kind": item["kind"], "day": item["day"],
                          "effective": effective, "observation": observation})
    return found


# ---------------------------------------------------------------------------------------------------------------------
# transformacao v2.1 -> v2.2
# ---------------------------------------------------------------------------------------------------------------------
def _next_observable_day(ctx: Context, effective: int) -> int:
    day, minute = from_absolute(effective)
    return day if minute <= ctx.snapshot else day + 1


def _retime(ctx: Context, item: dict, violation: dict) -> tuple:
    """-> ("RETIMED", novo_item) | ("NOT_COLLECTED", novo_item). Levanta TemporalAdmissibilityError se nao provavel."""
    rule = RULES[item["kind"]]
    if item["safe_at"] != "after_daily_barrier" or rule.value_at is None or rule.superseded_by is None:
        raise TemporalAdmissibilityError(
            f"{item['exp_id']}: {item['kind']}/{item['safe_at']} viola TA-1 e nao tem regra de retemporizacao")
    effective = violation["effective"]
    target = _next_observable_day(ctx, effective)
    if target > ctx.last_day:
        raise TemporalAdmissibilityError(
            f"{item['exp_id']}: nao ha snapshot posterior ao fato efetivo (dia {target} > {ctx.last_day})")
    next_obs = target * DAY + ctx.snapshot
    new = copy.deepcopy(item)
    if rule.value_at(ctx, item, target) == item["policy"]:
        new["day"] = target
        new["retimed"] = {"rule": "TA-1", "v2_1_day": item["day"], "to_day": target,
                          "effective_at": instant_doc(effective), "next_safe_at": instant_doc(next_obs)}
        return D_RETIMED, new
    superseded = rule.superseded_by(ctx, item, effective, next_obs)
    if superseded is None:
        raise TemporalAdmissibilityError(
            f"{item['exp_id']}: valor muda no proximo snapshot sem transicao que o explique (nao demonstravel)")
    new["safe_at"] = "not_collected"
    new["ta1"] = {"disposition": "NOT_COLLECTED", "reason_code": REASON_SUPERSEDED_BEFORE_NEXT,
                  "effective_at": instant_doc(effective), "next_safe_at": instant_doc(next_obs),
                  "superseded_by": {**instant_doc(superseded[0]), "status": superseded[1]}}
    return D_NOT_COLLECTED_TA1, new


def _recompute_supersession(items: list, touched: set, ctx: Context) -> tuple:
    """Reavalia a supersessao do mesmo dia (`superseded_same_day`) so nos grupos (titulo, dia) afetados pelo TA-1.
    O item que vale e o de MAIOR instante efetivo (empate: o ultimo da lista). Devolve (restaurados, rebaixados):
    nada muda de coletavel para nao coletavel, nem o contrario, sem disposicao explicita."""
    groups: dict = {}
    for index, item in enumerate(items):
        if item["kind"] == "account_status" and (item["subject"]["rec_ref"], item["day"]) in touched                 and item.get("ta1", {}).get("disposition") != "NOT_COLLECTED":
            groups.setdefault((item["subject"]["rec_ref"], item["day"]), []).append(index)
    restored, demoted = [], []
    for indexes in groups.values():
        keep = max(indexes, key=lambda i: (_account_status_effective(ctx, items[i]), i))
        for index in indexes:
            item = items[index]
            if index == keep and item.get("superseded_same_day"):
                item["safe_at"] = "after_daily_barrier"
                del item["superseded_same_day"]
                item["ta1_restored"] = {"rule": "TA-1", "reason_code": "SUPERSEDING_ITEM_RETIMED"}
                restored.append(index)
            elif index != keep and not item.get("superseded_same_day"):
                item["safe_at"] = "not_collected"
                item["superseded_same_day"] = True
                item["ta1"] = {"disposition": "NOT_COLLECTED", "reason_code": REASON_SUPERSEDED_SAME_DAY}
                demoted.append(index)
    return restored, demoted


def apply_ta1(v2_1_items: list, ctx: Context) -> tuple:
    """Funcao PURA: itens v2.1 -> (itens v2.2, relatorio de conservacao). Falha fechado."""
    check_registry_complete()
    items = copy.deepcopy(v2_1_items)
    ids = [item["exp_id"] for item in items]
    if len(set(ids)) != len(ids):
        raise TemporalAdmissibilityError("exp_id duplicado na entrada")
    dispositions: dict = {}
    touched: set = set()
    for violation in violations(items, ctx):
        index = ids.index(violation["exp_id"])
        disposition, new = _retime(ctx, items[index], violation)
        touched.add((items[index]["subject"].get("rec_ref"), items[index]["day"]))
        touched.add((new["subject"].get("rec_ref"), new["day"]))
        dispositions[violation["exp_id"]] = disposition
        items[index] = new
    restored, demoted = _recompute_supersession(items, touched, ctx)
    for index in restored:
        dispositions[items[index]["exp_id"]] = D_RESTORED
    for index in demoted:
        dispositions[items[index]["exp_id"]] = D_NOT_COLLECTED_TA1
    remaining = violations(items, ctx)
    if remaining:
        raise TemporalAdmissibilityError(f"TA-1 ainda violada apos a transformacao: {[v['exp_id'] for v in remaining]}")
    # itens que passaram a depender de `requires`/supersessao nao podem ter mudado de forma silenciosa
    return items, conservation_report(v2_1_items, items, dispositions)


def _pre_existing_reason(item: dict) -> str:
    if item.get("superseded_same_day"):
        return REASON_SUPERSEDED_SAME_DAY
    if item["kind"] == "not_representable":
        return REASON_NOT_REPRESENTABLE
    raise TemporalAdmissibilityError(f"{item['exp_id']}: not_collected sem razao conhecida")


def conservation_report(before: list, after: list, dispositions: dict) -> dict:
    """Cada expectativa v2.1 tem UMA disposicao explicita no v2.2. Nada some."""
    if [i["exp_id"] for i in before] != [i["exp_id"] for i in after]:
        raise TemporalAdmissibilityError("conservacao: ids/ordem diferem entre v2.1 e v2.2")
    counts: dict = {}
    pre_reasons: dict = {}
    per_item: dict = {}
    for old, new in zip(before, after):
        exp_id = old["exp_id"]
        disposition = dispositions.get(exp_id)
        if disposition is None:
            if old["safe_at"] == "not_collected":
                disposition = D_PRE_EXISTING
                pre_reasons[_pre_existing_reason(old)] = pre_reasons.get(_pre_existing_reason(old), 0) + 1
            else:
                disposition = D_UNCHANGED
                if new != old:
                    raise TemporalAdmissibilityError(f"{exp_id}: item mudou sem disposicao explicita")
        counts[disposition] = counts.get(disposition, 0) + 1
        per_item[exp_id] = disposition
    explicit = {k: v for k, v in per_item.items() if v in (D_RETIMED, D_NOT_COLLECTED_TA1, D_RESTORED)}
    return {
        "total_v2_1": len(before), "total_v2_2": len(after), "dispositions": dict(sorted(counts.items())),
        "pre_existing_not_collected_by_reason": dict(sorted(pre_reasons.items())),
        "explicit": dict(sorted(explicit.items())),
        "ids_sha256": sha256_bytes(canonical_bytes([i["exp_id"] for i in after])),
        "sum_check": sum(counts.values()) == len(before) == len(after),
    }


# ---------------------------------------------------------------------------------------------------------------------
# geracao dos artefatos
# ---------------------------------------------------------------------------------------------------------------------
def _build_world(variant: str, seed: int):
    """Mesma sequencia de `generate_oracle_v2_1` (o mundo v2 reutilizado por referencia)."""
    from sim.generator.cases_v2 import InjectorV2
    from sim.generator.cases_v2 import inject_agenda_v2
    from sim.generator.cases_v2 import restart_in_flight_v2
    from sim.generator.config import load_inputs_v2
    from sim.generator.world_v2 import SimulatorV2

    inputs = load_inputs_v2()
    spec = inputs.variant(variant)
    scenario_id = f"{variant}-v2-s{seed}"
    world = SimulatorV2(inputs, variant, seed, InjectorV2(inputs, variant, seed), scenario_id).run()
    agenda_cases = inject_agenda_v2(world, inputs, int(spec["min_case_instances"]))
    world.replayed = set(agenda_cases.get("C-ADV-2", []))
    world.duplicate_settled = set(agenda_cases.get("C-ADV-1", []) + agenda_cases.get("C-ADV-3", []))
    world.restart_in_flight = restart_in_flight_v2(world, inputs)
    return inputs, world, scenario_id


def generate_oracle_v2_2(variant: str, seed: int, scenarios_dir, v21_dir=None) -> tuple:
    """-> (nome_do_diretorio, {arquivo: bytes}). O v2.1 e REGENERADO (com a prova de reuso por referencia do v2.1) e,
    se `v21_dir` existir, comparado byte a byte com o commitado antes de qualquer derivacao."""
    check_registry_complete()
    v21_name, v21_files = v2_1.generate_oracle_v2_1(variant, seed, scenarios_dir)
    if v21_dir is not None:
        for name, data in v21_files.items():
            if Path(v21_dir, name).read_bytes() != data:
                raise RuntimeError(f"{name} regenerado do v2.1 difere do artefato commitado: v2.2 nao deriva dele")
    inputs, world, scenario_id = _build_world(variant, seed)
    ctx = Context(world, to_minutes(inputs.calendar.intraday["snapshot"]), inputs.calendar.last_day)
    v21_oracle = json.loads(v21_files["oracle.json"].decode("utf-8"))
    v21_manifest = json.loads(v21_files["manifest_v2_1.json"].decode("utf-8"))
    items, conservation = apply_ta1(v21_oracle["expectations"], ctx)

    oracle = copy.deepcopy(v21_oracle)
    oracle["expectations"] = items
    oracle["oracle_version"] = ORACLE_VERSION
    oracle["schema"] = ORACLE_SCHEMA
    oracle["claim"] = CLAIM_V2_2
    oracle["rule_versions"] = {**oracle["rule_versions"], "temporal_admissibility": TA1_RULE}
    oracle["claim_rules"] = list(oracle["claim_rules"]) + [
        "an expectation may only require facts effective at or before its observation instant (TA-1)",
    ]
    oracle["derived_from"] = {"oracle_version": v2_1.ORACLE_VERSION,
                              "oracle.json": sha256_bytes(v21_files["oracle.json"]),
                              "manifest_v2_1.json": sha256_bytes(v21_files["manifest_v2_1.json"])}
    oracle["temporal_admissibility"] = {"rule": TA1_RULE, "registry_sha256": registry_sha256(),
                                        "conservation": conservation}
    oracle_bytes = canonical_bytes(oracle)
    manifest = {
        "artifact": "manifest_v2_2",
        "oracle_version": ORACLE_VERSION,
        "schema": ORACLE_SCHEMA,
        "execution_scenario_id": scenario_id,
        "generator_version": GENERATOR_VERSION,
        "claim": CLAIM_V2_2,
        "derived_from": oracle["derived_from"],
        "reuses_by_reference": v21_manifest["reuses_by_reference"],
        "transformation": {"id": TA1_RULE, "registry_sha256": registry_sha256(),
                           "retimed": sorted(k for k, v in conservation["explicit"].items() if v == D_RETIMED),
                           "ta1_not_collected": sorted(k for k, v in conservation["explicit"].items()
                                                       if v == D_NOT_COLLECTED_TA1),
                           "restored_collectible": sorted(k for k, v in conservation["explicit"].items()
                                                          if v == D_RESTORED),
                           "dispositions": conservation["dispositions"]},
        "artifacts": {"oracle.json": sha256_bytes(oracle_bytes)},
        "inputs": v21_manifest["inputs"],
        "expectations": len(items),
    }
    directory = f"{scenario_id}-oracle-{ORACLE_VERSION}"
    return directory, {"oracle.json": oracle_bytes, "manifest_v2_2.json": canonical_bytes(manifest)}


def default_artifacts_dir() -> Path:
    return v2_1.default_artifacts_dir()


def write_oracle_v2_2(variant: str, seed: int, scenarios_dir, out_dir=None) -> Path:
    base = Path(out_dir or default_artifacts_dir())
    v21_dir = base / f"{variant}-v2-s{seed}-oracle-v2.1"
    directory, files = generate_oracle_v2_2(variant, seed, scenarios_dir, v21_dir if v21_dir.exists() else None)
    target = base / directory
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    return target


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="sim.oracle.v2_2")
    parser.add_argument("--variant", default="nh-small")
    parser.add_argument("--seed", type=int, default=340001)
    parser.add_argument("--scenarios", type=Path, default=Path(__file__).resolve().parents[1] / "scenarios")
    args = parser.parse_args(argv)
    print(write_oracle_v2_2(args.variant, args.seed, args.scenarios))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
