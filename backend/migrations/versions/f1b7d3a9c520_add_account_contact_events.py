"""add account_contact_events (A5: contato e promessa por titulo)

Revision ID: f1b7d3a9c520
Revises: c8e2a5f1d704
Create Date: 2026-11-18 00:00:00.000000

Somente create_table e create_index (tabela lateral, so inclusao). accounts
nao e alterada. A garantia de append-only e da aplicacao: nao ha rota nem
repositorio de update/delete.
"""

import sqlalchemy as sa
from alembic import op


revision = "f1b7d3a9c520"
down_revision = "c8e2a5f1d704"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_contact_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(
                "accounts.id",
                ondelete="RESTRICT",
                name="fk_account_contact_events_account_id",
            ),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("promised_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(280), nullable=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="fk_account_contact_events_actor_user_id",
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_account_contact_events_idempotency_key",
        ),
        sa.CheckConstraint(
            "event_type IN ('contato', 'promessa', 'remarcacao', "
            "'encerramento')",
            name="ck_account_contact_events_event_type_valid",
        ),
        sa.CheckConstraint(
            "(event_type IN ('promessa', 'remarcacao') "
            "AND promised_date IS NOT NULL) "
            "OR (event_type IN ('contato', 'encerramento') "
            "AND promised_date IS NULL)",
            name="ck_account_contact_events_promised_date_by_type",
        ),
        sa.CheckConstraint(
            "note IS NULL OR (char_length(btrim(note)) >= 1 "
            "AND char_length(note) <= 280)",
            name="ck_account_contact_events_note_size",
        ),
        sa.CheckConstraint(
            "char_length(btrim(idempotency_key)) >= 1",
            name="ck_account_contact_events_idempotency_key_not_blank",
        ),
    )
    op.create_index(
        "ix_account_contact_events_account_created",
        "account_contact_events",
        ["account_id", "created_at"],
    )


def downgrade() -> None:
    # Destrutivo: apaga o historico de contatos e promessas. Somente em
    # banco descartavel ou com aprovacao explicita e backup previo.
    op.drop_index(
        "ix_account_contact_events_account_created",
        table_name="account_contact_events",
    )
    op.drop_table("account_contact_events")
