"""
Guarda de disco FAIL-CLOSED (D-1.5E-6). Julgamentos puros; a medicao e injetada (e qualquer falha de medicao reprova).

Preflight: livre >= max(minimo, fator x crescimento projetado + reserva). Um `RUN` SEM modelo de crescimento
medido (SR-1) NUNCA passa; `REHEARSAL` usa so o minimo absoluto (e o que gera o modelo).
Durante o run (em cada fechamento de dia, nunca no meio de um dia): aviso abaixo de `warn`, `ABORTED(DISK_LOW)` abaixo
de `abort`. Nenhuma limpeza automatica de nada, jamais.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from sim.runner.errors import DiskGuardError

OK, WARN, ABORT = "OK", "WARN", "ABORT"


@dataclass(frozen=True)
class DiskVerdict:
    level: str                   # OK | WARN | ABORT | FAIL (preflight reprovado)
    free_bytes: int | None
    required_bytes: int | None
    reasons: tuple

    @property
    def ok(self) -> bool:
        return self.level in (OK, WARN)


def measure_free(path, usage=shutil.disk_usage) -> int:
    """Bytes livres no drive de `path`. Qualquer erro => DiskGuardError (nunca 'sem dados = ok')."""
    try:
        value = usage(str(path)).free
    except Exception as error:                                       # noqa: BLE001 - fail-closed por desenho
        raise DiskGuardError(f"falha ao medir espaco livre: {error}") from error
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DiskGuardError(f"medida de espaco livre invalida: {value!r}")
    return value


def required_for_run(policy: dict, projected_growth_bytes: int | None, kind: str) -> int | None:
    if kind == "RUN":
        if projected_growth_bytes is None:
            return None
        grown = projected_growth_bytes * policy["growth_factor_permille"] // 1000
        return max(policy["preflight_min_bytes"], grown + policy["reserve_bytes"])
    return policy["preflight_min_bytes"]


def judge_preflight(free_bytes, policy: dict, projected_growth_bytes: int | None, kind: str) -> DiskVerdict:
    if not isinstance(free_bytes, int) or isinstance(free_bytes, bool) or free_bytes < 0:
        return DiskVerdict("FAIL", None, None, ("medida de espaco livre ausente ou invalida",))
    required = required_for_run(policy, projected_growth_bytes, kind)
    if required is None:
        return DiskVerdict("FAIL", free_bytes, None, ("RUN sem modelo de crescimento medido (SR-1): nao abre",))
    if free_bytes < required:
        return DiskVerdict("FAIL", free_bytes, required, (f"livre {free_bytes} < exigido {required}",))
    return DiskVerdict(OK, free_bytes, required, ())


MIN_PROJECTION_POINTS = 3


def project_free_at_end(free_history: list, remaining_days: int, growth_per_day_bytes: int | None = None) -> int | None:
    """Espaco livre PROJETADO no fim do run: livre atual - dias restantes x max(queda media observada, crescimento do modelo).
    Menos de 3 pontos e sem modelo => sem projecao (None): so vale o piso absoluto."""
    if not free_history or any((not isinstance(v, int)) or isinstance(v, bool) or v < 0 for v in free_history):
        raise DiskGuardError("historico de espaco livre invalido")
    observed = 0
    if len(free_history) >= MIN_PROJECTION_POINTS:
        observed = max((free_history[0] - free_history[-1]) // (len(free_history) - 1), 0)
    per_day = max(observed, growth_per_day_bytes or 0)
    if len(free_history) < MIN_PROJECTION_POINTS and not growth_per_day_bytes:
        return None
    return free_history[-1] - per_day * max(remaining_days, 0)


def judge_projection(projected_free_bytes: int | None, policy: dict) -> DiskVerdict:
    """Projecao abaixo da RESERVA OPERACIONAL => parada controlada com preservacao (antes de faltar disco)."""
    if projected_free_bytes is None:
        return DiskVerdict(OK, None, policy["reserve_bytes"], ())
    if projected_free_bytes < policy["reserve_bytes"]:
        return DiskVerdict(ABORT, projected_free_bytes, policy["reserve_bytes"],
                           (f"livre projetado no fim {projected_free_bytes} < reserva operacional {policy['reserve_bytes']}",))
    return DiskVerdict(OK, projected_free_bytes, policy["reserve_bytes"], ())


def judge_day(free_bytes, policy: dict) -> DiskVerdict:
    if not isinstance(free_bytes, int) or isinstance(free_bytes, bool) or free_bytes < 0:
        return DiskVerdict(ABORT, None, None, ("medida de espaco livre ausente ou invalida",))
    if free_bytes < policy["abort_bytes"]:
        return DiskVerdict(ABORT, free_bytes, policy["abort_bytes"], (f"livre {free_bytes} < abort {policy['abort_bytes']}",))
    if free_bytes < policy["warn_bytes"]:
        return DiskVerdict(WARN, free_bytes, policy["warn_bytes"], (f"livre {free_bytes} < aviso {policy['warn_bytes']}",))
    return DiskVerdict(OK, free_bytes, policy["warn_bytes"], ())
