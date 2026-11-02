# Pure schema definitions: no engines, sessions, connections or DDL execution.
from sqlalchemy import BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, MetaData, Numeric, SmallInteger, String, Table, UniqueConstraint, func
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

# AREA2 TASK054 (TI1): referência estável de origem do título, numa tabela
# lateral insert-only (``accounts`` e o espelho acima não mudam). Uma
# referência por conta (UNIQUE account_id), única por (fonte, referência),
# FK RESTRICT e trigger append-only na migração. O valor pode conter dado
# pessoal: fica só aqui, nunca em auditoria, log ou corpo de erro.
# CHECKs sem ``IN (...)``: evitam a re-renderização varchar pós-restore.
TITLE_SOURCE_PATTERN = "^[a-z0-9][a-z0-9._-]{0,63}$"
TITLE_REFERENCE_MAX_LENGTH = 128

title_references_table = Table(
    "account_title_references",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "account_id",
        Integer,
        ForeignKey(
            "accounts.id",
            ondelete="RESTRICT",
            name="fk_account_title_references_account_id",
        ),
        nullable=False,
    ),
    Column("title_source", String(64), nullable=False),
    Column("title_reference", String(TITLE_REFERENCE_MAX_LENGTH), nullable=False),
    Column("assigned_via", String(16), nullable=False),
    Column("actor_user_id", Integer, nullable=False),
    Column("auth_session_id", Integer, nullable=True),
    Column("request_id", String(128), nullable=True),
    Column("import_idempotency_key", String(36), nullable=True),
    Column("import_content_sha256", String(64), nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint("account_id", name="uq_account_title_references_account"),
    CheckConstraint(
        f"title_source ~ '{TITLE_SOURCE_PATTERN}'",
        name="ck_account_title_references_title_source",
    ),
    CheckConstraint(
        f"char_length(title_reference) BETWEEN 1 AND {TITLE_REFERENCE_MAX_LENGTH} "
        "AND title_reference = btrim(title_reference) "
        "AND title_reference !~ '[\\x01-\\x1f\\x7f-\\x9f]'",
        name="ck_account_title_references_title_reference",
    ),
    CheckConstraint(
        "assigned_via = 'import_v2' OR assigned_via = 'manual_assign'",
        name="ck_account_title_references_assigned_via",
    ),
    CheckConstraint(
        "actor_user_id > 0",
        name="ck_account_title_references_actor_user_id",
    ),
    CheckConstraint(
        "auth_session_id IS NULL OR auth_session_id > 0",
        name="ck_account_title_references_auth_session_id",
    ),
    CheckConstraint(
        "import_content_sha256 IS NULL OR import_content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_account_title_references_import_content_sha256_hex",
    ),
    CheckConstraint(
        "(assigned_via = 'import_v2' AND import_content_sha256 IS NOT NULL "
        "AND import_idempotency_key IS NOT NULL) OR "
        "(assigned_via = 'manual_assign' AND import_content_sha256 IS NULL "
        "AND import_idempotency_key IS NULL)",
        name="ck_account_title_references_provenance",
    ),
    Index(
        "uq_account_title_references_source_ref",
        "title_source",
        "title_reference",
        unique=True,
    ),
)
