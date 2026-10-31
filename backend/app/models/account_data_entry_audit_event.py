from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


# Auditoria duravel de create/update/delete de accounts pela API manual e pelo
# upload CSV legado. Sem FK (nem para accounts, nem users, nem auth_sessions):
# a linha sobrevive ao DELETE da conta e nenhum CASCADE/SET NULL apaga a
# atribuicao. Append-only no banco (trigger da migracao c3a8f1e6d204).
# Nome/e-mail/whatsapp so como digest SHA-256 com separacao de dominio; o
# digest NAO e anonimo nem cifrado (dicionario recupera contatos).
_DIGEST_COLUMNS = (
    "before_cliente_sha256",
    "after_cliente_sha256",
    "before_email_sha256",
    "after_email_sha256",
    "before_whatsapp_sha256",
    "after_whatsapp_sha256",
    "source_sha256",
)


class AccountDataEntryAuditEvent(Base):
    __tablename__ = "account_data_entry_audit_events"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('create', 'update', 'delete')",
            name="ck_account_data_entry_audit_operation",
        ),
        CheckConstraint(
            "channel IN ('manual_api', 'legacy_csv_upload')",
            name="ck_account_data_entry_audit_channel",
        ),
        CheckConstraint(
            "channel = 'manual_api' OR operation = 'create'",
            name="ck_account_data_entry_audit_legacy_create_only",
        ),
        CheckConstraint(
            "account_reference > 0",
            name="ck_account_data_entry_audit_account_reference",
        ),
        CheckConstraint(
            "actor_type = 'user'",
            name="ck_account_data_entry_audit_actor_type",
        ),
        CheckConstraint(
            "actor_user_id > 0",
            name="ck_account_data_entry_audit_actor_user_id",
        ),
        CheckConstraint(
            "actor_reference = 'user:' || actor_user_id::text",
            name="ck_account_data_entry_audit_actor_reference",
        ),
        CheckConstraint(
            "auth_session_id IS NULL OR auth_session_id > 0",
            name="ck_account_data_entry_audit_auth_session_id",
        ),
        CheckConstraint(
            "jsonb_typeof(changed_fields) = 'array'",
            name="ck_account_data_entry_audit_changed_fields_array",
        ),
        CheckConstraint(
            "status_at_operation IN ('aberto', 'atrasado', 'pago')",
            name="ck_account_data_entry_audit_status",
        ),
        *(
            CheckConstraint(
                f"{column} IS NULL OR {column} ~ '^[0-9a-f]{{64}}$'",
                name=f"ck_account_data_entry_audit_{column}_hex",
            )
            for column in _DIGEST_COLUMNS
        ),
        CheckConstraint(
            "(channel = 'legacy_csv_upload') = "
            "(source_sha256 IS NOT NULL AND source_row IS NOT NULL)",
            name="ck_account_data_entry_audit_source_by_channel",
        ),
        CheckConstraint(
            "source_row IS NULL OR source_row >= 2",
            name="ck_account_data_entry_audit_source_row",
        ),
        CheckConstraint(
            "operation <> 'create' OR ("
            "before_valor IS NULL AND before_vencimento IS NULL "
            "AND before_cliente_sha256 IS NULL "
            "AND before_email_sha256 IS NULL "
            "AND before_whatsapp_sha256 IS NULL "
            "AND after_valor IS NOT NULL AND after_vencimento IS NOT NULL "
            "AND after_cliente_sha256 IS NOT NULL)",
            name="ck_account_data_entry_audit_create_shape",
        ),
        CheckConstraint(
            "operation <> 'update' OR ("
            "before_valor IS NOT NULL AND after_valor IS NOT NULL "
            "AND before_vencimento IS NOT NULL AND after_vencimento IS NOT NULL "
            "AND before_cliente_sha256 IS NOT NULL "
            "AND after_cliente_sha256 IS NOT NULL "
            "AND jsonb_array_length(changed_fields) > 0)",
            name="ck_account_data_entry_audit_update_shape",
        ),
        CheckConstraint(
            "operation <> 'delete' OR ("
            "after_valor IS NULL AND after_vencimento IS NULL "
            "AND after_cliente_sha256 IS NULL "
            "AND after_email_sha256 IS NULL "
            "AND after_whatsapp_sha256 IS NULL "
            "AND before_valor IS NOT NULL AND before_vencimento IS NOT NULL "
            "AND before_cliente_sha256 IS NOT NULL "
            "AND changed_fields = '[]'::jsonb)",
            name="ck_account_data_entry_audit_delete_shape",
        ),
    )

    id = Column(BigInteger, primary_key=True)
    operation = Column(String(16), nullable=False)
    channel = Column(String(24), nullable=False)
    account_reference = Column(Integer, nullable=False)
    actor_type = Column(String(20), nullable=False)
    actor_user_id = Column(Integer, nullable=False)
    actor_reference = Column(String(255), nullable=False)
    auth_session_id = Column(Integer, nullable=True)
    # Correlacao apenas: o cliente pode fornecer o X-Request-ID.
    request_id = Column(String(128), nullable=True)
    changed_fields = Column(JSONB, nullable=False)
    # Retrato do status no momento; nao e mudanca de status.
    status_at_operation = Column(String(30), nullable=False)
    before_valor = Column(Numeric(14, 2, asdecimal=True), nullable=True)
    after_valor = Column(Numeric(14, 2, asdecimal=True), nullable=True)
    before_vencimento = Column(Date, nullable=True)
    after_vencimento = Column(Date, nullable=True)
    before_cliente_sha256 = Column(String(64), nullable=True)
    after_cliente_sha256 = Column(String(64), nullable=True)
    before_email_sha256 = Column(String(64), nullable=True)
    after_email_sha256 = Column(String(64), nullable=True)
    before_whatsapp_sha256 = Column(String(64), nullable=True)
    after_whatsapp_sha256 = Column(String(64), nullable=True)
    source_sha256 = Column(String(64), nullable=True)
    source_row = Column(Integer, nullable=True)
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_account_data_entry_audit_account_occurred",
    AccountDataEntryAuditEvent.account_reference,
    AccountDataEntryAuditEvent.occurred_at,
)
