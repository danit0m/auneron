from sqlalchemy import BigInteger
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import UniqueConstraint
from sqlalchemy import func

from app.database.database import Base


class ApprovalRequestInput(Base):
    """
    Resumo legivel de um pedido de aprovacao (tabela lateral).

    Guarda a data efetiva que o aprovador precisa ver antes de decidir.
    O pedido continua guardando apenas o input_digest: a execucao
    recompoe o input com esta data e o digest precisa bater, entao uma
    adulteracao desta linha derruba a execucao.
    """

    __tablename__ = "approval_request_inputs"
    __table_args__ = (
        UniqueConstraint(
            "approval_request_id",
            name="uq_approval_request_inputs_request",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    approval_request_id = Column(
        BigInteger,
        ForeignKey(
            "approval_requests.id",
            name="fk_approval_request_inputs_request_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    effective_date = Column(Date, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
