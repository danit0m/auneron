from sqlalchemy import inspect
from sqlalchemy import text

from app.database.database import engine


def test_memory_schema_uses_expected_physical_types() -> None:
    inspector = inspect(engine)

    item_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "memory_items"
        )
    }
    evidence_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "memory_evidence"
        )
    }

    assert str(item_columns["id"]["type"]) == "BIGINT"
    assert (
        str(item_columns["account_id"]["type"])
        == "INTEGER"
    )
    assert (
        str(item_columns["subject_user_id"]["type"])
        == "INTEGER"
    )
    assert (
        str(item_columns["created_by_user_id"]["type"])
        == "INTEGER"
    )
    assert (
        str(item_columns["supersedes_memory_id"]["type"])
        == "BIGINT"
    )

    assert (
        str(evidence_columns["id"]["type"])
        == "BIGINT"
    )
    assert (
        str(evidence_columns["memory_id"]["type"])
        == "BIGINT"
    )
    assert (
        str(evidence_columns["source_memory_id"]["type"])
        == "BIGINT"
    )
    assert (
        str(evidence_columns["created_by_user_id"]["type"])
        == "INTEGER"
    )

    assert (
        str(item_columns["context_data"]["type"])
        == "JSONB"
    )
    assert (
        str(evidence_columns["context_data"]["type"])
        == "JSONB"
    )

    assert item_columns["confidence"]["default"] is None
    assert item_columns["valid_from"]["default"] is None
    assert item_columns["importance"]["default"] is not None
    assert item_columns["status"]["default"] is not None


def test_memory_schema_uses_timezone_aware_timestamps() -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                    table_name,
                    column_name,
                    data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'memory_items',
                      'memory_evidence'
                  )
                  AND column_name IN (
                      'status_changed_at',
                      'valid_from',
                      'valid_until',
                      'created_at',
                      'updated_at',
                      'observed_at'
                  )
                """
            )
        ).all()

    assert rows

    for row in rows:
        assert (
            row.data_type
            == "timestamp with time zone"
        )


def test_memory_partial_unique_indexes_are_present() -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                    indexname,
                    indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'memory_items'
                  AND indexname IN (
                      'uq_memory_items_active_global_key',
                      'uq_memory_items_active_account_key',
                      'uq_memory_items_active_user_key'
                  )
                ORDER BY indexname
                """
            )
        ).all()

    assert len(rows) == 3

    definitions = {
        row.indexname: row.indexdef
        for row in rows
    }

    for definition in definitions.values():
        assert "CREATE UNIQUE INDEX" in definition
        assert "status" in definition
        assert "active" in definition
        assert "memory_key IS NOT NULL" in definition


def test_memory_foreign_key_delete_actions() -> None:
    inspector = inspect(engine)

    item_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "memory_items"
        )
    }
    evidence_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "memory_evidence"
        )
    }

    assert (
        item_fks[
            "fk_memory_items_account_id_accounts"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        item_fks[
            "fk_memory_items_subject_user_id_users"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        item_fks[
            "fk_memory_items_created_by_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        item_fks[
            "fk_memory_items_supersedes_memory_id_memory_items"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )

    assert (
        evidence_fks[
            "fk_memory_evidence_memory_id_memory_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        evidence_fks[
            "fk_memory_evidence_source_memory_id_memory_items"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        evidence_fks[
            "fk_memory_evidence_created_by_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
