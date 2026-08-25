"""add deterministic Work outcome evaluations

Revision ID: b7e4a2c9d615
Revises: d9f6a4c8e137
"""

from alembic import op
import sqlalchemy as sa


revision = "b7e4a2c9d615"
down_revision = "d9f6a4c8e137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_outcome_evaluations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "work_skill_execution_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "terminal_status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "evaluation_code",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "learning_signal",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "evaluator_version",
            sa.String(length=32),
            server_default="deterministic_v1",
            nullable=False,
        ),
        sa.Column(
            "evaluation_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "memory_item_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "last_error_code",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
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
            "terminal_status IN ("
            "'succeeded', 'failed', 'timed_out', 'cancelled'"
            ")",
            name="ck_work_outcome_eval_terminal_status",
        ),
        sa.CheckConstraint(
            "evaluation_code IN ("
            "'execution_succeeded', 'execution_failed', "
            "'execution_timed_out', 'execution_cancelled'"
            ")",
            name="ck_work_outcome_eval_code",
        ),
        sa.CheckConstraint(
            "learning_signal IN ('positive', 'negative', 'neutral')",
            name="ck_work_outcome_eval_signal",
        ),
        sa.CheckConstraint(
            "evaluator_version = 'deterministic_v1'",
            name="ck_work_outcome_eval_version",
        ),
        sa.CheckConstraint(
            "evaluation_digest ~ '^[0-9a-f]{64}$'",
            name="ck_work_outcome_eval_digest",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'memory_recorded', 'completed', 'retry_required'"
            ")",
            name="ck_work_outcome_eval_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_work_outcome_eval_attempts",
        ),
        sa.CheckConstraint(
            "("
            "status = 'pending' "
            "AND completed_at IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'memory_recorded' "
            "AND memory_item_id IS NOT NULL "
            "AND completed_at IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'completed' "
            "AND memory_item_id IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'retry_required' "
            "AND completed_at IS NULL "
            "AND last_error_code IS NOT NULL "
            "AND char_length(btrim(last_error_code)) >= 1"
            ")",
            name="ck_work_outcome_eval_state_integrity",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= evaluated_at",
            name="ck_work_outcome_eval_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["work_skill_execution_id"],
            ["work_skill_executions.id"],
            name="fk_work_outcome_eval_execution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["memory_item_id"],
            ["memory_items.id"],
            name="fk_work_outcome_eval_memory",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_skill_execution_id",
            name="uq_work_outcome_eval_execution",
        ),
        sa.UniqueConstraint(
            "evaluation_digest",
            name="uq_work_outcome_eval_digest",
        ),
        sa.UniqueConstraint(
            "memory_item_id",
            name="uq_work_outcome_eval_memory",
        ),
    )
    op.create_index(
        "ix_work_outcome_eval_status_updated",
        "work_outcome_evaluations",
        ["status", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_outcome_eval_status_updated",
        table_name="work_outcome_evaluations",
    )
    op.drop_table(
        "work_outcome_evaluations"
    )
