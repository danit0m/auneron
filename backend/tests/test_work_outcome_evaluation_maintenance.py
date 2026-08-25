import asyncio
import inspect
import threading
from types import SimpleNamespace

import pytest

from app.core import work_outcome_evaluation_maintenance
from app.main import lifespan
from app.repositories.work_outcome_evaluation_repository import (
    WorkOutcomeEvaluationRepository,
)


class _FakeSession:
    def __init__(self) -> None:
        self.rollback_count = 0
        self.closed = False

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


class _FakeRepository:
    def __init__(self, ids) -> None:
        self.ids = list(ids)

    def list_recovery_candidate_execution_ids(self, *, limit):
        return list(self.ids)


def _result(execution_id):
    return SimpleNamespace(
        evaluation=SimpleNamespace(
            terminal_status="failed",
            evaluation_code="execution_failed",
            learning_signal="negative",
            status="completed",
            attempts=1,
        ),
        memory=SimpleNamespace(id=500 + execution_id),
        duplicate=True,
    )


def test_recovery_evaluates_candidates_and_never_dispatches() -> None:
    db = _FakeSession()
    repository = _FakeRepository([1, 2])

    class Service:
        def __init__(self):
            self.evaluated = []

        def evaluate(self, execution_id):
            self.evaluated.append(execution_id)
            return _result(execution_id)

        def dispatch(self, *args, **kwargs):
            raise AssertionError("maintenance cannot dispatch")

    service = Service()
    summary = (
        work_outcome_evaluation_maintenance
        .run_work_outcome_evaluation_recovery(
            limit=10,
            session_factory=lambda: db,
            repository_factory=lambda session: repository,
            service_factory=lambda session: service,
        )
    )
    assert service.evaluated == [1, 2]
    assert summary.candidate_count == 2
    assert summary.completed_count == 2
    assert summary.failure_count == 0
    assert db.closed is True


def test_recovery_continues_after_failure() -> None:
    db = _FakeSession()
    repository = _FakeRepository([1, 2])

    class Service:
        def evaluate(self, execution_id):
            if execution_id == 1:
                raise RuntimeError("simulated")
            return _result(execution_id)

    summary = (
        work_outcome_evaluation_maintenance
        .run_work_outcome_evaluation_recovery(
            limit=10,
            session_factory=lambda: db,
            repository_factory=lambda session: repository,
            service_factory=lambda session: Service(),
        )
    )
    assert summary.completed_count == 1
    assert summary.failure_count == 1
    assert summary.attention_required_count == 1
    assert db.rollback_count == 1


def test_recovery_rejects_invalid_limit_before_session() -> None:
    called = False

    def session_factory():
        nonlocal called
        called = True
        return _FakeSession()

    with pytest.raises(ValueError, match="limit inválido"):
        (
            work_outcome_evaluation_maintenance
            .run_work_outcome_evaluation_recovery(
                limit=0,
                session_factory=session_factory,
            )
        )
    assert called is False


def test_repository_recovery_limit_is_bounded_before_query() -> None:
    repository = WorkOutcomeEvaluationRepository(
        None  # type: ignore[arg-type]
    )
    for value in (0, 1001, True):
        with pytest.raises(ValueError, match="limit inválido"):
            repository.list_recovery_candidate_execution_ids(
                limit=value  # type: ignore[arg-type]
            )


def test_maintenance_source_preserves_non_execution_boundary() -> None:
    source = inspect.getsource(
        work_outcome_evaluation_maintenance
    )
    lifespan_source = inspect.getsource(
        lifespan
    )
    for forbidden in (
        ".dispatch(",
        "GovernedSkillExecutionService",
        "SkillRuntimeService",
        "ExecutionPipeline",
        "agent.handler",
    ):
        assert forbidden not in source
    assert "asyncio.shield" in source
    assert "run_work_outcome_evaluation_recovery_async" in lifespan_source
    assert "work_outcome_evaluation_maintenance_loop" in lifespan_source


def test_async_recovery_drain_waits_for_worker_before_cancel_completes(
    monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_recovery():
        started.set()
        try:
            if not release.wait(timeout=5.0):
                raise RuntimeError("test worker release timeout")
        finally:
            finished.set()

    monkeypatch.setattr(
        work_outcome_evaluation_maintenance,
        "run_work_outcome_evaluation_recovery",
        blocking_recovery,
    )

    async def exercise() -> None:
        task = asyncio.create_task(
            work_outcome_evaluation_maintenance
            .run_work_outcome_evaluation_recovery_async()
        )
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.005)

        assert started.is_set()
        task.cancel()
        await asyncio.sleep(0.05)
        assert task.done() is False
        assert finished.is_set() is False

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() is True
        assert task.done() is True

    asyncio.run(exercise())
