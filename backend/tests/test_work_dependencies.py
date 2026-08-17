from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.models.account import Account
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-dependencies",
)


def _create_work(
    service: WorkManagerService,
    key: str,
    **overrides: object,
):
    payload: dict[str, object] = {
        "work_type": "task",
        "title": key,
        "work_key": key,
        "scope_type": "global",
        "origin_type": "system",
        "origin_reference": "test:dependencies",
        "actor": SYSTEM_ACTOR,
    }
    payload.update(overrides)
    return service.create(**payload).work_item


def _transition(
    service: WorkManagerService,
    item,
    status: str,
    reason: str | None = None,
):
    return service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status=status,
        reason=reason,
    ).work_item


def _start(service: WorkManagerService, item):
    item = _transition(service, item, "ready")
    return _transition(service, item, "in_progress")


def _complete(service: WorkManagerService, item):
    item = _start(service, item)
    return _transition(service, item, "completed")


@pytest.mark.parametrize(
    "dependency_type",
    [
        "finish_to_start",
        "start_to_start",
        "finish_to_finish",
        "start_to_finish",
    ],
)
def test_dependency_add_list_and_remove_preserve_type_and_events(
    db_session: Session,
    dependency_type: str,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, f"test.dep.pred.{dependency_type}")
    item = _create_work(service, f"test.dep.item.{dependency_type}")

    added = service.add_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        dependency_type=dependency_type,
        expected_version=1,
        actor=SYSTEM_ACTOR,
    )
    dependencies = service.list_dependencies(item.id)

    assert added.event.event_type == "dependency_added"
    assert added.work_item.version == 2
    assert len(dependencies) == 1
    assert dependencies[0][0].dependency_type == dependency_type
    assert dependencies[0][1].id == predecessor.id

    removed = service.remove_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
    )

    assert removed.event.event_type == "dependency_removed"
    assert removed.work_item.version == 3
    assert service.list_dependencies(item.id) == ()


def test_dependency_rejects_self_reference(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.dep.self")

    with pytest.raises(WorkValidationError, match="si mesmo"):
        service.add_dependency(
            item.id,
            depends_on_work_item_id=item.id,
            dependency_type="finish_to_start",
            expected_version=1,
            actor=SYSTEM_ACTOR,
        )


def test_dependency_rejects_cross_scope_edges(
    db_session: Session,
) -> None:
    account = Account(
        cliente="Conta Dependência",
        valor=Decimal("100.00"),
        vencimento=datetime(2027, 1, 1).date(),
        status="aberto",
    )
    db_session.add(account)
    db_session.flush()
    service = WorkManagerService(db_session)
    global_item = _create_work(service, "test.dep.global")
    account_item = _create_work(
        service,
        "test.dep.account",
        scope_type="account",
        account_id=account.id,
    )

    with pytest.raises(WorkValidationError, match="mesmo escopo"):
        service.add_dependency(
            global_item.id,
            depends_on_work_item_id=account_item.id,
            dependency_type="finish_to_start",
            expected_version=1,
            actor=SYSTEM_ACTOR,
        )


def test_dependency_rejects_duplicate_edge(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, "test.dep.duplicate.pred")
    item = _create_work(service, "test.dep.duplicate.item")
    service.add_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        dependency_type="finish_to_start",
        expected_version=1,
        actor=SYSTEM_ACTOR,
    )

    with pytest.raises(WorkConflictError, match="já existe"):
        service.add_dependency(
            item.id,
            depends_on_work_item_id=predecessor.id,
            dependency_type="start_to_start",
            expected_version=2,
            actor=SYSTEM_ACTOR,
        )


def test_dependency_rejects_transitive_cycle(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    first = _create_work(service, "test.dep.cycle.first")
    second = _create_work(service, "test.dep.cycle.second")
    third = _create_work(service, "test.dep.cycle.third")
    service.add_dependency(
        first.id,
        depends_on_work_item_id=second.id,
        dependency_type="finish_to_start",
        expected_version=1,
        actor=SYSTEM_ACTOR,
    )
    service.add_dependency(
        second.id,
        depends_on_work_item_id=third.id,
        dependency_type="finish_to_start",
        expected_version=1,
        actor=SYSTEM_ACTOR,
    )

    with pytest.raises(WorkStateError, match="ciclo"):
        service.add_dependency(
            third.id,
            depends_on_work_item_id=first.id,
            dependency_type="finish_to_start",
            expected_version=1,
            actor=SYSTEM_ACTOR,
        )


@pytest.mark.parametrize(
    ("dependency_type", "target_status", "predecessor_action"),
    [
        ("finish_to_start", "in_progress", "complete"),
        ("start_to_start", "in_progress", "start"),
        ("finish_to_finish", "completed", "complete"),
        ("start_to_finish", "completed", "start"),
    ],
)
def test_dependency_gate_opens_only_after_required_predecessor_state(
    db_session: Session,
    dependency_type: str,
    target_status: str,
    predecessor_action: str,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, f"test.gate.pred.{dependency_type}")
    item = _create_work(service, f"test.gate.item.{dependency_type}")
    item = service.add_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        dependency_type=dependency_type,
        expected_version=1,
        actor=SYSTEM_ACTOR,
    ).work_item
    item = _transition(service, item, "ready")

    if target_status == "completed":
        item = _transition(service, item, "in_progress")

    with pytest.raises(WorkStateError, match="não satisfeitas"):
        _transition(service, item, target_status)

    predecessor = (
        _complete(service, predecessor)
        if predecessor_action == "complete"
        else _start(service, predecessor)
    )
    item = _transition(service, item, target_status)

    assert predecessor.started_at is not None
    assert item.status == target_status


@pytest.mark.parametrize("status", ["in_progress", "blocked", "completed", "cancelled"])
def test_dependency_edges_are_frozen_after_execution_starts(
    db_session: Session,
    status: str,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, f"test.freeze.pred.{status}")
    item = _create_work(service, f"test.freeze.item.{status}")
    item = _start(service, item)

    if status == "blocked":
        item = _transition(service, item, "blocked", "Aguardando")
    elif status == "completed":
        item = _transition(service, item, "completed")
    elif status == "cancelled":
        item = _transition(service, item, "cancelled", "Encerrado")

    with pytest.raises(WorkStateError, match="backlog ou ready"):
        service.add_dependency(
            item.id,
            depends_on_work_item_id=predecessor.id,
            dependency_type="finish_to_start",
            expected_version=item.version,
            actor=SYSTEM_ACTOR,
        )


def test_dependency_add_and_remove_are_idempotent(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, "test.dep.idempotent.pred")
    item = _create_work(service, "test.dep.idempotent.item")
    first = service.add_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        dependency_type="finish_to_start",
        expected_version=1,
        actor=SYSTEM_ACTOR,
        idempotency_key="test.dep.add",
    )
    replay = service.add_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        dependency_type="finish_to_start",
        expected_version=1,
        actor=SYSTEM_ACTOR,
        idempotency_key="test.dep.add",
    )
    removed = service.remove_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        idempotency_key="test.dep.remove",
    )
    removed_replay = service.remove_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        idempotency_key="test.dep.remove",
    )

    assert first.applied is True
    assert replay.duplicate is True
    assert removed.applied is True
    assert removed_replay.duplicate is True
    assert removed_replay.work_item.version == 3


def test_remove_missing_dependency_reports_not_found(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    predecessor = _create_work(service, "test.dep.missing.pred")
    item = _create_work(service, "test.dep.missing.item")

    with pytest.raises(WorkNotFoundError, match="Dependência"):
        service.remove_dependency(
            item.id,
            depends_on_work_item_id=predecessor.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
        )
