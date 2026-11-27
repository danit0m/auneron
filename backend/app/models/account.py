from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy.sql import func

from app.database.database import Base


class Account(Base):
    __tablename__ = "accounts"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(cliente)) >= 2",
            name="ck_accounts_cliente_min_length",
        ),
        CheckConstraint(
            "balance_cents IS NULL OR (balance_cents >= 0 AND "
            "balance_source IS NOT NULL AND balance_observed_at IS NOT NULL)",
            name="ck_accounts_confirmed_balance",
        ),
        CheckConstraint("grace_days >= 0", name="ck_accounts_grace_days"),
        CheckConstraint(
            "financial_hold IS NOT TRUE OR hold_reason IS NOT NULL",
            name="ck_accounts_hold_reason",
        ),
        CheckConstraint(
            "valor > 0",
            name="ck_accounts_valor_positive",
        ),
        CheckConstraint(
            "status IN ('aberto', 'atrasado', 'pago')",
            name="ck_accounts_status_valid",
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
    )

    cliente = Column(
        String(150),
        nullable=False,
    )

    email = Column(
        String(150),
        nullable=True,
    )

    whatsapp = Column(
        String(30),
        nullable=True,
    )

    valor = Column(
        Numeric(
            precision=14,
            scale=2,
            asdecimal=True,
        ),
        nullable=False,
    )

    vencimento = Column(
        Date,
        nullable=False,
    )

    status = Column(
        String(30),
        nullable=False,
        default="aberto",
        server_default="aberto",
    )

    balance_cents = Column(BigInteger, nullable=True)
    balance_source = Column(String(255), nullable=True)
    balance_observed_at = Column(DateTime(timezone=True), nullable=True)
    financial_hold = Column(Boolean, nullable=True)
    hold_reason = Column(String(255), nullable=True)
    hold_reviewed_by = Column(Integer, nullable=True)
    hold_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    balance_reviewed_by = Column(Integer, nullable=True)
    grace_days = Column(Integer, nullable=False, server_default="0")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
