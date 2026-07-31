from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.sql import func

from app.database.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)

    cliente = Column(String(150), nullable=False)

    email = Column(String(150))

    whatsapp = Column(String(30))

    valor = Column(Float, nullable=False)

    vencimento = Column(Date, nullable=False)

    status = Column(String(30), default="pendente")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )