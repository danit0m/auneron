"""
P2 / VALUE-3.4B -- fronteira de ativacao do lifespan().

Dois gates independentes governam o lifespan() de app.main:

* MAINTENANCE_ENABLED -- gate GLOBAL: 13 acoes de recovery-once no
  startup + 12 loops periodicos (25 operacoes, nominais abaixo).
* ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR -- gate DEDICADO do
  evidence worker (observed_fact): floor=None => nao agendado; floor
  valido => somente a coleta (1 recovery-once + 1 loop), independente
  de MAINTENANCE_ENABLED. O evidence worker NAO pertence ao conjunto
  global.

A garantia principal NAO e uma contagem: (a) as operacoes descobertas
por AST no lifespan sao exatamente a uniao nominal GLOBAL + EVIDENCE
(operacao nova sem classificacao => falha); (b) o corpo de
`if settings.maintenance_enabled` contem EXATAMENTE o conjunto global e
nenhuma operacao do evidence worker; (c) a matriz 2x2 prova, por caso,
quais operacoes rodam (sem duplicatas), que todas as tasks sao
canceladas no shutdown, que nada vaza e que o shutdown nao pendura.

VALUE-3.4D-1: a linha `application_started` expoe o estado AUDITAVEL dos
dois gates (somente escalares tecnicos, sem segredo/PII).

Alvo e o proprio `lifespan()` de app.main, nunca os modulos individuais
(nenhum e alterado por este gate). Idioma assincrono ja usado em
test_maintenance_loop_resilience.py: teste sincrono dirigindo uma
`async def` interna via asyncio.run(), sem pytest-asyncio/anyio.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

import pytest

from app import main as main_module
from app.core import build_identity
from app.core.config import settings as global_settings
from app.core.evidence_floor_contract import canonical_utc
from app.core.observability import _is_sensitive_key


# ---------------------------------------------------------------------
# Classificacao NOMINAL das operacoes do lifespan
# ---------------------------------------------------------------------

# Chamadas SINCRONAS via `asyncio.to_thread(func)` no bloco global.
GLOBAL_RECOVERY_SYNC = (
    "run_auth_session_cleanup",
    "run_skill_invocation_recovery",
)

# `await func()` diretamente no bloco global.
GLOBAL_RECOVERY_ASYNC = (
    "run_work_skill_execution_recovery_async",
    "run_work_outcome_evaluation_recovery_async",
    "run_pilot_mutation_recovery_async",
    "run_overdue_detection_async",
    "run_advisory_dispatch_recovery_async",
    "run_client_behavior_memory_recalculation_async",
    "run_client_classification_recalculation_async",
    "run_receivables_monitor_async",
    "run_business_effect_verification_recovery_async",
    "run_policy_account_mark_overdue_trigger_async",
    "check_production_developer_roles_async",
)

GLOBAL_LOOPS = (
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
    "business_effect_verification_maintenance_loop",
    "policy_account_mark_overdue_trigger_maintenance_loop",
)

EVIDENCE_RECOVERY = (
    "run_escalation_payment_observation_recovery_async",
)
EVIDENCE_LOOPS = (
    "escalation_payment_observation_maintenance_loop",
)

GLOBAL_OPERATIONS = frozenset(
    GLOBAL_RECOVERY_SYNC + GLOBAL_RECOVERY_ASYNC + GLOBAL_LOOPS
)
EVIDENCE_OPERATIONS = frozenset(EVIDENCE_RECOVERY + EVIDENCE_LOOPS)

FLOOR = datetime(2030, 1, 1, tzinfo=timezone.utc)

# Tempo maximo tolerado para o lifespan() sair: um task nao cancelado
# faz o `await` do finally pendurar -- aqui isso vira falha, nao hang.
SHUTDOWN_BUDGET_SECONDS = 2.0
HARD_TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------
# Prova ESTATICA (AST do lifespan real)
# ---------------------------------------------------------------------


def _lifespan_tree() -> ast.AST:
    return ast.parse(inspect.getsource(main_module.lifespan))


def _called_operations(node: ast.AST) -> set[str]:
    operations: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and (
            func.id.startswith(("run_", "check_"))
            or func.id.endswith("_loop")
        ):
            operations.add(func.id)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "to_thread"
            and child.args
            and isinstance(child.args[0], ast.Name)
        ):
            operations.add(child.args[0].id)
    operations.discard("check_database_connection")
    return operations


def _maintenance_gate_block() -> ast.If:
    for node in ast.walk(_lifespan_tree()):
        if (
            isinstance(node, ast.If)
            and ast.unparse(node.test)
            == "settings.maintenance_enabled"
        ):
            return node
    raise AssertionError(
        "lifespan() nao possui `if settings.maintenance_enabled`."
    )


def test_every_lifespan_operation_is_classified() -> None:
    discovered = _called_operations(_lifespan_tree())

    assert discovered == GLOBAL_OPERATIONS | EVIDENCE_OPERATIONS, (
        "Operacao(oes) do lifespan sem classificacao nominal "
        "(GLOBAL ou EVIDENCE) -- nao classificadas: "
        f"{sorted(discovered - GLOBAL_OPERATIONS - EVIDENCE_OPERATIONS)}"
        "; classificadas mas ausentes: "
        f"{sorted((GLOBAL_OPERATIONS | EVIDENCE_OPERATIONS) - discovered)}"
    )


def test_global_gate_contains_exactly_the_global_operations() -> None:
    inside_global_gate = _called_operations(_maintenance_gate_block())

    assert inside_global_gate == GLOBAL_OPERATIONS


def test_evidence_worker_is_outside_the_global_gate() -> None:
    inside_global_gate = _called_operations(_maintenance_gate_block())

    assert inside_global_gate.isdisjoint(EVIDENCE_OPERATIONS)
    assert (
        _called_operations(_lifespan_tree()) & EVIDENCE_OPERATIONS
        == EVIDENCE_OPERATIONS
    )


def test_nominal_classes_are_disjoint_and_complete() -> None:
    assert GLOBAL_OPERATIONS.isdisjoint(EVIDENCE_OPERATIONS)
    # sem duplicata dentro das tuplas nominais
    for group in (
        GLOBAL_RECOVERY_SYNC,
        GLOBAL_RECOVERY_ASYNC,
        GLOBAL_LOOPS,
        EVIDENCE_RECOVERY,
        EVIDENCE_LOOPS,
    ):
        assert len(group) == len(set(group))


# ---------------------------------------------------------------------
# Prova DINAMICA (fakes + lifespan real)
# ---------------------------------------------------------------------


@dataclass
class _Outcome:
    calls: list[str]
    cancelled: set[str]
    leaked_tasks: int
    shutdown_seconds: float
    logged_exceptions: list[str]
    started_extras: list[dict]


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    cancelled: set[str],
    *,
    database_online: bool,
    evidence_recovery_raises: bool,
    logged_exceptions: list[str],
    started_extras: list[dict],
) -> None:
    def make_sync(name: str):
        def fake(*args, **kwargs):
            calls.append(name)

        return fake

    def make_async(name: str):
        async def fake(*args, **kwargs):
            calls.append(name)
            if evidence_recovery_raises and name in EVIDENCE_RECOVERY:
                raise RuntimeError("falha simulada do evidence worker")

        return fake

    def make_loop(name: str):
        async def fake_loop():
            calls.append(name)
            try:
                # nunca completa sozinho -- so cancelado pelo finally
                # do lifespan(); prova que a task foi agendada e depois
                # cancelada, sem depender de sleep real.
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.add(name)
                raise

        return fake_loop

    for name in GLOBAL_RECOVERY_SYNC:
        monkeypatch.setattr(main_module, name, make_sync(name))
    for name in GLOBAL_RECOVERY_ASYNC + EVIDENCE_RECOVERY:
        monkeypatch.setattr(main_module, name, make_async(name))
    for name in GLOBAL_LOOPS + EVIDENCE_LOOPS:
        monkeypatch.setattr(main_module, name, make_loop(name))

    monkeypatch.setattr(
        main_module,
        "check_database_connection",
        lambda: database_online,
    )
    monkeypatch.setattr(
        main_module.application_logger,
        "exception",
        lambda message, *args, **kwargs: logged_exceptions.append(
            str(message)
        ),
    )

    def spy_info(message, *args, extra=None, **kwargs) -> None:
        if message == "application_started":
            started_extras.append(dict(extra or {}))

    monkeypatch.setattr(
        main_module.application_logger, "info", spy_info
    )


def _run_lifespan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    maintenance_enabled: bool,
    floor: datetime | None,
    database_online: bool = True,
    evidence_recovery_raises: bool = False,
) -> _Outcome:
    calls: list[str] = []
    cancelled: set[str] = set()
    logged_exceptions: list[str] = []
    started_extras: list[dict] = []
    _install_fakes(
        monkeypatch,
        calls,
        cancelled,
        database_online=database_online,
        evidence_recovery_raises=evidence_recovery_raises,
        logged_exceptions=logged_exceptions,
        started_extras=started_extras,
    )
    monkeypatch.setattr(
        global_settings, "maintenance_enabled", maintenance_enabled
    )
    monkeypatch.setattr(
        global_settings,
        "escalation_payment_observation_activation_floor",
        floor,
    )

    result: dict[str, object] = {}

    async def exercise() -> None:
        baseline = set(asyncio.all_tasks())
        async with main_module.lifespan(main_module.app):
            # cede o loop uma vez para as tasks criadas rodarem seu
            # primeiro passo (append em calls) antes do cleanup.
            await asyncio.sleep(0)
            exit_started = time.monotonic()
        result["shutdown_seconds"] = time.monotonic() - exit_started

        # Apos sair do lifespan NENHUMA task criada por ele pode
        # continuar viva.
        leaked = [
            task
            for task in asyncio.all_tasks()
            if task not in baseline
            and task is not asyncio.current_task()
            and not task.done()
        ]
        result["leaked"] = len(leaked)
        for task in leaked:
            task.cancel()
        await asyncio.gather(*leaked, return_exceptions=True)

    asyncio.run(
        asyncio.wait_for(exercise(), timeout=HARD_TIMEOUT_SECONDS)
    )

    return _Outcome(
        calls=calls,
        cancelled=cancelled,
        leaked_tasks=int(result["leaked"]),
        shutdown_seconds=float(result["shutdown_seconds"]),
        logged_exceptions=logged_exceptions,
        started_extras=started_extras,
    )


@pytest.mark.parametrize(
    "maintenance_enabled,floor",
    [
        (False, None),
        (False, FLOOR),
        (True, None),
        (True, FLOOR),
    ],
    ids=[
        "maintenance=false/floor=None",
        "maintenance=false/floor=valid",
        "maintenance=true/floor=None",
        "maintenance=true/floor=valid",
    ],
)
def test_activation_contract_matrix(
    monkeypatch: pytest.MonkeyPatch,
    maintenance_enabled: bool,
    floor: datetime | None,
) -> None:
    outcome = _run_lifespan(
        monkeypatch,
        maintenance_enabled=maintenance_enabled,
        floor=floor,
    )

    expected_global = (
        GLOBAL_OPERATIONS if maintenance_enabled else frozenset()
    )
    expected_evidence = (
        EVIDENCE_OPERATIONS if floor is not None else frozenset()
    )

    # classe GLOBAL: tudo ou nada, conforme MAINTENANCE_ENABLED
    assert set(outcome.calls) & GLOBAL_OPERATIONS == expected_global
    # classe EVIDENCE: tudo ou nada, conforme o floor
    assert set(outcome.calls) & EVIDENCE_OPERATIONS == expected_evidence
    # nada fora das duas classes e nenhuma operacao em duplicidade
    # ("exatamente uma vez")
    assert set(outcome.calls) <= GLOBAL_OPERATIONS | EVIDENCE_OPERATIONS
    assert len(outcome.calls) == len(set(outcome.calls))

    # todas as tasks agendadas foram canceladas no shutdown
    expected_cancelled = (
        set(GLOBAL_LOOPS) if maintenance_enabled else set()
    ) | (set(EVIDENCE_LOOPS) if floor is not None else set())
    assert outcome.cancelled == expected_cancelled
    assert outcome.leaked_tasks == 0
    assert outcome.shutdown_seconds < SHUTDOWN_BUDGET_SECONDS


def test_evidence_worker_alone_never_starts_a_global_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = _run_lifespan(
        monkeypatch, maintenance_enabled=False, floor=FLOOR
    )

    assert set(outcome.calls) == EVIDENCE_OPERATIONS
    assert set(outcome.calls).isdisjoint(GLOBAL_OPERATIONS)


def test_evidence_worker_without_database_schedules_loop_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mesma semantica do bloco global: sem banco online o pass de
    # startup e pulado; o loop periodico continua agendado.
    outcome = _run_lifespan(
        monkeypatch,
        maintenance_enabled=False,
        floor=FLOOR,
        database_online=False,
    )

    assert set(outcome.calls) == set(EVIDENCE_LOOPS)
    assert outcome.cancelled == set(EVIDENCE_LOOPS)
    assert outcome.leaked_tasks == 0


def test_evidence_startup_failure_is_logged_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # D5: falha na coleta (capacidade opcional) nao pode abortar o
    # startup nem vazar as tasks globais ja criadas; o except apenas
    # registra -- sem retry e sem recovery.
    outcome = _run_lifespan(
        monkeypatch,
        maintenance_enabled=True,
        floor=FLOOR,
        evidence_recovery_raises=True,
    )

    assert outcome.logged_exceptions == [
        "escalation_payment_observation_startup_failed"
    ]
    # exatamente uma tentativa (sem retry)
    assert outcome.calls.count(EVIDENCE_RECOVERY[0]) == 1
    # o loop do evidence segue agendado e o bloco global fica intacto
    assert set(outcome.calls) == GLOBAL_OPERATIONS | EVIDENCE_OPERATIONS
    assert outcome.cancelled == set(GLOBAL_LOOPS) | set(EVIDENCE_LOOPS)
    assert outcome.leaked_tasks == 0
    assert outcome.shutdown_seconds < SHUTDOWN_BUDGET_SECONDS


def test_evidence_startup_success_logs_no_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = _run_lifespan(
        monkeypatch, maintenance_enabled=False, floor=FLOOR
    )

    assert outcome.logged_exceptions == []


# ---------------------------------------------------------------------
# VALUE-3.4D-1 -- `application_started`: estado auditavel dos gates
# ---------------------------------------------------------------------

LEGACY_STARTED_KEYS = frozenset(
    {
        "event",
        "state",
        "environment",
        "version",
        "database_online",
        "forwarded_allow_ips",
    }
)
GATE_STARTED_KEYS = frozenset(
    {
        "maintenance_enabled",
        "evidence_worker_enabled",
        "evidence_floor",
        "evidence_floor_state",
        "evidence_floor_age_seconds",
        "evidence_interval_seconds",
        "evidence_batch_size",
    }
)
# VALUE-3.4D-2a -- diagnostico de identidade de build (somente escalares).
BUILD_STARTED_KEYS = frozenset(
    {
        "build_identity_state",
        "build_identity_code",
        "build_git_sha",
        "build_git_dirty",
        "build_source_digest",
        "build_source_digest_algorithm",
    }
)


def _started_extra(outcome: _Outcome) -> dict:
    assert len(outcome.started_extras) == 1, (
        "application_started deve ser emitido exatamente uma vez"
    )
    return outcome.started_extras[0]


def test_application_started_exposes_exactly_the_allowlisted_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=FLOOR
        )
    )

    assert set(extra) == (
        LEGACY_STARTED_KEYS | GATE_STARTED_KEYS | BUILD_STARTED_KEYS
    )


def test_application_started_keys_are_never_sensitive_or_identifying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=True, floor=FLOOR
        )
    )

    assert [key for key in extra if _is_sensitive_key(key)] == []
    # nenhum identificador de conta/cliente/episodio
    for key in extra:
        assert not any(
            part in key
            for part in ("account", "client", "customer", "email", "episode", "work_item")
        ), key
    # somente escalares serializaveis
    assert all(
        value is None or isinstance(value, (str, int, float, bool))
        for value in extra.values()
    )
    json.dumps(extra)


def test_application_started_never_carries_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_database_url = (
        "postgresql+psycopg://leak_user:LEAK_SENTINEL_DB@leak-host/leakdb"
    )
    monkeypatch.setattr(
        global_settings, "database_url", secret_database_url
    )
    monkeypatch.setattr(
        global_settings, "api_key", "LEAK_SENTINEL_API_KEY", raising=False
    )

    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=True, floor=FLOOR
        )
    )

    serialized = json.dumps(extra)
    assert "LEAK_SENTINEL" not in serialized
    assert "leak_user" not in serialized
    assert "leak-host" not in serialized


@pytest.mark.parametrize("maintenance_enabled", [False, True])
def test_application_started_with_absent_floor_keeps_worker_disarmed(
    monkeypatch: pytest.MonkeyPatch, maintenance_enabled: bool
) -> None:
    extra = _started_extra(
        _run_lifespan(
            monkeypatch,
            maintenance_enabled=maintenance_enabled,
            floor=None,
        )
    )

    assert extra["maintenance_enabled"] is maintenance_enabled
    assert extra["evidence_worker_enabled"] is False
    assert extra["evidence_floor"] is None
    assert extra["evidence_floor_state"] == "unset"
    assert extra["evidence_floor_age_seconds"] is None
    assert extra["evidence_interval_seconds"] is None
    assert extra["evidence_batch_size"] is None


def test_application_started_reports_an_armed_floor_in_canonical_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    armed_floor = datetime.now(timezone.utc) - timedelta(minutes=5)

    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=armed_floor
        )
    )

    assert extra["maintenance_enabled"] is False
    assert extra["evidence_worker_enabled"] is True
    assert extra["evidence_floor"] == canonical_utc(armed_floor)
    assert extra["evidence_floor"].endswith("Z")
    assert extra["evidence_floor_state"] == "armed"
    assert 295 <= extra["evidence_floor_age_seconds"] <= 330
    assert (
        extra["evidence_interval_seconds"]
        == global_settings.escalation_payment_observation_interval_seconds
    )
    assert (
        extra["evidence_batch_size"]
        == global_settings.escalation_payment_observation_batch_size
    )


def test_application_started_flags_a_future_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=FLOOR
        )
    )

    assert extra["evidence_floor_state"] == "future"
    assert extra["evidence_floor_age_seconds"] < 0
    assert extra["evidence_floor"] == canonical_utc(FLOOR)


def test_application_started_keeps_the_legacy_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=None
        )
    )

    assert extra["event"] == "application_lifecycle"
    assert extra["state"] == "started"
    assert extra["environment"] == global_settings.environment
    assert extra["version"] == global_settings.app_version
    assert extra["database_online"] is True
    assert extra["forwarded_allow_ips"] == (
        global_settings.forwarded_allow_ips
    )


# ---------------------------------------------------------------------
# VALUE-3.4D-2a -- identidade de build no `application_started`:
# SOMENTE diagnostico (nunca bloqueia startup nem altera worker algum)
# ---------------------------------------------------------------------

VALID_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def identity_isolated(monkeypatch: pytest.MonkeyPatch):
    """Isola a memoizacao por processo do diagnostico de identidade."""
    build_identity.reset_process_build_identity()
    monkeypatch.delenv(build_identity.ENV_GIT_SHA, raising=False)
    monkeypatch.delenv(build_identity.ENV_GIT_DIRTY, raising=False)
    yield
    build_identity.reset_process_build_identity()


def test_application_started_reports_invalid_identity_without_claims(
    monkeypatch: pytest.MonkeyPatch, identity_isolated
) -> None:
    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=None
        )
    )

    assert extra["build_identity_state"] == "invalid"
    assert extra["build_identity_code"] == "identity_sha_invalid"
    assert extra["build_git_sha"] is None
    assert extra["build_git_dirty"] is None
    assert extra["build_source_digest_algorithm"] == "sd1"
    # o digest e medido mesmo com a alegacao invalida (so diagnostico)
    assert isinstance(extra["build_source_digest"], str)
    assert len(extra["build_source_digest"]) == 64


def test_application_started_reports_valid_identity(
    monkeypatch: pytest.MonkeyPatch, identity_isolated
) -> None:
    monkeypatch.setenv(build_identity.ENV_GIT_SHA, VALID_SHA)
    monkeypatch.setenv(build_identity.ENV_GIT_DIRTY, "false")

    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=None
        )
    )

    assert extra["build_identity_state"] == "valid"
    assert extra["build_identity_code"] is None
    assert extra["build_git_sha"] == VALID_SHA
    assert extra["build_git_dirty"] == "false"


def test_application_started_never_echoes_arbitrary_identity_env(
    monkeypatch: pytest.MonkeyPatch, identity_isolated
) -> None:
    monkeypatch.setenv(
        build_identity.ENV_GIT_SHA, "LEAK_SENTINEL_SECRET: token=abc"
    )
    monkeypatch.setenv(build_identity.ENV_GIT_DIRTY, "LEAK sentinel/2")

    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=None
        )
    )

    serialized = json.dumps(extra)
    assert "LEAK" not in serialized
    assert "token=abc" not in serialized
    assert extra["build_git_sha"] == "<invalid>"
    assert extra["build_git_dirty"] == "<invalid>"
    assert extra["build_identity_state"] == "invalid"


@pytest.mark.parametrize(
    "sha,dirty",
    [
        ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", "false"),
        ("z" * 40, "false"),
        (VALID_SHA, "my_api_token_123456789"),
        ("tok-en.with-dash.and.dot_1234567890", "unknown"),
    ],
    ids=["token_sha", "forty_z", "token_dirty", "dash_dot_token"],
)
def test_startup_log_never_carries_an_invalid_raw_claim(
    monkeypatch: pytest.MonkeyPatch,
    identity_isolated,
    sha: str,
    dirty: str,
) -> None:
    monkeypatch.setenv(build_identity.ENV_GIT_SHA, sha)
    monkeypatch.setenv(build_identity.ENV_GIT_DIRTY, dirty)

    extra = _started_extra(
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=None
        )
    )

    serialized = json.dumps(extra)
    sha_valid = sha == VALID_SHA
    if not sha_valid:
        assert extra["build_git_sha"] == "<invalid>"
        assert sha not in serialized
    if dirty not in ("true", "false"):
        assert extra["build_git_dirty"] == "<invalid>"
        if dirty != "unknown":
            assert dirty not in serialized
    assert extra["build_identity_state"] == "invalid"


@pytest.mark.parametrize("maintenance_enabled", [False, True])
@pytest.mark.parametrize("floor_present", [False, True])
def test_invalid_identity_never_changes_gates_or_scheduling(
    monkeypatch: pytest.MonkeyPatch,
    identity_isolated,
    maintenance_enabled: bool,
    floor_present: bool,
) -> None:
    floor = FLOOR if floor_present else None
    baseline = _run_lifespan(
        monkeypatch, maintenance_enabled=maintenance_enabled, floor=floor
    )
    # identidade invalida (sem alegacao) x valida: mesmos efeitos
    monkeypatch.setenv(build_identity.ENV_GIT_SHA, VALID_SHA)
    monkeypatch.setenv(build_identity.ENV_GIT_DIRTY, "false")
    build_identity.reset_process_build_identity()
    valid = _run_lifespan(
        monkeypatch, maintenance_enabled=maintenance_enabled, floor=floor
    )

    assert _started_extra(baseline)["build_identity_state"] == "invalid"
    assert _started_extra(valid)["build_identity_state"] == "valid"
    assert baseline.calls == valid.calls
    assert baseline.cancelled == valid.cancelled
    assert baseline.leaked_tasks == valid.leaked_tasks == 0
    assert (
        _started_extra(baseline)["evidence_worker_enabled"]
        == _started_extra(valid)["evidence_worker_enabled"]
        == floor_present
    )


def test_startup_survives_a_failing_identity_diagnosis(
    monkeypatch: pytest.MonkeyPatch, identity_isolated
) -> None:
    def explode() -> None:
        raise RuntimeError("diagnostico quebrado")

    monkeypatch.setattr(
        main_module, "process_build_identity_diagnosis", explode
    )

    outcome = _run_lifespan(
        monkeypatch, maintenance_enabled=True, floor=FLOOR
    )

    extra = _started_extra(outcome)
    assert extra["build_identity_state"] == "diagnosis_failed"
    assert extra["build_source_digest"] is None
    # o startup completou e todos os loops/gates seguem intactos
    assert set(outcome.calls) == GLOBAL_OPERATIONS | EVIDENCE_OPERATIONS
    assert outcome.leaked_tasks == 0


def test_identity_diagnosis_is_computed_once_per_process(
    monkeypatch: pytest.MonkeyPatch, identity_isolated
) -> None:
    computed: list[int] = []
    real = build_identity.diagnose_build_identity

    def counting(*args, **kwargs):
        computed.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(
        build_identity, "diagnose_build_identity", counting
    )

    for _ in range(3):
        _run_lifespan(
            monkeypatch, maintenance_enabled=False, floor=None
        )

    assert len(computed) == 1
