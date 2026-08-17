from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


RULE_INSERT = text(
    """
    INSERT INTO work_recurrence_rules (
        work_item_id,
        frequency,
        interval_value,
        timezone_name,
        starts_at,
        ends_at,
        max_occurrences,
        generated_occurrences,
        next_occurrence_at,
        last_occurrence_at,
        sla_lead_minutes,
        active
    )
    VALUES (
        :work_item_id,
        :frequency,
        :interval_value,
        :timezone_name,
        :starts_at,
        :ends_at,
        :max_occurrences,
        :generated_occurrences,
        :next_occurrence_at,
        :last_occurrence_at,
        :sla_lead_minutes,
        :active
    )
    RETURNING id
    """
)


OCCURRENCE_INSERT = text(
    """
    INSERT INTO work_recurrence_occurrences (
        recurrence_rule_id,
        work_item_id,
        occurrence_number,
        scheduled_for
    )
    VALUES (
        :recurrence_rule_id,
        :work_item_id,
        :occurrence_number,
        :scheduled_for
    )
    RETURNING id
    """
)


SYSTEM_ACTOR = WorkActor(
    "system",
    "test:recurrence-constraints",
)


def _create_work(
    db_session: Session,
    *,
    key: str,
) -> int:
    return WorkManagerService(db_session).create(
        work_type="task",
        title="Item para constraint",
        work_key=key,
        scope_type="global",
        origin_type="system",
        origin_reference="test:recurrence-constraint",
        actor=SYSTEM_ACTOR,
    ).work_item.id


def _rule_values(work_item_id: int) -> dict[str, Any]:
    starts_at = datetime(
        2026,
        8,
        20,
        9,
        0,
        tzinfo=timezone.utc,
    )
    return {
        "work_item_id": work_item_id,
        "frequency": "daily",
        "interval_value": 1,
        "timezone_name": "America/Sao_Paulo",
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(days=30),
        "max_occurrences": 10,
        "generated_occurrences": 0,
        "next_occurrence_at": starts_at,
        "last_occurrence_at": None,
        "sla_lead_minutes": 60,
        "active": True,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"frequency": "yearly"},
        {"interval_value": 0},
        {"interval_value": 366},
        {"timezone_name": " "},
        {"max_occurrences": 0},
        {"generated_occurrences": -1},
        {"max_occurrences": 2, "generated_occurrences": 3},
        {"sla_lead_minutes": -1},
        {"sla_lead_minutes": 525601},
        {"active": True, "next_occurrence_at": None},
    ],
)
def test_recurrence_rule_rejects_invalid_scalar_invariants(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    work_item_id = _create_work(
        db_session,
        key="test.recurrence.constraint.scalar",
    )
    values = _rule_values(work_item_id)
    values.update(overrides)

    with pytest.raises(IntegrityError):
        db_session.execute(RULE_INSERT, values)

    db_session.rollback()


@pytest.mark.parametrize(
    "mutation",
    [
        "ends_before_start",
        "next_before_start",
        "next_after_end",
        "next_before_last",
        "inactive_with_next",
    ],
)
def test_recurrence_rule_rejects_invalid_time_invariants(
    db_session: Session,
    mutation: str,
) -> None:
    work_item_id = _create_work(
        db_session,
        key="test.recurrence.constraint.time",
    )
    values = _rule_values(work_item_id)
    starts_at = values["starts_at"]

    if mutation == "ends_before_start":
        values["ends_at"] = starts_at
    elif mutation == "next_before_start":
        values["next_occurrence_at"] = (
            starts_at - timedelta(minutes=1)
        )
    elif mutation == "next_after_end":
        values["next_occurrence_at"] = (
            values["ends_at"] + timedelta(minutes=1)
        )
    elif mutation == "next_before_last":
        values["last_occurrence_at"] = starts_at
        values["next_occurrence_at"] = starts_at
    else:
        values["active"] = False

    with pytest.raises(IntegrityError):
        db_session.execute(RULE_INSERT, values)

    db_session.rollback()


def test_recurrence_rule_is_unique_per_work_item(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        key="test.recurrence.constraint.unique-rule",
    )
    values = _rule_values(work_item_id)
    db_session.execute(RULE_INSERT, values)

    with pytest.raises(IntegrityError):
        db_session.execute(RULE_INSERT, values)

    db_session.rollback()


def test_occurrence_number_must_be_positive(
    db_session: Session,
) -> None:
    template_id = _create_work(
        db_session,
        key="test.recurrence.constraint.number-template",
    )
    occurrence_id = _create_work(
        db_session,
        key="test.recurrence.constraint.number-item",
    )
    values = _rule_values(template_id)
    rule_id = db_session.execute(
        RULE_INSERT,
        values,
    ).scalar_one()

    with pytest.raises(IntegrityError):
        db_session.execute(
            OCCURRENCE_INSERT,
            {
                "recurrence_rule_id": rule_id,
                "work_item_id": occurrence_id,
                "occurrence_number": 0,
                "scheduled_for": values["starts_at"],
            },
        )

    db_session.rollback()


@pytest.mark.parametrize(
    "duplicate_field",
    ["occurrence_number", "scheduled_for", "work_item_id"],
)
def test_occurrence_identity_is_unique(
    db_session: Session,
    duplicate_field: str,
) -> None:
    template_id = _create_work(
        db_session,
        key="test.recurrence.constraint.identity-template",
    )
    first_item_id = _create_work(
        db_session,
        key="test.recurrence.constraint.identity-first",
    )
    second_item_id = _create_work(
        db_session,
        key="test.recurrence.constraint.identity-second",
    )
    values = _rule_values(template_id)
    rule_id = db_session.execute(
        RULE_INSERT,
        values,
    ).scalar_one()
    first = {
        "recurrence_rule_id": rule_id,
        "work_item_id": first_item_id,
        "occurrence_number": 1,
        "scheduled_for": values["starts_at"],
    }
    db_session.execute(OCCURRENCE_INSERT, first)
    second = {
        "recurrence_rule_id": rule_id,
        "work_item_id": second_item_id,
        "occurrence_number": 2,
        "scheduled_for": (
            values["starts_at"] + timedelta(days=1)
        ),
    }
    second[duplicate_field] = first[duplicate_field]

    with pytest.raises(IntegrityError):
        db_session.execute(OCCURRENCE_INSERT, second)

    db_session.rollback()
