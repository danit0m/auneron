"""add governed approval consumption

Revision ID: c8e5f3b7d026
Revises: b7d4e2a6c915
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8e5f3b7d026"
down_revision: str | None = "b7d4e2a6c915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approval_decisions",
        sa.Column(
            "sensitive_elevation_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "approval_consumptions",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
        ),
        sa.Column(
            "approval_request_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "approval_decision_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_invocation_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "consumer_actor_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "consumer_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "authority_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "authority_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "authority_role",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "runtime_idempotency_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "request_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "input_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="reserved",
        ),
        sa.Column(
            "error_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name=(
                "fk_approval_consumptions_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_decision_id"],
            ["approval_decisions.id"],
            name=(
                "fk_approval_consumptions_decision_id_"
                "approval_decisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_invocation_id"],
            ["skill_invocations.id"],
            name=(
                "fk_approval_consumptions_invocation_id_"
                "skill_invocations"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authority_user_id"],
            ["users.id"],
            name=(
                "fk_approval_consumptions_authority_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "consumer_actor_type IN "
            "('agent', 'system', 'integration')",
            name=(
                "ck_approval_consumptions_actor_type_valid"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(consumer_reference)) >= 1",
            name=(
                "ck_approval_consumptions_consumer_ref_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(authority_reference)) >= 1",
            name=(
                "ck_approval_consumptions_authority_ref_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "authority_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name=(
                "ck_approval_consumptions_authority_role_valid"
            ),
        ),
        sa.CheckConstraint(
            "authority_user_id IS NULL OR authority_user_id > 0",
            name=(
                "ck_approval_consumptions_authority_user_positive"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(runtime_idempotency_key)) >= 1 "
            "AND runtime_idempotency_key = "
            "lower(btrim(runtime_idempotency_key))",
            name=(
                "ck_approval_consumptions_runtime_key_valid"
            ),
        ),
        sa.CheckConstraint(
            "char_length(request_fingerprint) = 64 "
            "AND request_fingerprint = lower(request_fingerprint)",
            name=(
                "ck_approval_consumptions_fingerprint_format"
            ),
        ),
        sa.CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name=(
                "ck_approval_consumptions_input_digest_format"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'consumed', 'failed')",
            name=(
                "ck_approval_consumptions_status_valid"
            ),
        ),
        sa.CheckConstraint(
            "(status = 'reserved' "
            "AND skill_invocation_id IS NULL "
            "AND finalized_at IS NULL "
            "AND error_code IS NULL) "
            "OR (status = 'consumed' "
            "AND skill_invocation_id IS NOT NULL "
            "AND finalized_at IS NOT NULL "
            "AND error_code IS NULL) "
            "OR (status = 'failed' "
            "AND skill_invocation_id IS NULL "
            "AND finalized_at IS NOT NULL "
            "AND error_code IS NOT NULL "
            "AND char_length(btrim(error_code)) >= 1)",
            name=(
                "ck_approval_consumptions_state_integrity"
            ),
        ),
        sa.UniqueConstraint(
            "approval_request_id",
            name=(
                "uq_approval_consumptions_request"
            ),
        ),
        sa.UniqueConstraint(
            "approval_decision_id",
            name=(
                "uq_approval_consumptions_decision"
            ),
        ),
        sa.UniqueConstraint(
            "skill_invocation_id",
            name=(
                "uq_approval_consumptions_invocation"
            ),
        ),
    )

    op.create_index(
        "ix_approval_consumptions_status_reserved",
        "approval_consumptions",
        [
            "status",
            "reserved_at",
            "id",
        ],
    )
    op.create_index(
        "ix_approval_consumptions_authority_reserved",
        "approval_consumptions",
        [
            "authority_user_id",
            "reserved_at",
            "id",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_consumptions_authority_reserved",
        table_name="approval_consumptions",
    )
    op.drop_index(
        "ix_approval_consumptions_status_reserved",
        table_name="approval_consumptions",
    )
    op.drop_table(
        "approval_consumptions"
    )
    op.drop_column(
        "approval_decisions",
        "sensitive_elevation_verified",
    )
