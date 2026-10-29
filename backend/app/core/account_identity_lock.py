"""
Protocolo comum de exclusão dos escritores que criam, apagam ou alteram a
identidade (cliente, valor, vencimento) de ``accounts`` (MVP-FIN-IMPORT-01
/ TASK-024).

``lock_account_identity(session)`` adquire ``pg_advisory_xact_lock`` com a
mesma chave e o mesmo SQL de ``lock_account_writers()`` do repositório da
confirmação. O lock vive na transação já aberta pelo chamador e cai no
commit/rollback que o chamador já faz: sem ``begin``/``commit`` extra.

Fora do PostgreSQL (SQLite legado) não faz nada e devolve ``False``,
preservando o comportamento existente; a confirmação continua recusando
qualquer dialeto não PG. Não altera semântica financeira, permissões nem
eventos: apenas serializa. PREPARADO, NÃO PROVADO num PostgreSQL real.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy import bindparam
from sqlalchemy import func
from sqlalchemy import select

from app.import_confirmation_schema import ACCOUNT_IDENTITY_LOCK_KEY


LOCKING_DIALECT = "postgresql"


def account_identity_lock_statement():
    return select(
        func.pg_advisory_xact_lock(
            bindparam("lock_key", ACCOUNT_IDENTITY_LOCK_KEY, type_=BigInteger)
        )
    )


def lock_account_identity(session: Any) -> bool:
    dialect = getattr(getattr(session.get_bind(), "dialect", None), "name", None)

    if dialect != LOCKING_DIALECT:
        return False

    if session.connection().get_isolation_level() != 'READ COMMITTED':
        raise RuntimeError('Account identity writers require READ COMMITTED.')
    session.execute(account_identity_lock_statement())

    return True


def account_identity_exists(session: Any, account_model: Any, cliente: str,
                            valor: Any, vencimento: Any, *, exclude_id=None) -> bool:
    # Caller must already hold lock_account_identity in this transaction.
    # Preserve the legacy SQLite behavior; confirmation accepts PostgreSQL only.
    if getattr(getattr(session.get_bind(), 'dialect', None), 'name', None) != LOCKING_DIALECT:
        return False
    candidate = ' '.join(cliente.split()).casefold()
    statement = select(account_model.cliente).where(
        account_model.valor == valor, account_model.vencimento == vencimento,
    )
    if exclude_id is not None:
        statement = statement.where(account_model.id != exclude_id)
    names = session.execute(statement.limit(100001)).scalars().all()
    if len(names) > 100000 or any(type(name) is not str for name in names):
        raise ValueError('Account identity lookup is invalid or exceeds its limit.')
    return any(' '.join(name.split()).casefold() == candidate for name in names)
