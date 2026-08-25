"""add approval foundation

Revision ID: b7d4e2a6c915
Revises: f6c9a1d4b702
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b7d4e2a6c915"
down_revision: str | None = "f6c9a1d4b702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "action_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "requester_actor_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "requester_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "idempotency_key",
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
            "risk_level",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "required_permission",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "target_account_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_type IN ('skill_execution')",
            name="ck_approval_requests_action_type_valid",
        ),
        sa.CheckConstraint(
            "requester_actor_type IN ("
            "'user', 'agent', 'system', 'integration'"
            ")",
            name="ck_approval_requests_actor_type_valid",
        ),
        sa.CheckConstraint(
            "requester_actor_type = 'user' "
            "OR requester_user_id IS NULL",
            name="ck_approval_requests_non_user_has_no_user_id",
        ),
        sa.CheckConstraint(
            "char_length(btrim(requester_reference)) >= 1",
            name="ck_approval_requests_requester_ref_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(idempotency_key)) >= 1",
            name="ck_approval_requests_idempotency_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(request_fingerprint) = 64 "
            "AND request_fingerprint = lower(request_fingerprint)",
            name="ck_approval_requests_fingerprint_format",
        ),
        sa.CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_approval_requests_input_digest_format",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_approval_requests_risk_level_valid",
        ),
        sa.CheckConstraint(
            "required_permission IN ("
            "'approval:decide', 'approval:decide_sensitive'"
            ")",
            name="ck_approval_requests_permission_valid",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'approved', 'rejected', 'expired', 'cancelled'"
            ")",
            name="ck_approval_requests_status_valid",
        ),
        sa.CheckConstraint(
            "target_account_id IS NULL OR target_account_id > 0",
            name="ck_approval_requests_account_id_positive",
        ),
        sa.CheckConstraint(
            "target_user_id IS NULL OR target_user_id > 0",
            name="ck_approval_requests_target_user_id_positive",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_approval_requests_expiration_order",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_approval_requests_status_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"],
            ["users.id"],
            name=(
                "fk_approval_requests_requester_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name=(
                "fk_approval_requests_skill_version_id_"
                "skill_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requester_actor_type",
            "requester_reference",
            "idempotency_key",
            name="uq_approval_requests_requester_idempotency",
        ),
    )

    op.create_index(
        "ix_approval_requests_requester_created",
        "approval_requests",
        [
            "requester_actor_type",
            "requester_reference",
            "created_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_status_expires",
        "approval_requests",
        [
            "status",
            "expires_at",
            "id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_version_status",
        "approval_requests",
        [
            "skill_version_id",
            "status",
            "id",
        ],
        unique=False,
    )

    op.create_table(
        "approval_decisions",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "approval_request_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "decision",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "decided_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "decided_by_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "decided_by_role",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "permission_used",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "decision_note",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_approval_decisions_decision_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(decided_by_reference)) >= 1",
            name="ck_approval_decisions_decider_ref_not_blank",
        ),
        sa.CheckConstraint(
            "decided_by_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_approval_decisions_role_valid",
        ),
        sa.CheckConstraint(
            "permission_used IN ("
            "'approval:decide', 'approval:decide_sensitive'"
            ")",
            name="ck_approval_decisions_permission_valid",
        ),
        sa.CheckConstraint(
            "decision_note IS NULL OR ("
            "char_length(btrim(decision_note)) >= 1 "
            "AND char_length(decision_note) <= 500"
            ")",
            name="ck_approval_decisions_note_valid",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name=(
                "fk_approval_decisions_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=(
                "fk_approval_decisions_decided_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_request_id",
            name="uq_approval_decisions_request",
        ),
    )

    op.create_index(
        "ix_approval_decisions_decider_created",
        "approval_decisions",
        [
            "decided_by_user_id",
            "created_at",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_decisions_decider_created",
        table_name="approval_decisions",
    )
    op.drop_table(
        "approval_decisions"
    )

    op.drop_index(
        "ix_approval_requests_version_status",
        table_name="approval_requests",
    )
    op.drop_index(
        "ix_approval_requests_status_expires",
        table_name="approval_requests",
    )
    op.drop_index(
        "ix_approval_requests_requester_created",
        table_name="approval_requests",
    )
    op.drop_table(
        "approval_requests"
    )
