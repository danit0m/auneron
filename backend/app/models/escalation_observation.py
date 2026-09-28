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


ASSESSMENT_CODES = (
    "contact_made",
    "payment_promised",
    "payment_refused",
    "unreachable",
    "partial_agreement",
)


class EscalationObservation(Base):
    """
    VALUE-2.3 -- registro append-only (0..N por WorkItem de
    escalonamento) do que foi observado depois de um episodio de
    escalonamento (VALUE-2.1/2.2). Nunca mutado apos criado -- sem
    UPDATE, sem DELETE aplicacional.

    observed_fact e human_assessment sao estruturalmente disjuntos
    (ck_escalation_observations_type_disjoint) -- nenhum codigo de
    servico pode promover um humano a fato mecanico nem o inverso.

    declared_by_user_id usa ON DELETE RESTRICT (VALUE-2.3A, C1): com
    SET NULL, apagar o User violaria o CHECK disjunto (que exige
    declared_by_user_id IS NOT NULL para human_assessment) e a
    transacao falharia de qualquer forma -- RESTRICT torna essa falha
    explicita e preserva a identidade historica do declarante, em vez
    de depender de um SET NULL que nunca conseguiria executar.

    Nao ha coluna `linkage`: com um unico produtor de observed_fact
    hoje, o valor ('correlated') e uma constante de codigo, nunca
    campo gravavel (VALUE-2.2, pergunta extra).
    """

    __tablename__ = "escalation_observations"

    __table_args__ = (
        CheckConstraint(
            "observation_type IN ('observed_fact', 'human_assessment')",
            name="ck_escalation_observations_type_valid",
        ),
        CheckConstraint(
            "assessment_code IS NULL OR assessment_code IN ("
            "'contact_made', 'payment_promised', 'payment_refused', "
            "'unreachable', 'partial_agreement'"
            ")",
            name="ck_escalation_observations_assessment_code_valid",
        ),
        CheckConstraint(
            "declared_by_role IS NULL OR declared_by_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_escalation_observations_declared_by_role_valid",
        ),
        CheckConstraint(
            "("
            "observation_type = 'observed_fact' "
            "AND linked_account_event_id IS NOT NULL "
            "AND assessment_code IS NULL "
            "AND declared_by_user_id IS NULL "
            "AND declared_by_role IS NULL "
            "AND observed_at IS NOT NULL "
            "AND declared_at IS NULL"
            ") OR ("
            "observation_type = 'human_assessment' "
            "AND linked_account_event_id IS NULL "
            "AND assessment_code IS NOT NULL "
            "AND declared_by_user_id IS NOT NULL "
            "AND declared_by_role IS NOT NULL "
            "AND declared_at IS NOT NULL "
            "AND observed_at IS NULL"
            ")",
            name="ck_escalation_observations_type_disjoint",
        ),
        CheckConstraint(
            "idempotency_key IS NULL "
            "OR char_length(btrim(idempotency_key)) >= 1",
            name="ck_escalation_observations_idempotency_key_not_blank",
        ),
        UniqueConstraint(
            "escalation_work_item_id",
            "idempotency_key",
            name="uq_escalation_observations_work_item_idempotency",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    escalation_work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name="fk_escalation_observations_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    observation_type = Column(
        String(20),
        nullable=False,
    )

    linked_account_event_id = Column(
        BigInteger,
        ForeignKey(
            "account_events.id",
            name=(
                "fk_escalation_observations_account_event_id_"
                "account_events"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    assessment_code = Column(
        String(40),
        nullable=True,
    )

    declared_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_escalation_observations_declared_by_user_id_"
                "users"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    declared_by_role = Column(
        String(32),
        nullable=True,
    )

    observed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    declared_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    idempotency_key = Column(
        String(255),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_escalation_observations_work_item_created",
    EscalationObservation.escalation_work_item_id,
    EscalationObservation.created_at,
    EscalationObservation.id,
)
