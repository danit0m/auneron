"""
P2 -- MAINTENANCE_ENABLED gate. Prova a propriedade de seguranca
central do Application Recovery Smoke: com `maintenance_enabled=False`,
nenhuma das 11 operacoes recovery-once e chamada e nenhuma das 10
maintenance tasks e criada. O teste com `True` e so contraste/regressao
do comportamento ja existente -- a prova do `False` e que fecha P2.

Alvo e o proprio `lifespan()` de app.main, nunca os modulos individuais
de manutencao (nenhum deles e alterado por este gate). Mesmo idioma
assincrono ja usado em test_maintenance_loop_resilience.py: teste
sincrono dirigindo uma funcao interna `async def exercise()` via
asyncio.run(), sem pytest-asyncio/anyio.
"""

from __future__ import annotations

import asyncio

import pytest

from app import main as main_module
from app.core.config import settings as global_settings


# As 11 chamadas reais dentro do bloco `if database_online:` do
# lifespan() -- corrigido apos contagem mecanica do codigo atual (nao
# sao 10; check_production_developer_roles_async e a 11a). As duas
# primeiras sao chamadas via `asyncio.to_thread(func)` -- funcoes
# SINCRONAS no codigo real, nunca corrotinas; as demais 9 sao
# `await func()` diretamente.
SYNC_RECOVERY_ONCE_NAMES = (
    "run_auth_session_cleanup",
    "run_skill_invocation_recovery",
)

ASYNC_RECOVERY_ONCE_NAMES = (
    "run_work_skill_execution_recovery_async",
    "run_work_outcome_evaluation_recovery_async",
    "run_pilot_mutation_recovery_async",
    "run_overdue_detection_async",
    "run_advisory_dispatch_recovery_async",
    "run_client_behavior_memory_recalculation_async",
    "run_client_classification_recalculation_async",
    "run_receivables_monitor_async",
    "check_production_developer_roles_async",
)

RECOVERY_ONCE_NAMES = (
    SYNC_RECOVERY_ONCE_NAMES + ASYNC_RECOVERY_ONCE_NAMES
)

MAINTENANCE_LOOP_NAMES = (
    "auth_session_maintenance_loop",
    "skill_invocation_maintenance_loop",
    "work_skill_execution_maintenance_loop",
    "work_outcome_evaluation_maintenance_loop",
    "pilot_mutation_maintenance_loop",
    "advisory_dispatch_maintenance_loop",
    "overdue_detection_maintenance_loop",
    "client_behavior_memory_maintenance_loop",
    "client_classification_maintenance_loop",
    "receivables_monitor_maintenance_loop",
)


def _install_fakes(monkeypatch, calls: list[str]) -> None:
    def make_sync_recovery_once(name: str):
        def fake(*args, **kwargs):
            calls.append(name)

        return fake

    for name in SYNC_RECOVERY_ONCE_NAMES:
        monkeypatch.setattr(
            main_module, name, make_sync_recovery_once(name)
        )

    def make_async_recovery_once(name: str):
        async def fake(*args, **kwargs):
            calls.append(name)

        return fake

    for name in ASYNC_RECOVERY_ONCE_NAMES:
        monkeypatch.setattr(
            main_module, name, make_async_recovery_once(name)
        )

    def make_loop(name: str):
        async def fake_loop():
            calls.append(name)
            # nunca completa sozinho -- so cancelado pelo finally do
            # lifespan(); prova que a task foi de fato agendada sem
            # depender de um sleep real longo.
            await asyncio.Event().wait()

        return fake_loop

    for name in MAINTENANCE_LOOP_NAMES:
        monkeypatch.setattr(
            main_module, name, make_loop(name)
        )

    monkeypatch.setattr(
        main_module,
        "check_database_connection",
        lambda: True,
    )


def _run_lifespan_once() -> None:
    async def exercise() -> None:
        async with main_module.lifespan(main_module.app):
            # cede o loop uma vez para as tasks criadas rodarem seu
            # primeiro passo (append em calls) antes do cleanup.
            await asyncio.sleep(0)

    asyncio.run(exercise())


def test_maintenance_disabled_runs_zero_of_21_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _install_fakes(monkeypatch, calls)
    monkeypatch.setattr(
        global_settings, "maintenance_enabled", False
    )

    _run_lifespan_once()

    assert calls == []
    assert len(RECOVERY_ONCE_NAMES) + len(
        MAINTENANCE_LOOP_NAMES
    ) == 21


def test_maintenance_enabled_runs_all_21_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _install_fakes(monkeypatch, calls)
    monkeypatch.setattr(
        global_settings, "maintenance_enabled", True
    )

    _run_lifespan_once()

    assert set(calls) == set(RECOVERY_ONCE_NAMES) | set(
        MAINTENANCE_LOOP_NAMES
    )
    assert len(calls) == 21
