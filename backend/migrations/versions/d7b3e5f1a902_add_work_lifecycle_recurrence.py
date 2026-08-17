"""add work lifecycle recurrence

Revision ID: d7b3e5f1a902
Revises: c2f7a9d4e681
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d7b3e5f1a902"
down_revision: str | None = "c2f7a9d4e681"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVENT_TYPES_22C = (
    "event_type IN ("
    "'created', "
    "'details_changed', "
    "'status_changed', "
    "'priority_changed', "
    "'assignee_changed', "
    "'schedule_changed', "
    "'dependency_added', "
    "'dependency_removed', "
    "'memory_linked', "
    "'memory_unlinked', "
    "'comment_added', "
    "'system_note', "
    "'recurrence_configured', "
    "'recurrence_disabled', "
    "'recurrence_generated'"
    ")"
)


EVENT_TYPES_22A = (
    "event_type IN ("
    "'created', "
    "'details_changed', "
    "'status_changed', "
    "'priority_changed', "
    "'assignee_changed', "
    "'schedule_changed', "
    "'dependency_added', "
    "'dependency_removed', "
    "'memory_linked', "
    "'memory_unlinked', "
    "'comment_added', "
    "'system_note'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_work_events_event_type_valid",
        "work_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_events_event_type_valid",
        "work_events",
        EVENT_TYPES_22C,
    )

    op.create_table(
        "work_recurrence_rules",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "frequency",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "interval_value",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "timezone_name",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "starts_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ends_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "max_occurrences",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "generated_occurrences",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "next_occurrence_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_occurrence_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "sla_lead_minutes",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_work_recurrence_rules_frequency_valid",
        ),
        sa.CheckConstraint(
            "interval_value >= 1 AND interval_value <= 365",
            name="ck_work_recurrence_rules_interval_range",
        ),
        sa.CheckConstraint(
            "char_length(btrim(timezone_name)) >= 1",
            name="ck_work_recurrence_rules_timezone_not_blank",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_work_recurrence_rules_time_range",
        ),
        sa.CheckConstraint(
            "max_occurrences IS NULL OR max_occurrences >= 1",
            name=(
                "ck_work_recurrence_rules_"
                "max_occurrences_positive"
            ),
        ),
        sa.CheckConstraint(
            "generated_occurrences >= 0",
            name=(
                "ck_work_recurrence_rules_"
                "generated_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "max_occurrences IS NULL "
            "OR generated_occurrences <= max_occurrences",
            name=(
                "ck_work_recurrence_rules_"
                "generated_within_max"
            ),
        ),
        sa.CheckConstraint(
            "sla_lead_minutes IS NULL "
            "OR (sla_lead_minutes >= 0 "
            "AND sla_lead_minutes <= 525600)",
            name="ck_work_recurrence_rules_sla_lead_range",
        ),
        sa.CheckConstraint(
            "next_occurrence_at IS NULL "
            "OR next_occurrence_at >= starts_at",
            name="ck_work_recurrence_rules_next_after_start",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL "
            "OR next_occurrence_at IS NULL "
            "OR next_occurrence_at <= ends_at",
            name="ck_work_recurrence_rules_next_before_end",
        ),
        sa.CheckConstraint(
            "last_occurrence_at IS NULL "
            "OR next_occurrence_at IS NULL "
            "OR next_occurrence_at > last_occurrence_at",
            name="ck_work_recurrence_rules_next_after_last",
        ),
        sa.CheckConstraint(
            "(active AND next_occurrence_at IS NOT NULL) "
            "OR (NOT active AND next_occurrence_at IS NULL)",
            name="ck_work_recurrence_rules_active_next_integrity",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=(
                "fk_work_recurrence_rules_"
                "work_item_id_work_items"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=(
                "fk_work_recurrence_rules_"
                "created_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            name="uq_work_recurrence_rules_work_item_id",
        ),
    )

    op.create_index(
        "ix_work_recurrence_rules_due",
        "work_recurrence_rules",
        ["active", "next_occurrence_at", "id"],
        unique=False,
    )

    op.create_table(
        "work_recurrence_occurrences",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "recurrence_rule_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "occurrence_number",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "occurrence_number >= 1",
            name=(
                "ck_work_recurrence_occurrences_"
                "number_positive"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_rule_id"],
            ["work_recurrence_rules.id"],
            name=(
                "fk_work_recurrence_occurrences_"
                "rule_id_rules"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=(
                "fk_work_recurrence_occurrences_"
                "work_item_id_work_items"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_rule_id",
            "occurrence_number",
            name=(
                "uq_work_recurrence_occurrences_"
                "rule_number"
            ),
        ),
        sa.UniqueConstraint(
            "recurrence_rule_id",
            "scheduled_for",
            name=(
                "uq_work_recurrence_occurrences_"
                "rule_scheduled_for"
            ),
        ),
        sa.UniqueConstraint(
            "work_item_id",
            name=(
                "uq_work_recurrence_occurrences_"
                "work_item_id"
            ),
        ),
    )

    op.create_index(
        "ix_work_recurrence_occurrences_scheduled",
        "work_recurrence_occurrences",
        ["scheduled_for", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_recurrence_occurrences_scheduled",
        table_name="work_recurrence_occurrences",
    )
    op.drop_table("work_recurrence_occurrences")

    op.drop_index(
        "ix_work_recurrence_rules_due",
        table_name="work_recurrence_rules",
    )
    op.drop_table("work_recurrence_rules")

    op.drop_constraint(
        "ck_work_events_event_type_valid",
        "work_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_events_event_type_valid",
        "work_events",
        EVENT_TYPES_22A,
    )
