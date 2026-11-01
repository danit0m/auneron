# Prepared migration; NOT applied. Adds the isolated, append-only data-entry
# audit of accounts (manual API + legacy CSV upload). No FK to accounts,
# users or auth_sessions: no CASCADE/SET NULL can erase a row or its actor.
from alembic import op
from sqlalchemy import BigInteger, CheckConstraint, Column, Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB

revision = 'c3a8f1e6d204'
down_revision = 'b428d1a6c903'
branch_labels = None
depends_on = None

TABLE = 'account_data_entry_audit_events'
INDEX = 'ix_account_data_entry_audit_account_occurred'
FUNCTION = 'account_data_entry_audit_append_only'
TRIGGER = 'trg_account_data_entry_audit_append_only'

DIGEST_COLUMNS = (
    'before_cliente_sha256',
    'after_cliente_sha256',
    'before_email_sha256',
    'after_email_sha256',
    'before_whatsapp_sha256',
    'after_whatsapp_sha256',
    'source_sha256',
)


def upgrade():
    op.create_table(
        TABLE,
        Column('id', BigInteger, primary_key=True),
        Column('operation', String(16), nullable=False),
        Column('channel', String(24), nullable=False),
        Column('account_reference', Integer, nullable=False),
        Column('actor_type', String(20), nullable=False),
        Column('actor_user_id', Integer, nullable=False),
        Column('actor_reference', String(255), nullable=False),
        Column('auth_session_id', Integer, nullable=True),
        Column('request_id', String(128), nullable=True),
        Column('changed_fields', JSONB, nullable=False),
        Column('status_at_operation', String(30), nullable=False),
        Column('before_valor', Numeric(14, 2, asdecimal=True), nullable=True),
        Column('after_valor', Numeric(14, 2, asdecimal=True), nullable=True),
        Column('before_vencimento', Date, nullable=True),
        Column('after_vencimento', Date, nullable=True),
        Column('before_cliente_sha256', String(64), nullable=True),
        Column('after_cliente_sha256', String(64), nullable=True),
        Column('before_email_sha256', String(64), nullable=True),
        Column('after_email_sha256', String(64), nullable=True),
        Column('before_whatsapp_sha256', String(64), nullable=True),
        Column('after_whatsapp_sha256', String(64), nullable=True),
        Column('source_sha256', String(64), nullable=True),
        Column('source_row', Integer, nullable=True),
        Column(
            'occurred_at',
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
        CheckConstraint(
            "operation IN ('create', 'update', 'delete')",
            name='ck_account_data_entry_audit_operation',
        ),
        CheckConstraint(
            "channel IN ('manual_api', 'legacy_csv_upload')",
            name='ck_account_data_entry_audit_channel',
        ),
        CheckConstraint(
            "channel = 'manual_api' OR operation = 'create'",
            name='ck_account_data_entry_audit_legacy_create_only',
        ),
        CheckConstraint(
            'account_reference > 0',
            name='ck_account_data_entry_audit_account_reference',
        ),
        CheckConstraint(
            "actor_type = 'user'",
            name='ck_account_data_entry_audit_actor_type',
        ),
        CheckConstraint(
            'actor_user_id > 0',
            name='ck_account_data_entry_audit_actor_user_id',
        ),
        CheckConstraint(
            "actor_reference = 'user:' || actor_user_id::text",
            name='ck_account_data_entry_audit_actor_reference',
        ),
        CheckConstraint(
            'auth_session_id IS NULL OR auth_session_id > 0',
            name='ck_account_data_entry_audit_auth_session_id',
        ),
        CheckConstraint(
            "jsonb_typeof(changed_fields) = 'array'",
            name='ck_account_data_entry_audit_changed_fields_array',
        ),
        CheckConstraint(
            "status_at_operation IN ('aberto', 'atrasado', 'pago')",
            name='ck_account_data_entry_audit_status',
        ),
        *(
            CheckConstraint(
                f"{column} IS NULL OR {column} ~ '^[0-9a-f]{{64}}$'",
                name=f'ck_account_data_entry_audit_{column}_hex',
            )
            for column in DIGEST_COLUMNS
        ),
        CheckConstraint(
            "(channel = 'legacy_csv_upload') = "
            "(source_sha256 IS NOT NULL AND source_row IS NOT NULL)",
            name='ck_account_data_entry_audit_source_by_channel',
        ),
        CheckConstraint(
            'source_row IS NULL OR source_row >= 2',
            name='ck_account_data_entry_audit_source_row',
        ),
        CheckConstraint(
            "operation <> 'create' OR ("
            "before_valor IS NULL AND before_vencimento IS NULL "
            "AND before_cliente_sha256 IS NULL "
            "AND before_email_sha256 IS NULL "
            "AND before_whatsapp_sha256 IS NULL "
            "AND after_valor IS NOT NULL AND after_vencimento IS NOT NULL "
            "AND after_cliente_sha256 IS NOT NULL)",
            name='ck_account_data_entry_audit_create_shape',
        ),
        CheckConstraint(
            "operation <> 'update' OR ("
            "before_valor IS NOT NULL AND after_valor IS NOT NULL "
            "AND before_vencimento IS NOT NULL AND after_vencimento IS NOT NULL "
            "AND before_cliente_sha256 IS NOT NULL "
            "AND after_cliente_sha256 IS NOT NULL "
            "AND jsonb_array_length(changed_fields) > 0)",
            name='ck_account_data_entry_audit_update_shape',
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
            name='ck_account_data_entry_audit_delete_shape',
        ),
    )
    op.create_index(INDEX, TABLE, ['account_reference', 'occurred_at'])

    # Append-only: UPDATE/DELETE por linha falham. TRUNCATE nao e bloqueado
    # (limpeza de testes); dono da tabela/superusuario pode desativar o
    # trigger. Ambos sao limites declarados, nao garantias.
    op.execute(
        f"CREATE FUNCTION {FUNCTION}() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        f"'{TABLE} is append-only'; END $$"
    )
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE "
        f"ON {TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION {FUNCTION}()"
    )


def downgrade():
    # Destrutivo: apaga todas as linhas de auditoria. Somente em banco
    # descartavel ou com aprovacao explicita e backup previo.
    op.execute(f'DROP TRIGGER {TRIGGER} ON {TABLE}')
    op.execute(f'DROP FUNCTION {FUNCTION}()')
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
