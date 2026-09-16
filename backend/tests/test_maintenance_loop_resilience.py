"""
PR-1 -- Maintenance Loop Resilience / Observability. Testes obrigatorios
do Executable Contract (congelado com Tomaz em 16/09/2026): uma excecao
nao capturada em run_*_async nao mata o loop (evento *.maintenance_failed
emitido, proxima iteracao acontece); nenhum retry apertado (sleep
continua sendo chamado uma vez por ciclo, com o intervalo configurado);
CancelledError nao e convertido em erro operacional -- propaga
normalmente ao cancelar/aguardar a task.

Mesmo idioma assincrono ja usado em test_work_skill_maintenance.py/
test_work_outcome_evaluation_maintenance.py: teste sincrono com uma
funcao interna `async def exercise()`, dirigida via asyncio.run(),
sincronizada por polling curto -- este repositorio nao usa
pytest-asyncio/anyio em nenhum outro teste.

Escopo: somente os 6 loops que o Survey provou desprotegidos. Os 4 ja
protegidos (auth_session, skill_invocation, overdue_detection,
receivables_monitor) nao sao tocados nem testados aqui.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.core import authenticated_advisory_dispatch_maintenance
from app.core import client_behavior_memory_maintenance
from app.core import client_classification
from app.core import pilot_mutation_maintenance
from app.core import work_outcome_evaluation_maintenance
from app.core import work_skill_maintenance
from app.core.config import settings as global_settings


TEST_INTERVAL_SECONDS = 0.05

LOOP_CASES = [
    (
        pilot_mutation_maintenance,
        "pilot_mutation_maintenance_loop",
        "run_pilot_mutation_recovery_async",
        "work_skill_recovery_interval_seconds",
        "pilot.mutation.maintenance_failed",
    ),
    (
        work_skill_maintenance,
        "work_skill_execution_maintenance_loop",
        "run_work_skill_execution_recovery_async",
        "work_skill_recovery_interval_seconds",
        "work.skill_execution.maintenance_failed",
    ),
    (
        work_outcome_evaluation_maintenance,
        "work_outcome_evaluation_maintenance_loop",
        "run_work_outcome_evaluation_recovery_async",
        "work_skill_recovery_interval_seconds",
        "work.outcome_evaluation.maintenance_failed",
    ),
    (
        authenticated_advisory_dispatch_maintenance,
        "advisory_dispatch_maintenance_loop",
        "run_advisory_dispatch_recovery_async",
        "work_skill_recovery_interval_seconds",
        "advisory.dispatch.maintenance_failed",
    ),
    (
        client_behavior_memory_maintenance,
        "client_behavior_memory_maintenance_loop",
        "run_client_behavior_memory_recalculation_async",
        "client_behavior_recalculation_interval_seconds",
        "client_behavior_memory.maintenance_failed",
    ),
    (
        client_classification,
        "client_classification_maintenance_loop",
        "run_client_classification_recalculation_async",
        "client_classification_recalculation_interval_seconds",
        "client_classification.maintenance_failed",
    ),
]

LOOP_IDS = [
    case[1].removesuffix("_maintenance_loop") for case in LOOP_CASES
]


async def _wait_until(condition, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(
                "Tempo esgotado aguardando condição do teste."
            )
        await asyncio.sleep(0.005)


@pytest.mark.parametrize(
    "module, loop_name, run_async_name, interval_setting_name, "
    "expected_event",
    LOOP_CASES,
    ids=LOOP_IDS,
)
def test_loop_survives_exception_and_retries(
    monkeypatch,
    caplog,
    module,
    loop_name,
    run_async_name,
    interval_setting_name,
    expected_event,
) -> None:
    monkeypatch.setattr(
        global_settings,
        interval_setting_name,
        TEST_INTERVAL_SECONDS,
    )

    call_timestamps: list[float] = []

    async def fake_run_async() -> None:
        call_timestamps.append(time.monotonic())
        if len(call_timestamps) == 1:
            raise RuntimeError(
                "forced failure for PR-1 resilience test"
            )

    monkeypatch.setattr(module, run_async_name, fake_run_async)

    async def exercise() -> None:
        loop_coro = getattr(module, loop_name)
        task = asyncio.create_task(loop_coro())
        try:
            await _wait_until(lambda: len(call_timestamps) >= 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.ERROR):
        asyncio.run(exercise())

    assert len(call_timestamps) >= 2

    gap = call_timestamps[1] - call_timestamps[0]
    assert gap >= TEST_INTERVAL_SECONDS * 0.8, (
        "O segundo ciclo aconteceu cedo demais -- indício de retry "
        "apertado sem respeitar o intervalo configurado."
    )

    matching = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == expected_event
    ]
    assert len(matching) == 1
    assert matching[0].error_type == "RuntimeError"


@pytest.mark.parametrize(
    "module, loop_name, run_async_name, interval_setting_name, "
    "expected_event",
    LOOP_CASES,
    ids=LOOP_IDS,
)
def test_cancellation_is_not_treated_as_maintenance_failure(
    monkeypatch,
    caplog,
    module,
    loop_name,
    run_async_name,
    interval_setting_name,
    expected_event,
) -> None:
    monkeypatch.setattr(
        global_settings,
        interval_setting_name,
        TEST_INTERVAL_SECONDS,
    )

    entered = asyncio.Event()

    async def fake_run_async() -> None:
        entered.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(module, run_async_name, fake_run_async)

    async def exercise() -> None:
        loop_coro = getattr(module, loop_name)
        task = asyncio.create_task(loop_coro())
        await _wait_until(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.ERROR):
        asyncio.run(exercise())

    assert not any(
        getattr(record, "event", None) == expected_event
        for record in caplog.records
    )
