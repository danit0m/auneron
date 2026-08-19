"""add skill invocation runtime ledger

Revision ID: f6c9a1d4b702
Revises: e4a6c8d2f913
Create Date: 2026-08-18 13:20:00.000000

"""
from typing import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f6c9a1d4b702"
down_revision: Union[str, Sequence[str], None] = (
    "e4a6c8d2f913"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NULLABLE_SKILL_JSON = sa.JSON(
    none_as_null=True
).with_variant(
    postgresql.JSONB(
        none_as_null=True,
        astext_type=sa.Text(),
    ),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "skill_invocations",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "actor_type",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "actor_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
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
            server_default="running",
            nullable=False,
        ),
        sa.Column(
            "output_payload",
            NULLABLE_SKILL_JSON,
            nullable=True,
        ),
        sa.Column(
            "output_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "output_bytes",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "error_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "actor_type IN ("
            "'user', 'agent', 'system', 'integration'"
            ")",
            name="ck_skill_invocations_actor_type_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name=(
                "ck_skill_invocations_"
                "actor_reference_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR ("
            "char_length(btrim(idempotency_key)) >= 1 "
            "AND idempotency_key = "
            "lower(btrim(idempotency_key))"
            ")",
            name=(
                "ck_skill_invocations_"
                "idempotency_key_canonical"
            ),
        ),
        sa.CheckConstraint(
            "char_length(request_fingerprint) = 64 "
            "AND request_fingerprint = "
            "lower(request_fingerprint)",
            name=(
                "ck_skill_invocations_"
                "request_fingerprint_format"
            ),
        ),
        sa.CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name=(
                "ck_skill_invocations_"
                "input_digest_format"
            ),
        ),
        sa.CheckConstraint(
            "output_digest IS NULL OR ("
            "char_length(output_digest) = 64 "
            "AND output_digest = lower(output_digest)"
            ")",
            name=(
                "ck_skill_invocations_"
                "output_digest_format"
            ),
        ),
        sa.CheckConstraint(
            "status IN ("
            "'running', 'succeeded', 'failed', "
            "'timed_out', 'rejected'"
            ")",
            name="ck_skill_invocations_status_valid",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=(
                "ck_skill_invocations_"
                "duration_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "output_bytes IS NULL OR ("
            "output_bytes >= 0 "
            "AND output_bytes <= 1048576"
            ")",
            name=(
                "ck_skill_invocations_"
                "output_bytes_range"
            ),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL "
            "OR finished_at >= started_at",
            name=(
                "ck_skill_invocations_"
                "finished_after_started"
            ),
        ),
        sa.CheckConstraint(
            "("
            "status = 'running' "
            "AND finished_at IS NULL "
            "AND duration_ms IS NULL "
            "AND output_payload IS NULL "
            "AND output_digest IS NULL "
            "AND output_bytes IS NULL "
            "AND error_code IS NULL"
            ") OR ("
            "status = 'succeeded' "
            "AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL "
            "AND output_payload IS NOT NULL "
            "AND output_digest IS NOT NULL "
            "AND output_bytes IS NOT NULL "
            "AND error_code IS NULL"
            ") OR ("
            "status IN ('failed', 'timed_out', 'rejected') "
            "AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL "
            "AND output_payload IS NULL "
            "AND output_digest IS NULL "
            "AND output_bytes IS NULL "
            "AND error_code IS NOT NULL "
            "AND char_length(btrim(error_code)) >= 1"
            ")",
            name=(
                "ck_skill_invocations_"
                "terminal_integrity"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=(
                "fk_skill_invocations_"
                "actor_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name=(
                "fk_skill_invocations_"
                "version_id_skill_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_skill_invocations",
        ),
        sa.UniqueConstraint(
            "skill_version_id",
            "actor_type",
            "actor_reference",
            "idempotency_key",
            name=(
                "uq_skill_invocations_"
                "idempotency_scope"
            ),
        ),
    )

    op.create_index(
        "ix_skill_invocations_version_started",
        "skill_invocations",
        [
            "skill_version_id",
            "started_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_skill_invocations_actor_started",
        "skill_invocations",
        [
            "actor_type",
            "actor_reference",
            "started_at",
        ],
        unique=False,
    )
    op.create_index(
        "ix_skill_invocations_status_started",
        "skill_invocations",
        [
            "status",
            "started_at",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_invocations_status_started",
        table_name="skill_invocations",
    )
    op.drop_index(
        "ix_skill_invocations_actor_started",
        table_name="skill_invocations",
    )
    op.drop_index(
        "ix_skill_invocations_version_started",
        table_name="skill_invocations",
    )
    op.drop_table(
        "skill_invocations"
    )
