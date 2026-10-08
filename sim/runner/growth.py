"""
Modelo de crescimento (SIM-1.5F D.4 + emenda C do PO): uma ESTIMATIVA conservadora, nunca prova de capacidade.

Entrada: os registros diarios VERIFICADOS (cadeia) de um run de ENSAIO. Saida: `growth_model.json` com amostras, unidade,
periodo, premissas, margem de seguranca e limitacoes declaradas. Dado ausente, invalido, fora de ordem ou insuficiente
REPROVA a geracao (`GrowthModelError`); nada vira zero em silencio. O `sha256` do arquivo entra no RIM do NH (`RUN`), que so
abre com modelo VALIDADO e espaco suficiente (`disk.judge_preflight`).

Projecao por componente (bytes):  v_primeiro + max(taxa/op x ops_NH, taxa/dia x dias_NH, taxa/hora x horas_NH);
total do host = max(projecao do VHDX, soma dos componentes x sobrecarga observada do VHDX); depois x margem de seguranca.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import daily
from sim.runner.errors import RunnerError

GROWTH_SCHEMA = "sim.run.growth_model.v1"
UNIT = "bytes"
SAFETY_PERMILLE = 1500
MIN_SAMPLES = 15
NH_REAL_HOURS = 11                                # premissa: ~10-10,5 h de run + folga (declarada, nao medida)
COMPONENTS = ("db", "state_driver", "state_collector", "evidence", "logs")
LIMITATIONS = (
    "estimativa linear a partir de UM ensaio tecnico; nao e prova de capacidade",
    "a primeira amostra (D0, depois das operacoes do D0) e usada como linha de base: custos anteriores nao medidos ficam fora",
    "nao cobre endurance de ~10 h nem deriva de longo prazo (SR-2 condicional)",
    "logs sem rotacao: o crescimento observado so vale para o volume de requisicoes do ensaio",
    "a fixture nao tem o conteudo empresarial do NH (tamanho de linhas pode diferir)",
)
ASSUMPTIONS = (
    "taxa por operacao, por dia virtual e por hora real; vale o maior dos tres para cada componente",
    "o VHDX do Docker cresce e nao encolhe: sobrecarga = crescimento do VHDX / soma dos componentes (minimo 1,0)",
    f"duracao real do NH assumida em {NH_REAL_HOURS} h",
)


class GrowthModelError(RunnerError):
    pass


def _int_series(records: list, getter, name: str) -> list:
    out = []
    for record in records:
        value = getter(record)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise GrowthModelError(f"serie `{name}` invalida no dia {record.get('day')}: {value!r}")
        out.append(value)
    return out


def _ops_done(record: dict) -> int:
    counts = record["facts"]["driver"]["counts"]
    return counts.get("done", 0) + counts.get("reconciled", 0)


def _components(record: dict) -> dict:
    g = record["facts"]["growth"]
    return {"db": g["db_bytes"], "state_driver": g["state_bytes"].get("driver"), "state_collector": g["state_bytes"].get("collector"),
            "evidence": g["evidence_bytes"], "logs": sum(g["log_bytes"].values())}


def build_growth_model(run_dir, *, nh_targets: dict, real_hours: float, source_run_id: str, package_digest: str) -> dict:
    """`nh_targets`: {"ops": int, "accounts": int, "days": int} (inteiros do perfil do NH). `real_hours`: duracao real do ensaio."""
    run_dir = Path(run_dir)
    files = sorted((run_dir / "days").glob("D*.json"))
    if len(files) < MIN_SAMPLES:
        raise GrowthModelError(f"amostras insuficientes: {len(files)} < {MIN_SAMPLES}")
    records = [json.loads(f.read_bytes().decode("utf-8")) for f in files]
    try:
        daily.verify_day_chain(records)
    except daily.DayRecordError as error:
        raise GrowthModelError(f"cadeia diaria invalida: {error}") from error
    for record in records:
        problems = daily.growth_problems(record["facts"]["growth"])
        if problems:
            raise GrowthModelError(f"dia {record['day']}: crescimento invalido: {problems}")
    for key in ("ops", "accounts", "days"):
        if not isinstance(nh_targets.get(key), int) or nh_targets[key] <= 0:
            raise GrowthModelError(f"alvo do NH invalido: {key}")
    if not isinstance(real_hours, (int, float)) or real_hours <= 0:
        raise GrowthModelError("duracao real invalida")
    ops = _int_series(records, _ops_done, "ops")
    if ops[-1] <= ops[0]:
        raise GrowthModelError("o ensaio nao executou operacoes entre a primeira e a ultima amostra")
    virtual_days = len(records) - 1
    series = {name: _int_series(records, lambda r, n=name: _components(r)[n], name) for name in COMPONENTS}
    vhdx = _int_series(records, lambda r: r["facts"]["growth"]["vhdx_bytes"], "vhdx")
    free = _int_series(records, lambda r: r["facts"]["disk"]["free_bytes"], "free")
    components = {}
    for name, values in series.items():
        delta = max(values[-1] - values[0], 0)
        increment = max(delta * nh_targets["ops"] // (ops[-1] - ops[0]), delta * nh_targets["days"] // max(virtual_days, 1),
                        int(delta * NH_REAL_HOURS / real_hours))
        components[name] = {"first": values[0], "last": values[-1], "delta": delta,
                            "per_op_millibytes": delta * 1000 // (ops[-1] - ops[0]),
                            "projected_nh_bytes": values[0] + increment, "projected_nh_increment_bytes": increment}
    component_delta = sum(c["delta"] for c in components.values())
    vhdx_delta = max(vhdx[-1] - vhdx[0], 0)
    overhead_permille = max(1000, vhdx_delta * 1000 // component_delta) if component_delta else 1000
    by_components = sum(c["projected_nh_increment_bytes"] for c in components.values()) * overhead_permille // 1000
    by_vhdx = max(vhdx_delta * nh_targets["ops"] // (ops[-1] - ops[0]), vhdx_delta * nh_targets["days"] // max(virtual_days, 1),
                  int(vhdx_delta * NH_REAL_HOURS / real_hours))
    estimate = max(by_components, by_vhdx)
    model = {
        "schema": GROWTH_SCHEMA, "status": "ESTIMATE", "capacity_proof": False, "unit": UNIT,
        "source": {"run_id": source_run_id, "package_digest": package_digest},
        "period": {"samples": len(records), "virtual_days": virtual_days, "ops": ops[-1] - ops[0], "real_hours_x100": int(real_hours * 100)},
        "nh_targets": dict(nh_targets), "components": components,
        "host": {"vhdx_first": vhdx[0], "vhdx_last": vhdx[-1], "vhdx_delta": vhdx_delta, "overhead_permille": overhead_permille,
                 "free_first": free[0], "free_last": free[-1], "estimate_by_components_bytes": by_components,
                 "estimate_by_vhdx_bytes": by_vhdx},
        "safety_margin_permille": SAFETY_PERMILLE,
        "projected_growth_bytes": estimate * SAFETY_PERMILLE // 1000,
        "assumptions": list(ASSUMPTIONS), "limitations": list(LIMITATIONS),
    }
    return validate_growth_model(model)


def validate_growth_model(model: dict) -> dict:
    """Valida a estrutura e RECALCULA o total a partir dos componentes (adulteracao ou campo ausente => erro)."""
    required = ("schema", "status", "capacity_proof", "unit", "source", "period", "nh_targets", "components", "host",
                "safety_margin_permille", "projected_growth_bytes", "assumptions", "limitations")
    missing = [k for k in required if k not in model]
    if missing:
        raise GrowthModelError(f"modelo incompleto: {missing}")
    if model["schema"] != GROWTH_SCHEMA or model["unit"] != UNIT or model["status"] != "ESTIMATE" or model["capacity_proof"] is not False:
        raise GrowthModelError("o modelo e uma ESTIMATIVA em bytes (schema/status/capacity_proof)")
    if model["period"].get("samples", 0) < MIN_SAMPLES:
        raise GrowthModelError("amostras insuficientes no modelo")
    if not model["assumptions"] or not model["limitations"]:
        raise GrowthModelError("premissas e limitacoes devem estar declaradas")
    if set(model["components"]) != set(COMPONENTS):
        raise GrowthModelError("componentes do modelo incompletos")
    for name, c in model["components"].items():
        for key in ("first", "last", "delta", "projected_nh_bytes", "projected_nh_increment_bytes"):
            if not isinstance(c.get(key), int) or isinstance(c.get(key), bool) or c[key] < 0:
                raise GrowthModelError(f"componente {name}.{key} invalido")
    margin = model["safety_margin_permille"]
    if not isinstance(margin, int) or margin < 1000:
        raise GrowthModelError("margem de seguranca invalida (minimo 1000 permil)")
    host = model["host"]
    by_components = sum(c["projected_nh_increment_bytes"] for c in model["components"].values()) * host["overhead_permille"] // 1000
    estimate = max(by_components, host["estimate_by_vhdx_bytes"])
    if host["estimate_by_components_bytes"] != by_components or model["projected_growth_bytes"] != estimate * margin // 1000:
        raise GrowthModelError("total do modelo difere do recalculo (adulterado ou inconsistente)")
    return model


def model_bytes(model: dict) -> bytes:
    return canonical_bytes(validate_growth_model(model))


def model_sha256(model: dict) -> str:
    return sha256_bytes(model_bytes(model))


def per_virtual_day_bytes(model: dict, days: int = 180) -> int:
    return validate_growth_model(model)["projected_growth_bytes"] // days
