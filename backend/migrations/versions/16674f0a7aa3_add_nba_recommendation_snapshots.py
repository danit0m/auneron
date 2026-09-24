"""add nba recommendation snapshots

Revision ID: 16674f0a7aa3
Revises: dfccb26f0711
Create Date: 2026-09-24 15:54:45.839891

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '16674f0a7aa3'
down_revision: Union[str, Sequence[str], None] = 'dfccb26f0711'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "nba_recommendation_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "policy_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "decision_type",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "snapshot_payload",
            sa.JSON().with_variant(
                postgresql.JSONB(), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "snapshot_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_type IN "
            "('single_action', 'action_bundle', 'no_action')",
            name="ck_nba_snapshot_decision_type",
        ),
        sa.CheckConstraint(
            "char_length(btrim(policy_version)) >= 1",
            name="ck_nba_snapshot_policy_version_not_blank",
        ),
        sa.CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_nba_snapshot_digest_format",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_nba_snapshot_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nba_snapshot_account_due_date",
        "nba_recommendation_snapshots",
        ["account_id", "due_date", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_nba_snapshot_account_due_date",
        table_name="nba_recommendation_snapshots",
    )
    op.drop_table("nba_recommendation_snapshots")
