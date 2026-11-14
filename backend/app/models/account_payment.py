from sqlalchemy import BigInteger
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import UniqueConstraint
from sqlalchemy import func

from app.database.database import Base


class AccountPayment(Base):
    """
    Data efetiva do pagamento de um titulo (tabela lateral).

    Escrita somente pelo corredor governado de account.mark_paid, na
    mesma transacao da mudanca de status. accounts nao e alterada.
    """

    __tablename__ = "account_payments"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            name="uq_account_payments_account",
        ),
        UniqueConstraint(
            "approval_request_id",
            name="uq_account_payments_approval_request",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="fk_account_payments_account_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    paid_on = Column(Date, nullable=False)
    approval_request_id = Column(
        BigInteger,
        ForeignKey(
            "approval_requests.id",
            name="fk_account_payments_approval_request_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
