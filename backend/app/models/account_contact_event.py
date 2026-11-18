from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import func

from app.database.database import Base


class AccountContactEvent(Base):
    """
    A5 -- contato e promessa de pagamento por titulo (tabela lateral).

    So inclusao: nao ha rota nem repositorio de update/delete. A promessa
    vigente e derivada na leitura do ultimo evento promessa/remarcacao/
    encerramento; accounts nao e alterada.
    """

    __tablename__ = "account_contact_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_account_contact_events_idempotency_key",
        ),
        CheckConstraint(
            "event_type IN ('contato', 'promessa', 'remarcacao', "
            "'encerramento')",
            name="ck_account_contact_events_event_type_valid",
        ),
        CheckConstraint(
            "(event_type IN ('promessa', 'remarcacao') "
            "AND promised_date IS NOT NULL) "
            "OR (event_type IN ('contato', 'encerramento') "
            "AND promised_date IS NULL)",
            name="ck_account_contact_events_promised_date_by_type",
        ),
        CheckConstraint(
            "note IS NULL OR (char_length(btrim(note)) >= 1 "
            "AND char_length(note) <= 280)",
            name="ck_account_contact_events_note_size",
        ),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) >= 1",
            name="ck_account_contact_events_idempotency_key_not_blank",
        ),
        Index(
            "ix_account_contact_events_account_created",
            "account_id",
            "created_at",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="fk_account_contact_events_account_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    event_type = Column(String(20), nullable=False)
    promised_date = Column(Date, nullable=True)
    note = Column(String(280), nullable=True)
    actor_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_account_contact_events_actor_user_id",
        ),
        nullable=False,
    )
    idempotency_key = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
