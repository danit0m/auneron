from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


NbaRecommendationSnapshotJSON = JSON().with_variant(
    JSONB(),
    "postgresql",
)


class NbaRecommendationSnapshot(Base):
    """
    DW-6.4A -- proveniencia append-only de que uma recomendacao NBA foi
    gerada/servida para (account_id, due_date) num instante especifico.
    Nao e uma decisao, nao concede autoridade, nunca e reavaliada nem
    deduplicada -- cada GET produz uma ocorrencia nova, mesmo que o
    conteudo computado seja identico a uma anterior; snapshot_digest
    prova integridade do conteudo, nunca identidade da ocorrencia. I2
    emendado (nba_policy.py): o GET do NBA permanece sem efeitos de
    negocio (Account/WorkItem/Knowledge/MemoryItem/Approval/Execution/
    BusinessEffectVerification) -- este e o unico apendice permitido, e
    imutavel apos a criacao.
    """

    __tablename__ = "nba_recommendation_snapshots"

    __table_args__ = (
        CheckConstraint(
            "decision_type IN "
            "('single_action', 'action_bundle', 'no_action')",
            name="ck_nba_snapshot_decision_type",
        ),
        CheckConstraint(
            "char_length(btrim(policy_version)) >= 1",
            name="ck_nba_snapshot_policy_version_not_blank",
        ),
        CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_nba_snapshot_digest_format",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="fk_nba_snapshot_account_id_accounts",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    due_date = Column(
        Date,
        nullable=False,
    )

    policy_version = Column(
        String(64),
        nullable=False,
    )

    decision_type = Column(
        String(16),
        nullable=False,
    )

    requires_human_review = Column(
        Boolean,
        nullable=False,
    )

    snapshot_payload = Column(
        NbaRecommendationSnapshotJSON,
        nullable=False,
    )

    snapshot_digest = Column(
        String(64),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_nba_snapshot_account_due_date",
    NbaRecommendationSnapshot.account_id,
    NbaRecommendationSnapshot.due_date,
    NbaRecommendationSnapshot.created_at,
)
