"""add users temp password columns (B1: credencial temporaria e troca obrigatoria)

Revision ID: a7c4e9b2d618
Revises: f1b7d3a9c520
Create Date: 2026-11-18 00:00:00.000000

Somente add_column em users. Nenhuma tabela criada, nenhuma constraint tocada.
"""

import sqlalchemy as sa
from alembic import op


revision = "a7c4e9b2d618"
down_revision = "f1b7d3a9c520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "temp_password_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "temp_password_expires_at")
    op.drop_column("users", "must_change_password")
