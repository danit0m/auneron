from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


AdvisoryProposalJSON = JSON().with_variant(
    JSONB(),
    "postgresql",
)


class AuthenticatedAdvisoryProposal(Base):
    __tablename__ = "authenticated_advisory_proposals"

    __table_args__ = (
        CheckConstraint(
            "authority_user_id > 0",
            name="ck_authenticated_advisory_proposals_user_positive",
        ),
        CheckConstraint(
            "("
            "  authority_source = 'authenticated_http_session'"
            "  AND protocol = 'authenticated_advisory_v1'"
            "  AND auth_session_id IS NOT NULL"
            "  AND auth_session_id > 0"
            ") OR ("
            "  authority_source = 'system_principal'"
            "  AND protocol = 'system_advisory_v1'"
            "  AND auth_session_id IS NULL"
            ")",
            name="ck_authenticated_advisory_proposals_provenance",
        ),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) >= 1 "
            "AND idempotency_key = lower(btrim(idempotency_key)) "
            "AND idempotency_key ~ "
            "'^[a-z0-9][a-z0-9._:-]{0,254}$'",
            name="ck_authenticated_advisory_proposals_idempotency_key",
        ),
        CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_authenticated_advisory_proposals_digest",
        ),
        CheckConstraint(
            "agent_count >= 0 AND agent_count <= 32",
            name="ck_authenticated_advisory_proposals_agent_count",
        ),
        CheckConstraint(
            "binding_count >= 0 AND binding_count <= 512",
            name="ck_authenticated_advisory_proposals_binding_count",
        ),
        CheckConstraint(
            "snapshot_bytes >= 2 AND snapshot_bytes <= 65536",
            name="ck_authenticated_advisory_proposals_snapshot_bytes",
        ),
        UniqueConstraint(
            "authority_user_id",
            "auth_session_id",
            "idempotency_key",
            name=(
                "uq_authenticated_advisory_proposals_"
                "authority_session_key"
            ),
        ),
        Index(
            "uq_authenticated_advisory_proposals_system_principal_key",
            "authority_user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text(
                "authority_source = 'system_principal'"
            ),
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    authority_user_id = Column(
        Integer,
        nullable=False,
    )

    auth_session_id = Column(
        Integer,
        nullable=True,
    )

    authority_source = Column(
        String(32),
        nullable=False,
    )

    request_id = Column(
        String(128),
        nullable=True,
    )

    idempotency_key = Column(
        String(255),
        nullable=False,
    )

    protocol = Column(
        String(32),
        nullable=False,
    )

    snapshot_payload = Column(
        AdvisoryProposalJSON,
        nullable=False,
    )

    snapshot_digest = Column(
        String(64),
        nullable=False,
    )

    agent_count = Column(
        Integer,
        nullable=False,
    )

    binding_count = Column(
        Integer,
        nullable=False,
    )

    snapshot_bytes = Column(
        Integer,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
