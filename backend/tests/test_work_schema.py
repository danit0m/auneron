from sqlalchemy import inspect
from sqlalchemy import text

from app.database.database import engine


WORK_TABLES = {
    "work_items",
    "work_dependencies",
    "work_events",
    "work_memory_links",
}


def test_work_tables_are_registered_in_postgresql() -> None:
    inspector = inspect(engine)

    assert WORK_TABLES.issubset(
        set(inspector.get_table_names())
    )


def test_work_schema_uses_expected_physical_types() -> None:
    inspector = inspect(engine)

    item_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "work_items"
        )
    }
    dependency_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "work_dependencies"
        )
    }
    event_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "work_events"
        )
    }
    link_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "work_memory_links"
        )
    }

    assert str(item_columns["id"]["type"]) == "BIGINT"
    assert (
        str(item_columns["parent_work_item_id"]["type"])
        == "BIGINT"
    )
    assert (
        str(item_columns["account_id"]["type"])
        == "INTEGER"
    )
    assert (
        str(item_columns["subject_user_id"]["type"])
        == "INTEGER"
    )
    assert (
        str(item_columns["assignee_user_id"]["type"])
        == "INTEGER"
    )
    assert (
        str(dependency_columns["work_item_id"]["type"])
        == "BIGINT"
    )
    assert (
        str(
            dependency_columns[
                "depends_on_work_item_id"
            ]["type"]
        )
        == "BIGINT"
    )
    assert (
        str(event_columns["work_item_id"]["type"])
        == "BIGINT"
    )
    assert (
        str(link_columns["memory_id"]["type"])
        == "BIGINT"
    )
    assert str(item_columns["context_data"]["type"]) == "JSONB"
    assert str(event_columns["event_data"]["type"]) == "JSONB"

    assert item_columns["status"]["default"] is not None
    assert item_columns["priority"]["default"] is not None
    assert item_columns["version"]["default"] is not None
    assert item_columns["context_data"]["default"] is not None
    assert event_columns["event_data"]["default"] is not None


def test_work_schema_uses_timezone_aware_timestamps() -> None:
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
                      'work_items',
                      'work_dependencies',
                      'work_events',
                      'work_memory_links'
                  )
                  AND column_name IN (
                      'status_changed_at',
                      'due_at',
                      'sla_due_at',
                      'started_at',
                      'completed_at',
                      'cancelled_at',
                      'created_at',
                      'updated_at'
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


def test_work_foreign_key_delete_actions() -> None:
    inspector = inspect(engine)

    item_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "work_items"
        )
    }
    dependency_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "work_dependencies"
        )
    }
    event_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "work_events"
        )
    }
    link_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "work_memory_links"
        )
    }

    assert (
        item_fks[
            "fk_work_items_account_id_accounts"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        item_fks[
            "fk_work_items_subject_user_id_users"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        item_fks[
            "fk_work_items_parent_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        item_fks[
            "fk_work_items_created_by_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        item_fks[
            "fk_work_items_assignee_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        dependency_fks[
            "fk_work_dependencies_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        dependency_fks[
            "fk_work_dependencies_depends_on_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        event_fks[
            "fk_work_events_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        event_fks[
            "fk_work_events_actor_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        link_fks[
            "fk_work_memory_links_work_item_id_work_items"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        link_fks[
            "fk_work_memory_links_memory_id_memory_items"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )


def test_work_partial_indexes_are_present() -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                    indexname,
                    indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'work_items'
                  AND indexname IN (
                      'uq_work_items_global_key',
                      'uq_work_items_account_key',
                      'uq_work_items_user_key',
                      'ix_work_items_open_due_at'
                  )
                ORDER BY indexname
                """
            )
        ).all()

    assert len(rows) == 4

    definitions = {
        row.indexname: row.indexdef
        for row in rows
    }

    for index_name in (
        "uq_work_items_global_key",
        "uq_work_items_account_key",
        "uq_work_items_user_key",
    ):
        definition = definitions[index_name]
        assert "CREATE UNIQUE INDEX" in definition
        assert "work_key IS NOT NULL" in definition

    open_due_definition = definitions[
        "ix_work_items_open_due_at"
    ]
    assert "completed" in open_due_definition
    assert "cancelled" in open_due_definition
    assert "due_at IS NOT NULL" in open_due_definition
