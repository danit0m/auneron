from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkValidationError
from app.core.work_errors import WorkVersionConflictError
from app.database.database import SessionLocal
from app.models.work import WorkEvent
from app.models.work import WorkItem
from app.repositories.work_repository import WorkRepository
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-transaction",
)


def _create_work(db_session: Session) -> WorkItem:
    return WorkManagerService(db_session).create(
        work_type="task",
        title="Item transacional",
        work_key="test.work.transaction",
        scope_type="global",
        origin_type="system",
        origin_reference="test:transaction",
        actor=SYSTEM_ACTOR,
    ).work_item


class FailingEventRepository(WorkRepository):
    def add_event(self, event: WorkEvent) -> WorkEvent:
        if event.event_type != "created":
            raise RuntimeError("simulated event failure")
        return super().add_event(event)


def test_event_failure_rolls_back_state_and_version(
    db_session: Session,
) -> None:
    item = _create_work(db_session)
    service = WorkManagerService(
        db_session,
        repository=FailingEventRepository(db_session),
    )

    with pytest.raises(
        RuntimeError,
        match="simulated event failure",
    ):
        service.change_priority(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            priority="urgent",
        )

    db_session.refresh(item)
    event_count = db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.work_item_id == item.id
        )
    ).scalar_one()

    assert item.priority == "normal"
    assert item.version == 1
    assert event_count == 1


def test_foreign_key_failure_rolls_back_version_and_event(
    db_session: Session,
) -> None:
    item = _create_work(db_session)

    with pytest.raises(WorkValidationError):
        WorkManagerService(db_session).change_assignee(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            assignee_user_id=999999999,
        )

    db_session.refresh(item)
    event_count = db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.work_item_id == item.id
        )
    ).scalar_one()

    assert item.assignee_user_id is None
    assert item.version == 1
    assert event_count == 1


def test_concurrent_mutations_allow_only_one_expected_version(
    db_session: Session,
) -> None:
    item = _create_work(db_session)
    work_item_id = item.id
    barrier = Barrier(2)

    def worker(priority: str) -> str:
        session = SessionLocal()

        try:
            service = WorkManagerService(session)
            barrier.wait(timeout=10)

            try:
                service.change_priority(
                    work_item_id,
                    expected_version=1,
                    actor=SYSTEM_ACTOR,
                    priority=priority,
                )
            except WorkVersionConflictError:
                return "version_conflict"

            return "success"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(worker, ("high", "urgent"))
        )

    assert sorted(outcomes) == [
        "success",
        "version_conflict",
    ]

    db_session.expire_all()
    persisted = db_session.get(WorkItem, work_item_id)
    event_count = db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.work_item_id == work_item_id
        )
    ).scalar_one()

    assert persisted is not None
    assert persisted.version == 2
    assert persisted.priority in {"high", "urgent"}
    assert event_count == 2


def test_concurrent_idempotent_retries_apply_exactly_once(
    db_session: Session,
) -> None:
    item = _create_work(db_session)
    work_item_id = item.id
    barrier = Barrier(2)

    def worker(_: int) -> str:
        session = SessionLocal()

        try:
            service = WorkManagerService(session)
            barrier.wait(timeout=10)
            result = service.change_priority(
                work_item_id,
                expected_version=1,
                actor=SYSTEM_ACTOR,
                priority="urgent",
                idempotency_key="test.concurrent.retry",
            )
            return "applied" if result.applied else "duplicate"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, (1, 2)))

    assert sorted(outcomes) == ["applied", "duplicate"]

    db_session.expire_all()
    persisted = db_session.get(WorkItem, work_item_id)
    event_count = db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.work_item_id == work_item_id
        )
    ).scalar_one()

    assert persisted is not None
    assert persisted.priority == "urgent"
    assert persisted.version == 2
    assert event_count == 2


def test_concurrent_equivalent_creates_persist_once(
    db_session: Session,
) -> None:
    barrier = Barrier(2)

    def worker(_: int) -> tuple[str, int]:
        session = SessionLocal()

        try:
            barrier.wait(timeout=10)
            result = WorkManagerService(session).create(
                work_type="task",
                title="Criação concorrente",
                work_key="test.concurrent.create",
                scope_type="global",
                origin_type="system",
                origin_reference="test:concurrent-create",
                actor=SYSTEM_ACTOR,
                idempotency_key="test.concurrent.created-event",
            )
            outcome = "created" if result.created else "duplicate"
            return outcome, result.work_item.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, (1, 2)))

    assert sorted(outcome for outcome, _ in outcomes) == [
        "created",
        "duplicate",
    ]
    assert len({work_item_id for _, work_item_id in outcomes}) == 1

    work_item_count = db_session.execute(
        select(func.count(WorkItem.id)).where(
            WorkItem.work_key == "test.concurrent.create"
        )
    ).scalar_one()
    event_count = db_session.execute(
        select(func.count(WorkEvent.id)).join(
            WorkItem,
            WorkItem.id == WorkEvent.work_item_id,
        ).where(
            WorkItem.work_key == "test.concurrent.create"
        )
    ).scalar_one()

    assert work_item_count == 1
    assert event_count == 1


def test_concurrent_different_creates_report_conflict(
    db_session: Session,
) -> None:
    barrier = Barrier(2)

    def worker(title: str) -> str:
        session = SessionLocal()

        try:
            barrier.wait(timeout=10)

            try:
                WorkManagerService(session).create(
                    work_type="task",
                    title=title,
                    work_key="test.concurrent.create-conflict",
                    scope_type="global",
                    origin_type="system",
                    origin_reference="test:create-conflict",
                    actor=SYSTEM_ACTOR,
                )
            except WorkConflictError:
                return "conflict"

            return "created"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(worker, ("Versão A", "Versão B"))
        )

    assert sorted(outcomes) == ["conflict", "created"]

    work_item_count = db_session.execute(
        select(func.count(WorkItem.id)).where(
            WorkItem.work_key
            == "test.concurrent.create-conflict"
        )
    ).scalar_one()

    assert work_item_count == 1


def test_each_sequential_mutation_increments_exactly_once(
    db_session: Session,
) -> None:
    item = _create_work(db_session)
    service = WorkManagerService(db_session)

    first = service.change_priority(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        priority="high",
    )
    second = service.add_comment(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        comment="Segunda mutação.",
    )
    third = service.add_system_note(
        item.id,
        expected_version=3,
        actor=SYSTEM_ACTOR,
        note="Terceira mutação.",
    )

    assert [
        first.event.event_data["to_version"],
        second.event.event_data["to_version"],
        third.event.event_data["to_version"],
    ] == [2, 3, 4]
    assert third.work_item.version == 4
    assert len(service.list_events(item.id)) == 4
