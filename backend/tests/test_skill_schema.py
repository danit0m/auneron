from sqlalchemy import inspect
from sqlalchemy import text

from app.database.database import engine
from app.models import AgentSkillBinding
from app.models import SkillCapability
from app.models import SkillDefinition
from app.models import SkillVersion


SKILL_TABLES = {
    "skills",
    "skill_versions",
    "skill_capabilities",
    "agent_skill_bindings",
}


def test_skill_models_export_expected_tables() -> None:
    assert SkillDefinition.__tablename__ == "skills"
    assert SkillVersion.__tablename__ == "skill_versions"
    assert (
        SkillCapability.__tablename__
        == "skill_capabilities"
    )
    assert (
        AgentSkillBinding.__tablename__
        == "agent_skill_bindings"
    )


def test_skill_tables_are_registered_in_postgresql() -> None:
    inspector = inspect(engine)

    assert SKILL_TABLES.issubset(
        set(inspector.get_table_names())
    )


def test_skill_schema_uses_expected_physical_types() -> None:
    inspector = inspect(engine)

    skill_columns = {
        column["name"]: column
        for column in inspector.get_columns("skills")
    }
    version_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "skill_versions"
        )
    }
    capability_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "skill_capabilities"
        )
    }
    binding_columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "agent_skill_bindings"
        )
    }

    assert str(skill_columns["id"]["type"]) == "BIGINT"
    assert (
        str(version_columns["skill_id"]["type"])
        == "BIGINT"
    )
    assert (
        str(
            capability_columns[
                "skill_version_id"
            ]["type"]
        )
        == "BIGINT"
    )
    assert (
        str(
            binding_columns[
                "skill_version_id"
            ]["type"]
        )
        == "BIGINT"
    )

    for json_column in (
        "manifest",
        "input_schema",
        "output_schema",
    ):
        assert (
            str(version_columns[json_column]["type"])
            == "JSONB"
        )
        assert (
            version_columns[json_column]["default"]
            is not None
        )

    assert (
        str(binding_columns["configuration"]["type"])
        == "JSONB"
    )
    assert skill_columns["status"]["default"] is not None
    assert version_columns["status"]["default"] is not None
    assert (
        capability_columns["required"]["default"]
        is not None
    )
    assert binding_columns["enabled"]["default"] is not None


def test_skill_schema_uses_timezone_aware_timestamps() -> None:
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
                      'skills',
                      'skill_versions',
                      'skill_capabilities',
                      'agent_skill_bindings'
                  )
                  AND column_name IN (
                      'published_at',
                      'retired_at',
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


def test_skill_foreign_key_delete_actions() -> None:
    inspector = inspect(engine)

    skill_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys("skills")
    }
    version_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "skill_versions"
        )
    }
    capability_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "skill_capabilities"
        )
    }
    binding_fks = {
        fk["name"]: fk
        for fk in inspector.get_foreign_keys(
            "agent_skill_bindings"
        )
    }

    assert (
        skill_fks[
            "fk_skills_created_by_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        version_fks[
            "fk_skill_versions_skill_id_skills"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        version_fks[
            "fk_skill_versions_created_by_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )
    assert (
        capability_fks[
            "fk_skill_capabilities_version_id_skill_versions"
        ]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        binding_fks[
            "fk_agent_skill_bindings_version_id_skill_versions"
        ]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        binding_fks[
            "fk_agent_skill_bindings_created_by_user_id_users"
        ]["options"]["ondelete"]
        == "SET NULL"
    )


def test_skill_indexes_and_unique_contracts_are_present() -> None:
    inspector = inspect(engine)

    expected_indexes = {
        "skills": {
            "ix_skills_status_key",
        },
        "skill_versions": {
            "ix_skill_versions_skill_status_created",
            "ix_skill_versions_status_runtime",
        },
        "skill_capabilities": {
            "ix_skill_capabilities_key_mode",
            "ix_skill_capabilities_version_required",
        },
        "agent_skill_bindings": {
            "ix_agent_skill_bindings_agent_enabled_priority",
            "ix_agent_skill_bindings_version_enabled",
        },
    }

    for table_name, index_names in expected_indexes.items():
        actual_names = {
            index["name"]
            for index in inspector.get_indexes(table_name)
        }
        assert index_names.issubset(actual_names)

    expected_unique = {
        "skills": {"uq_skills_skill_key"},
        "skill_versions": {
            "uq_skill_versions_skill_version",
            "uq_skill_versions_skill_digest",
        },
        "skill_capabilities": {
            "uq_skill_capabilities_declaration",
        },
        "agent_skill_bindings": {
            "uq_agent_skill_bindings_agent_version",
        },
    }

    for table_name, constraint_names in expected_unique.items():
        actual_names = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                table_name
            )
        }
        assert constraint_names.issubset(actual_names)
