"""add escalation_observations

Revision ID: 7432a1c2dd66
Revises: a3f7c9d15e28
Create Date: 2026-09-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "7432a1c2dd66"
down_revision = "a3f7c9d15e28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalation_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "escalation_work_item_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "work_items.id",
                name="fk_escalation_observations_work_item_id_work_items",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("observation_type", sa.String(20), nullable=False),
        sa.Column(
            "linked_account_event_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "account_events.id",
                name=(
                    "fk_escalation_observations_account_event_id_"
                    "account_events"
                ),
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column("assessment_code", sa.String(40), nullable=True),
        sa.Column(
            "declared_by_user_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name=(
                    "fk_escalation_observations_declared_by_user_id_"
                    "users"
                ),
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column("declared_by_role", sa.String(32), nullable=True),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "declared_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "observation_type IN ('observed_fact', 'human_assessment')",
            name="ck_escalation_observations_type_valid",
        ),
        sa.CheckConstraint(
            "assessment_code IS NULL OR assessment_code IN ("
            "'contact_made', 'payment_promised', 'payment_refused', "
            "'unreachable', 'partial_agreement'"
            ")",
            name="ck_escalation_observations_assessment_code_valid",
        ),
        sa.CheckConstraint(
            "declared_by_role IS NULL OR declared_by_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_escalation_observations_declared_by_role_valid",
        ),
        sa.CheckConstraint(
            "(observation_type = 'observed_fact' "
            "AND linked_account_event_id IS NOT NULL "
            "AND assessment_code IS NULL "
            "AND declared_by_user_id IS NULL "
            "AND declared_by_role IS NULL "
            "AND observed_at IS NOT NULL "
            "AND declared_at IS NULL) "
            "OR (observation_type = 'human_assessment' "
            "AND linked_account_event_id IS NULL "
            "AND assessment_code IS NOT NULL "
            "AND declared_by_user_id IS NOT NULL "
            "AND declared_by_role IS NOT NULL "
            "AND declared_at IS NOT NULL "
            "AND observed_at IS NULL)",
            name="ck_escalation_observations_type_disjoint",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL "
            "OR char_length(btrim(idempotency_key)) >= 1",
            name=(
                "ck_escalation_observations_"
                "idempotency_key_not_blank"
            ),
        ),
        sa.UniqueConstraint(
            "escalation_work_item_id",
            "idempotency_key",
            name=(
                "uq_escalation_observations_"
                "work_item_idempotency"
            ),
        ),
    )
    op.create_index(
        "ix_escalation_observations_work_item_created",
        "escalation_observations",
        ["escalation_work_item_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_escalation_observations_work_item_created",
        table_name="escalation_observations",
    )
    op.drop_table("escalation_observations")
