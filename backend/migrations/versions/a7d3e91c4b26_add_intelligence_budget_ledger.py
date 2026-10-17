"""add intelligence budget ledger (balances and model calls)

Revision ID: a7d3e91c4b26
Revises: 5c1e7a90d2b4
Create Date: 2026-10-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a7d3e91c4b26"
down_revision = "5c1e7a90d2b4"
branch_labels = None
depends_on = None


_BUDGETS = "intelligence_budgets"
_CALLS = "intelligence_model_calls"


def _bigint_zero(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.BigInteger(),
        nullable=False,
        server_default="0",
    )


def upgrade() -> None:
    op.create_table(
        _BUDGETS,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("scope_kind", sa.String(24), nullable=False),
        sa.Column("scope_ref", sa.String(128), nullable=False),
        sa.Column(
            "period_start", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "period_end", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("limit_micro", sa.BigInteger(), nullable=False),
        sa.Column("limit_tokens", sa.BigInteger(), nullable=False),
        _bigint_zero("reserved_micro"),
        _bigint_zero("reserved_tokens"),
        _bigint_zero("settled_micro"),
        _bigint_zero("settled_tokens"),
        _bigint_zero("unknown_micro"),
        _bigint_zero("unknown_tokens"),
        _bigint_zero("overrun_micro"),
        _bigint_zero("overrun_tokens"),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "scope_kind IN ("
            "'user_period', 'objective', 'deployment_period'"
            ")",
            name="ck_intelligence_budgets_scope_kind_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(scope_ref)) >= 1",
            name="ck_intelligence_budgets_scope_ref_not_blank",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'objective' "
            "AND period_start IS NULL AND period_end IS NULL) "
            "OR (scope_kind != 'objective' "
            "AND period_start IS NOT NULL "
            "AND period_end IS NOT NULL "
            "AND period_end > period_start)",
            name="ck_intelligence_budgets_period_integrity",
        ),
        sa.CheckConstraint(
            "limit_micro >= 0 AND limit_tokens >= 0 "
            "AND reserved_micro >= 0 AND reserved_tokens >= 0 "
            "AND settled_micro >= 0 AND settled_tokens >= 0 "
            "AND unknown_micro >= 0 AND unknown_tokens >= 0 "
            "AND overrun_micro >= 0 AND overrun_tokens >= 0",
            name="ck_intelligence_budgets_non_negative",
        ),
        sa.CheckConstraint(
            "reserved_micro + settled_micro + unknown_micro "
            "<= limit_micro",
            name="ck_intelligence_budgets_micro_within_limit",
        ),
        sa.CheckConstraint(
            "reserved_tokens + settled_tokens + unknown_tokens "
            "<= limit_tokens",
            name="ck_intelligence_budgets_tokens_within_limit",
        ),
        sa.UniqueConstraint(
            "scope_kind",
            "scope_ref",
            "period_start",
            name="uq_intelligence_budgets_scope_period",
        ),
    )
    op.create_index(
        "uq_intelligence_budgets_scope_no_period",
        _BUDGETS,
        ["scope_kind", "scope_ref"],
        unique=True,
        postgresql_where=sa.text("period_start IS NULL"),
    )

    op.create_table(
        _CALLS,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("call_key", sa.String(160), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("objective_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "parent_call_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "intelligence_model_calls.id",
                name="fk_intelligence_calls_parent_call_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_profile", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("prompt_template_id", sa.String(64), nullable=True),
        sa.Column("output_schema_id", sa.String(64), nullable=True),
        sa.Column(
            "data_classification",
            sa.String(16),
            nullable=False,
            server_default="synthetic",
        ),
        sa.Column(
            "state",
            sa.String(16),
            nullable=False,
            server_default="reserved",
        ),
        sa.Column(
            "user_budget_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "intelligence_budgets.id",
                name="fk_intelligence_calls_user_budget_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column(
            "objective_budget_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "intelligence_budgets.id",
                name="fk_intelligence_calls_objective_budget_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column(
            "deployment_budget_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "intelligence_budgets.id",
                name="fk_intelligence_calls_deployment_budget_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column("reserved_micro", sa.BigInteger(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("actual_micro", sa.BigInteger(), nullable=True),
        sa.Column("actual_tokens", sa.BigInteger(), nullable=True),
        sa.Column("release_basis", sa.String(24), nullable=True),
        sa.Column("evidence_ref", sa.String(256), nullable=True),
        sa.Column(
            "reconciled_by_reference", sa.String(128), nullable=True
        ),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("request_digest", sa.String(64), nullable=True),
        sa.Column("response_digest", sa.String(64), nullable=True),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "unknown_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "settled_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "released_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "reconciled_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "deadline_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ("
            "'reserved', 'in_flight', 'settled', 'unknown', "
            "'released'"
            ")",
            name="ck_intelligence_model_calls_state_valid",
        ),
        sa.CheckConstraint(
            "data_classification = 'synthetic'",
            name="ck_intelligence_model_calls_synthetic_only",
        ),
        sa.CheckConstraint(
            "provider IN ('fake')",
            name="ck_intelligence_model_calls_provider_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(call_key)) >= 1",
            name="ck_intelligence_model_calls_call_key_not_blank",
        ),
        sa.CheckConstraint(
            "attempt >= 1 AND ((attempt = 1 "
            "AND parent_call_id IS NULL) "
            "OR (attempt > 1 AND parent_call_id IS NOT NULL))",
            name="ck_intelligence_model_calls_attempt_parent",
        ),
        sa.CheckConstraint(
            "user_budget_id IS NOT NULL "
            "OR objective_budget_id IS NOT NULL "
            "OR deployment_budget_id IS NOT NULL",
            name="ck_intelligence_model_calls_has_budget",
        ),
        sa.CheckConstraint(
            "reserved_micro > 0 AND reserved_tokens > 0 "
            "AND (actual_micro IS NULL OR actual_micro >= 0) "
            "AND (actual_tokens IS NULL OR actual_tokens >= 0)",
            name="ck_intelligence_model_calls_amounts_valid",
        ),
        sa.CheckConstraint(
            "release_basis IS NULL OR release_basis IN ("
            "'never_sent', 'provider_documented', "
            "'admin_reconciliation'"
            ")",
            name="ck_intelligence_model_calls_release_basis_valid",
        ),
        sa.CheckConstraint(
            "(request_digest IS NULL "
            "OR request_digest ~ '^[0-9a-f]{64}$') "
            "AND (response_digest IS NULL "
            "OR response_digest ~ '^[0-9a-f]{64}$')",
            name="ck_intelligence_model_calls_digest_format",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND sent_at IS NULL "
            "AND unknown_at IS NULL AND settled_at IS NULL "
            "AND released_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'in_flight' AND sent_at IS NOT NULL "
            "AND unknown_at IS NULL AND settled_at IS NULL "
            "AND released_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'unknown' AND sent_at IS NOT NULL "
            "AND unknown_at IS NOT NULL AND settled_at IS NULL "
            "AND released_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'settled' AND sent_at IS NOT NULL "
            "AND settled_at IS NOT NULL AND released_at IS NULL "
            "AND actual_micro IS NOT NULL "
            "AND actual_tokens IS NOT NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'released' AND released_at IS NOT NULL "
            "AND release_basis IS NOT NULL "
            "AND settled_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL)",
            name="ck_intelligence_model_calls_state_integrity",
        ),
        sa.CheckConstraint(
            "(release_basis IS DISTINCT FROM 'provider_documented' "
            "OR evidence_ref IS NOT NULL) "
            "AND (release_basis IS DISTINCT FROM "
            "'admin_reconciliation' "
            "OR (evidence_ref IS NOT NULL "
            "AND reconciled_by_reference IS NOT NULL "
            "AND reconciled_at IS NOT NULL)) "
            "AND (release_basis IS DISTINCT FROM 'never_sent' "
            "OR sent_at IS NULL OR evidence_ref IS NOT NULL) "
            "AND ((reconciled_at IS NULL) = "
            "(reconciled_by_reference IS NULL)) "
            "AND (reconciled_at IS NULL "
            "OR evidence_ref IS NOT NULL)",
            name="ck_intelligence_model_calls_evidence_integrity",
        ),
        sa.UniqueConstraint(
            "call_key",
            name="uq_intelligence_model_calls_call_key",
        ),
    )
    op.create_index(
        "ix_intelligence_model_calls_state_deadline",
        _CALLS,
        ["state", "deadline_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_intelligence_model_calls_state_deadline",
        table_name=_CALLS,
    )
    op.drop_table(_CALLS)
    op.drop_index(
        "uq_intelligence_budgets_scope_no_period",
        table_name=_BUDGETS,
    )
    op.drop_table(_BUDGETS)
