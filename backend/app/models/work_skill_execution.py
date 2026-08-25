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


class WorkSkillExecution(Base):
    __tablename__ = "work_skill_executions"

    __table_args__ = (
        CheckConstraint(
            "actor_type = 'system'",
            name="ck_work_skill_executions_actor_system",
        ),
        CheckConstraint(
            "actor_reference LIKE 'system:work:%' "
            "AND char_length(btrim(actor_reference)) >= 13",
            name="ck_work_skill_executions_actor_reference",
        ),
        CheckConstraint(
            "char_length(btrim(dispatch_key)) >= 1 "
            "AND dispatch_key = lower(btrim(dispatch_key))",
            name="ck_work_skill_executions_dispatch_key",
        ),
        CheckConstraint(
            "execution_mode IN ('read_only', 'mutating')",
            name="ck_work_skill_executions_mode_valid",
        ),
        CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_work_skill_executions_input_digest",
        ),
        CheckConstraint(
            "status IN ("
            "'configured', 'approval_pending', 'ready', "
            "'succeeded', 'failed', 'timed_out', 'cancelled'"
            ")",
            name="ck_work_skill_executions_status_valid",
        ),
        CheckConstraint(
            "dispatch_attempts >= 0",
            name="ck_work_skill_executions_attempts_nonnegative",
        ),
        CheckConstraint(
            "authority_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_work_skill_executions_authority_role",
        ),
        CheckConstraint(
            "("
            "execution_mode = 'read_only' "
            "AND approval_request_id IS NULL "
            "AND approval_consumption_id IS NULL"
            ") OR ("
            "execution_mode = 'mutating' "
            "AND (status = 'configured' OR approval_request_id IS NOT NULL)"
            ")",
            name="ck_work_skill_executions_approval_shape",
        ),
        CheckConstraint(
            "("
            "status IN ('configured', 'approval_pending', 'ready') "
            "AND finished_at IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'succeeded' "
            "AND skill_invocation_id IS NOT NULL "
            "AND finished_at IS NOT NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status IN ('failed', 'timed_out', 'cancelled') "
            "AND finished_at IS NOT NULL "
            "AND last_error_code IS NOT NULL "
            "AND char_length(btrim(last_error_code)) >= 1"
            ")",
            name="ck_work_skill_executions_terminal_integrity",
        ),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL "
            "OR finished_at >= started_at",
            name="ck_work_skill_executions_time_order",
        ),
        UniqueConstraint(
            "work_item_id",
            name="uq_work_skill_executions_work_item",
        ),
        UniqueConstraint(
            "dispatch_key",
            name="uq_work_skill_executions_dispatch_key",
        ),
        UniqueConstraint(
            "approval_request_id",
            name="uq_work_skill_executions_approval_request",
        ),
        UniqueConstraint(
            "approval_consumption_id",
            name="uq_work_skill_executions_approval_consumption",
        ),
        UniqueConstraint(
            "skill_invocation_id",
            name="uq_work_skill_executions_skill_invocation",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )
    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name="fk_work_skill_executions_work_item_id_work_items",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    skill_version_id = Column(
        BigInteger,
        ForeignKey(
            "skill_versions.id",
            name="fk_work_skill_executions_skill_version_id_skill_versions",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    approval_request_id = Column(
        BigInteger,
        ForeignKey(
            "approval_requests.id",
            name=(
                "fk_work_skill_executions_approval_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    approval_consumption_id = Column(
        BigInteger,
        ForeignKey(
            "approval_consumptions.id",
            name="fk_work_skill_exec_approval_consumption",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    skill_invocation_id = Column(
        BigInteger,
        ForeignKey(
            "skill_invocations.id",
            name=(
                "fk_work_skill_executions_skill_invocation_id_"
                "skill_invocations"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    authority_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_work_skill_executions_authority_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    authority_role = Column(
        String(32),
        nullable=False,
    )
    actor_type = Column(
        String(20),
        nullable=False,
        default="system",
        server_default="system",
    )
    actor_reference = Column(
        String(255),
        nullable=False,
    )
    dispatch_key = Column(
        String(255),
        nullable=False,
    )
    execution_mode = Column(
        String(20),
        nullable=False,
    )
    input_digest = Column(
        String(64),
        nullable=False,
    )
    status = Column(
        String(24),
        nullable=False,
        default="configured",
        server_default="configured",
    )
    last_error_code = Column(
        String(64),
        nullable=True,
    )
    dispatch_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at = Column(
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
    "ix_work_skill_executions_status_updated",
    WorkSkillExecution.status,
    WorkSkillExecution.updated_at,
    WorkSkillExecution.id,
)

Index(
    "ix_work_skill_executions_authority_status",
    WorkSkillExecution.authority_user_id,
    WorkSkillExecution.status,
    WorkSkillExecution.id,
)

Index(
    "ix_work_skill_executions_version_status",
    WorkSkillExecution.skill_version_id,
    WorkSkillExecution.status,
    WorkSkillExecution.id,
)
