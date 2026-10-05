from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import func

from app.database.database import Base


class EvidenceProvenanceContext(Base):
    """
    VALUE-3.4D-2b -- contexto de proveniencia (imutavel, content-addressed)
    sob o qual o produtor automatico interpretou a evidencia.

    Nao contem nada de execucao (pass, host, tempo): isso e
    `escalation_observations.producer_pass_id`. Uma linha por combinacao
    distinta de build + produtor + floor efetivo + identidade de schema.
    Imutavel por trigger (UPDATE/DELETE proibidos).
    """

    __tablename__ = "evidence_provenance_contexts"

    __table_args__ = (
        UniqueConstraint(
            "context_digest",
            name="uq_evidence_provenance_contexts_digest",
        ),
        UniqueConstraint(
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
        CheckConstraint(
            "context_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_provenance_contexts_digest_hex",
        ),
        CheckConstraint(
            "source_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_provenance_contexts_source_digest_hex",
        ),
        CheckConstraint(
            "producer_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_provenance_contexts_fingerprint_hex",
        ),
        CheckConstraint(
            "git_sha ~ '^[0-9a-f]{40}$'",
            name="ck_evidence_provenance_contexts_git_sha",
        ),
        CheckConstraint(
            "git_dirty = false",
            name="ck_evidence_provenance_contexts_git_clean",
        ),
        CheckConstraint(
            "context_digest_algorithm ~ '^[a-z]+[0-9]+$' "
            "AND source_digest_algorithm ~ '^[a-z]+[0-9]+$' "
            "AND producer_fingerprint_algorithm ~ '^[a-z]+[0-9]+$'",
            name="ck_evidence_provenance_contexts_algorithms",
        ),
        CheckConstraint(
            "producer_spec ~ '^[a-z0-9_]+[:]v[0-9]+$'",
            name="ck_evidence_provenance_contexts_producer_spec",
        ),
        CheckConstraint(
            "source_file_count >= 1",
            name="ck_evidence_provenance_contexts_file_count",
        ),
        CheckConstraint(
            "char_length(btrim(expected_schema_revision)) >= 1 "
            "AND char_length(btrim(actual_database_revision)) >= 1",
            name="ck_evidence_provenance_contexts_revisions",
        ),
    )

    id = Column(BigInteger, primary_key=True)

    context_digest = Column(String(64), nullable=False)
    context_digest_algorithm = Column(String(16), nullable=False)

    git_sha = Column(String(40), nullable=False)
    git_dirty = Column(Boolean, nullable=False)

    source_digest_algorithm = Column(String(16), nullable=False)
    source_digest = Column(String(64), nullable=False)
    source_file_count = Column(Integer, nullable=False)

    producer_spec = Column(String(64), nullable=False)
    producer_fingerprint_algorithm = Column(String(16), nullable=False)
    producer_fingerprint = Column(String(64), nullable=False)

    activation_floor = Column(DateTime(timezone=True), nullable=False)

    expected_schema_revision = Column(String(64), nullable=False)
    actual_database_revision = Column(String(64), nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
