from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Float
from sqlalchemy import Date

from .database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)

    cliente = Column(String)

    email = Column(String)

    whatsapp = Column(String)

    valor = Column(Float)

    vencimento = Column(Date)

    status = Column(String)
