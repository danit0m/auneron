from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import func

from app.database.database import Base


class BusinessEffectVerification(Base):
    """
    DW-3 V1 -- prova persistente de que o efeito de negocio esperado de um
    ApprovalConsumption governado (account.mark_overdue / account.mark_paid)
    foi observado no estado empresarial commitado. Nao e reavaliacao de
    execucao (WorkOutcomeEvaluation): responde "o efeito existe?", nunca
    "a chamada terminou sem erro?". PENDING e o unico status reavaliavel;
    verified/contradicted/unverifiable sao terminais e imutaveis.
    """

    __tablename__ = "business_effect_verifications"

    __table_args__ = (
        CheckConstraint(
            "skill_key IN ("
            "'account.mark_overdue', 'account.mark_paid'"
            ")",
            name="ck_bev_skill_key",
        ),
        CheckConstraint(
            "expected_status IN ('atrasado', 'pago')",
            name="ck_bev_expected_status",
        ),
        CheckConstraint(
            "account_status_observed IS NULL "
            "OR account_status_observed IN "
            "('aberto', 'atrasado', 'pago')",
            name="ck_bev_observed_status",
        ),
        CheckConstraint(
            "result IN "
            "('pending', 'verified', 'contradicted', 'unverifiable')",
            name="ck_bev_result",
        ),
        CheckConstraint(
            "char_length(btrim(account_event_key_searched)) >= 1",
            name="ck_bev_key_not_blank",
        ),
        CheckConstraint(
            "(result = 'pending' AND checked_at IS NULL) "
            "OR (result != 'pending' AND checked_at IS NOT NULL)",
            name="ck_bev_checked_at_terminal",
        ),
        CheckConstraint(
            "(result != 'verified') "
            "OR (account_event_id IS NOT NULL "
            "AND account_status_observed IS NOT NULL)",
            name="ck_bev_verified_has_evidence",
        ),
        CheckConstraint(
            "(approval_consumption_id IS NOT NULL "
            "AND policy_authority_consumption_id IS NULL) "
            "OR (approval_consumption_id IS NULL "
            "AND policy_authority_consumption_id IS NOT NULL)",
            name="ck_bev_consumption_source_xor",
        ),
        UniqueConstraint(
            "approval_consumption_id",
            name="uq_bev_consumption",
        ),
        UniqueConstraint(
            "policy_authority_consumption_id",
            name="uq_bev_policy_consumption",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    approval_consumption_id = Column(
        BigInteger,
        ForeignKey(
            "approval_consumptions.id",
            name=(
                "fk_bev_"
                "consumption_id_approval_consumptions"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    policy_authority_consumption_id = Column(
        BigInteger,
        ForeignKey(
            "policy_authority_consumptions.id",
            name="fk_bev_policy_authority_consumption_id_pac",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    skill_key = Column(
        String(128),
        nullable=False,
    )

    target_account_id = Column(
        Integer,
        nullable=True,
    )

    expected_status = Column(
        String(16),
        nullable=False,
    )

    account_event_id = Column(
        BigInteger,
        ForeignKey(
            "account_events.id",
            name=(
                "fk_bev_"
                "account_event_id_account_events"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    account_event_key_searched = Column(
        String(255),
        nullable=False,
    )

    account_status_observed = Column(
        String(30),
        nullable=True,
    )

    result = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    checked_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "ix_bev_result_updated",
    BusinessEffectVerification.result,
    BusinessEffectVerification.updated_at,
    BusinessEffectVerification.id,
)
