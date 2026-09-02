"""widen users role check constraint for system service accounts

Revision ID: c829becaaacc
Revises: f2a9c7e4b318
Create Date: 2026-09-02 10:00:19.819547

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c829becaaacc'
down_revision: Union[str, Sequence[str], None] = 'f2a9c7e4b318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "ck_users_role_valid",
        "users",
        type_="check",
    )
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('viewer', 'analyst', 'manager', 'executive', "
        "'administrator', 'developer', 'system')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_users_role_valid",
        "users",
        type_="check",
    )
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('viewer', 'analyst', 'manager', 'executive', "
        "'administrator', 'developer')",
    )
