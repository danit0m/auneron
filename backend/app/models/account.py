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

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
