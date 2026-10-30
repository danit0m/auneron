"""
Adaptador PostgreSQL (SQLAlchemy Core) do repositório transacional da
confirmação de importação (MVP-FIN-IMPORT-01 / TASK-024).

NÃO REGISTRADO e NÃO PRONTO PARA PRODUÇÃO. A integração em ``main.py``
é habilitada apenas no banco local auneron_test sob flag explícita. As tabelas abaixo são
apenas METADATA de contrato: a migração é separada e este módulo não a executa.
O import não cria engine, Session, conexão nem schema; não importa
``app.database``, ``Base`` nem modelos.

Implementa o protocolo ``ImportConfirmationRepository`` de
``import_confirmation_execution`` (módulo injetado, DTOs reutilizados):

- ``transaction()`` abre uma Session do ``session_factory`` injetado,
  exige dialeto ``postgresql`` e isolamento ``READ COMMITTED``, chama
  ``session.begin()``, faz commit só na saída normal e rollback diante de
  qualquer exceção (inclusive no commit), sempre seguido de ``close()``;
  nenhuma exceção é suprimida.
- ``lock_account_writers()`` executa ``pg_advisory_xact_lock`` com a chave
  ``ACCOUNT_IDENTITY_LOCK_KEY`` do schema puro, a mesma que
  ``app.core.account_identity_lock`` adquire em ``POST/PUT/DELETE
  /accounts`` e no upload legado (``import_service.import_clients``).
- Toda outra operação só roda depois do lock, na mesma transação.

Pré-condição de ATIVAÇÃO: o protocolo comum acima está PREPARADO no
código, mas NÃO PROVADO num PostgreSQL real (concorrência entre os
escritores, com o schema b428d1a6c903 aplicado). Por
isso ``writer_protocol_verified`` é ``False`` por padrão e, assim,
``transaction()`` falha antes de abrir sessão; não há modo alternativo.
A flag é uma declaração do chamador, NÃO uma prova: testes offline a
ligam com fakes, e isso não demonstra nada sobre o banco real.

SQL sempre parametrizado; nenhuma normalização de texto em SQL (a
identidade v1 é calculada em Python pelo coordenador). Erros do banco
viram ``ImportConfirmationRepositoryError`` com código fixo e sem a
mensagem original (que pode conter valores); nada é logado.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from datetime import datetime
from decimal import Decimal
from types import ModuleType
from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy import Table
from sqlalchemy import bindparam
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import literal
from sqlalchemy import select
from sqlalchemy import tuple_
from sqlalchemy.exc import SQLAlchemyError


from app.import_confirmation_schema import ACCOUNT_IDENTITY_LOCK_KEY

# Alias histórico; o valor vive apenas no schema puro.
IMPORT_CONFIRMATION_LOCK_KEY = ACCOUNT_IDENTITY_LOCK_KEY
REQUIRED_DIALECT = "postgresql"
REQUIRED_ISOLATION = "READ COMMITTED"
IDENTITY_CHUNK_SIZE = 500
MAX_RECORD_COUNT = 5000
UNIQUE_VIOLATION = "23505"

REPOSITORY_ERROR_MESSAGES = {
    "writer_protocol_unverified": (
        "Protocolo comum de escritores de contas não verificado."
    ),
    "invalid_configuration": "Configuração do adaptador inválida.",
    "unsupported_dialect": "Banco não suportado pelo adaptador.",
    "unsupported_isolation": "Nível de isolamento não suportado.",
    "invalid_session": "Sessão de banco inválida.",
    "lock_required": "Operação exige o lock prévio na transação.",
    "invalid_argument": "Argumento interno inválido.",
    "invalid_row": "Dado inválido devolvido pelo banco.",
    "conflict": "Conflito de unicidade no banco.",
    "database_error": "Falha no banco de dados.",
}


class ImportConfirmationRepositoryError(RuntimeError):
    """Falha do adaptador com código e mensagem fixos, sem valores."""

    def __init__(self, code: str) -> None:
        if code not in REPOSITORY_ERROR_MESSAGES:
            raise ValueError("Código de erro do adaptador desconhecido.")

        self.code = code
        self.message = REPOSITORY_ERROR_MESSAGES[code]

        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"ImportConfirmationRepositoryError(code={self.code!r})"


# ---------------------------------------------------------------- metadata
# Contrato de schema APENAS COMO METADATA. Não é migração e não é aplicado.

from app.import_confirmation_schema import (
    metadata,
    accounts_table,
    receipts_table,
    audit_events_table,
)

_ACCOUNT_COLUMNS = (
    "id",
    "cliente",
    "email",
    "whatsapp",
    "valor",
    "vencimento",
    "status",
)
_SESSION_METHODS = (
    "begin",
    "commit",
    "rollback",
    "close",
    "execute",
    "connection",
    "get_bind",
)
_EXECUTION_NAMES = (
    "AccountDraft",
    "ImportAuditEvent",
    "ImportConfirmationReceipt",
    "IDENTITY_VERSION",
    "INITIAL_STATUS",
    "FILE_FORMATS",
    "MAX_EXISTING_IDENTITIES",
    "MAX_REFERENCE_LENGTH",
    "AUDIT_ACCOUNT_CREATED",
    "AUDIT_IMPORT_CONFIRMED",
)
_CENT = Decimal("0.01").as_tuple().exponent


class PostgresImportConfirmationRepository:
    """
    Repositório transacional não registrado. ``session_factory`` deve
    devolver uma nova ``Session`` SQLAlchemy a cada chamada.
    """

    def __init__(
        self,
        session_factory: Any,
        execution_module: ModuleType,
        *,
        writer_protocol_verified: bool = False,
        accounts: Table | None = None,
    ) -> None:
        if (
            not callable(session_factory)
            or type(writer_protocol_verified) is not bool
            or any(not hasattr(execution_module, n) for n in _EXECUTION_NAMES)
            or execution_module.IDENTITY_VERSION != 1
        ):
            raise ImportConfirmationRepositoryError("invalid_configuration")

        accounts = accounts_table if accounts is None else accounts

        if not isinstance(accounts, Table) or any(
            name not in accounts.c for name in _ACCOUNT_COLUMNS
        ):
            raise ImportConfirmationRepositoryError("invalid_configuration")

        self._session_factory = session_factory
        self._execution = execution_module
        self._writer_protocol_verified = writer_protocol_verified
        self._accounts = accounts

    def __repr__(self) -> str:
        return (
            "PostgresImportConfirmationRepository(writer_protocol_verified="
            f"{self._writer_protocol_verified})"
        )

    def transaction(self):
        # Bloqueio antes de qualquer sessão: ativação exige protocolo comum.
        if self._writer_protocol_verified is not True:
            raise ImportConfirmationRepositoryError("writer_protocol_unverified")

        return self._transaction()

    @contextmanager
    def _transaction(self):
        failure = None
        try:
            with self._raw_transaction() as unit:
                yield unit
        except SQLAlchemyError as exc:
            orig = getattr(exc, 'orig', None)
            failure = 'conflict' if getattr(orig, 'sqlstate', None) == UNIQUE_VIOLATION else 'database_error'
        if failure is not None:
            raise ImportConfirmationRepositoryError(failure)

    @contextmanager
    def _raw_transaction(self):
        session = self._session_factory()

        if any(not callable(getattr(session, n, None)) for n in _SESSION_METHODS):
            close = getattr(session, "close", None)

            if callable(close):
                close()

            raise ImportConfirmationRepositoryError("invalid_session")

        unit = None

        try:
            dialect = getattr(getattr(session.get_bind(), "dialect", None), "name", None)

            if dialect != REQUIRED_DIALECT:
                raise ImportConfirmationRepositoryError("unsupported_dialect")

            session.begin()

            try:
                level = session.connection().get_isolation_level()

                if level != REQUIRED_ISOLATION:
                    raise ImportConfirmationRepositoryError(
                        "unsupported_isolation"
                    )

                unit = _UnitOfWork(session, self._execution, self._accounts)

                yield unit

                unit._active = False
                session.commit()
            except BaseException:
                session.rollback()
                raise
        finally:
            if unit is not None:
                unit._active = False

            session.close()


class _UnitOfWork:
    """Operações da transação; todas, exceto o lock, exigem lock prévio."""

    def __init__(self, session: Any, execution: ModuleType, accounts: Table):
        self._session = session
        self._execution = execution
        self._accounts = accounts
        self._active = True
        self._locked = False

    def __repr__(self) -> str:
        return f"_UnitOfWork(active={self._active}, locked={self._locked})"

    # ------------------------------------------------------------ execução

    def _execute(self, statement: Any) -> list:
        failure = None

        try:
            result = self._session.execute(statement)
            rows = list(result.all()) if result.returns_rows else []
        except SQLAlchemyError as exc:
            orig = getattr(exc, "orig", None)
            failure = (
                "conflict"
                if getattr(orig, "sqlstate", None) == UNIQUE_VIOLATION
                else "database_error"
            )

        if failure is not None:
            # Fora do ``except``: sem __context__ com valores do banco.
            raise ImportConfirmationRepositoryError(failure)

        return rows

    def _require_locked(self) -> None:
        if not self._active:
            raise ImportConfirmationRepositoryError("invalid_session")

        if not self._locked:
            raise ImportConfirmationRepositoryError("lock_required")

    # ------------------------------------------------------------ protocolo

    def lock_account_writers(self) -> None:
        if not self._active or self._locked:
            raise ImportConfirmationRepositoryError("invalid_session")

        self._execute(
            select(
                func.pg_advisory_xact_lock(
                    bindparam(
                        "lock_key", IMPORT_CONFIRMATION_LOCK_KEY, type_=BigInteger
                    )
                )
            )
        )
        self._locked = True

    def find_receipt(self, actor_reference: str, idempotency_key: str):
        self._require_locked()
        self._require_text(actor_reference)
        self._require_text(idempotency_key)

        r = receipts_table.c
        rows = self._execute(
            select(
                r.actor_reference,
                r.idempotency_key,
                r.identity_version,
                r.content_sha256,
                r.file_kind,
                r.record_count,
                r.total,
                r.account_references,
            )
            .where(
                r.actor_reference == actor_reference,
                r.idempotency_key == idempotency_key,
            )
            .limit(2)
        )

        if not rows:
            return None

        if len(rows) != 1:
            raise ImportConfirmationRepositoryError("invalid_row")

        return self._receipt_from_row(rows[0], actor_reference, idempotency_key)

    def content_already_confirmed(
        self, content_sha256: str, identity_version: int
    ) -> bool:
        self._require_locked()
        self._require_text(content_sha256)

        if type(identity_version) is not int:
            raise ImportConfirmationRepositoryError("invalid_argument")

        r = receipts_table.c
        rows = self._execute(
            select(literal(1))
            .select_from(receipts_table)
            .where(
                r.content_sha256 == content_sha256,
                r.identity_version == identity_version,
            )
            .limit(1)
        )

        return bool(rows)

    def find_account_identities(self, candidates: frozenset) -> tuple:
        self._require_locked()

        limit = self._execution.MAX_EXISTING_IDENTITIES

        if type(candidates) is not frozenset or len(candidates) > limit:
            raise ImportConfirmationRepositoryError("invalid_argument")

        for pair in candidates:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or not _is_money(pair[0])
                or not _is_plain_date(pair[1])
            ):
                raise ImportConfirmationRepositoryError("invalid_argument")

        ordered = sorted(candidates, key=lambda pair: (pair[1], pair[0]))
        a = self._accounts.c
        found = []

        for start in range(0, len(ordered), IDENTITY_CHUNK_SIZE):
            chunk = ordered[start : start + IDENTITY_CHUNK_SIZE]
            rows = self._execute(
                select(a.cliente, a.valor, a.vencimento)
                .where(tuple_(a.valor, a.vencimento).in_(chunk))
                .order_by(a.id)
                .limit(limit - len(found) + 1)
            )

            for row in rows:
                if len(row) != 3:
                    raise ImportConfirmationRepositoryError("invalid_row")

                cliente, valor, vencimento = row

                if (
                    type(cliente) is not str
                    or not _is_money(valor)
                    or not _is_plain_date(vencimento)
                ):
                    raise ImportConfirmationRepositoryError("invalid_row")

                # Nomes brutos: a normalização v1 é feita em Python.
                found.append((cliente, valor, vencimento))

            if len(found) > limit:
                raise ImportConfirmationRepositoryError("invalid_row")

        return tuple(found)

    def create_account(self, draft: Any) -> int:
        self._require_locked()
        execution = self._execution

        if (
            type(draft) is not execution.AccountDraft
            or draft.status != execution.INITIAL_STATUS
            or type(draft.cliente) is not str
            or not _is_money(draft.valor)
            or not _is_plain_date(draft.vencimento)
        ):
            raise ImportConfirmationRepositoryError("invalid_argument")

        a = self._accounts.c
        rows = self._execute(
            insert(self._accounts)
            .values(
                {
                    a.cliente: draft.cliente,
                    a.email: draft.email,
                    a.whatsapp: draft.whatsapp,
                    a.valor: draft.valor,
                    a.vencimento: draft.vencimento,
                    a.status: execution.INITIAL_STATUS,
                }
            )
            .returning(a.id)
        )

        if len(rows) != 1 or len(rows[0]) != 1:
            raise ImportConfirmationRepositoryError("invalid_row")

        reference = rows[0][0]

        if type(reference) is not int or reference <= 0:
            raise ImportConfirmationRepositoryError("invalid_row")

        return reference

    def append_audit_event(self, event: Any) -> None:
        self._require_locked()
        execution = self._execution

        if (
            type(event) is not execution.ImportAuditEvent
            or event.action
            not in (execution.AUDIT_ACCOUNT_CREATED, execution.AUDIT_IMPORT_CONFIRMED)
            or not _is_optional_positive_int(event.account_reference)
            or not _is_optional_positive_int(event.row_number)
            or not _is_optional_positive_int(event.record_count)
        ):
            raise ImportConfirmationRepositoryError("invalid_argument")

        self._require_text(event.actor_reference)
        self._require_text(event.idempotency_key)
        self._require_text(event.content_sha256)

        self._execute(
            insert(audit_events_table).values(
                action=event.action,
                actor_reference=event.actor_reference,
                idempotency_key=event.idempotency_key,
                content_sha256=event.content_sha256,
                account_reference=event.account_reference,
                row_number=event.row_number,
                record_count=event.record_count,
            )
        )

    def insert_receipt(self, receipt: Any) -> None:
        self._require_locked()
        execution = self._execution

        if (
            type(receipt) is not execution.ImportConfirmationReceipt
            or receipt.file_kind not in execution.FILE_FORMATS
            or receipt.identity_version != execution.IDENTITY_VERSION
            or type(receipt.record_count) is not int
            or not 1 <= receipt.record_count <= MAX_RECORD_COUNT
            or not _is_money(receipt.total)
            or receipt.total <= 0
            or type(receipt.account_references) is not tuple
            or len(receipt.account_references) != receipt.record_count
            or not all(_is_positive_int(r) for r in receipt.account_references)
        ):
            raise ImportConfirmationRepositoryError("invalid_argument")

        self._require_text(receipt.actor_reference)
        self._require_text(receipt.idempotency_key)
        self._require_text(receipt.content_sha256)

        self._execute(
            insert(receipts_table).values(
                actor_reference=receipt.actor_reference,
                idempotency_key=receipt.idempotency_key,
                identity_version=receipt.identity_version,
                content_sha256=receipt.content_sha256,
                file_kind=receipt.file_kind,
                record_count=receipt.record_count,
                total=receipt.total,
                account_references=list(receipt.account_references),
            )
        )

    # ------------------------------------------------------------ validação

    def _require_text(self, value: Any) -> None:
        if (
            type(value) is not str
            or not value
            or len(value) > self._execution.MAX_REFERENCE_LENGTH
        ):
            raise ImportConfirmationRepositoryError("invalid_argument")

    def _receipt_from_row(self, row: Any, actor: str, key: str):
        execution = self._execution

        if len(row) != 8:
            raise ImportConfirmationRepositoryError("invalid_row")

        (
            actor_reference,
            idempotency_key,
            identity_version,
            content_sha256,
            file_kind,
            record_count,
            total,
            references,
        ) = row

        if (
            actor_reference != actor
            or idempotency_key != key
            or type(identity_version) is not int
            or type(content_sha256) is not str
            or file_kind not in execution.FILE_FORMATS
            or type(record_count) is not int
            or not _is_money(total)
            or type(references) is not list
            or len(references) != record_count
            or not all(_is_positive_int(r) for r in references)
        ):
            raise ImportConfirmationRepositoryError("invalid_row")

        return execution.ImportConfirmationReceipt(
            file_kind=file_kind,
            record_count=record_count,
            identity_version=identity_version,
            actor_reference=actor_reference,
            idempotency_key=idempotency_key,
            content_sha256=content_sha256,
            total=total,
            account_references=tuple(references),
        )


def _is_money(value: Any) -> bool:
    return (
        type(value) is Decimal
        and value.is_finite()
        and value.as_tuple().exponent == _CENT
    )


def _is_plain_date(value: Any) -> bool:
    return type(value) is date and not isinstance(value, datetime)


def _is_positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _is_optional_positive_int(value: Any) -> bool:
    return value is None or _is_positive_int(value)
