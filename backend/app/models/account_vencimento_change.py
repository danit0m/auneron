from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import func

from app.database.database import Base


class AccountVencimentoChange(Base):
    __tablename__ = "account_vencimento_changes"
    __table_args__ = (
        CheckConstraint(
            "previous_vencimento <> new_vencimento",
            name="ck_account_vencimento_changes_actual_change",
        ),
        CheckConstraint(
            "actor_type IN ("
            "'user', "
            "'agent', "
            "'system', "
            "'integration'"
            ")",
            name="ck_account_vencimento_changes_actor_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_account_vencimento_changes_actor_reference_not_blank",
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
            name="fk_account_vencimento_changes_account_id_accounts",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    previous_vencimento = Column(
        Date,
        nullable=False,
    )
    new_vencimento = Column(
        Date,
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
            name="fk_account_vencimento_changes_actor_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    changed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_account_vencimento_changes_account_changed",
    AccountVencimentoChange.account_id,
    AccountVencimentoChange.changed_at,
)
