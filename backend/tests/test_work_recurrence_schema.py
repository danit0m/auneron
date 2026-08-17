from sqlalchemy import inspect
from sqlalchemy import text

from app.database.database import engine


RECURRENCE_TABLES = {
    "work_recurrence_rules",
    "work_recurrence_occurrences",
}


def test_recurrence_tables_are_registered() -> None:
    inspector = inspect(engine)

    assert RECURRENCE_TABLES.issubset(
        set(inspector.get_table_names())
    )


def test_recurrence_columns_use_expected_types_and_defaults() -> None:
    inspector = inspect(engine)
    rule_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "work_recurrence_rules"
        )
    }
    occurrence_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "work_recurrence_occurrences"
        )
    }

    assert str(rule_columns["id"]["type"]) == "BIGINT"
    assert str(rule_columns["work_item_id"]["type"]) == "BIGINT"
    assert str(rule_columns["active"]["type"]) == "BOOLEAN"
    assert (
        str(occurrence_columns["work_item_id"]["type"])
        == "BIGINT"
    )
    assert rule_columns["interval_value"]["default"] is not None
    assert (
        rule_columns["generated_occurrences"]["default"]
        is not None
    )
    assert rule_columns["active"]["default"] is not None


def test_recurrence_timestamps_are_timezone_aware() -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'work_recurrence_rules',
                      'work_recurrence_occurrences'
                  )
                  AND column_name IN (
                      'starts_at',
                      'ends_at',
                      'next_occurrence_at',
                      'last_occurrence_at',
                      'scheduled_for',
                      'created_at',
                      'updated_at'
                  )
                """
            )
        ).all()

    assert rows
    assert all(
        row.data_type == "timestamp with time zone"
        for row in rows
    )


def test_recurrence_foreign_keys_and_unique_constraints() -> None:
    inspector = inspect(engine)
    rule_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "work_recurrence_rules"
        )
    }
    occurrence_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "work_recurrence_occurrences"
        )
    }
    rule_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "work_recurrence_rules"
        )
    }
    occurrence_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "work_recurrence_occurrences"
        )
    }

    assert (
        rule_fks[
            "fk_work_recurrence_rules_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        occurrence_fks[
            "fk_work_recurrence_occurrences_rule_id_rules"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        occurrence_fks[
            "fk_work_recurrence_occurrences_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert "uq_work_recurrence_rules_work_item_id" in rule_uniques
    assert (
        "uq_work_recurrence_occurrences_rule_number"
        in occurrence_uniques
    )
    assert (
        "uq_work_recurrence_occurrences_rule_scheduled_for"
        in occurrence_uniques
    )


def test_event_vocabulary_and_recurrence_indexes_are_current() -> None:
    inspector = inspect(engine)
    rule_indexes = {
        index["name"]
        for index in inspector.get_indexes(
            "work_recurrence_rules"
        )
    }
    occurrence_indexes = {
        index["name"]
        for index in inspector.get_indexes(
            "work_recurrence_occurrences"
        )
    }

    with engine.connect() as connection:
        definition = connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'ck_work_events_event_type_valid'
                """
            )
        ).scalar_one()

    assert "ix_work_recurrence_rules_due" in rule_indexes
    assert (
        "ix_work_recurrence_occurrences_scheduled"
        in occurrence_indexes
    )
    assert "recurrence_configured" in definition
    assert "recurrence_disabled" in definition
    assert "recurrence_generated" in definition
