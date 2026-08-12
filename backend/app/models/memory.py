from decimal import Decimal

from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database.database import Base


ContextJSON = JSON().with_variant(
    JSONB(),
    "postgresql",
)


class MemoryItem(Base):
    __tablename__ = "memory_items"

    __table_args__ = (
        CheckConstraint(
            "memory_type IN ("
            "'fact', "
            "'event', "
            "'observation', "
            "'decision', "
            "'summary'"
            ")",
            name="ck_memory_items_memory_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(title)) >= 1",
            name="ck_memory_items_title_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(content)) >= 1",
            name="ck_memory_items_content_not_blank",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'account', 'user')",
            name="ck_memory_items_scope_type_valid",
        ),
        CheckConstraint(
            "("
            "scope_type = 'global' "
            "AND account_id IS NULL "
            "AND subject_user_id IS NULL"
            ") OR ("
            "scope_type = 'account' "
            "AND account_id IS NOT NULL "
            "AND subject_user_id IS NULL"
            ") OR ("
            "scope_type = 'user' "
            "AND account_id IS NULL "
            "AND subject_user_id IS NOT NULL"
            ")",
            name="ck_memory_items_scope_integrity",
        ),
        CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_memory_items_importance_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_items_confidence_range",
        ),
        CheckConstraint(
            "status IN ("
            "'active', "
            "'superseded', "
            "'expired', "
            "'invalidated', "
            "'archived'"
            ")",
            name="ck_memory_items_status_valid",
        ),
        CheckConstraint(
            "status NOT IN ('superseded', 'invalidated') "
            "OR ("
            "status_reason IS NOT NULL "
            "AND char_length(btrim(status_reason)) >= 1"
            ")",
            name="ck_memory_items_status_reason_required",
        ),
        CheckConstraint(
            "source_type IN ("
            "'database', "
            "'upload', "
            "'user', "
            "'agent', "
            "'system', "
            "'api', "
            "'derived'"
            ")",
            name="ck_memory_items_source_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(source_reference)) >= 1",
            name="ck_memory_items_source_reference_not_blank",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_memory_items_valid_time_range",
        ),
        CheckConstraint(
            "supersedes_memory_id IS NULL "
            "OR supersedes_memory_id <> id",
            name="ck_memory_items_not_self_superseding",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    memory_type = Column(
        String(32),
        nullable=False,
    )

    title = Column(
        String(200),
        nullable=False,
    )

    content = Column(
        Text,
        nullable=False,
    )

    memory_key = Column(
        String(255),
        nullable=True,
    )

    scope_type = Column(
        String(20),
        nullable=False,
    )

    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="fk_memory_items_account_id_accounts",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    subject_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_memory_items_subject_user_id_users",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_memory_items_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    importance = Column(
        Numeric(
            precision=4,
            scale=3,
            asdecimal=True,
        ),
        nullable=False,
        default=Decimal("0.500"),
        server_default="0.500",
    )

    confidence = Column(
        Numeric(
            precision=4,
            scale=3,
            asdecimal=True,
        ),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )

    status_reason = Column(
        Text,
        nullable=True,
    )

    status_changed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    valid_from = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    valid_until = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    source_type = Column(
        String(24),
        nullable=False,
    )

    source_reference = Column(
        String(500),
        nullable=False,
    )

    supersedes_memory_id = Column(
        BigInteger,
        ForeignKey(
            "memory_items.id",
            name="fk_memory_items_supersedes_memory_id_memory_items",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    context_data = Column(
        ContextJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
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
    "ix_memory_items_status_type_created_at",
    MemoryItem.status,
    MemoryItem.memory_type,
    MemoryItem.created_at,
)

Index(
    "ix_memory_items_account_status_type_valid_from",
    MemoryItem.account_id,
    MemoryItem.status,
    MemoryItem.memory_type,
    MemoryItem.valid_from,
)

Index(
    "ix_memory_items_user_status_type_valid_from",
    MemoryItem.subject_user_id,
    MemoryItem.status,
    MemoryItem.memory_type,
    MemoryItem.valid_from,
)

Index(
    "ix_memory_items_source_type_reference",
    MemoryItem.source_type,
    MemoryItem.source_reference,
)

_active_expiration_where = text(
    "status = 'active' AND valid_until IS NOT NULL"
)

Index(
    "ix_memory_items_active_valid_until",
    MemoryItem.valid_until,
    postgresql_where=_active_expiration_where,
    sqlite_where=_active_expiration_where,
)

_active_global_key_where = text(
    "scope_type = 'global' "
    "AND status = 'active' "
    "AND memory_key IS NOT NULL"
)

Index(
    "uq_memory_items_active_global_key",
    MemoryItem.memory_key,
    unique=True,
    postgresql_where=_active_global_key_where,
    sqlite_where=_active_global_key_where,
)

_active_account_key_where = text(
    "scope_type = 'account' "
    "AND status = 'active' "
    "AND memory_key IS NOT NULL"
)

Index(
    "uq_memory_items_active_account_key",
    MemoryItem.account_id,
    MemoryItem.memory_key,
    unique=True,
    postgresql_where=_active_account_key_where,
    sqlite_where=_active_account_key_where,
)

_active_user_key_where = text(
    "scope_type = 'user' "
    "AND status = 'active' "
    "AND memory_key IS NOT NULL"
)

Index(
    "uq_memory_items_active_user_key",
    MemoryItem.subject_user_id,
    MemoryItem.memory_key,
    unique=True,
    postgresql_where=_active_user_key_where,
    sqlite_where=_active_user_key_where,
)


class MemoryEvidence(Base):
    __tablename__ = "memory_evidence"

    __table_args__ = (
        CheckConstraint(
            "relation IN ('supports', 'contradicts', 'context')",
            name="ck_memory_evidence_relation_valid",
        ),
        CheckConstraint(
            "source_type IN ("
            "'database', "
            "'upload', "
            "'user', "
            "'agent', "
            "'system', "
            "'api', "
            "'derived'"
            ")",
            name="ck_memory_evidence_source_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(source_reference)) >= 1",
            name="ck_memory_evidence_source_reference_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(evidence_text)) >= 1",
            name="ck_memory_evidence_text_not_blank",
        ),
        CheckConstraint(
            "char_length(evidence_hash) = 64",
            name="ck_memory_evidence_hash_length",
        ),
        CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="ck_memory_evidence_weight_range",
        ),
        CheckConstraint(
            "source_memory_id IS NULL "
            "OR source_memory_id <> memory_id",
            name="ck_memory_evidence_not_self_reference",
        ),
        UniqueConstraint(
            "memory_id",
            "evidence_hash",
            name="uq_memory_evidence_memory_hash",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    memory_id = Column(
        BigInteger,
        ForeignKey(
            "memory_items.id",
            name="fk_memory_evidence_memory_id_memory_items",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    relation = Column(
        String(20),
        nullable=False,
    )

    source_type = Column(
        String(24),
        nullable=False,
    )

    source_reference = Column(
        String(500),
        nullable=False,
    )

    source_memory_id = Column(
        BigInteger,
        ForeignKey(
            "memory_items.id",
            name="fk_memory_evidence_source_memory_id_memory_items",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    evidence_text = Column(
        Text,
        nullable=False,
    )

    evidence_hash = Column(
        String(64),
        nullable=False,
    )

    weight = Column(
        Numeric(
            precision=4,
            scale=3,
            asdecimal=True,
        ),
        nullable=False,
        default=Decimal("1.000"),
        server_default="1.000",
    )

    observed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_memory_evidence_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    context_data = Column(
        ContextJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_memory_evidence_memory_created_at",
    MemoryEvidence.memory_id,
    MemoryEvidence.created_at,
)

Index(
    "ix_memory_evidence_source_memory_id",
    MemoryEvidence.source_memory_id,
)
