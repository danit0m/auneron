"""add agent skill foundation

Revision ID: e4a6c8d2f913
Revises: d7b3e5f1a902
Create Date: 2026-08-17 16:30:00.000000

"""
from typing import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e4a6c8d2f913"
down_revision: Union[str, Sequence[str], None] = (
    "d7b3e5f1a902"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SKILL_JSON = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_key",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(skill_key)) >= 1",
            name="ck_skills_skill_key_not_blank",
        ),
        sa.CheckConstraint(
            "skill_key = lower(btrim(skill_key))",
            name="ck_skills_skill_key_canonical",
        ),
        sa.CheckConstraint(
            "char_length(btrim(provider)) >= 1",
            name="ck_skills_provider_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) >= 1",
            name="ck_skills_display_name_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(description)) >= 1",
            name="ck_skills_description_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'retired')",
            name="ck_skills_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_skills_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_skills",
        ),
        sa.UniqueConstraint(
            "skill_key",
            name="uq_skills_skill_key",
        ),
    )
    op.create_index(
        "ix_skills_status_key",
        "skills",
        ["status", "skill_key"],
        unique=False,
    )

    op.create_table(
        "skill_versions",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "runtime_kind",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "handler_reference",
            sa.String(length=320),
            nullable=False,
        ),
        sa.Column(
            "execution_mode",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "manifest_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "manifest",
            SKILL_JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "input_schema",
            SKILL_JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "output_schema",
            SKILL_JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
        sa.Column(
            "max_output_bytes",
            sa.Integer(),
            server_default="65536",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(version)) >= 1",
            name="ck_skill_versions_version_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(handler_reference)) >= 1",
            name="ck_skill_versions_handler_not_blank",
        ),
        sa.CheckConstraint(
            "runtime_kind IN ('internal_python', 'plugin')",
            name="ck_skill_versions_runtime_kind_valid",
        ),
        sa.CheckConstraint(
            "execution_mode IN ('read_only', 'mutating', 'external')",
            name="ck_skill_versions_execution_mode_valid",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="ck_skill_versions_status_valid",
        ),
        sa.CheckConstraint(
            "char_length(manifest_digest) = 64 "
            "AND manifest_digest = lower(manifest_digest)",
            name="ck_skill_versions_manifest_digest_format",
        ),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 300",
            name="ck_skill_versions_timeout_range",
        ),
        sa.CheckConstraint(
            "max_output_bytes >= 1024 "
            "AND max_output_bytes <= 1048576",
            name="ck_skill_versions_max_output_range",
        ),
        sa.CheckConstraint(
            "(status = 'draft' "
            "AND published_at IS NULL "
            "AND retired_at IS NULL) OR "
            "(status = 'published' "
            "AND published_at IS NOT NULL "
            "AND retired_at IS NULL) OR "
            "(status = 'retired' "
            "AND published_at IS NOT NULL "
            "AND retired_at IS NOT NULL "
            "AND retired_at >= published_at)",
            name="ck_skill_versions_status_timestamps",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=(
                "fk_skill_versions_created_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name="fk_skill_versions_skill_id_skills",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_skill_versions",
        ),
        sa.UniqueConstraint(
            "skill_id",
            "manifest_digest",
            name="uq_skill_versions_skill_digest",
        ),
        sa.UniqueConstraint(
            "skill_id",
            "version",
            name="uq_skill_versions_skill_version",
        ),
    )
    op.create_index(
        "ix_skill_versions_skill_status_created",
        "skill_versions",
        ["skill_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_skill_versions_status_runtime",
        "skill_versions",
        ["status", "runtime_kind"],
        unique=False,
    )

    op.create_table(
        "skill_capabilities",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "capability_key",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "access_mode",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "resource_scope",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "required",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(capability_key)) >= 1",
            name="ck_skill_capabilities_key_not_blank",
        ),
        sa.CheckConstraint(
            "capability_key = lower(btrim(capability_key))",
            name="ck_skill_capabilities_key_canonical",
        ),
        sa.CheckConstraint(
            "access_mode IN ('read', 'write', 'execute')",
            name="ck_skill_capabilities_access_mode_valid",
        ),
        sa.CheckConstraint(
            "resource_scope IN ("
            "'internal', 'account', 'user', 'external'"
            ")",
            name="ck_skill_capabilities_resource_scope_valid",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name=(
                "fk_skill_capabilities_version_id_"
                "skill_versions"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_skill_capabilities",
        ),
        sa.UniqueConstraint(
            "skill_version_id",
            "capability_key",
            "access_mode",
            "resource_scope",
            name="uq_skill_capabilities_declaration",
        ),
    )
    op.create_index(
        "ix_skill_capabilities_key_mode",
        "skill_capabilities",
        [
            "capability_key",
            "access_mode",
            "resource_scope",
        ],
        unique=False,
    )
    op.create_index(
        "ix_skill_capabilities_version_required",
        "skill_capabilities",
        ["skill_version_id", "required"],
        unique=False,
    )

    op.create_table(
        "agent_skill_bindings",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "agent_name",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default="100",
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "configuration",
            SKILL_JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(agent_name)) >= 1",
            name=(
                "ck_agent_skill_bindings_agent_name_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "priority >= 1 AND priority <= 1000",
            name="ck_agent_skill_bindings_priority_range",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=(
                "fk_agent_skill_bindings_created_by_user_id_"
                "users"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name=(
                "fk_agent_skill_bindings_version_id_"
                "skill_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_skill_bindings",
        ),
        sa.UniqueConstraint(
            "agent_name",
            "skill_version_id",
            name="uq_agent_skill_bindings_agent_version",
        ),
    )
    op.create_index(
        "ix_agent_skill_bindings_agent_enabled_priority",
        "agent_skill_bindings",
        ["agent_name", "enabled", "priority", "id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_skill_bindings_version_enabled",
        "agent_skill_bindings",
        ["skill_version_id", "enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_skill_bindings_version_enabled",
        table_name="agent_skill_bindings",
    )
    op.drop_index(
        "ix_agent_skill_bindings_agent_enabled_priority",
        table_name="agent_skill_bindings",
    )
    op.drop_table("agent_skill_bindings")

    op.drop_index(
        "ix_skill_capabilities_version_required",
        table_name="skill_capabilities",
    )
    op.drop_index(
        "ix_skill_capabilities_key_mode",
        table_name="skill_capabilities",
    )
    op.drop_table("skill_capabilities")

    op.drop_index(
        "ix_skill_versions_status_runtime",
        table_name="skill_versions",
    )
    op.drop_index(
        "ix_skill_versions_skill_status_created",
        table_name="skill_versions",
    )
    op.drop_table("skill_versions")

    op.drop_index(
        "ix_skills_status_key",
        table_name="skills",
    )
    op.drop_table("skills")
