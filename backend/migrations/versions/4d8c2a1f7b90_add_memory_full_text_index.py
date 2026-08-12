"""add memory full text index

Revision ID: 4d8c2a1f7b90
Revises: ed6fc40a90a4
Create Date: 2026-08-12 04:15:00.000000

"""
from typing import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d8c2a1f7b90"
down_revision: Union[str, Sequence[str], None] = (
    "ed6fc40a90a4"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEARCH_VECTOR_SQL = (
    "to_tsvector('portuguese'::regconfig, "
    "(coalesce(title, ''::character varying)::text "
    "|| ' '::text) || coalesce(content, ''::text))"
)


def upgrade() -> None:
    op.create_index(
        "ix_memory_items_search_portuguese_gin",
        "memory_items",
        [sa.text(SEARCH_VECTOR_SQL)],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_items_search_portuguese_gin",
        table_name="memory_items",
        postgresql_using="gin",
    )
