"""Referencia estavel de origem de um titulo (AREA2 TASK054, TI1).

Somente a atribuicao manual a um titulo legado (sem referencia). Import v2,
criacao com referencia, override de duplicidade e UI ficam fora (TI2/area4).

O chamador ja segura ``lock_account_identity`` e a linha da conta com
``FOR UPDATE`` (mesma ordem de PUT/DELETE). Este modulo nunca faz commit
nem rollback: grava a linha lateral por INSERT Core e a auditoria TASK045
(so nomes de campo) por ``db.add``; o commit unico do chamador grava as
duas ou nenhuma, e qualquer erro leva o chamador ao rollback das duas.

A referencia pode conter dado pessoal (CPF/CNPJ, nome, pedido). Ela nunca
entra em log, mensagem de erro, ``repr`` ou auditoria; os erros carregam so
codigos fixos e ids de conta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.import_confirmation_schema import TITLE_REFERENCE_MAX_LENGTH
from app.models.account import Account
from app.models.import_confirmation import account_title_references
from app.services.account_data_entry_audit import (
    AuditActor,
    record_title_reference_assigned,
)
from app.core.observability import get_request_id


ASSIGNED_VIA_MANUAL = "manual_assign"
TITLE_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
# Cc: controles C0/C1; Cf: formatacao invisivel (bidi, zero-width);
# Cs/Co/Cn: surrogate, uso privado, nao atribuido; Zl/Zp: separadores.
REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
TUPLE_LOOKUP_LIMIT = 100000
UNIQUE_VIOLATION = "23505"
ACCOUNT_UNIQUE = "uq_account_title_references_account"

ERROR_STATUS = {
    "invalid_title_reference_request": 422,
    "invalid_title_source": 422,
    "invalid_title_reference": 422,
    "title_reference_already_assigned": 409,
    "reference_exists": 409,
    "legacy_duplicate_review": 409,
}


class TitleReferenceError(Exception):
    """Codigo fixo + ids de conta. Nunca carrega fonte nem referencia."""

    def __init__(self, code: str, *, account_id: int | None = None,
                 account_ids: tuple[int, ...] = ()) -> None:
        if code not in ERROR_STATUS:
            raise ValueError("codigo de erro de referencia desconhecido")
        self.code = code
        self.status_code = ERROR_STATUS[code]
        self.account_id = account_id
        self.account_ids = tuple(account_ids)
        super().__init__(code)

    def detail(self) -> dict:
        detail: dict = {"code": self.code}
        if self.account_id is not None:
            detail["account_id"] = self.account_id
        if self.account_ids:
            detail["account_ids"] = list(self.account_ids)
        return detail

    def __repr__(self) -> str:
        return f"TitleReferenceError(code={self.code!r})"


class TitleReferenceStorageError(RuntimeError):
    """Falha do INSERT lateral que nao e conflito de unicidade: 500 sem valores."""

    def __init__(self) -> None:
        super().__init__("title_reference_insert_failed")


@dataclass(frozen=True)
class AssignedTitleReference:
    account_id: int
    title_source: str
    title_reference: str
    coincident_account_ids: tuple[int, ...]

    def __repr__(self) -> str:  # sem o valor da referencia
        return f"AssignedTitleReference(account_id={self.account_id})"


def _normalized(value) -> str | None:
    if type(value) is not str:
        return None
    return unicodedata.normalize("NFC", value.strip())


def normalize_title_source(value) -> str:
    source = _normalized(value)
    if source is None or not TITLE_SOURCE_RE.fullmatch(source):
        raise TitleReferenceError("invalid_title_source")
    return source


def normalize_title_reference(value) -> str:
    # strip -> NFC -> validacao; o resultado e o que fica gravado.
    reference = _normalized(value)
    if (
        reference is None
        or not 1 <= len(reference) <= TITLE_REFERENCE_MAX_LENGTH
        or reference != reference.strip()
        or any(unicodedata.category(char) in REJECTED_CATEGORIES for char in reference)
    ):
        raise TitleReferenceError("invalid_title_reference")
    return reference


def _identity_name(name: str) -> str:
    # Mesma normalizacao de account_identity_exists (v1).
    return " ".join(name.split()).casefold()


def classify_coincident_tuples(db: Session, account: Account) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(nao referenciadas, referenciadas) com o mesmo cliente normalizado,
    valor e vencimento da conta, excluindo a propria conta."""
    rows = db.execute(
        select(Account.id, Account.cliente, account_title_references.c.id)
        .outerjoin(
            account_title_references,
            account_title_references.c.account_id == Account.id,
        )
        .where(
            Account.valor == account.valor,
            Account.vencimento == account.vencimento,
            Account.id != account.id,
        )
        .order_by(Account.id)
        .limit(TUPLE_LOOKUP_LIMIT + 1)
    ).all()
    if len(rows) > TUPLE_LOOKUP_LIMIT or any(type(row[1]) is not str for row in rows):
        raise RuntimeError("consulta de identidade invalida ou acima do limite")
    candidate = _identity_name(account.cliente)
    unreferenced, referenced = [], []
    for account_id, cliente, reference_id in rows:
        if _identity_name(cliente) == candidate:
            (referenced if reference_id is not None else unreferenced).append(account_id)
    return tuple(unreferenced), tuple(referenced)


