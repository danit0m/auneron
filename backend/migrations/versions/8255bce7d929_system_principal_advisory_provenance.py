"""system principal advisory provenance

Revision ID: 8255bce7d929
Revises: b1664eb9535b
Create Date: 2026-09-11 09:20:45.996774

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8255bce7d929'
down_revision: Union[str, Sequence[str], None] = 'b1664eb9535b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "ck_authenticated_advisory_proposals_session_positive",
        "authenticated_advisory_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_authenticated_advisory_proposals_source",
        "authenticated_advisory_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_authenticated_advisory_proposals_protocol",
        "authenticated_advisory_proposals",
        type_="check",
    )

    op.alter_column(
        "authenticated_advisory_proposals",
        "auth_session_id",
        nullable=True,
    )

    op.create_check_constraint(
        "ck_authenticated_advisory_proposals_provenance",
        "authenticated_advisory_proposals",
        "("
        "  authority_source = 'authenticated_http_session'"
        "  AND protocol = 'authenticated_advisory_v1'"
        "  AND auth_session_id IS NOT NULL"
        "  AND auth_session_id > 0"
        ") OR ("
        "  authority_source = 'system_principal'"
        "  AND protocol = 'system_advisory_v1'"
        "  AND auth_session_id IS NULL"
        ")",
    )

    op.create_index(
        "uq_authenticated_advisory_proposals_system_principal_key",
        "authenticated_advisory_proposals",
        ["authority_user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "authority_source = 'system_principal'"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    system_rows = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM authenticated_advisory_proposals "
            "WHERE authority_source = 'system_principal'"
        )
    ).scalar()

    if system_rows:
        raise RuntimeError(
            "Downgrade abortado: existem "
            f"{system_rows} proposals com authority_source="
            "'system_principal'. Restaurar auth_session_id NOT NULL "
            "destruiria proveniencia auditavel. Nenhuma proposal sera "
            "apagada para viabilizar este downgrade."
        )

    op.drop_index(
        "uq_authenticated_advisory_proposals_system_principal_key",
        table_name="authenticated_advisory_proposals",
    )
    op.drop_constraint(
        "ck_authenticated_advisory_proposals_provenance",
        "authenticated_advisory_proposals",
        type_="check",
    )
    op.alter_column(
        "authenticated_advisory_proposals",
        "auth_session_id",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_authenticated_advisory_proposals_session_positive",
        "authenticated_advisory_proposals",
        "auth_session_id > 0",
    )
    op.create_check_constraint(
        "ck_authenticated_advisory_proposals_source",
        "authenticated_advisory_proposals",
        "authority_source = 'authenticated_http_session'",
    )
    op.create_check_constraint(
        "ck_authenticated_advisory_proposals_protocol",
        "authenticated_advisory_proposals",
        "protocol = 'authenticated_advisory_v1'",
    )
