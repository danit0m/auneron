from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.core.work_errors import WorkVersionConflictError
from app.models.work import WorkEvent
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-lifecycle",
)


def _create_work(
    service: WorkManagerService,
    *,
    key: str = "test.lifecycle.default",
):
    return service.create(
        work_type="task",
        title="Item de ciclo de vida",
        work_key=key,
        scope_type="global",
        origin_type="system",
        origin_reference="test:lifecycle",
        actor=SYSTEM_ACTOR,
    ).work_item


def _transition(
    service: WorkManagerService,
    item,
    status: str,
    *,
    reason: str | None = None,
):
    return service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status=status,
        reason=reason,
    ).work_item


def test_lifecycle_supports_the_full_nonterminal_path(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service)

    item = _transition(service, item, "ready")
    item = _transition(service, item, "in_progress")
    started_at = item.started_at
    item = _transition(
        service,
        item,
        "blocked",
        reason="Aguardando dependência externa",
    )

    assert item.blocked_reason == "Aguardando dependência externa"
    assert item.status_reason is None

    item = _transition(service, item, "in_progress")
    item = _transition(service, item, "completed")

    assert item.status == "completed"
    assert item.started_at == started_at
    assert item.completed_at is not None
    assert item.cancelled_at is None
    assert item.version == 6
    assert item.status_changed_at <= item.completed_at


@pytest.mark.parametrize(
    "source_status",
    ["backlog", "ready", "in_progress", "blocked"],
)
def test_lifecycle_allows_cancellation_from_each_nonterminal_state(
    db_session: Session,
    source_status: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key=f"test.cancel.{source_status}")

    if source_status != "backlog":
        item = _transition(service, item, "ready")
    if source_status in {"in_progress", "blocked"}:
        item = _transition(service, item, "in_progress")
    if source_status == "blocked":
        item = _transition(
            service,
            item,
            "blocked",
            reason="Bloqueado antes do cancelamento",
        )

    item = _transition(
        service,
        item,
        "cancelled",
        reason="Cancelamento de teste",
    )

    assert item.status == "cancelled"
    assert item.status_reason == "Cancelamento de teste"
    assert item.cancelled_at is not None
    assert item.completed_at is None
    assert item.blocked_reason is None


@pytest.mark.parametrize(
    ("source_status", "target_status"),
    [
        ("backlog", "in_progress"),
        ("backlog", "completed"),
        ("ready", "completed"),
        ("in_progress", "backlog"),
        ("in_progress", "ready"),
        ("blocked", "ready"),
        ("blocked", "completed"),
    ],
)
def test_lifecycle_rejects_transitions_outside_the_matrix(
    db_session: Session,
    source_status: str,
    target_status: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(
        service,
        key=f"test.invalid.{source_status}.{target_status}",
    )

    if source_status != "backlog":
        item = _transition(service, item, "ready")
    if source_status in {"in_progress", "blocked"}:
        item = _transition(service, item, "in_progress")
    if source_status == "blocked":
        item = _transition(
            service,
            item,
            "blocked",
            reason="Bloqueio válido",
        )

    with pytest.raises(WorkStateError, match="Transição"):
        _transition(service, item, target_status)


@pytest.mark.parametrize("status", ["blocked", "cancelled"])
def test_lifecycle_requires_reason_for_explanatory_states(
    db_session: Session,
    status: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key=f"test.reason.{status}")

    if status == "blocked":
        item = _transition(service, item, "ready")
        item = _transition(service, item, "in_progress")

    with pytest.raises(WorkValidationError, match="reason"):
        _transition(service, item, status, reason="   ")


@pytest.mark.parametrize("terminal_status", ["completed", "cancelled"])
def test_terminal_state_rejects_mutable_business_fields(
    db_session: Session,
    terminal_status: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key=f"test.terminal.{terminal_status}")
    item = _transition(service, item, "ready")

    if terminal_status == "completed":
        item = _transition(service, item, "in_progress")
        item = _transition(service, item, "completed")
    else:
        item = _transition(
            service,
            item,
            "cancelled",
            reason="Encerrado",
        )

    due = datetime.now(timezone.utc) + timedelta(days=1)
    operations = (
        lambda: service.update_details(
            item.id,
            expected_version=item.version,
            actor=SYSTEM_ACTOR,
            title="Novo título",
            description=None,
            context_data=None,
        ),
        lambda: service.change_priority(
            item.id,
            expected_version=item.version,
            actor=SYSTEM_ACTOR,
            priority="urgent",
        ),
        lambda: service.change_assignee(
            item.id,
            expected_version=item.version,
            actor=SYSTEM_ACTOR,
            assignee_user_id=None,
        ),
        lambda: service.change_schedule(
            item.id,
            expected_version=item.version,
            actor=SYSTEM_ACTOR,
            due_at=due,
            sla_due_at=None,
        ),
    )

    for operation in operations:
        with pytest.raises(WorkStateError, match="terminal"):
            operation()


def test_terminal_state_preserves_append_only_notes(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key="test.terminal.notes")
    item = _transition(
        service,
        item,
        "cancelled",
        reason="Encerrado",
    )

    comment = service.add_comment(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        comment="Auditoria posterior",
    )
    note = service.add_system_note(
        item.id,
        expected_version=comment.work_item.version,
        actor=SYSTEM_ACTOR,
        note="Registro imutável",
    )

    assert comment.event.event_type == "comment_added"
    assert note.event.event_type == "system_note"
    assert note.work_item.status == "cancelled"


def test_status_transition_is_idempotent_and_emits_once(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key="test.lifecycle.idempotent")
    first = service.transition_status(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        status="ready",
        idempotency_key="test.lifecycle.ready",
    )
    second = service.transition_status(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        status="ready",
        idempotency_key="test.lifecycle.ready",
    )
    status_events = db_session.execute(
        select(WorkEvent).where(
            WorkEvent.work_item_id == item.id,
            WorkEvent.event_type == "status_changed",
        )
    ).scalars().all()

    assert first.applied is True
    assert second.duplicate is True
    assert second.work_item.version == 2
    assert len(status_events) == 1


def test_status_transition_honors_optimistic_version(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key="test.lifecycle.version")
    _transition(service, item, "ready")

    with pytest.raises(WorkVersionConflictError):
        service.transition_status(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            status="backlog",
        )
