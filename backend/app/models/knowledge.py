from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.sql import func

from app.database.database import Base


class Knowledge(Base):
    __tablename__ = "knowledge"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
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
        index=True,
    )

    severity = Column(
        String(30),
        nullable=False,
        default="info",
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
        nullable=True,
        index=True,
    )

    resolved = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
