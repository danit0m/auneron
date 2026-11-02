# Prepared migration; NOT applied. AREA2 TASK054 (TI1): insert-only side
# table with the stable origin reference of a title. accounts, the TASK045
# audit table and the import tables are not altered. FK accounts.id
# RESTRICT: a referenced account cannot be deleted while its reference
# exists. The reference may contain personal data; it lives only here.
from alembic import op
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func

revision = 'e9a4c7d2b135'
down_revision = 'c3a8f1e6d204'
branch_labels = None
depends_on = None

TABLE = 'account_title_references'
INDEX = 'uq_account_title_references_source_ref'
FUNCTION = 'account_title_references_append_only'
TRIGGER = 'trg_account_title_references_append_only'


def upgrade():
    op.create_table(
        TABLE,
        Column('id', BigInteger, primary_key=True),
        Column(
            'account_id',
            Integer,
            ForeignKey(
                'accounts.id',
                ondelete='RESTRICT',
                name='fk_account_title_references_account_id',
            ),
            nullable=False,
        ),
        Column('title_source', String(64), nullable=False),
        Column('title_reference', String(128), nullable=False),
        Column('assigned_via', String(16), nullable=False),
        Column('actor_user_id', Integer, nullable=False),
        Column('auth_session_id', Integer, nullable=True),
        Column('request_id', String(128), nullable=True),
        Column('import_idempotency_key', String(36), nullable=True),
        Column('import_content_sha256', String(64), nullable=True),
        Column(
            'created_at',
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
        UniqueConstraint('account_id', name='uq_account_title_references_account'),
        CheckConstraint(
            "title_source ~ '^[a-z0-9][a-z0-9._-]{0,63}$'",
            name='ck_account_title_references_title_source',
        ),
        CheckConstraint(
            "char_length(title_reference) BETWEEN 1 AND 128 "
            "AND title_reference = btrim(title_reference) "
            "AND title_reference !~ '[\\x01-\\x1f\\x7f-\\x9f]'",
            name='ck_account_title_references_title_reference',
        ),
        # Sem IN (...): pg_restore re-renderiza listas varchar (TASK051).
        CheckConstraint(
            "assigned_via = 'import_v2' OR assigned_via = 'manual_assign'",
            name='ck_account_title_references_assigned_via',
        ),
        CheckConstraint(
            'actor_user_id > 0',
            name='ck_account_title_references_actor_user_id',
        ),
        CheckConstraint(
            'auth_session_id IS NULL OR auth_session_id > 0',
            name='ck_account_title_references_auth_session_id',
        ),
        CheckConstraint(
            "import_content_sha256 IS NULL OR import_content_sha256 ~ '^[0-9a-f]{64}$'",
            name='ck_account_title_references_import_content_sha256_hex',
        ),
        CheckConstraint(
            "(assigned_via = 'import_v2' AND import_content_sha256 IS NOT NULL "
            "AND import_idempotency_key IS NOT NULL) OR "
            "(assigned_via = 'manual_assign' AND import_content_sha256 IS NULL "
            "AND import_idempotency_key IS NULL)",
            name='ck_account_title_references_provenance',
        ),
    )
    op.create_index(INDEX, TABLE, ['title_source', 'title_reference'], unique=True)

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
    # Destrutivo: apaga todas as referencias de titulo. Somente em banco
    # descartavel ou com aprovacao explicita e backup previo.
    op.execute(f'DROP TRIGGER {TRIGGER} ON {TABLE}')
    op.execute(f'DROP FUNCTION {FUNCTION}()')
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
