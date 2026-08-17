from datetime import datetime
from datetime import timedelta
from datetime import timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-recurrence",
)


def _create_work(
    service: WorkManagerService,
    key: str | None,
):
    return service.create(
        work_type="task",
        title="Modelo recorrente",
        work_key=key,
        scope_type="global",
        origin_type="system",
        origin_reference="test:recurrence",
        actor=SYSTEM_ACTOR,
        context_data={"source": "recurrence-test"},
    ).work_item


def _configure(
    service: WorkManagerService,
    item,
    starts_at: datetime,
    **overrides: object,
):
    payload: dict[str, object] = {
        "expected_version": item.version,
        "actor": SYSTEM_ACTOR,
        "frequency": "daily",
        "starts_at": starts_at,
        "timezone_name": "UTC",
    }
    payload.update(overrides)
    return service.configure_recurrence(item.id, **payload)


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


def test_recurrence_configuration_persists_normalized_rule_and_event(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.recurrence.configure")
    starts_at = datetime(
        2026,
        10,
        1,
        9,
        tzinfo=ZoneInfo("America/Sao_Paulo"),
    )
    result = _configure(
        service,
        item,
        starts_at,
        frequency="WEEKLY",
        interval_value=2,
        timezone_name="America/Sao_Paulo",
        max_occurrences=8,
        sla_lead_minutes=90,
        idempotency_key="test.recurrence.configure",
    )

    assert result.mutation.event.event_type == "recurrence_configured"
    assert result.mutation.work_item.version == 2
    assert result.rule.frequency == "weekly"
    assert result.rule.interval_value == 2
    assert result.rule.timezone_name == "America/Sao_Paulo"
    assert result.rule.starts_at == starts_at.astimezone(timezone.utc)
    assert result.rule.next_occurrence_at == result.rule.starts_at
    assert result.rule.generated_occurrences == 0
    assert result.rule.active is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frequency", "yearly"),
        ("interval_value", 0),
        ("interval_value", 366),
        ("interval_value", True),
        ("timezone_name", "Mars/Olympus"),
        ("max_occurrences", 0),
        ("max_occurrences", 1_000_001),
        ("sla_lead_minutes", -1),
        ("sla_lead_minutes", 525601),
    ],
)
def test_recurrence_configuration_rejects_invalid_scalar_input(
    db_session: Session,
    field: str,
    value: object,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, f"test.recurrence.invalid.{field}")
    starts_at = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)

    with pytest.raises(WorkValidationError):
        _configure(service, item, starts_at, **{field: value})


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
def test_recurrence_configuration_rejects_naive_dates(
    db_session: Session,
    field: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, f"test.recurrence.naive.{field}")
    starts_at = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
    with pytest.raises(WorkValidationError, match=field):
        if field == "starts_at":
            _configure(
                service,
                item,
                datetime(2026, 10, 4, 9),
            )
        else:
            _configure(
                service,
                item,
                starts_at,
                ends_at=datetime(2026, 10, 4, 9),
            )


def test_recurrence_configuration_rejects_invalid_end_range(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.recurrence.end-range")
    starts_at = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)

    with pytest.raises(WorkValidationError, match="posterior"):
        _configure(
            service,
            item,
            starts_at,
            ends_at=starts_at,
        )


@pytest.mark.parametrize(
    ("key", "message"),
    [(None, "work_key"), ("a" * 231, "longa demais")],
)
def test_recurrence_requires_a_bounded_template_key(
    db_session: Session,
    key: str | None,
    message: str,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, key)

    with pytest.raises(WorkValidationError, match=message):
        _configure(
            service,
            item,
            datetime(2026, 10, 6, 9, tzinfo=timezone.utc),
        )


def test_recurrence_rejects_a_second_rule_for_the_template(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.recurrence.duplicate")
    starts_at = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
    first = _configure(service, item, starts_at)

    with pytest.raises(WorkConflictError, match="já possui"):
        _configure(
            service,
            first.mutation.work_item,
            starts_at + timedelta(days=1),
        )


def test_due_generation_creates_audited_work_item_and_sla(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, "test.recurrence.generate")
    starts_at = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)
    configured = _configure(
        service,
        item,
        starts_at,
        sla_lead_minutes=60,
    )
    generated = service.generate_due_occurrence(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        as_of=starts_at,
        idempotency_key="test.recurrence.generate.1",
    )

    assert generated.applied is True
    assert generated.event.event_type == "recurrence_generated"
    assert generated.template.version == 3
    assert generated.occurrence.occurrence_number == 1
    assert generated.occurrence.scheduled_for == starts_at
    assert generated.occurrence_work_item.work_key.endswith(":occ:1")
    assert generated.occurrence_work_item.status == "backlog"
    assert generated.occurrence_work_item.version == 1
    assert generated.occurrence_work_item.due_at == starts_at
    assert (
        generated.occurrence_work_item.sla_due_at
        == starts_at - timedelta(minutes=60)
    )
    assert generated.occurrence_work_item.context_data == {
        "source": "recurrence-test"
    }
    occurrence_events = service.list_events(
        generated.occurrence_work_item.id
    )
    assert [event.event_type for event in occurrence_events] == ["created"]
    assert configured.rule.id == generated.rule.id


