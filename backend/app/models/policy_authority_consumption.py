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


class PolicyAuthorityConsumption(Base):
    """
    DW-7.3 V1 -- Episode Policy Decision / Authority Consumption
    (DW-7.1 Q2). Existe se, e somente se, o efeito de negocio do
    episodio foi commitado atomicamente com esta linha (B3 do DW-7.2 --
    sem estados reserved/failed: o motor generico 24D precisa deles
    porque invoca um runtime isolado fora de processo; este corredor
    bespoke e sincrono, em processo, transacao unica).

    A autoridade desta consumption e o proprio
    `policy_authority_grant_id` -- nao ha `authority_user_id` aqui
    (diferente de ApprovalConsumption): esta e a aplicacao fisica direta
    de Authority != Actor (DW-7.1 Q1), nao uma omissao.
    """

    __tablename__ = "policy_authority_consumptions"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(episode_scope_key)) >= 1",
            name="ck_policy_authority_consumptions_episode_key_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(policy_version_applied)) >= 1",
            name="ck_policy_authority_consumptions_version_not_blank",
        ),
        CheckConstraint(
            "char_length(evidence_digest) = 64 "
            "AND evidence_digest = lower(evidence_digest)",
            name="ck_policy_authority_consumptions_evidence_digest_format",
        ),
        CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_policy_authority_consumptions_input_digest_format",
        ),
        CheckConstraint(
            "consumer_actor_type IN "
            "('agent', 'system', 'integration')",
            name="ck_policy_authority_consumptions_actor_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(consumer_reference)) >= 1",
            name="ck_policy_authority_consumptions_consumer_ref_not_blank",
        ),
        CheckConstraint(
            "target_account_id IS NULL OR target_account_id > 0",
            name="ck_policy_authority_consumptions_account_id_positive",
        ),
        UniqueConstraint(
            "episode_scope_key",
            name="uq_policy_authority_consumptions_episode",
        ),
        UniqueConstraint(
            "skill_invocation_id",
            name="uq_policy_authority_consumptions_invocation",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    policy_authority_grant_id = Column(
        BigInteger,
        ForeignKey(
            "policy_authority_grants.id",
            name="fk_pac_grant_id_policy_authority_grants",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    skill_invocation_id = Column(
        BigInteger,
        ForeignKey(
            "skill_invocations.id",
            name="fk_pac_invocation_id_skill_invocations",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    episode_scope_key = Column(
        String(255),
        nullable=False,
    )

    target_account_id = Column(
        Integer,
        nullable=True,
    )

    policy_version_applied = Column(
        String(32),
        nullable=False,
    )

    evidence_digest = Column(
        String(64),
        nullable=False,
    )

    input_digest = Column(
        String(64),
        nullable=False,
    )

    consumer_actor_type = Column(
        String(20),
        nullable=False,
    )

    consumer_reference = Column(
        String(255),
        nullable=False,
    )

    consumed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_policy_authority_consumptions_grant_consumed",
    PolicyAuthorityConsumption.policy_authority_grant_id,
    PolicyAuthorityConsumption.consumed_at,
    PolicyAuthorityConsumption.id,
)
