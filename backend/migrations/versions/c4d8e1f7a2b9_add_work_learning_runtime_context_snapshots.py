"""add immutable Work learning runtime context snapshots

Revision ID: c4d8e1f7a2b9
Revises: b7e4a2c9d615
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c4d8e1f7a2b9"
down_revision = "b7e4a2c9d615"
branch_labels = None
depends_on = None


RUNTIME_CONTEXT_JSON = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "work_learning_runtime_context_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_skill_execution_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "protocol",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "context_payload",
            RUNTIME_CONTEXT_JSON,
            nullable=False,
        ),
        sa.Column(
            "context_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "item_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "context_bytes",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "resolved_as_of",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "protocol = 'work_learning_v1'",
            name="ck_work_learning_runtime_context_protocol",
        ),
        sa.CheckConstraint(
            "context_digest ~ '^[0-9a-f]{64}$'",
            name="ck_work_learning_runtime_context_digest",
        ),
        sa.CheckConstraint(
            "item_count >= 0 AND item_count <= 10",
            name="ck_work_learning_runtime_context_item_count",
        ),
        sa.CheckConstraint(
            "context_bytes >= 1 AND context_bytes <= 16384",
            name="ck_work_learning_runtime_context_bytes",
        ),
        sa.ForeignKeyConstraint(
            ["work_skill_execution_id"],
            ["work_skill_executions.id"],
            name="fk_work_learning_runtime_context_execution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_learning_runtime_context_work",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name="fk_work_learning_runtime_context_skill_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "work_skill_execution_id",
            name="uq_work_learning_runtime_context_execution",
        ),
    )


def downgrade() -> None:
    op.drop_table(
        "work_learning_runtime_context_snapshots"
    )