def test_generation_rejects_not_due_and_disabled_rules(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 10, 9, 9, tzinfo=timezone.utc)
    item = _create_work(service, "test.recurrence.not-due")
    configured = _configure(service, item, starts_at)

    with pytest.raises(WorkStateError, match="ainda não"):
        service.generate_due_occurrence(
            item.id,
            expected_version=2,
            actor=SYSTEM_ACTOR,
            as_of=starts_at - timedelta(seconds=1),
        )

    disabled = service.disable_recurrence(
        item.id,
        expected_version=configured.mutation.work_item.version,
        actor=SYSTEM_ACTOR,
        reason="Pausa operacional",
    )

    with pytest.raises(WorkStateError, match="não está ativa"):
        service.generate_due_occurrence(
            item.id,
            expected_version=disabled.mutation.work_item.version,
            actor=SYSTEM_ACTOR,
            as_of=starts_at,
        )


@pytest.mark.parametrize(
    ("frequency", "interval_value", "timezone_name", "starts_at", "expected"),
    [
        (
            "daily",
            1,
            "America/New_York",
            datetime(2026, 3, 7, 9, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 3, 8, 13, tzinfo=timezone.utc),
        ),
        (
            "weekly",
            2,
            "UTC",
            datetime(2026, 10, 10, 9, tzinfo=timezone.utc),
            datetime(2026, 10, 24, 9, tzinfo=timezone.utc),
        ),
        (
            "monthly",
            1,
            "UTC",
            datetime(2027, 1, 31, 9, tzinfo=timezone.utc),
            datetime(2027, 2, 28, 9, tzinfo=timezone.utc),
        ),
    ],
)
def test_recurrence_advances_in_configured_wall_clock_time(
    db_session: Session,
    frequency: str,
    interval_value: int,
    timezone_name: str,
    starts_at: datetime,
    expected: datetime,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service, f"test.recurrence.advance.{frequency}")
    _configure(
        service,
        item,
        starts_at,
        frequency=frequency,
        interval_value=interval_value,
        timezone_name=timezone_name,
    )
    generated = service.generate_due_occurrence(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        as_of=starts_at.astimezone(timezone.utc),
    )

    assert generated.rule.next_occurrence_at == expected


@pytest.mark.parametrize("stop_mode", ["max", "end"])
def test_recurrence_deactivates_at_configured_boundary(
    db_session: Session,
    stop_mode: str,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)
    item = _create_work(service, f"test.recurrence.stop.{stop_mode}")
    configured = _configure(
        service,
        item,
        starts_at,
        max_occurrences=1 if stop_mode == "max" else None,
        ends_at=(
            starts_at + timedelta(hours=12)
            if stop_mode == "end"
            else None
        ),
    )
    generated = service.generate_due_occurrence(
        item.id,
        expected_version=configured.mutation.work_item.version,
        actor=SYSTEM_ACTOR,
        as_of=starts_at,
    )

    assert generated.rule.active is False
    assert generated.rule.next_occurrence_at is None
    assert generated.rule.generated_occurrences == 1


def test_generation_is_idempotent_and_occurrence_is_listed_once(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)
    item = _create_work(service, "test.recurrence.idempotent")
    _configure(service, item, starts_at)
    first = service.generate_due_occurrence(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        as_of=starts_at,
        idempotency_key="test.recurrence.idempotent.1",
    )
    replay = service.generate_due_occurrence(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        as_of=starts_at + timedelta(days=10),
        idempotency_key="test.recurrence.idempotent.1",
    )
    occurrences = service.list_recurrence_occurrences(item.id)

    assert first.applied is True
    assert replay.duplicate is True
    assert replay.occurrence.id == first.occurrence.id
    assert replay.occurrence_work_item.id == first.occurrence_work_item.id
    assert len(occurrences) == 1


def test_due_rule_listing_is_ordered_bounded_and_active_only(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    reference = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    later = _create_work(service, "test.recurrence.due.later")
    earlier = _create_work(service, "test.recurrence.due.earlier")
    future = _create_work(service, "test.recurrence.due.future")
    disabled = _create_work(service, "test.recurrence.due.disabled")
    _configure(service, later, reference - timedelta(hours=1))
    _configure(service, earlier, reference - timedelta(hours=2))
    _configure(service, future, reference + timedelta(hours=1))
    disabled_result = _configure(
        service,
        disabled,
        reference - timedelta(hours=3),
    )
    service.disable_recurrence(
        disabled.id,
        expected_version=disabled_result.mutation.work_item.version,
        actor=SYSTEM_ACTOR,
        reason="Não executar",
    )

    due = service.list_due_recurrences(as_of=reference, limit=2)

    assert [rule.work_item_id for rule in due] == [earlier.id, later.id]


@pytest.mark.parametrize("limit", [0, 101, False])
def test_due_rule_listing_enforces_bounded_limit(
    db_session: Session,
    limit: int,
) -> None:
    with pytest.raises(WorkValidationError, match="limit"):
        WorkManagerService(db_session).list_due_recurrences(limit=limit)


def test_active_recurrence_must_be_disabled_before_terminal_state(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    starts_at = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
    item = _create_work(service, "test.recurrence.terminal")
    configured = _configure(service, item, starts_at)

    with pytest.raises(WorkStateError, match="Recorrência ativa"):
        service.transition_status(
            item.id,
            expected_version=configured.mutation.work_item.version,
            actor=SYSTEM_ACTOR,
            status="cancelled",
            reason="Encerrar modelo",
        )

    disabled = service.disable_recurrence(
        item.id,
        expected_version=configured.mutation.work_item.version,
        actor=SYSTEM_ACTOR,
        reason="Encerrar recorrência",
    )
    cancelled = service.transition_status(
        item.id,
        expected_version=disabled.mutation.work_item.version,
        actor=SYSTEM_ACTOR,
        status="cancelled",
        reason="Encerrar modelo",
    )

    assert cancelled.work_item.status == "cancelled"
