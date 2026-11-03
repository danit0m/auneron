"""
Adaptador PostgreSQL (SQLAlchemy Core) da confirmação de importação com
identidade v2 (AREA2 TI2, CONTRACT-046 §A5).

NÃO PRONTO PARA PRODUÇÃO. Registrado em ``main.py`` apenas sob o mesmo guard
do v1 (banco local auneron_test, flag explícita). As tabelas são apenas
METADATA de ``app.import_confirmation_schema``; nenhuma DDL nova: a tabela
lateral ``account_title_references`` (TI1, migração e9a4c7d2b135) já aceita
``assigned_via='import_v2'`` com a proveniência da importação.

Compõe o adaptador v1 já construído (``PostgresImportConfirmationRepository``)
em vez de alterá-lo: transação única, dialeto/isolamento exigidos,
``writer_protocol_verified``, advisory lock ``ACCOUNT_IDENTITY_LOCK_KEY``
(o mesmo de ``POST/PUT/DELETE /accounts``, do upload legado e de
``POST /accounts/{id}/title-reference``), leitura de recibo e mapeamento de
erros (23505 -> ``conflict``, 55P03/57014 -> ``account_writers_busy``) são os
do v1. Este módulo só acrescenta os statements v2, todos depois do lock e na
mesma transação. Usa ``_execute``/``_require_locked`` da unidade v1 (mesmo
pacote); o módulo v1 continua sem ler nem escrever a tabela lateral.

O índice único (title_source, title_reference) e ``UNIQUE(account_id)`` são
o backstop do banco; o lock serializa os escritores. A referência pode
conter dado pessoal: SQL sempre parametrizado, erros com código fixo, nada é
logado. Nada aqui foi PROVADO num PostgreSQL real.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date
from datetime import datetime
from decimal import Decimal
from types import ModuleType
from typing import Any

from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import tuple_

from app.import_confirmation_schema import TITLE_REFERENCE_MAX_LENGTH
from app.import_confirmation_schema import TITLE_SOURCE_PATTERN
from app.import_confirmation_schema import accounts_table
from app.import_confirmation_schema import audit_events_table
from app.import_confirmation_schema import receipts_table
from app.import_confirmation_schema import title_references_table


ASSIGNED_VIA_IMPORT = "import_v2"
CHUNK_SIZE = 500
MAX_RECORD_COUNT = 5000
MAX_ACTOR_USER_ID = 2**31 - 1
IDEMPOTENCY_KEY_LENGTH = 36

_ACTOR = re.compile(r"^user:([1-9][0-9]{0,9})$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_TITLE_SOURCE = re.compile(TITLE_SOURCE_PATTERN)
_CENT = Decimal("0.01").as_tuple().exponent
_V2_NAMES = (
    "IDENTITY_VERSION",
    "INITIAL_STATUS",
    "FILE_FORMATS",
    "AUDIT_ACTIONS",
    "MAX_EXISTING_IDENTITIES",
    "MAX_REFERENCE_LENGTH",
    "AccountDraftV2",
    "TitleReferenceAssignment",
    "ImportAuditEventV2",
    "ImportReceiptV2",
)
_BASE_UNIT_METHODS = (
    "lock_account_writers",
    "find_receipt",
    "content_already_confirmed",
    "_execute",
    "_require_locked",
)


class PostgresImportIdentityV2Repository:
    """
    ``base_repository``: instância do adaptador v1 (dona da sessão e do
    lock). ``repository_module``: módulo v1, para a classe de erro comum.
    ``v2_module``: ``import_identity_v2`` (DTOs e constantes).
    """

    def __init__(
        self,
        base_repository: Any,
        repository_module: ModuleType,
        v2_module: ModuleType,
    ) -> None:
        error = getattr(repository_module, "ImportConfirmationRepositoryError", None)

        if not isinstance(error, type) or not issubclass(error, Exception):
            raise ValueError("Configuração do adaptador v2 inválida.")

        if (
            not callable(getattr(base_repository, "transaction", None))
            or any(not hasattr(v2_module, name) for name in _V2_NAMES)
            or v2_module.IDENTITY_VERSION != 2
        ):
            raise error("invalid_configuration")

        self._base = base_repository
        self._error = error
        self._v2 = v2_module

    def __repr__(self) -> str:
        return f"PostgresImportIdentityV2Repository(base={self._base!r})"

    def transaction(self):
        # O v1 falha antes de abrir sessão se o protocolo não foi verificado.
        return self._transaction(self._base.transaction())

    @contextmanager
    def _transaction(self, base_cm):
        with base_cm as base_unit:
            unit = _UnitOfWorkV2(base_unit, self._v2, self._error)

            try:
                yield unit
            finally:
                unit._active = False


class _UnitOfWorkV2:
    """Operações v2; todas, exceto o lock, exigem o lock prévio do v1."""

    def __init__(self, base: Any, v2: ModuleType, error: type) -> None:
        if any(not callable(getattr(base, n, None)) for n in _BASE_UNIT_METHODS):
            raise error("invalid_session")

        self._base = base
        self._v2 = v2
        self._error = error
        self._active = True

    def __repr__(self) -> str:
        return f"_UnitOfWorkV2(active={self._active})"

    # ------------------------------------------------------------ base v1

    def _require(self) -> None:
        if not self._active:
            raise self._error("invalid_session")

        self._base._require_locked()

    def _execute(self, statement: Any) -> list:
        self._require()
        return self._base._execute(statement)

    def lock_account_writers(self) -> None:
        if not self._active:
            raise self._error("invalid_session")

        self._base.lock_account_writers()

    def find_receipt(self, actor_reference: str, idempotency_key: str):
        self._require()
        # Recibo de qualquer versão: a coordenação v2 rejeita v1 como
        # ``idempotency_conflict``.
        return self._base.find_receipt(actor_reference, idempotency_key)

    def content_already_confirmed(
        self, content_sha256: str, identity_version: int
    ) -> bool:
        self._require()

        if type(identity_version) is not int or identity_version != 2:
            raise self._error("invalid_argument")

        return self._base.content_already_confirmed(content_sha256, identity_version)

    # ------------------------------------------------------------ leitura

    def find_title_references(self, title_source: str, references: frozenset) -> tuple:
        self._require()
        self._require_source(title_source)

        if type(references) is not frozenset or not 1 <= len(references) <= MAX_RECORD_COUNT:
            raise self._error("invalid_argument")

        for reference in references:
            self._require_reference(reference)

        t = title_references_table.c
        a = accounts_table.c
        ordered = sorted(references)
        found = []
        seen = set()

        for start in range(0, len(ordered), CHUNK_SIZE):
            chunk = ordered[start : start + CHUNK_SIZE]
            rows = self._execute(
                select(t.title_reference, t.account_id, a.cliente, a.valor, a.vencimento)
                .select_from(
                    title_references_table.join(accounts_table, a.id == t.account_id)
                )
                .where(t.title_source == title_source, t.title_reference.in_(chunk))
                .order_by(t.id)
                .limit(len(chunk) + 1)
            )

            for row in rows:
                if len(row) != 5:
                    raise self._error("invalid_row")

                reference, account_id, cliente, valor, vencimento = row

                if (
                    type(reference) is not str
                    or reference not in references
                    or reference in seen
                    or not _is_positive_int(account_id)
                    or type(cliente) is not str
                    or not _is_money(valor)
                    or not _is_plain_date(vencimento)
                ):
                    raise self._error("invalid_row")

                seen.add(reference)
                found.append((reference, account_id, cliente, valor, vencimento))

        return tuple(found)

    def find_account_tuples(self, candidates: frozenset) -> tuple:
        self._require()
        limit = self._v2.MAX_EXISTING_IDENTITIES

        if type(candidates) is not frozenset or len(candidates) > limit:
            raise self._error("invalid_argument")

        for pair in candidates:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or not _is_money(pair[0])
                or not _is_plain_date(pair[1])
            ):
                raise self._error("invalid_argument")

        ordered = sorted(candidates, key=lambda pair: (pair[1], pair[0]))
        a = accounts_table.c
        t = title_references_table.c
        found = []

        for start in range(0, len(ordered), CHUNK_SIZE):
            chunk = ordered[start : start + CHUNK_SIZE]
            rows = self._execute(
                select(a.id, a.cliente, a.valor, a.vencimento, t.id)
                .select_from(
                    accounts_table.outerjoin(
                        title_references_table, t.account_id == a.id
                    )
                )
                .where(tuple_(a.valor, a.vencimento).in_(chunk))
                .order_by(a.id)
                .limit(limit - len(found) + 1)
            )

            for row in rows:
                if len(row) != 5:
                    raise self._error("invalid_row")

                account_id, cliente, valor, vencimento, reference_id = row

                if (
                    not _is_positive_int(account_id)
                    or type(cliente) is not str
                    or not _is_money(valor)
                    or not _is_plain_date(vencimento)
                    or not (reference_id is None or _is_positive_int(reference_id))
                ):
                    raise self._error("invalid_row")

                # Nome bruto: a normalização é feita em Python pela coordenação.
                found.append(
                    (account_id, cliente, valor, vencimento, reference_id is not None)
                )

            if len(found) > limit:
                raise self._error("invalid_row")

        return tuple(found)

    # ------------------------------------------------------------ escrita

    def create_account(self, draft: Any) -> int:
        self._require()
        v2 = self._v2

        if (
            type(draft) is not v2.AccountDraftV2
            or draft.status != v2.INITIAL_STATUS
            or type(draft.cliente) is not str
            or not _is_money(draft.valor)
            or not _is_plain_date(draft.vencimento)
        ):
            raise self._error("invalid_argument")

        a = accounts_table.c
        rows = self._execute(
            insert(accounts_table)
            .values(
                {
                    a.cliente: draft.cliente,
                    a.email: draft.email,
                    a.whatsapp: draft.whatsapp,
                    a.valor: draft.valor,
                    a.vencimento: draft.vencimento,
                    a.status: v2.INITIAL_STATUS,
                }
            )
            .returning(a.id)
        )

        if len(rows) != 1 or len(rows[0]) != 1 or not _is_positive_int(rows[0][0]):
            raise self._error("invalid_row")

        return rows[0][0]

    def create_title_reference(self, assignment: Any) -> None:
        self._require()

        if (
            type(assignment) is not self._v2.TitleReferenceAssignment
            or not _is_positive_int(assignment.account_reference)
            or type(assignment.idempotency_key) is not str
            or len(assignment.idempotency_key) != IDEMPOTENCY_KEY_LENGTH
            or type(assignment.content_sha256) is not str
            or not _SHA256_HEX.fullmatch(assignment.content_sha256)
        ):
            raise self._error("invalid_argument")

        self._require_source(assignment.title_source)
        self._require_reference(assignment.title_reference)
        actor_user_id = self._actor_user_id(assignment.actor_reference)

        self._execute(
            insert(title_references_table).values(
                account_id=assignment.account_reference,
                title_source=assignment.title_source,
                title_reference=assignment.title_reference,
                assigned_via=ASSIGNED_VIA_IMPORT,
                actor_user_id=actor_user_id,
                auth_session_id=None,
                request_id=None,
                import_idempotency_key=assignment.idempotency_key,
                import_content_sha256=assignment.content_sha256,
            )
        )

    def append_audit_event(self, event: Any) -> None:
        self._require()
        v2 = self._v2

        if (
            type(event) is not v2.ImportAuditEventV2
            or event.action not in v2.AUDIT_ACTIONS
            or not _is_optional_positive_int(event.account_reference)
            or not _is_optional_positive_int(event.row_number)
            or not _is_optional_positive_int(event.record_count)
        ):
            raise self._error("invalid_argument")

        for value in (event.actor_reference, event.idempotency_key, event.content_sha256):
            self._require_text(value)

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
        self._require()
        v2 = self._v2

        if (
            type(receipt) is not v2.ImportReceiptV2
            or receipt.file_kind not in v2.FILE_FORMATS
            or receipt.identity_version != v2.IDENTITY_VERSION
            or type(receipt.record_count) is not int
            or not 1 <= receipt.record_count <= MAX_RECORD_COUNT
            or not _is_money(receipt.total)
            or receipt.total <= 0
            or type(receipt.account_references) is not tuple
            or len(receipt.account_references) != receipt.record_count
            or not all(_is_positive_int(r) for r in receipt.account_references)
        ):
            raise self._error("invalid_argument")

        for value in (receipt.actor_reference, receipt.idempotency_key, receipt.content_sha256):
            self._require_text(value)

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
            or len(value) > self._v2.MAX_REFERENCE_LENGTH
        ):
            raise self._error("invalid_argument")

    def _require_source(self, value: Any) -> None:
        if type(value) is not str or not _TITLE_SOURCE.fullmatch(value):
            raise self._error("invalid_argument")

    def _require_reference(self, value: Any) -> None:
        # A normalização completa (NFC, categorias) já foi feita pelo parser;
        # aqui só o que a CHECK do banco também exige.
        if (
            type(value) is not str
            or not 1 <= len(value) <= TITLE_REFERENCE_MAX_LENGTH
            or value != value.strip()
        ):
            raise self._error("invalid_argument")

    def _actor_user_id(self, actor_reference: Any) -> int:
        # A rota sempre usa ``user:<id>`` da sessão autenticada.
        match = _ACTOR.fullmatch(actor_reference) if type(actor_reference) is str else None

        if match is None or int(match.group(1)) > MAX_ACTOR_USER_ID:
            raise self._error("invalid_argument")

        return int(match.group(1))


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
