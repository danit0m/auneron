"""add business effect verifications

Revision ID: dfccb26f0711
Revises: 298a1e501b39
Create Date: 2026-09-23 01:11:25.874500

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfccb26f0711'
down_revision: Union[str, Sequence[str], None] = '298a1e501b39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "business_effect_verifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "approval_consumption_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column("skill_key", sa.String(length=128), nullable=False),
        sa.Column("target_account_id", sa.Integer(), nullable=True),
        sa.Column("expected_status", sa.String(length=16), nullable=False),
        sa.Column("account_event_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "account_event_key_searched",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "account_status_observed",
            sa.String(length=30),
            nullable=True,
        ),
        sa.Column(
            "result",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
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
            "skill_key IN ("
            "'account.mark_overdue', 'account.mark_paid'"
            ")",
            name="ck_bev_skill_key",
        ),
        sa.CheckConstraint(
            "expected_status IN ('atrasado', 'pago')",
            name="ck_bev_expected_status",
        ),
        sa.CheckConstraint(
            "account_status_observed IS NULL "
            "OR account_status_observed IN "
            "('aberto', 'atrasado', 'pago')",
            name="ck_bev_observed_status",
        ),
        sa.CheckConstraint(
            "result IN "
            "('pending', 'verified', 'contradicted', 'unverifiable')",
            name="ck_bev_result",
        ),
        sa.CheckConstraint(
            "char_length(btrim(account_event_key_searched)) >= 1",
            name="ck_bev_key_not_blank",
        ),
        sa.CheckConstraint(
            "(result = 'pending' AND checked_at IS NULL) "
            "OR (result != 'pending' AND checked_at IS NOT NULL)",
            name="ck_bev_checked_at_terminal",
        ),
        sa.CheckConstraint(
            "(result != 'verified') "
            "OR (account_event_id IS NOT NULL "
            "AND account_status_observed IS NOT NULL)",
            name="ck_bev_verified_has_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["approval_consumption_id"],
            ["approval_consumptions.id"],
            name=(
                "fk_bev_"
                "consumption_id_approval_consumptions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_event_id"],
            ["account_events.id"],
            name=(
                "fk_bev_"
                "account_event_id_account_events"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_consumption_id",
            name="uq_bev_consumption",
        ),
    )
    op.create_index(
        "ix_bev_result_updated",
        "business_effect_verifications",
        ["result", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_bev_result_updated",
        table_name="business_effect_verifications",
    )
    op.drop_table("business_effect_verifications")
