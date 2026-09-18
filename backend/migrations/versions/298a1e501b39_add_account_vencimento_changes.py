"""add account vencimento changes

Revision ID: 298a1e501b39
Revises: c4718a49cebf
Create Date: 2026-09-18 13:01:23.158379

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '298a1e501b39'
down_revision: Union[str, Sequence[str], None] = 'c4718a49cebf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "account_vencimento_changes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("previous_vencimento", sa.Date(), nullable=False),
        sa.Column("new_vencimento", sa.Date(), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_reference", sa.String(length=255), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "previous_vencimento <> new_vencimento",
            name="ck_account_vencimento_changes_actual_change",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'system', 'integration')",
            name="ck_account_vencimento_changes_actor_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_account_vencimento_changes_actor_reference_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_vencimento_changes_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_account_vencimento_changes_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_vencimento_changes_account_changed",
        "account_vencimento_changes",
        ["account_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_account_vencimento_changes_account_changed",
        table_name="account_vencimento_changes",
    )
    op.drop_table("account_vencimento_changes")
