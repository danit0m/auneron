from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkValidationError
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-schedule",
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
        "origin_reference": "test:schedule",
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


def test_schedule_can_be_set_and_cleared_with_audit_events(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.schedule.set-clear")
    due_at = datetime(2026, 9, 10, 18, tzinfo=timezone.utc)
    sla_due_at = due_at - timedelta(hours=2)

    scheduled = service.change_schedule(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        due_at=due_at,
        sla_due_at=sla_due_at,
    )

    assert scheduled.event.event_type == "schedule_changed"
    assert scheduled.work_item.due_at == due_at
    assert scheduled.work_item.sla_due_at == sla_due_at

    cleared = service.change_schedule(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        due_at=None,
        sla_due_at=None,
    )

    assert cleared.work_item.due_at is None
    assert cleared.work_item.sla_due_at is None
    assert cleared.work_item.version == 3


def test_schedule_change_is_idempotent(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.schedule.idempotent")
    due_at = datetime(2026, 9, 11, 18, tzinfo=timezone.utc)
    first = service.change_schedule(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        due_at=due_at,
        sla_due_at=None,
        idempotency_key="test.schedule.change",
    )
    replay = service.change_schedule(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        due_at=due_at,
        sla_due_at=None,
        idempotency_key="test.schedule.change",
    )

    assert first.applied is True
    assert replay.duplicate is True
    assert replay.work_item.version == 2


def test_schedule_rejects_no_op(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    due_at = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)
    item = _create_work(
        service,
        "test.schedule.no-op",
        due_at=due_at,
    )

    with pytest.raises(WorkValidationError, match="não produz"):
        service.change_schedule(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            due_at=due_at,
            sla_due_at=None,
        )


@pytest.mark.parametrize("method", ["create", "change"])
def test_schedule_rejects_sla_after_due_date(
    db_session: Session,
    method: str,
) -> None:
    service = WorkManagerService(db_session)
    due_at = datetime(2026, 9, 13, 18, tzinfo=timezone.utc)
    sla_due_at = due_at + timedelta(seconds=1)

    with pytest.raises(WorkValidationError, match="posterior"):
        if method == "create":
            _create_work(
                service,
                "test.schedule.invalid-create",
                due_at=due_at,
                sla_due_at=sla_due_at,
            )
        else:
            item = _create_work(
                service,
                "test.schedule.invalid-change",
            )
            service.change_schedule(
                item.id,
                expected_version=1,
                actor=SYSTEM_ACTOR,
                due_at=due_at,
                sla_due_at=sla_due_at,
            )


@pytest.mark.parametrize("field", ["due_at", "sla_due_at", "as_of"])
def test_schedule_and_sla_reject_naive_datetimes(
    db_session: Session,
    field: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, f"test.schedule.naive.{field}")
    naive = datetime(2026, 9, 14, 18)

    with pytest.raises(WorkValidationError, match=field):
        if field == "as_of":
            service.evaluate_sla(item.id, as_of=naive)
        else:
            service.change_schedule(
                item.id,
                expected_version=1,
                actor=SYSTEM_ACTOR,
                due_at=naive if field == "due_at" else None,
                sla_due_at=(
                    naive if field == "sla_due_at" else None
                ),
            )


def test_sla_reports_not_configured_on_track_and_breached(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    reference = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    plain = _create_work(service, "test.sla.none")
    tracked = _create_work(
        service,
        "test.sla.tracked",
        due_at=reference + timedelta(hours=2),
        sla_due_at=reference + timedelta(hours=1),
    )

    not_configured = service.evaluate_sla(plain.id, as_of=reference)
    on_track = service.evaluate_sla(tracked.id, as_of=reference)
    breached = service.evaluate_sla(
        tracked.id,
        as_of=reference + timedelta(hours=1, seconds=1),
    )

    assert not_configured.status == "not_configured"
    assert not_configured.remaining_seconds is None
    assert on_track.status == "on_track"
    assert on_track.remaining_seconds == 3600
    assert breached.status == "breached"
    assert breached.remaining_seconds == -1


def test_sla_reports_met_missed_and_cancelled_terminal_outcomes(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    now = datetime.now(timezone.utc)
    met = _create_work(
        service,
        "test.sla.met",
        due_at=now + timedelta(days=2),
        sla_due_at=now + timedelta(days=1),
    )
    missed = _create_work(
        service,
        "test.sla.missed",
        due_at=now - timedelta(hours=1),
        sla_due_at=now - timedelta(hours=2),
    )
    cancelled = _create_work(
        service,
        "test.sla.cancelled",
        due_at=now + timedelta(days=1),
        sla_due_at=now + timedelta(hours=1),
    )

    for item in (met, missed):
        item = _transition(service, item, "ready")
        item = _transition(service, item, "in_progress")
        completed = _transition(service, item, "completed")
        if item.work_key == "test.sla.met":
            met = completed
        else:
            missed = completed

    cancelled = _transition(
        service,
        cancelled,
        "cancelled",
        "Cancelado pelo solicitante",
    )

    assert service.evaluate_sla(met.id, as_of=now).status == "met"
    assert service.evaluate_sla(missed.id, as_of=now).status == "missed"
    assert (
        service.evaluate_sla(cancelled.id, as_of=now).status
        == "cancelled"
    )


def test_sla_breach_list_filters_terminals_and_orders_deadlines(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    reference = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    later = _create_work(
        service,
        "test.sla.list.later",
        sla_due_at=reference - timedelta(hours=1),
    )
    earlier = _create_work(
        service,
        "test.sla.list.earlier",
        sla_due_at=reference - timedelta(hours=2),
    )
    future = _create_work(
        service,
        "test.sla.list.future",
        sla_due_at=reference + timedelta(hours=1),
    )
    cancelled = _create_work(
        service,
        "test.sla.list.cancelled",
        sla_due_at=reference - timedelta(hours=3),
    )
    _transition(service, cancelled, "cancelled", "Encerrado")

    breaches = service.list_sla_breaches(as_of=reference, limit=2)

    assert [item.id for item in breaches] == [earlier.id, later.id]
    assert future.id not in {item.id for item in breaches}


@pytest.mark.parametrize("limit", [0, 101, True])
def test_sla_breach_list_enforces_bounded_limit(
    db_session: Session,
    limit: int,
) -> None:
    with pytest.raises(WorkValidationError, match="limit"):
        WorkManagerService(db_session).list_sla_breaches(limit=limit)
