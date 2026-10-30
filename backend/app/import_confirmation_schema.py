# Pure schema definitions: no engines, sessions, connections or DDL execution.
from sqlalchemy import BigInteger, CheckConstraint, Column, Date, DateTime, Integer, MetaData, Numeric, SmallInteger, String, Table, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

MAX_RECORD_COUNT = 5000

# Chave única do advisory lock de identidade (cliente, valor, vencimento)
# de ``accounts`` ("AUNRIMP1" em ASCII). Compartilhada pela confirmação e
# por app/core/account_identity_lock.py; não duplicar.
ACCOUNT_IDENTITY_LOCK_KEY = 0x41554E52494D5031

metadata = MetaData()

# Espelho local das colunas reais de ``app.models.account.Account``.
accounts_table = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("cliente", String(150), nullable=False),
    Column("email", String(150), nullable=True),
    Column("whatsapp", String(30), nullable=True),
    Column("valor", Numeric(14, 2, asdecimal=True), nullable=False),
    Column("vencimento", Date, nullable=False),
    Column("status", String(30), nullable=False),
)

receipts_table = Table(
    "import_confirmation_receipts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("actor_reference", String(128), nullable=False),
    Column("idempotency_key", String(36), nullable=False),
    Column("identity_version", SmallInteger, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("file_kind", String(8), nullable=False),
    Column("record_count", Integer, nullable=False),
    Column("total", Numeric(18, 2, asdecimal=True), nullable=False),
    # Referências históricas sem FK: excluir contas não apaga o recibo.
    Column("account_references", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint(
        "actor_reference",
        "idempotency_key",
        name="uq_import_receipts_actor_key",
    ),
    # Conservador: mesmo conteúdo nunca é confirmado duas vezes (v1).
    UniqueConstraint(
        "content_sha256",
        "identity_version",
        name="uq_import_receipts_content_version",
    ),
    CheckConstraint(
        "file_kind IN ('csv', 'xlsx')", name="ck_import_receipts_file_kind"
    ),
    CheckConstraint(
        f"record_count BETWEEN 1 AND {MAX_RECORD_COUNT}",
        name="ck_import_receipts_record_count",
    ),
    CheckConstraint("total > 0", name="ck_import_receipts_total_positive"),
    CheckConstraint(
        "identity_version >= 1", name="ck_import_receipts_identity_version"
    ),
    CheckConstraint(
        "jsonb_typeof(account_references) = 'array'",
        name="ck_import_receipts_references_array",
    ),
)

# Auditoria separada, sem FK e sem dados pessoais do arquivo.
audit_events_table = Table(
    "import_confirmation_audit_events",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("action", String(64), nullable=False),
    Column("actor_reference", String(128), nullable=False),
    Column("idempotency_key", String(36), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("account_reference", BigInteger, nullable=True),
    Column("row_number", Integer, nullable=True),
    Column("record_count", Integer, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
