from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


RuntimeContextJSON = JSON().with_variant(
    JSONB(),
    "postgresql",
)


class WorkLearningRuntimeContextSnapshot(Base):
    __tablename__ = "work_learning_runtime_context_snapshots"

    __table_args__ = (
        CheckConstraint(
            "protocol = 'work_learning_v1'",
            name="ck_work_learning_runtime_context_protocol",
        ),
        CheckConstraint(
            "context_digest ~ '^[0-9a-f]{64}$'",
            name="ck_work_learning_runtime_context_digest",
        ),
        CheckConstraint(
            "item_count >= 0 AND item_count <= 10",
            name="ck_work_learning_runtime_context_item_count",
        ),
        CheckConstraint(
            "context_bytes >= 1 AND context_bytes <= 16384",
            name="ck_work_learning_runtime_context_bytes",
        ),
        UniqueConstraint(
            "work_skill_execution_id",
            name="uq_work_learning_runtime_context_execution",
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
            name="fk_work_learning_runtime_context_execution",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name="fk_work_learning_runtime_context_work",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    skill_version_id = Column(
        BigInteger,
        ForeignKey(
            "skill_versions.id",
            name="fk_work_learning_runtime_context_skill_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    protocol = Column(
        String(32),
        nullable=False,
    )
    context_payload = Column(
        RuntimeContextJSON,
        nullable=False,
    )
    context_digest = Column(
        String(64),
        nullable=False,
    )
    item_count = Column(
        Integer,
        nullable=False,
    )
    context_bytes = Column(
        Integer,
        nullable=False,
    )
    resolved_as_of = Column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
