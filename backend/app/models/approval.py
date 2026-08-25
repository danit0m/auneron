from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import func

from app.database.database import Base


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('skill_execution')",
            name="ck_approval_requests_action_type_valid",
        ),
        CheckConstraint(
            "requester_actor_type IN ("
            "'user', 'agent', 'system', 'integration'"
            ")",
            name="ck_approval_requests_actor_type_valid",
        ),
        CheckConstraint(
            "requester_actor_type = 'user' "
            "OR requester_user_id IS NULL",
            name="ck_approval_requests_non_user_has_no_user_id",
        ),
        CheckConstraint(
            "char_length(btrim(requester_reference)) >= 1",
            name="ck_approval_requests_requester_ref_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) >= 1",
            name="ck_approval_requests_idempotency_not_blank",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64 "
            "AND request_fingerprint = lower(request_fingerprint)",
            name="ck_approval_requests_fingerprint_format",
        ),
        CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_approval_requests_input_digest_format",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_approval_requests_risk_level_valid",
        ),
        CheckConstraint(
            "required_permission IN ("
            "'approval:decide', 'approval:decide_sensitive'"
            ")",
            name="ck_approval_requests_permission_valid",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'approved', 'rejected', 'expired', 'cancelled'"
            ")",
            name="ck_approval_requests_status_valid",
        ),
        CheckConstraint(
            "target_account_id IS NULL OR target_account_id > 0",
            name="ck_approval_requests_account_id_positive",
        ),
        CheckConstraint(
            "target_user_id IS NULL OR target_user_id > 0",
            name="ck_approval_requests_target_user_id_positive",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_approval_requests_expiration_order",
        ),
        CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_approval_requests_status_resolution",
        ),
        UniqueConstraint(
            "requester_actor_type",
            "requester_reference",
            "idempotency_key",
            name="uq_approval_requests_requester_idempotency",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    action_type = Column(
        String(32),
        nullable=False,
    )

    skill_version_id = Column(
        BigInteger,
        ForeignKey(
            "skill_versions.id",
            name=(
                "fk_approval_requests_skill_version_id_"
                "skill_versions"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    requester_actor_type = Column(
        String(20),
        nullable=False,
    )

    requester_reference = Column(
        String(255),
        nullable=False,
    )

    requester_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_approval_requests_requester_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    idempotency_key = Column(
        String(255),
        nullable=False,
    )

    request_fingerprint = Column(
        String(64),
        nullable=False,
    )

    input_digest = Column(
        String(64),
        nullable=False,
    )

    risk_level = Column(
        String(16),
        nullable=False,
    )

    required_permission = Column(
        String(64),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    target_account_id = Column(
        Integer,
        nullable=True,
    )

    target_user_id = Column(
        Integer,
        nullable=True,
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    resolved_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_approval_requests_status_expires",
    ApprovalRequest.status,
    ApprovalRequest.expires_at,
    ApprovalRequest.id,
)

Index(
    "ix_approval_requests_version_status",
    ApprovalRequest.skill_version_id,
    ApprovalRequest.status,
    ApprovalRequest.id,
)

Index(
    "ix_approval_requests_requester_created",
    ApprovalRequest.requester_actor_type,
    ApprovalRequest.requester_reference,
    ApprovalRequest.created_at,
    ApprovalRequest.id,
)


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_approval_decisions_decision_valid",
        ),
        CheckConstraint(
            "char_length(btrim(decided_by_reference)) >= 1",
            name="ck_approval_decisions_decider_ref_not_blank",
        ),
        CheckConstraint(
            "decided_by_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_approval_decisions_role_valid",
        ),
        CheckConstraint(
            "permission_used IN ("
            "'approval:decide', 'approval:decide_sensitive'"
            ")",
            name="ck_approval_decisions_permission_valid",
        ),
        CheckConstraint(
            "decision_note IS NULL OR ("
            "char_length(btrim(decision_note)) >= 1 "
            "AND char_length(decision_note) <= 500"
            ")",
            name="ck_approval_decisions_note_valid",
        ),
        UniqueConstraint(
            "approval_request_id",
            name="uq_approval_decisions_request",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    approval_request_id = Column(
        BigInteger,
        ForeignKey(
            "approval_requests.id",
            name=(
                "fk_approval_decisions_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    decision = Column(
        String(16),
        nullable=False,
    )

    decided_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_approval_decisions_decided_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    decided_by_reference = Column(
        String(255),
        nullable=False,
    )

    decided_by_role = Column(
        String(32),
        nullable=False,
    )

    permission_used = Column(
        String(64),
        nullable=False,
    )

    decision_note = Column(
        Text,
        nullable=True,
    )

    sensitive_elevation_verified = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_approval_decisions_decider_created",
    ApprovalDecision.decided_by_user_id,
    ApprovalDecision.created_at,
    ApprovalDecision.id,
)

class ApprovalConsumption(Base):
    __tablename__ = "approval_consumptions"

    __table_args__ = (
        CheckConstraint(
            "consumer_actor_type IN "
            "('agent', 'system', 'integration')",
            name="ck_approval_consumptions_actor_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(consumer_reference)) >= 1",
            name="ck_approval_consumptions_consumer_ref_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(authority_reference)) >= 1",
            name="ck_approval_consumptions_authority_ref_not_blank",
        ),
        CheckConstraint(
            "authority_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_approval_consumptions_authority_role_valid",
        ),
        CheckConstraint(
            "authority_user_id IS NULL OR authority_user_id > 0",
            name="ck_approval_consumptions_authority_user_positive",
        ),
        CheckConstraint(
            "char_length(btrim(runtime_idempotency_key)) >= 1 "
            "AND runtime_idempotency_key = "
            "lower(btrim(runtime_idempotency_key))",
            name="ck_approval_consumptions_runtime_key_valid",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64 "
            "AND request_fingerprint = lower(request_fingerprint)",
            name="ck_approval_consumptions_fingerprint_format",
        ),
        CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_approval_consumptions_input_digest_format",
        ),
        CheckConstraint(
            "status IN ('reserved', 'consumed', 'failed')",
            name="ck_approval_consumptions_status_valid",
        ),
        CheckConstraint(
            "(status = 'reserved' "
            "AND skill_invocation_id IS NULL "
            "AND finalized_at IS NULL "
            "AND error_code IS NULL) "
            "OR (status = 'consumed' "
            "AND skill_invocation_id IS NOT NULL "
            "AND finalized_at IS NOT NULL "
            "AND error_code IS NULL) "
            "OR (status = 'failed' "
            "AND skill_invocation_id IS NULL "
            "AND finalized_at IS NOT NULL "
            "AND error_code IS NOT NULL "
            "AND char_length(btrim(error_code)) >= 1)",
            name="ck_approval_consumptions_state_integrity",
        ),
        UniqueConstraint(
            "approval_request_id",
            name="uq_approval_consumptions_request",
        ),
        UniqueConstraint(
            "approval_decision_id",
            name="uq_approval_consumptions_decision",
        ),
        UniqueConstraint(
            "skill_invocation_id",
            name="uq_approval_consumptions_invocation",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    approval_request_id = Column(
        BigInteger,
        ForeignKey(
            "approval_requests.id",
            name=(
                "fk_approval_consumptions_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    approval_decision_id = Column(
        BigInteger,
        ForeignKey(
            "approval_decisions.id",
            name=(
                "fk_approval_consumptions_decision_id_"
                "approval_decisions"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    skill_invocation_id = Column(
        BigInteger,
        ForeignKey(
            "skill_invocations.id",
            name=(
                "fk_approval_consumptions_invocation_id_"
                "skill_invocations"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    consumer_actor_type = Column(
        String(20),
        nullable=False,
    )

    consumer_reference = Column(
        String(255),
        nullable=False,
    )

    authority_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_approval_consumptions_authority_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    authority_reference = Column(
        String(255),
        nullable=False,
    )

    authority_role = Column(
        String(32),
        nullable=False,
    )

    runtime_idempotency_key = Column(
        String(255),
        nullable=False,
    )

    request_fingerprint = Column(
        String(64),
        nullable=False,
    )

    input_digest = Column(
        String(64),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="reserved",
        server_default="reserved",
    )

    error_code = Column(
        String(64),
        nullable=True,
    )

    reserved_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    finalized_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )


Index(
    "ix_approval_consumptions_status_reserved",
    ApprovalConsumption.status,
    ApprovalConsumption.reserved_at,
    ApprovalConsumption.id,
)

Index(
    "ix_approval_consumptions_authority_reserved",
    ApprovalConsumption.authority_user_id,
    ApprovalConsumption.reserved_at,
    ApprovalConsumption.id,
)
