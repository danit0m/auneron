"""add evidence provenance contexts and observation provenance columns

Revision ID: 5c1e7a90d2b4
Revises: 7432a1c2dd66
Create Date: 2026-10-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "5c1e7a90d2b4"
down_revision = "7432a1c2dd66"
branch_labels = None
depends_on = None


_CONTEXTS = "evidence_provenance_contexts"
_OBSERVATIONS = "escalation_observations"

_FN_CONTEXT = "fn_evidence_provenance_context_immutable"
_FN_OBSERVATION = "fn_escalation_observation_provenance_immutable"
_TRG_CONTEXT = "trg_evidence_provenance_contexts_immutable"
_TRG_OBSERVATION = "trg_escalation_observations_provenance_immutable"


def upgrade() -> None:
    op.create_table(
        _CONTEXTS,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("context_digest", sa.String(64), nullable=False),
        sa.Column(
            "context_digest_algorithm", sa.String(16), nullable=False
        ),
        sa.Column("git_sha", sa.String(40), nullable=False),
        sa.Column("git_dirty", sa.Boolean(), nullable=False),
        sa.Column(
            "source_digest_algorithm", sa.String(16), nullable=False
        ),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("source_file_count", sa.Integer(), nullable=False),
        sa.Column("producer_spec", sa.String(64), nullable=False),
        sa.Column(
            "producer_fingerprint_algorithm",
            sa.String(16),
            nullable=False,
        ),
        sa.Column(
            "producer_fingerprint", sa.String(64), nullable=False
        ),
        sa.Column(
            "activation_floor",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expected_schema_revision", sa.String(64), nullable=False
        ),
        sa.Column(
            "actual_database_revision", sa.String(64), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "context_digest",
            name="uq_evidence_provenance_contexts_digest",
        ),
        sa.UniqueConstraint(
            "context_digest_algorithm",
            "git_sha",
            "git_dirty",
            "source_digest_algorithm",
            "source_digest",
            "source_file_count",
            "producer_spec",
            "producer_fingerprint_algorithm",
            "producer_fingerprint",
            "activation_floor",
            "expected_schema_revision",
            "actual_database_revision",
            name="uq_evidence_provenance_contexts_content",
        ),
        sa.CheckConstraint(
            "context_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_provenance_contexts_digest_hex",
        ),
        sa.CheckConstraint(
            "source_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_provenance_contexts_source_digest_hex",
        ),
        sa.CheckConstraint(
            "producer_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_provenance_contexts_fingerprint_hex",
        ),
        sa.CheckConstraint(
            "git_sha ~ '^[0-9a-f]{40}$'",
            name="ck_evidence_provenance_contexts_git_sha",
        ),
        sa.CheckConstraint(
            "git_dirty = false",
            name="ck_evidence_provenance_contexts_git_clean",
        ),
        sa.CheckConstraint(
            "context_digest_algorithm ~ '^[a-z]+[0-9]+$' "
            "AND source_digest_algorithm ~ '^[a-z]+[0-9]+$' "
            "AND producer_fingerprint_algorithm ~ '^[a-z]+[0-9]+$'",
            name="ck_evidence_provenance_contexts_algorithms",
        ),
        sa.CheckConstraint(
            "producer_spec ~ '^[a-z0-9_]+[:]v[0-9]+$'",
            name="ck_evidence_provenance_contexts_producer_spec",
        ),
        sa.CheckConstraint(
            "source_file_count >= 1",
            name="ck_evidence_provenance_contexts_file_count",
        ),
        sa.CheckConstraint(
            "char_length(btrim(expected_schema_revision)) >= 1 "
            "AND char_length(btrim(actual_database_revision)) >= 1",
            name="ck_evidence_provenance_contexts_revisions",
        ),
    )

    op.add_column(
        _OBSERVATIONS,
        sa.Column("provenance_context_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        _OBSERVATIONS,
        sa.Column("producer_pass_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_escalation_observations_provenance_context_id_contexts",
        _OBSERVATIONS,
        _CONTEXTS,
        ["provenance_context_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_escalation_observations_provenance_context",
        _OBSERVATIONS,
        ["provenance_context_id"],
    )
    op.create_index(
        "ix_escalation_observations_producer_pass",
        _OBSERVATIONS,
        ["producer_pass_id"],
    )

    # NOT VALID: impoe a regra a TODA linha nova/atualizada e tolera o
    # legado (observed_fact pre-D-2b com proveniencia NULL). Sem backfill.
    op.execute(
        sa.text(
            "ALTER TABLE escalation_observations "
            "ADD CONSTRAINT ck_escalation_observations_provenance_by_type "
            "CHECK ("
            "(observation_type = 'observed_fact' "
            "AND provenance_context_id IS NOT NULL "
            "AND producer_pass_id IS NOT NULL) "
            "OR (observation_type = 'human_assessment' "
            "AND provenance_context_id IS NULL "
            "AND producer_pass_id IS NULL)"
            ") NOT VALID"
        )
    )

    op.execute(
        sa.text(
            f"CREATE FUNCTION {_FN_CONTEXT}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ "
            "BEGIN "
            "RAISE EXCEPTION 'evidence_provenance_contexts is immutable' "
            "USING ERRCODE = 'restrict_violation'; "
            "END $$"
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER {_TRG_CONTEXT} "
            f"BEFORE UPDATE OR DELETE ON {_CONTEXTS} "
            f"FOR EACH ROW EXECUTE FUNCTION {_FN_CONTEXT}()"
        )
    )
    op.execute(
        sa.text(
            f"CREATE FUNCTION {_FN_OBSERVATION}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ "
            "BEGIN "
            "IF NEW.provenance_context_id IS DISTINCT FROM "
            "OLD.provenance_context_id "
            "OR NEW.producer_pass_id IS DISTINCT FROM "
            "OLD.producer_pass_id THEN "
            "RAISE EXCEPTION "
            "'escalation_observations provenance columns are immutable' "
            "USING ERRCODE = 'restrict_violation'; "
            "END IF; "
            "RETURN NEW; "
            "END $$"
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER {_TRG_OBSERVATION} "
            "BEFORE UPDATE OF provenance_context_id, producer_pass_id "
            f"ON {_OBSERVATIONS} "
            f"FOR EACH ROW EXECUTE FUNCTION {_FN_OBSERVATION}()"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(f"DROP TRIGGER {_TRG_OBSERVATION} ON {_OBSERVATIONS}")
    )
    op.execute(sa.text(f"DROP FUNCTION {_FN_OBSERVATION}()"))
    op.execute(sa.text(f"DROP TRIGGER {_TRG_CONTEXT} ON {_CONTEXTS}"))
    op.execute(sa.text(f"DROP FUNCTION {_FN_CONTEXT}()"))
    op.drop_constraint(
        "ck_escalation_observations_provenance_by_type",
        _OBSERVATIONS,
        type_="check",
    )
    op.drop_index(
        "ix_escalation_observations_producer_pass",
        table_name=_OBSERVATIONS,
    )
    op.drop_index(
        "ix_escalation_observations_provenance_context",
        table_name=_OBSERVATIONS,
    )
    op.drop_constraint(
        "fk_escalation_observations_provenance_context_id_contexts",
        _OBSERVATIONS,
        type_="foreignkey",
    )
    op.drop_column(_OBSERVATIONS, "producer_pass_id")
    op.drop_column(_OBSERVATIONS, "provenance_context_id")
    op.drop_table(_CONTEXTS)
