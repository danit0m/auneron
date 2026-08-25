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


class WorkOutcomeEvaluation(Base):
    __tablename__ = "work_outcome_evaluations"

    __table_args__ = (
        CheckConstraint(
            "terminal_status IN ("
            "'succeeded', 'failed', 'timed_out', 'cancelled'"
            ")",
            name="ck_work_outcome_eval_terminal_status",
        ),
        CheckConstraint(
            "evaluation_code IN ("
            "'execution_succeeded', 'execution_failed', "
            "'execution_timed_out', 'execution_cancelled'"
            ")",
            name="ck_work_outcome_eval_code",
        ),
        CheckConstraint(
            "learning_signal IN ('positive', 'negative', 'neutral')",
            name="ck_work_outcome_eval_signal",
        ),
        CheckConstraint(
            "evaluator_version = 'deterministic_v1'",
            name="ck_work_outcome_eval_version",
        ),
        CheckConstraint(
            "evaluation_digest ~ '^[0-9a-f]{64}$'",
            name="ck_work_outcome_eval_digest",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'memory_recorded', 'completed', 'retry_required'"
            ")",
            name="ck_work_outcome_eval_status",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_work_outcome_eval_attempts",
        ),
        CheckConstraint(
            "("
            "status = 'pending' "
            "AND completed_at IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'memory_recorded' "
            "AND memory_item_id IS NOT NULL "
            "AND completed_at IS NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'completed' "
            "AND memory_item_id IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND last_error_code IS NULL"
            ") OR ("
            "status = 'retry_required' "
            "AND completed_at IS NULL "
            "AND last_error_code IS NOT NULL "
            "AND char_length(btrim(last_error_code)) >= 1"
            ")",
            name="ck_work_outcome_eval_state_integrity",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= evaluated_at",
            name="ck_work_outcome_eval_time_order",
        ),
        UniqueConstraint(
            "work_skill_execution_id",
            name="uq_work_outcome_eval_execution",
        ),
        UniqueConstraint(
            "evaluation_digest",
            name="uq_work_outcome_eval_digest",
        ),
        UniqueConstraint(
            "memory_item_id",
            name="uq_work_outcome_eval_memory",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )
    work_skill_execution_id = Column(
        BigInteger,
        ForeignKey(
            "work_skill_executions.id",
            name="fk_work_outcome_eval_execution",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    terminal_status = Column(
        String(20),
        nullable=False,
    )
    evaluation_code = Column(
        String(40),
        nullable=False,
    )
    learning_signal = Column(
        String(16),
        nullable=False,
    )
    evaluator_version = Column(
        String(32),
        nullable=False,
        default="deterministic_v1",
        server_default="deterministic_v1",
    )
    evaluation_digest = Column(
        String(64),
        nullable=False,
    )
    status = Column(
        String(24),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    memory_item_id = Column(
        BigInteger,
        ForeignKey(
            "memory_items.id",
            name="fk_work_outcome_eval_memory",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_error_code = Column(
        String(100),
        nullable=True,
    )
    evaluated_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at = Column(
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
    "ix_work_outcome_eval_status_updated",
    WorkOutcomeEvaluation.status,
    WorkOutcomeEvaluation.updated_at,
    WorkOutcomeEvaluation.id,
)
