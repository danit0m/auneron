"""add account foreign key to knowledge

Revision ID: 0c9723753bb9
Revises: 0a49c3c1acb6
Create Date: 2026-08-05 18:15:50.408336

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0c9723753bb9"
down_revision: Union[str, Sequence[str], None] = "0a49c3c1acb6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Adiciona integridade referencial aos conhecimentos."""

    op.create_foreign_key(
        "fk_knowledge_account_id_accounts",
        "knowledge",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove a integridade referencial dos conhecimentos."""

    op.drop_constraint(
        "fk_knowledge_account_id_accounts",
        "knowledge",
        type_="foreignkey",
    )
