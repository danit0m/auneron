from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import func

from app.database.database import Base


class AccountEvent(Base):
    __tablename__ = "account_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('status_changed')",
            name="ck_account_events_event_type_valid",
        ),
        CheckConstraint(
            "actor_type IN ("
            "'user', "
            "'agent', "
            "'system', "
            "'integration'"
            ")",
            name="ck_account_events_actor_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_account_events_actor_reference_not_blank",
        ),
        CheckConstraint(
            "new_status IN ('aberto', 'atrasado', 'pago')",
            name="ck_account_events_new_status_valid",
        ),
        CheckConstraint(
            "previous_status IS NULL "
            "OR previous_status IN ('aberto', 'atrasado', 'pago')",
            name="ck_account_events_previous_status_valid",
        ),
    )
    id = Column(
        BigInteger,
        primary_key=True,
    )
    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="fk_account_events_account_id_accounts",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    event_type = Column(
        String(20),
        nullable=False,
    )
    actor_type = Column(
        String(20),
        nullable=False,
    )
    actor_reference = Column(
        String(255),
        nullable=False,
    )
    actor_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_account_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    previous_status = Column(
        String(30),
        nullable=True,
    )
    new_status = Column(
        String(30),
        nullable=False,
    )
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    idempotency_key = Column(
        String(255),
        nullable=True,
    )


Index(
    "ix_account_events_account_occurred",
    AccountEvent.account_id,
    AccountEvent.occurred_at,
)
