from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.sql import true

from app.database.database import Base


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        UniqueConstraint(
            "email",
            name="uq_users_email",
        ),
        CheckConstraint(
            "char_length(btrim(name)) >= 2",
            name="ck_users_name_min_length",
        ),
        CheckConstraint(
            "char_length(btrim(email)) >= 3",
            name="ck_users_email_min_length",
        ),
        CheckConstraint(
            "email = lower(email)",
            name="ck_users_email_lowercase",
        ),
        CheckConstraint(
            "role IN ("
            "'viewer', "
            "'analyst', "
            "'manager', "
            "'executive', "
            "'administrator', "
            "'developer', "
            "'system'"
            ")",
            name="ck_users_role_valid",
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
    )

    name = Column(
        String(120),
        nullable=False,
    )

    email = Column(
        String(254),
        nullable=False,
    )

    password_hash = Column(
        String(255),
        nullable=False,
    )

    role = Column(
        String(32),
        nullable=False,
        default="viewer",
        server_default="viewer",
    )

    active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_login_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
