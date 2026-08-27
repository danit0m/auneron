"""add durable authenticated advisory proposals

Revision ID: f2a9c7e4b318
Revises: c4d8e1f7a2b9
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f2a9c7e4b318"
down_revision = "c4d8e1f7a2b9"
branch_labels = None
depends_on = None


ADVISORY_PROPOSAL_JSON = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "authenticated_advisory_proposals",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "authority_user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "auth_session_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "authority_source",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "protocol",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "snapshot_payload",
            ADVISORY_PROPOSAL_JSON,
            nullable=False,
        ),
        sa.Column(
            "snapshot_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "agent_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "binding_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "snapshot_bytes",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "authority_user_id > 0",
            name="ck_authenticated_advisory_proposals_user_positive",
        ),
        sa.CheckConstraint(
            "auth_session_id > 0",
            name="ck_authenticated_advisory_proposals_session_positive",
        ),
        sa.CheckConstraint(
            "authority_source = 'authenticated_http_session'",
            name="ck_authenticated_advisory_proposals_source",
        ),
        sa.CheckConstraint(
            "protocol = 'authenticated_advisory_v1'",
            name="ck_authenticated_advisory_proposals_protocol",
        ),
        sa.CheckConstraint(
            "char_length(btrim(idempotency_key)) >= 1 "
            "AND idempotency_key = lower(btrim(idempotency_key)) "
            "AND idempotency_key ~ "
            "'^[a-z0-9][a-z0-9._:-]{0,254}$'",
            name="ck_authenticated_advisory_proposals_idempotency_key",
        ),
        sa.CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_authenticated_advisory_proposals_digest",
        ),
        sa.CheckConstraint(
            "agent_count >= 0 AND agent_count <= 32",
            name="ck_authenticated_advisory_proposals_agent_count",
        ),
        sa.CheckConstraint(
            "binding_count >= 0 AND binding_count <= 512",
            name="ck_authenticated_advisory_proposals_binding_count",
        ),
        sa.CheckConstraint(
            "snapshot_bytes >= 2 AND snapshot_bytes <= 65536",
            name="ck_authenticated_advisory_proposals_snapshot_bytes",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "authority_user_id",
            "auth_session_id",
            "idempotency_key",
            name=(
                "uq_authenticated_advisory_proposals_"
                "authority_session_key"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_table(
        "authenticated_advisory_proposals"
    )
