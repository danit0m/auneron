"""
Classificação pura de "escritores de contas ocupados" (IMPORT-READINESS-2A).

Só os SQLSTATE ``55P03`` (lock_not_available, ``lock_timeout``) e ``57014``
(query_canceled, ``statement_timeout``) contam, e só quando o chamador os
recebe na aquisição do lock de identidade ou, na confirmação, num execute
dentro da transação que ainda será desfeita por rollback ANTES do commit.
Falhas no commit nunca passam por aqui: o resultado seria incerto.

Sem SQL, sem valores e sem mensagem original do banco. Não importa
SQLAlchemy, FastAPI nem ``app``; não faz retry automático.
"""

from __future__ import annotations

from typing import Any


RETRYABLE_SQLSTATES = frozenset({"55P03", "57014"})
RETRY_AFTER_SECONDS = 5
BUSY_CODE = "account_writers_busy"
BUSY_MESSAGE = "Escritores de contas ocupados. Tente novamente em instantes."
BUSY_STATUS_CODE = 503


class AccountWritersBusy(RuntimeError):
    """Lock de identidade não obtido a tempo; transação deve ser desfeita."""

    code = BUSY_CODE

    def __init__(self) -> None:
        super().__init__(BUSY_MESSAGE)

    def __repr__(self) -> str:
        return "AccountWritersBusy()"


def is_retryable_sqlstate(error: Any) -> bool:
    # psycopg 3 expõe ``sqlstate``; psycopg2 expõe ``pgcode``.
    orig = getattr(error, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)

    if sqlstate is None:
        sqlstate = getattr(orig, "pgcode", None)

    return type(sqlstate) is str and sqlstate in RETRYABLE_SQLSTATES


def busy_detail() -> dict:
    return {"code": BUSY_CODE, "message": BUSY_MESSAGE}


def busy_headers() -> dict:
    return {"Retry-After": str(RETRY_AFTER_SECONDS)}
