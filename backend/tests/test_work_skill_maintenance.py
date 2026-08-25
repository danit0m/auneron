import asyncio
import inspect
import threading
from types import SimpleNamespace

import pytest

from app.core import work_skill_maintenance
from app.main import lifespan
from app.repositories.work_skill_execution_repository import (
    WorkSkillExecutionRepository,
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
    def __init__(self, db, *, ids) -> None:
        self.db = db
        self.ids = list(ids)

    def list_recovery_candidate_work_ids(self, *, limit):
        return list(self.ids)


def _result(work_item_id, outcome):
    return SimpleNamespace(
        execution=SimpleNamespace(
            id=100 + work_item_id,
            skill_version_id=200,
            approval_request_id=None,
            skill_invocation_id=None,
            status="ready",
            dispatch_attempts=1,
        ),
        work_item=SimpleNamespace(id=work_item_id),
        outcome=outcome,
        duplicate=True,
    )


def test_work_recovery_calls_reconcile_never_dispatch() -> None:
    db = _FakeSession()
    repository = _FakeRepository(db, ids=[1, 2])

    class Service:
        def __init__(self):
            self.reconciled = []

        def reconcile(self, work_item_id):
            self.reconciled.append(work_item_id)
            return _result(work_item_id, "ready")

        def dispatch(self, *args, **kwargs):
            raise AssertionError("maintenance cannot dispatch")

    service = Service()
    summary = work_skill_maintenance.run_work_skill_execution_recovery(
        limit=10,
        session_factory=lambda: db,
        repository_factory=lambda session: repository,
        service_factory=lambda session: service,
    )
    assert service.reconciled == [1, 2]
    assert summary.candidate_count == 2
    assert summary.reconciled_count == 2
    assert summary.failure_count == 0
    assert db.closed is True


def test_work_recovery_continues_after_one_failure() -> None:
    db = _FakeSession()
    repository = _FakeRepository(db, ids=[1, 2])

    class Service:
        def reconcile(self, work_item_id):
            if work_item_id == 1:
                raise RuntimeError("simulated")
            return _result(work_item_id, "ready")

    summary = work_skill_maintenance.run_work_skill_execution_recovery(
        limit=10,
        session_factory=lambda: db,
        repository_factory=lambda session: repository,
        service_factory=lambda session: Service(),
    )
    assert summary.reconciled_count == 1
    assert summary.failure_count == 1
    assert db.rollback_count == 1
    assert db.closed is True


def test_work_recovery_counts_retry_required_as_attention() -> None:
    db = _FakeSession()
    repository = _FakeRepository(db, ids=[1, 2])
    outcomes = {1: "retry_required", 2: "configuration_retry_required"}

    class Service:
        def reconcile(self, work_item_id):
            return _result(work_item_id, outcomes[work_item_id])

    summary = work_skill_maintenance.run_work_skill_execution_recovery(
        limit=10,
        session_factory=lambda: db,
        repository_factory=lambda session: repository,
        service_factory=lambda session: Service(),
    )
    assert summary.attention_required_count == 2
    assert summary.reconciled_count == 0


def test_work_recovery_rejects_invalid_limit_before_session() -> None:
    called = False

    def session_factory():
        nonlocal called
        called = True
        return _FakeSession()

    with pytest.raises(ValueError, match="limit inválido"):
        work_skill_maintenance.run_work_skill_execution_recovery(
            limit=0,
            session_factory=session_factory,
        )
    assert called is False


def test_repository_recovery_limit_is_bounded_before_query() -> None:
    repository = WorkSkillExecutionRepository(None)  # type: ignore[arg-type]
    for value in (0, 1001, True):
        with pytest.raises(ValueError, match="limit inválido"):
            repository.list_recovery_candidate_work_ids(
                limit=value  # type: ignore[arg-type]
            )


def test_work_recovery_source_and_lifespan_preserve_non_execution_boundary() -> None:
    maintenance_source = inspect.getsource(work_skill_maintenance)
    lifespan_source = inspect.getsource(lifespan)
    assert ".dispatch(" not in maintenance_source
    assert "GovernedSkillExecutionService" not in maintenance_source
    assert "run_work_skill_execution_recovery_async" in lifespan_source
    assert "work_skill_execution_maintenance_loop" in lifespan_source
    assert "asyncio.shield" in maintenance_source
    assert (
        "asyncio.to_thread(\n"
        "            run_work_skill_execution_recovery\n"
        "        )"
        not in lifespan_source
    )

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
        work_skill_maintenance,
        "run_work_skill_execution_recovery",
        blocking_recovery,
    )

    async def exercise() -> None:
        task = asyncio.create_task(
            work_skill_maintenance
            .run_work_skill_execution_recovery_async()
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
