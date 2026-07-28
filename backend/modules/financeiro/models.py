from sqlalchemy import Column, Integer, String, Float, Date
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class AccountsReceivable(Base):
    __tablename__ = "accounts_receivable"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String, index=True)
    invoice_number = Column(String, unique=True, index=True)
    due_date = Column(Date)
    amount = Column(Float)
    status = Column(String, default="pending") # pending, paid, overdue
    # Adicionar mais campos conforme necessário para a análise
