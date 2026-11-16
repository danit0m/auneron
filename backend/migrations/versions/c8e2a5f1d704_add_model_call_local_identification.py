"""identify the exact local model on intelligence_model_calls (S2)

Revision ID: c8e2a5f1d704
Revises: d5b8e2f4a671
Create Date: 2026-11-15 00:00:00.000000

Adds, all NULLABLE (the fake provider leaves them NULL):
  model_digest            exact model digest, 64 lowercase hex, WITHOUT
                          the "sha256:" prefix (the adapter normalizes)
  provider_reported_model the `model` the runtime reported
  runtime_version         Ollama version
  latency_ms              int >= 0

Provider CHECK becomes IN ('fake', 'ollama'). A settled, non-reconciled
ollama row must carry model_digest and runtime_version.

The downgrade REFUSES (does not delete data) while 'ollama' rows exist.
"""

from alembic import op
import sqlalchemy as sa


revision = "c8e2a5f1d704"
down_revision = "d5b8e2f4a671"
branch_labels = None
depends_on = None


_CALLS = "intelligence_model_calls"
_PROVIDER_CK = "ck_intelligence_model_calls_provider_valid"
_NEW_CKS = (
    "ck_intelligence_model_calls_model_identification_format",
    "ck_intelligence_model_calls_ollama_settled_identified",
)
_NEW_COLUMNS = (
    "latency_ms",
    "runtime_version",
    "provider_reported_model",
    "model_digest",
)


def upgrade() -> None:
    op.add_column(
        _CALLS, sa.Column("model_digest", sa.String(64), nullable=True)
    )
    op.add_column(
        _CALLS,
        sa.Column(
            "provider_reported_model", sa.String(128), nullable=True
        ),
    )
    op.add_column(
        _CALLS,
        sa.Column("runtime_version", sa.String(64), nullable=True),
    )
    op.add_column(
        _CALLS, sa.Column("latency_ms", sa.Integer(), nullable=True)
    )

    op.drop_constraint(_PROVIDER_CK, _CALLS, type_="check")
    op.create_check_constraint(
        _PROVIDER_CK, _CALLS, "provider IN ('fake', 'ollama')"
    )
    op.create_check_constraint(
        _NEW_CKS[0],
        _CALLS,
        "(model_digest IS NULL "
        "OR model_digest ~ '^[0-9a-f]{64}$') "
        "AND (latency_ms IS NULL OR latency_ms >= 0) "
        "AND (runtime_version IS NULL "
        "OR char_length(btrim(runtime_version)) >= 1) "
        "AND (provider_reported_model IS NULL "
        "OR char_length(btrim(provider_reported_model)) >= 1)",
    )
    op.create_check_constraint(
        _NEW_CKS[1],
        _CALLS,
        "NOT (provider = 'ollama' AND state = 'settled' "
        "AND reconciled_at IS NULL) "
        "OR (model_digest IS NOT NULL "
        "AND runtime_version IS NOT NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            "SELECT count(*) FROM intelligence_model_calls "
            "WHERE provider = 'ollama'"
        )
    ).scalar()

    if count:
        raise RuntimeError(
            "Downgrade refused: intelligence_model_calls holds "
            f"{count} row(s) with provider='ollama'. Rows are audit "
            "evidence and are never deleted by a migration; archive "
            "or reconcile them manually before downgrading."
        )

    for name in _NEW_CKS:
        op.drop_constraint(name, _CALLS, type_="check")

    op.drop_constraint(_PROVIDER_CK, _CALLS, type_="check")
    op.create_check_constraint(
        _PROVIDER_CK, _CALLS, "provider IN ('fake')"
    )

    for column in _NEW_COLUMNS:
        op.drop_column(_CALLS, column)
