from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.sql import false
from sqlalchemy.sql import func

from app.database.database import Base


class Knowledge(Base):
    __tablename__ = "knowledge"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(agent_name)) >= 2",
            name="ck_knowledge_agent_name_min_length",
        ),
        CheckConstraint(
            "char_length(btrim(event_name)) >= 2",
            name="ck_knowledge_event_name_min_length",
        ),
        CheckConstraint(
            "char_length(btrim(knowledge_type)) >= 2",
            name="ck_knowledge_type_min_length",
        ),
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'info')",
            name="ck_knowledge_severity_valid",
        ),
        CheckConstraint(
            "char_length(btrim(title)) >= 2",
            name="ck_knowledge_title_min_length",
        ),
        CheckConstraint(
            "char_length(btrim(message)) >= 2",
            name="ck_knowledge_message_min_length",
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
    )

    agent_name = Column(
        String(100),
        nullable=False,
        index=True,
    )

    event_name = Column(
        String(100),
        nullable=False,
        index=True,
    )

    knowledge_type = Column(
        String(50),
        nullable=False,
        default="insight",
        server_default="insight",
        index=True,
    )

    severity = Column(
        String(30),
        nullable=False,
        default="info",
        server_default="info",
        index=True,
    )

    title = Column(
        String(200),
        nullable=False,
    )

    message = Column(
        Text,
        nullable=False,
    )

    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    resolved = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
