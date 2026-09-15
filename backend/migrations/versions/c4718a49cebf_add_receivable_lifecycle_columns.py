"""add receivable lifecycle columns to knowledge

Revision ID: c4718a49cebf
Revises: 8255bce7d929
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4718a49cebf'
down_revision: Union[str, Sequence[str], None] = '8255bce7d929'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "knowledge",
        sa.Column(
            "correlation_key",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge",
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_knowledge_correlation_key"),
        "knowledge",
        ["correlation_key"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    populated_rows = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM knowledge "
            "WHERE correlation_key IS NOT NULL "
            "OR resolved_at IS NOT NULL"
        )
    ).scalar()

    if populated_rows:
        raise RuntimeError(
            "Downgrade abortado: existem "
            f"{populated_rows} linhas de knowledge com "
            "correlation_key/resolved_at preenchidos. Remover essas "
            "colunas destruiria a idempotencia auditavel do "
            "Receivables Monitor (F3). Nenhuma linha sera apagada "
            "para viabilizar este downgrade."
        )

    op.drop_index(
        op.f("ix_knowledge_correlation_key"),
        table_name="knowledge",
    )
    op.drop_column("knowledge", "resolved_at")
    op.drop_column("knowledge", "correlation_key")
