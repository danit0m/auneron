"""Add fields for confirmed receivable facts."""

import sqlalchemy as sa
from alembic import op

revision = "9b7a5d2e610f"
down_revision = "e9a4c7d2b135"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("balance_cents", sa.BigInteger()))
    op.add_column("accounts", sa.Column("balance_source", sa.String(255)))
    op.add_column("accounts", sa.Column("balance_observed_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("financial_hold", sa.Boolean()))
    op.add_column("accounts", sa.Column("hold_reason", sa.String(255)))
    op.add_column("accounts", sa.Column("hold_reviewed_by", sa.Integer()))
    op.add_column("accounts", sa.Column("hold_reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("accounts", sa.Column("balance_reviewed_by", sa.Integer()))
    op.add_column("accounts", sa.Column("grace_days", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_accounts_confirmed_balance", "accounts",
        "balance_cents IS NULL OR (balance_cents >= 0 AND "
        "balance_source IS NOT NULL AND balance_observed_at IS NOT NULL)",
    )
    op.create_check_constraint("ck_accounts_grace_days", "accounts", "grace_days >= 0")
    op.create_check_constraint(
        "ck_accounts_hold_reason", "accounts",
        "financial_hold IS NOT TRUE OR hold_reason IS NOT NULL",
    )


def downgrade():
    op.drop_constraint("ck_accounts_hold_reason", "accounts", type_="check")
    op.drop_constraint("ck_accounts_grace_days", "accounts", type_="check")
    op.drop_constraint("ck_accounts_confirmed_balance", "accounts", type_="check")
    for column in ("balance_reviewed_by", "hold_reviewed_at", "hold_reviewed_by", "grace_days", "hold_reason", "financial_hold", "balance_observed_at",
                   "balance_source", "balance_cents"):
        op.drop_column("accounts", column)
