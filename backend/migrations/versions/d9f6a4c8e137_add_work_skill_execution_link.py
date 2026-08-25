"""add durable Work Skill execution linkage

Revision ID: d9f6a4c8e137
Revises: c8e5f3b7d026
"""

from alembic import op
import sqlalchemy as sa


revision = "d9f6a4c8e137"
down_revision = "c8e5f3b7d026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_skill_executions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("work_item_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_version_id", sa.BigInteger(), nullable=False),
        sa.Column("approval_request_id", sa.BigInteger(), nullable=True),
        sa.Column("approval_consumption_id", sa.BigInteger(), nullable=True),
        sa.Column("skill_invocation_id", sa.BigInteger(), nullable=True),
        sa.Column("authority_user_id", sa.Integer(), nullable=True),
        sa.Column("authority_role", sa.String(length=32), nullable=False),
        sa.Column(
            "actor_type",
            sa.String(length=20),
            server_default="system",
            nullable=False,
        ),
        sa.Column("actor_reference", sa.String(length=255), nullable=False),
        sa.Column("dispatch_key", sa.String(length=255), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="configured",
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type = 'system'",
            name="ck_work_skill_executions_actor_system",
        ),
        sa.CheckConstraint(
            "actor_reference LIKE 'system:work:%' "
            "AND char_length(btrim(actor_reference)) >= 13",
            name="ck_work_skill_executions_actor_reference",
        ),
        sa.CheckConstraint(
            "char_length(btrim(dispatch_key)) >= 1 "
            "AND dispatch_key = lower(btrim(dispatch_key))",
            name="ck_work_skill_executions_dispatch_key",
        ),
        sa.CheckConstraint(
            "execution_mode IN ('read_only', 'mutating')",
            name="ck_work_skill_executions_mode_valid",
        ),
        sa.CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_work_skill_executions_input_digest",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'configured', 'approval_pending', 'ready', "
            "'succeeded', 'failed', 'timed_out', 'cancelled'"
            ")",
            name="ck_work_skill_executions_status_valid",
        ),
        sa.CheckConstraint(
            "dispatch_attempts >= 0",
            name="ck_work_skill_executions_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "authority_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_work_skill_executions_authority_role",
        ),
        sa.CheckConstraint(
            "("
            "execution_mode = 'read_only' "
            "AND approval_request_id IS NULL "
            "AND approval_consumption_id IS NULL"
            ") OR ("
            "execution_mode = 'mutating' "
            "AND (status = 'configured' OR approval_request_id IS NOT NULL)"
            ")",
            name="ck_work_skill_executions_approval_shape",
        ),
        sa.CheckConstraint(
            "("
            "status IN ('configured', 'approval_pending', 'ready') "
            "AND finished_at IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'succeeded' "
            "AND skill_invocation_id IS NOT NULL "
            "AND finished_at IS NOT NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status IN ('failed', 'timed_out', 'cancelled') "
            "AND finished_at IS NOT NULL "
            "AND last_error_code IS NOT NULL "
            "AND char_length(btrim(last_error_code)) >= 1"
            ")",
            name="ck_work_skill_executions_terminal_integrity",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL "
            "OR finished_at >= started_at",
            name="ck_work_skill_executions_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_skill_executions_work_item_id_work_items",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name=(
                "fk_work_skill_executions_skill_version_id_skill_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name=(
                "fk_work_skill_executions_approval_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_consumption_id"],
            ["approval_consumptions.id"],
            name="fk_work_skill_exec_approval_consumption",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_invocation_id"],
            ["skill_invocations.id"],
            name=(
                "fk_work_skill_executions_skill_invocation_id_"
                "skill_invocations"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authority_user_id"],
            ["users.id"],
            name="fk_work_skill_executions_authority_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            name="uq_work_skill_executions_work_item",
        ),
        sa.UniqueConstraint(
            "dispatch_key",
            name="uq_work_skill_executions_dispatch_key",
        ),
        sa.UniqueConstraint(
            "approval_request_id",
            name="uq_work_skill_executions_approval_request",
        ),
        sa.UniqueConstraint(
            "approval_consumption_id",
            name="uq_work_skill_executions_approval_consumption",
        ),
        sa.UniqueConstraint(
            "skill_invocation_id",
            name="uq_work_skill_executions_skill_invocation",
        ),
    )

    op.create_index(
        "ix_work_skill_executions_status_updated",
        "work_skill_executions",
        ["status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_work_skill_executions_authority_status",
        "work_skill_executions",
        ["authority_user_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_work_skill_executions_version_status",
        "work_skill_executions",
        ["skill_version_id", "status", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_skill_executions_version_status",
        table_name="work_skill_executions",
    )
    op.drop_index(
        "ix_work_skill_executions_authority_status",
        table_name="work_skill_executions",
    )
    op.drop_index(
        "ix_work_skill_executions_status_updated",
        table_name="work_skill_executions",
    )
    op.drop_table("work_skill_executions")
