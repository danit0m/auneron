"""add work manager foundation

Revision ID: c2f7a9d4e681
Revises: 4d8c2a1f7b90
Create Date: 2026-08-14 15:00:00.000000

"""
from typing import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c2f7a9d4e681"
down_revision: Union[str, Sequence[str], None] = (
    "4d8c2a1f7b90"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WORK_JSON = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_type",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(length=240),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "work_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "scope_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "parent_work_item_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "assignee_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="backlog",
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.String(length=16),
            server_default="normal",
            nullable=False,
        ),
        sa.Column(
            "blocked_reason",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "status_reason",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "due_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "sla_due_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "origin_type",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "origin_reference",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "context_data",
            WORK_JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
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
            "work_type IN ('task', 'project', 'milestone')",
            name="ck_work_items_work_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) >= 1",
            name="ck_work_items_title_not_blank",
        ),
        sa.CheckConstraint(
            "description IS NULL "
            "OR char_length(btrim(description)) >= 1",
            name="ck_work_items_description_not_blank",
        ),
        sa.CheckConstraint(
            "work_key IS NULL "
            "OR char_length(btrim(work_key)) >= 1",
            name="ck_work_items_work_key_not_blank",
        ),
        sa.CheckConstraint(
            "scope_type IN ('global', 'account', 'user')",
            name="ck_work_items_scope_type_valid",
        ),
        sa.CheckConstraint(
            "(scope_type = 'global' "
            "AND account_id IS NULL "
            "AND subject_user_id IS NULL) OR "
            "(scope_type = 'account' "
            "AND account_id IS NOT NULL "
            "AND subject_user_id IS NULL) OR "
            "(scope_type = 'user' "
            "AND account_id IS NULL "
            "AND subject_user_id IS NOT NULL)",
            name="ck_work_items_scope_integrity",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'backlog', 'ready', 'in_progress', "
            "'blocked', 'completed', 'cancelled'"
            ")",
            name="ck_work_items_status_valid",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_work_items_priority_valid",
        ),
        sa.CheckConstraint(
            "(status = 'blocked' "
            "AND blocked_reason IS NOT NULL "
            "AND char_length(btrim(blocked_reason)) >= 1) OR "
            "(status <> 'blocked' AND blocked_reason IS NULL)",
            name="ck_work_items_blocked_reason_integrity",
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' "
            "OR (status_reason IS NOT NULL "
            "AND char_length(btrim(status_reason)) >= 1)",
            name="ck_work_items_cancel_reason_required",
        ),
        sa.CheckConstraint(
            "(status = 'completed' "
            "AND completed_at IS NOT NULL "
            "AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' "
            "AND completed_at IS NULL "
            "AND cancelled_at IS NOT NULL) OR "
            "(status NOT IN ('completed', 'cancelled') "
            "AND completed_at IS NULL "
            "AND cancelled_at IS NULL)",
            name="ck_work_items_terminal_timestamp_integrity",
        ),
        sa.CheckConstraint(
            "status NOT IN ('in_progress', 'blocked', 'completed') "
            "OR started_at IS NOT NULL",
            name="ck_work_items_started_at_required",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL "
            "OR started_at IS NULL "
            "OR completed_at >= started_at",
            name="ck_work_items_completed_after_started",
        ),
        sa.CheckConstraint(
            "cancelled_at IS NULL "
            "OR started_at IS NULL "
            "OR cancelled_at >= started_at",
            name="ck_work_items_cancelled_after_started",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_work_items_version_positive",
        ),
        sa.CheckConstraint(
            "parent_work_item_id IS NULL "
            "OR parent_work_item_id <> id",
            name="ck_work_items_not_self_parent",
        ),
        sa.CheckConstraint(
            "origin_type IN ("
            "'user', 'agent', 'system', 'api', 'integration'"
            ")",
            name="ck_work_items_origin_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(origin_reference)) >= 1",
            name="ck_work_items_origin_reference_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_work_items_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name="fk_work_items_subject_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_work_item_id"],
            ["work_items.id"],
            name=(
                "fk_work_items_parent_work_item_id_"
                "work_items"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_work_items_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"],
            ["users.id"],
            name="fk_work_items_assignee_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_work_items_status_priority_due_at",
        "work_items",
        ["status", "priority", "due_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_account_status_due_at",
        "work_items",
        ["account_id", "status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_user_status_due_at",
        "work_items",
        ["subject_user_id", "status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_assignee_status_priority",
        "work_items",
        ["assignee_user_id", "status", "priority", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_parent_status",
        "work_items",
        ["parent_work_item_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_origin_type_reference",
        "work_items",
        ["origin_type", "origin_reference"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_open_due_at",
        "work_items",
        ["due_at", "priority"],
        unique=False,
        postgresql_where=sa.text(
            "status NOT IN ('completed', 'cancelled') "
            "AND due_at IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "status NOT IN ('completed', 'cancelled') "
            "AND due_at IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_work_items_global_key",
        "work_items",
        ["work_key"],
        unique=True,
        postgresql_where=sa.text(
            "scope_type = 'global' "
            "AND work_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "scope_type = 'global' "
            "AND work_key IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_work_items_account_key",
        "work_items",
        ["account_id", "work_key"],
        unique=True,
        postgresql_where=sa.text(
            "scope_type = 'account' "
            "AND work_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "scope_type = 'account' "
            "AND work_key IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_work_items_user_key",
        "work_items",
        ["subject_user_id", "work_key"],
        unique=True,
        postgresql_where=sa.text(
            "scope_type = 'user' "
            "AND work_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "scope_type = 'user' "
            "AND work_key IS NOT NULL"
        ),
    )

    op.create_table(
        "work_dependencies",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "depends_on_work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "dependency_type",
            sa.String(length=32),
            server_default="finish_to_start",
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
        sa.CheckConstraint(
            "work_item_id <> depends_on_work_item_id",
            name="ck_work_dependencies_not_self",
        ),
        sa.CheckConstraint(
            "dependency_type IN ("
            "'finish_to_start', 'start_to_start', "
            "'finish_to_finish', 'start_to_finish'"
            ")",
            name="ck_work_dependencies_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=(
                "fk_work_dependencies_work_item_id_"
                "work_items"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_work_item_id"],
            ["work_items.id"],
            name=(
                "fk_work_dependencies_depends_on_work_item_id_"
                "work_items"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=(
                "fk_work_dependencies_created_by_user_id_"
                "users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            "depends_on_work_item_id",
            name="uq_work_dependencies_pair",
        ),
    )
    op.create_index(
        "ix_work_dependencies_depends_on_type",
        "work_dependencies",
        ["depends_on_work_item_id", "dependency_type"],
        unique=False,
    )

    op.create_table(
        "work_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "actor_type",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "actor_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "event_data",
            WORK_JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'created', 'details_changed', 'status_changed', "
            "'priority_changed', 'assignee_changed', "
            "'schedule_changed', 'dependency_added', "
            "'dependency_removed', 'memory_linked', "
            "'memory_unlinked', 'comment_added', 'system_note'"
            ")",
            name="ck_work_events_event_type_valid",
        ),
        sa.CheckConstraint(
            "actor_type IN ("
            "'user', 'agent', 'system', 'integration'"
            ")",
            name="ck_work_events_actor_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_work_events_actor_reference_not_blank",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL "
            "OR char_length(btrim(idempotency_key)) >= 1",
            name="ck_work_events_idempotency_key_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_events_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_work_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            "idempotency_key",
            name="uq_work_events_item_idempotency_key",
        ),
    )
    op.create_index(
        "ix_work_events_item_created_at",
        "work_events",
        ["work_item_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_work_events_type_created_at",
        "work_events",
        ["event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_events_actor_reference",
        "work_events",
        ["actor_type", "actor_reference"],
        unique=False,
    )

    op.create_table(
        "work_memory_links",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "relation",
            sa.String(length=20),
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
        sa.CheckConstraint(
            "relation IN ('context', 'source', 'decision', 'outcome')",
            name="ck_work_memory_links_relation_valid",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=(
                "fk_work_memory_links_work_item_id_"
                "work_items"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memory_items.id"],
            name=(
                "fk_work_memory_links_memory_id_"
                "memory_items"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=(
                "fk_work_memory_links_created_by_user_id_"
                "users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            "memory_id",
            "relation",
            name="uq_work_memory_links_item_memory_relation",
        ),
    )
    op.create_index(
        "ix_work_memory_links_memory_relation",
        "work_memory_links",
        ["memory_id", "relation"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_memory_links_memory_relation",
        table_name="work_memory_links",
    )
    op.drop_table("work_memory_links")

    op.drop_index(
        "ix_work_events_actor_reference",
        table_name="work_events",
    )
    op.drop_index(
        "ix_work_events_type_created_at",
        table_name="work_events",
    )
    op.drop_index(
        "ix_work_events_item_created_at",
        table_name="work_events",
    )
    op.drop_table("work_events")

    op.drop_index(
        "ix_work_dependencies_depends_on_type",
        table_name="work_dependencies",
    )
    op.drop_table("work_dependencies")

    op.drop_index(
        "uq_work_items_user_key",
        table_name="work_items",
        postgresql_where=sa.text(
            "scope_type = 'user' "
            "AND work_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "scope_type = 'user' "
            "AND work_key IS NOT NULL"
        ),
    )
    op.drop_index(
        "uq_work_items_account_key",
        table_name="work_items",
        postgresql_where=sa.text(
            "scope_type = 'account' "
            "AND work_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "scope_type = 'account' "
            "AND work_key IS NOT NULL"
        ),
    )
    op.drop_index(
        "uq_work_items_global_key",
        table_name="work_items",
        postgresql_where=sa.text(
            "scope_type = 'global' "
            "AND work_key IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "scope_type = 'global' "
            "AND work_key IS NOT NULL"
        ),
    )
    op.drop_index(
        "ix_work_items_open_due_at",
        table_name="work_items",
        postgresql_where=sa.text(
            "status NOT IN ('completed', 'cancelled') "
            "AND due_at IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "status NOT IN ('completed', 'cancelled') "
            "AND due_at IS NOT NULL"
        ),
    )
    op.drop_index(
        "ix_work_items_origin_type_reference",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_parent_status",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_assignee_status_priority",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_user_status_due_at",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_account_status_due_at",
        table_name="work_items",
    )
    op.drop_index(
        "ix_work_items_status_priority_due_at",
        table_name="work_items",
    )
    op.drop_table("work_items")
