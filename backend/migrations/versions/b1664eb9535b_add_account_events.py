"""add account events

Revision ID: b1664eb9535b
Revises: c829becaaacc
"""
from alembic import op
import sqlalchemy as sa

revision = "b1664eb9535b"
down_revision = "c829becaaacc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_reference", sa.String(length=255), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("previous_status", sa.String(length=30), nullable=True),
        sa.Column("new_status", sa.String(length=30), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('status_changed')",
            name="ck_account_events_event_type_valid",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'system', 'integration')",
            name="ck_account_events_actor_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_account_events_actor_reference_not_blank",
        ),
        sa.CheckConstraint(
            "new_status IN ('aberto', 'atrasado', 'pago')",
            name="ck_account_events_new_status_valid",
        ),
        sa.CheckConstraint(
            "previous_status IS NULL OR previous_status IN "
            "('aberto', 'atrasado', 'pago')",
            name="ck_account_events_previous_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_events_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_account_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_events_account_occurred",
        "account_events",
        ["account_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_events_account_occurred",
        table_name="account_events",
    )
    op.drop_table("account_events")
