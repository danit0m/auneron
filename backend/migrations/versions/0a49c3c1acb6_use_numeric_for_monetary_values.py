"""use numeric for monetary values

Revision ID: 0a49c3c1acb6
Revises: 558931d55c94
Create Date: 2026-08-05 16:19:42.593730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a49c3c1acb6'
down_revision: Union[str, Sequence[str], None] = '558931d55c94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Converte valores monetários para precisão decimal."""

    op.alter_column(
        "accounts",
        "valor",
        existing_type=sa.DOUBLE_PRECISION(precision=53),
        type_=sa.Numeric(
            precision=14,
            scale=2,
        ),
        existing_nullable=False,
        postgresql_using="ROUND(valor::numeric, 2)",
    )


def downgrade() -> None:
    """Retorna valores monetários para ponto flutuante."""

    op.alter_column(
        "accounts",
        "valor",
        existing_type=sa.Numeric(
            precision=14,
            scale=2,
        ),
        type_=sa.DOUBLE_PRECISION(precision=53),
        existing_nullable=False,
        postgresql_using="valor::double precision",
    )