def _unique_violation(error: DBAPIError) -> str | None:
    orig = getattr(error, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate != UNIQUE_VIOLATION:
        return None
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None) or ""


def assign_title_reference(db: Session, account: Account, title_source, title_reference,
                           actor: AuditActor) -> AssignedTitleReference:
    if not isinstance(actor, AuditActor):
        raise RuntimeError("ator humano autenticado obrigatorio")
    source = normalize_title_source(title_source)
    reference = normalize_title_reference(title_reference)

    table = account_title_references
    if db.execute(
        select(table.c.id).where(table.c.account_id == account.id).limit(1)
    ).first() is not None:
        raise TitleReferenceError("title_reference_already_assigned")

    owner, failed = None, False
    try:
        owner = db.execute(
            select(table.c.account_id).where(
                table.c.title_source == source,
                table.c.title_reference == reference,
            ).limit(1)
        ).first()
    except DBAPIError:
        failed = True
    if failed:
        # A mensagem do SQLAlchemy cita os parametros: nada dela sobe.
        raise TitleReferenceStorageError()
    if owner is not None:
        raise TitleReferenceError("reference_exists", account_id=owner[0])

    unreferenced, referenced = classify_coincident_tuples(db, account)
    if unreferenced:
        # Duplicata legada (pre-TASK045): sem override nesta area (D6).
        raise TitleReferenceError("legacy_duplicate_review", account_ids=unreferenced)

    failed, conflict = False, None
    try:
        db.execute(
            insert(table).values(
                account_id=account.id,
                title_source=source,
                title_reference=reference,
                assigned_via=ASSIGNED_VIA_MANUAL,
                actor_user_id=actor.user_id,
                auth_session_id=actor.auth_session_id,
                request_id=get_request_id(),
            )
        )
    except DBAPIError as error:
        # Backstop do banco (o lock de identidade ja serializa os escritores).
        failed, conflict = True, _unique_violation(error)
    # Fora do ``except``: a mensagem do driver/SQLAlchemy cita os parametros
    # (a referencia); nada dela vira contexto, log ou corpo de resposta. O
    # chamador desfaz a transacao.
    if failed and conflict is None:
        raise TitleReferenceStorageError()
    if failed:
        if conflict == ACCOUNT_UNIQUE:
            raise TitleReferenceError("title_reference_already_assigned")
        raise TitleReferenceError("reference_exists")

    record_title_reference_assigned(db, account, actor)
    return AssignedTitleReference(
        account_id=account.id,
        title_source=source,
        title_reference=reference,
        coincident_account_ids=referenced,
    )
