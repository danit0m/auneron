from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy.sql import func

from app.database.database import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    __table_args__ = (
        UniqueConstraint(
            "token_hash",
            name="uq_auth_sessions_token_hash",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expiration",
        ),
        CheckConstraint(
            "elevated_until IS NULL "
            "OR elevated_until <= expires_at",
            name="ck_auth_sessions_elevation_expiration",
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
    )

    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    token_hash = Column(
        String(64),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    elevated_until = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
