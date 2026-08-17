from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from threading import Barrier

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkVersionConflictError
from app.database.database import SessionLocal
from app.models.work import WorkDependency
from app.models.work import WorkEvent
from app.models.work import WorkItem
from app.models.work import WorkRecurrenceOccurrence
from app.repositories.work_repository import WorkRepository
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-lifecycle-transactions",
)


def _create_work(
    service: WorkManagerService,
    key: str,
) -> WorkItem:
    return service.create(
        work_type="task",
        title=key,
        work_key=key,
        scope_type="global",
        origin_type="system",
        origin_reference="test:lifecycle-transactions",
        actor=SYSTEM_ACTOR,
    ).work_item


class FailingLifecycleEventRepository(WorkRepository):
    def add_event(self, event: WorkEvent) -> WorkEvent:
        if event.event_type in {
            "dependency_added",
            "recurrence_generated",
        }:
            raise RuntimeError("simulated lifecycle event failure")
        return super().add_event(event)


def test_dependency_event_failure_rolls_back_edge_and_version(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, "test.tx.dep.predecessor")
    item = _create_work(service, "test.tx.dep.item")
    failing = WorkManagerService(
        db_session,
        repository=FailingLifecycleEventRepository(db_session),
    )

    with pytest.raises(
        RuntimeError,
        match="simulated lifecycle event failure",
    ):
        failing.add_dependency(
            item.id,
            depends_on_work_item_id=predecessor.id,
            dependency_type="finish_to_start",
            expected_version=1,
            actor=SYSTEM_ACTOR,
        )

    db_session.expire_all()
    persisted = db_session.get(WorkItem, item.id)
    dependency_count = db_session.execute(
        select(func.count(WorkDependency.id)).where(
            WorkDependency.work_item_id == item.id
        )
    ).scalar_one()

    assert persisted is not None
    assert persisted.version == 1
    assert dependency_count == 0


def test_concurrent_reverse_edges_cannot_create_a_cycle(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    first = _create_work(service, "test.tx.cycle.first")
    second = _create_work(service, "test.tx.cycle.second")
    barrier = Barrier(2)

    def worker(pair: tuple[int, int]) -> str:
        session = SessionLocal()

        try:
            barrier.wait(timeout=10)

            try:
                WorkManagerService(session).add_dependency(
                    pair[0],
                    depends_on_work_item_id=pair[1],
                    dependency_type="finish_to_start",
                    expected_version=1,
                    actor=SYSTEM_ACTOR,
                )
            except WorkStateError:
                return "cycle_rejected"

            return "success"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                worker,
                ((first.id, second.id), (second.id, first.id)),
            )
        )

    edge_count = db_session.execute(
        select(func.count(WorkDependency.id))
    ).scalar_one()

    assert sorted(outcomes) == ["cycle_rejected", "success"]
    assert edge_count == 1


def test_concurrent_dependency_retries_apply_once(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, "test.tx.dep.retry.pred")
    item = _create_work(service, "test.tx.dep.retry.item")
    barrier = Barrier(2)

    def worker(_: int) -> str:
        session = SessionLocal()

        try:
            barrier.wait(timeout=10)
            result = WorkManagerService(session).add_dependency(
                item.id,
                depends_on_work_item_id=predecessor.id,
                dependency_type="finish_to_start",
                expected_version=1,
                actor=SYSTEM_ACTOR,
                idempotency_key="test.tx.dep.retry",
            )
            return "applied" if result.applied else "duplicate"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, (1, 2)))

    edge_count = db_session.execute(
        select(func.count(WorkDependency.id)).where(
            WorkDependency.work_item_id == item.id
        )
    ).scalar_one()

    assert sorted(outcomes) == ["applied", "duplicate"]
    assert edge_count == 1


def test_recurrence_event_failure_rolls_back_every_generated_record(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    item = _create_work(service, "test.tx.recurrence.failure")
    configured = service.configure_recurrence(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        frequency="daily",
        starts_at=starts_at,
        timezone_name="UTC",
    )
    failing = WorkManagerService(
        db_session,
        repository=FailingLifecycleEventRepository(db_session),
    )

    with pytest.raises(
        RuntimeError,
        match="simulated lifecycle event failure",
    ):
        failing.generate_due_occurrence(
            item.id,
            expected_version=configured.mutation.work_item.version,
            actor=SYSTEM_ACTOR,
            as_of=starts_at,
        )

    db_session.expire_all()
    persisted = db_session.get(WorkItem, item.id)
    occurrence_items = db_session.execute(
        select(func.count(WorkItem.id)).where(
            WorkItem.work_key.like("test.tx.recurrence.failure:occ:%")
        )
    ).scalar_one()
    occurrence_rows = db_session.execute(
        select(func.count(WorkRecurrenceOccurrence.id))
    ).scalar_one()

    assert persisted is not None
    assert persisted.version == 2
    assert occurrence_items == 0
    assert occurrence_rows == 0
    assert service.get_recurrence(item.id).generated_occurrences == 0


def test_concurrent_recurrence_retries_generate_once(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)
    item = _create_work(service, "test.tx.recurrence.retry")
    service.configure_recurrence(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        frequency="daily",
        starts_at=starts_at,
        timezone_name="UTC",
    )
    barrier = Barrier(2)

    def worker(_: int) -> str:
        session = SessionLocal()

        try:
            barrier.wait(timeout=10)
            result = WorkManagerService(
                session
            ).generate_due_occurrence(
                item.id,
                expected_version=2,
                actor=SYSTEM_ACTOR,
                as_of=starts_at,
                idempotency_key="test.tx.recurrence.retry.1",
            )
            return "applied" if result.applied else "duplicate"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, (1, 2)))

    occurrence_count = db_session.execute(
        select(func.count(WorkRecurrenceOccurrence.id))
    ).scalar_one()

    assert sorted(outcomes) == ["applied", "duplicate"]
    assert occurrence_count == 1


def test_concurrent_distinct_generation_allows_one_version(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)
    item = _create_work(service, "test.tx.recurrence.version")
    service.configure_recurrence(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        frequency="daily",
        starts_at=starts_at,
        timezone_name="UTC",
    )
    barrier = Barrier(2)

    def worker(index: int) -> str:
        session = SessionLocal()

        try:
            barrier.wait(timeout=10)

            try:
                WorkManagerService(
                    session
                ).generate_due_occurrence(
                    item.id,
                    expected_version=2,
                    actor=SYSTEM_ACTOR,
                    as_of=starts_at,
                    idempotency_key=f"test.tx.recurrence.version.{index}",
                )
            except WorkVersionConflictError:
                return "version_conflict"

            return "success"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, (1, 2)))

    occurrence_count = db_session.execute(
        select(func.count(WorkRecurrenceOccurrence.id))
    ).scalar_one()

    assert sorted(outcomes) == ["success", "version_conflict"]
    assert occurrence_count == 1
