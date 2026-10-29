"""
Coordenação pura da confirmação de importação CSV/XLSX
(MVP-FIN-IMPORT-01 / TASK-024).

Recebe um ``ImportConfirmationPlan`` (por duck typing), o ator já
autenticado pelo chamador e um repositório transacional injetado, e
coordena: exclusão, recibo idempotente, revisão das identidades atuais,
criação das contas em aberto e auditoria. Escopo: a instalação
single-company atual; não há tenant nem organização.

Este módulo NÃO autentica, NÃO autoriza, NÃO acessa banco, rede, disco ou
logs e NÃO implementa as garantias abaixo num banco real: ele apenas as
exige do adaptador e as exercita contra fakes. Nenhum adaptador real
existe ainda.

Contrato do adaptador (``ImportConfirmationRepository``):

1. ``transaction()`` devolve um context manager com uma única transação
   atômica para recibo, contas e auditoria: commit apenas na saída
   normal, rollback total diante de qualquer exceção (inclusive no
   commit), sem suprimir a exceção.
2. ``lock_account_writers()`` adquire, dentro da transação, uma exclusão
   compartilhada com TODOS os escritores de contas (esta confirmação,
   upload legado e criação manual), mantida até o fim da transação. É
   chamado antes de qualquer leitura de recibo ou de identidade.
3. Recibos são vinculados ao ator: ``find_receipt`` consulta apenas o par
   (ator, chave) e a unicidade persistente é (ator, chave). Chave de
   outro ator nunca devolve recibo alheio.
4. ``content_already_confirmed`` considera recibos de qualquer ator e
   sobrevive à exclusão das contas: mesmo conteúdo com chave nova fica
   bloqueado até haver política explícita de reimportação.
5. ``find_account_identities`` devolve os valores brutos
   (cliente, valor, vencimento) de todas as contas cujo par exato
   (valor, vencimento) esteja entre os candidatos. A normalização do
   cliente é feita aqui em Python; nenhuma ``lower()`` de SQL.
6. Nenhum efeito externo (e-mail, WhatsApp, webhook, fila) acontece na
   transação nem é disparado por este módulo.

Erros próprios carregam apenas códigos e mensagens fixos. Exceções do
adaptador propagam sem tradução.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any
from typing import Protocol
from typing import Union


IDENTITY_VERSION = 1
INITIAL_STATUS = "aberto"
FILE_FORMATS = ("csv", "xlsx")
MAX_REFERENCE_LENGTH = 128
MAX_EXISTING_IDENTITIES = 100_000

AUDIT_ACCOUNT_CREATED = "account.created_by_import"
AUDIT_IMPORT_CONFIRMED = "import.confirmed"

EXECUTION_ERROR_MESSAGES = {
    "invalid_actor": "Ator autenticado ausente ou inválido.",
    "invalid_plan": "Plano de confirmação inválido.",
    "invalid_repository": "Configuração interna de persistência inválida.",
    "idempotency_conflict": (
        "Idempotency-Key já usada com outro conteúdo; nada será importado."
    ),
    "content_already_imported": (
        "Este conteúdo já foi importado; nada será importado."
    ),
    "existing_identity_conflict": (
        "O arquivo contém conta já existente; nada será importado."
    ),
}

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CENT = Decimal("0.01").as_tuple().exponent
_UNIT_METHODS = (
    "lock_account_writers",
    "find_receipt",
    "content_already_confirmed",
    "find_account_identities",
    "create_account",
    "append_audit_event",
    "insert_receipt",
)

_MISSING = object()

AccountReference = Union[int, str]
IdentityKey = tuple[str, Decimal, date]


class ImportConfirmationExecutionError(ValueError):
    """
    Rejeição da confirmação inteira. Carrega apenas um código fixo.
    """

    def __init__(self, code: str) -> None:
        if code not in EXECUTION_ERROR_MESSAGES:
            raise ValueError("Código de erro de execução desconhecido.")

        self.code = code
        self.message = EXECUTION_ERROR_MESSAGES[code]

        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"ImportConfirmationExecutionError(code={self.code!r})"


@dataclass(frozen=True)
class AccountDraft:
    """Conta a criar, sempre com status aberto. Repr sem dados pessoais."""

    row_number: int
    cliente: str = field(repr=False)
    valor: Decimal = field(repr=False)
    vencimento: date = field(repr=False)
    status: str = field(repr=False)
    email: str | None = field(repr=False)
    whatsapp: str | None = field(repr=False)


@dataclass(frozen=True)
class ImportConfirmationReceipt:
    """Recibo persistido pelo adaptador. Repr expõe só formato e contagem."""

    file_kind: str
    record_count: int
    identity_version: int
    actor_reference: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    content_sha256: str = field(repr=False)
    total: Decimal = field(repr=False)
    account_references: tuple[AccountReference, ...] = field(repr=False)


@dataclass(frozen=True)
class ImportAuditEvent:
    """Evento de auditoria sem dados pessoais do arquivo."""

    action: str
    actor_reference: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    content_sha256: str = field(repr=False)
    account_reference: AccountReference | None = field(repr=False)
    row_number: int | None = field(repr=False)
    record_count: int | None = field(repr=False)


@dataclass(frozen=True)
class ImportConfirmationResult:
    """Resultado imutável; ``replayed`` indica recibo original devolvido."""

    replayed: bool
    record_count: int
    receipt: ImportConfirmationReceipt = field(repr=False)


class ImportConfirmationUnitOfWork(Protocol):
    def lock_account_writers(self) -> None: ...

    def find_receipt(
        self, actor_reference: str, idempotency_key: str
    ) -> ImportConfirmationReceipt | None: ...

    def content_already_confirmed(
        self, content_sha256: str, identity_version: int
    ) -> bool: ...

    def find_account_identities(
        self, candidates: frozenset[tuple[Decimal, date]]
    ) -> Iterable[tuple[str, Decimal, date]]: ...

    def create_account(self, draft: AccountDraft) -> AccountReference: ...

    def append_audit_event(self, event: ImportAuditEvent) -> None: ...

    def insert_receipt(self, receipt: ImportConfirmationReceipt) -> None: ...


class ImportConfirmationRepository(Protocol):
    def transaction(
        self,
    ) -> AbstractContextManager[ImportConfirmationUnitOfWork]: ...


@dataclass(frozen=True, repr=False)
class _PlanSnapshot:
    file_kind: str
    idempotency_key: str
    content_sha256: str
    total: Decimal
    drafts: tuple[AccountDraft, ...]


def execute_import_confirmation(
    repository: ImportConfirmationRepository,
    plan: Any,
    *,
    actor_reference: str,
) -> ImportConfirmationResult:
    """
    Executa o plano numa única transação do adaptador, tudo ou nada.
    O resultado só é devolvido depois que a transação saiu sem exceção.
    """

    actor = _require_actor(actor_reference)
    snapshot = _snapshot_plan(plan)

    transaction = getattr(repository, "transaction", None)

    if not callable(transaction):
        raise ImportConfirmationExecutionError("invalid_repository")

    result = None

    with transaction() as unit:
        _require_unit(unit)
        unit.lock_account_writers()

        stored = unit.find_receipt(actor, snapshot.idempotency_key)

        if stored is None:
            result = _create(unit, actor, snapshot)
        else:
            result = _replay(stored, actor, snapshot)

    if result is None:
        # Context manager que suprimiu exceção: falha fechada.
        raise ImportConfirmationExecutionError("invalid_repository")

    return result


# ---------------------------------------------------------------- fluxo


def _replay(
    stored: Any,
    actor: str,
    snapshot: _PlanSnapshot,
) -> ImportConfirmationResult:
    if (
        type(stored) is not ImportConfirmationReceipt
        or stored.actor_reference != actor
        or stored.idempotency_key != snapshot.idempotency_key
    ):
        raise ImportConfirmationExecutionError("invalid_repository")

    if (
        stored.content_sha256 != snapshot.content_sha256
        or stored.identity_version != IDENTITY_VERSION
    ):
        raise ImportConfirmationExecutionError("idempotency_conflict")

    if (
        type(stored.identity_version) is not int
        or type(stored.record_count) is not int
        or stored.record_count != len(snapshot.drafts)
        or stored.file_kind not in FILE_FORMATS
        or not _is_money(stored.total)
        or stored.total != snapshot.total
        or type(stored.account_references) is not tuple
        or len(stored.account_references) != stored.record_count
    ):
        raise ImportConfirmationExecutionError('invalid_repository')
    references = tuple(_require_account_reference(ref) for ref in stored.account_references)
    if len(set(references)) != len(references):
        raise ImportConfirmationExecutionError('invalid_repository')

    # Recibo original, sem recriar nem reverificar contas (mesmo apagadas).
    return ImportConfirmationResult(
        replayed=True,
        record_count=stored.record_count,
        receipt=stored,
    )


def _create(
    unit: ImportConfirmationUnitOfWork,
    actor: str,
    snapshot: _PlanSnapshot,
) -> ImportConfirmationResult:
    already = unit.content_already_confirmed(
        snapshot.content_sha256, IDENTITY_VERSION
    )

    if type(already) is not bool:
        raise ImportConfirmationExecutionError("invalid_repository")

    if already:
        raise ImportConfirmationExecutionError("content_already_imported")

    drafts = snapshot.drafts
    candidates = frozenset((draft.valor, draft.vencimento) for draft in drafts)
    existing = _existing_identity_set(unit.find_account_identities(candidates))

    if existing.intersection(_draft_identity(draft) for draft in drafts):
        raise ImportConfirmationExecutionError("existing_identity_conflict")

    references = tuple(
        _require_account_reference(unit.create_account(draft))
        for draft in drafts
    )

    if len(set(references)) != len(references):
        raise ImportConfirmationExecutionError("invalid_repository")

    def event(action, reference=None, row_number=None, record_count=None):
        return ImportAuditEvent(
            action=action,
            actor_reference=actor,
            idempotency_key=snapshot.idempotency_key,
            content_sha256=snapshot.content_sha256,
            account_reference=reference,
            row_number=row_number,
            record_count=record_count,
        )

    for draft, reference in zip(drafts, references):
        unit.append_audit_event(
            event(AUDIT_ACCOUNT_CREATED, reference, draft.row_number)
        )

    receipt = ImportConfirmationReceipt(
        file_kind=snapshot.file_kind,
        record_count=len(drafts),
        identity_version=IDENTITY_VERSION,
        actor_reference=actor,
        idempotency_key=snapshot.idempotency_key,
        content_sha256=snapshot.content_sha256,
        total=snapshot.total,
        account_references=references,
    )

    unit.insert_receipt(receipt)
    unit.append_audit_event(
        event(AUDIT_IMPORT_CONFIRMED, record_count=len(drafts))
    )

    return ImportConfirmationResult(
        replayed=False,
        record_count=len(drafts),
        receipt=receipt,
    )


# ---------------------------------------------------------------- entradas


def _require_actor(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_REFERENCE_LENGTH
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ImportConfirmationExecutionError("invalid_actor")

    return value


def _require_unit(unit: Any) -> None:
    if any(not callable(getattr(unit, name, None)) for name in _UNIT_METHODS):
        raise ImportConfirmationExecutionError("invalid_repository")


def _require_account_reference(value: Any) -> AccountReference:
    if type(value) is int and value > 0:
        return value

    if (
        type(value) is str
        and value
        and len(value) <= MAX_REFERENCE_LENGTH
        and value.isprintable()
    ):
        return value

    raise ImportConfirmationExecutionError("invalid_repository")


def _attr(source: Any, name: str) -> Any:
    value = getattr(source, name, _MISSING)

    if value is _MISSING:
        raise ImportConfirmationExecutionError("invalid_plan")

    return value


def _snapshot_plan(plan: Any) -> _PlanSnapshot:
    """
    Copia e revalida o plano recebido por duck typing, falhando fechado.
    """

    file_kind = _attr(plan, "file_kind")
    idempotency_key = _attr(plan, "idempotency_key")
    content_sha256 = _attr(plan, "content_sha256")
    record_count = _attr(plan, "record_count")
    total = _attr(plan, "total")
    records = _attr(plan, "records")

    if (
        type(file_kind) is not str
        or file_kind not in FILE_FORMATS
        or type(idempotency_key) is not str
        or not _UUID4.fullmatch(idempotency_key)
        or type(content_sha256) is not str
        or not _SHA256_HEX.fullmatch(content_sha256)
        or type(records) is not tuple
        or not records
        or type(record_count) is not int
        or record_count != len(records)
    ):
        raise ImportConfirmationExecutionError("invalid_plan")

    drafts = tuple(_draft(record) for record in records)
    numbers = [draft.row_number for draft in drafts]
    identities = {_draft_identity(draft) for draft in drafts}

    if (
        any(left >= right for left, right in zip(numbers, numbers[1:]))
        or len(identities) != len(drafts)
        or not _is_money(total)
        or total != sum((draft.valor for draft in drafts), Decimal("0.00"))
    ):
        raise ImportConfirmationExecutionError("invalid_plan")

    return _PlanSnapshot(
        file_kind=file_kind,
        idempotency_key=idempotency_key,
        content_sha256=content_sha256,
        total=total,
        drafts=drafts,
    )


def _draft(record: Any) -> AccountDraft:
    row_number = _attr(record, "row_number")
    cliente = _attr(record, "cliente")
    valor = _attr(record, "valor")
    vencimento = _attr(record, "vencimento")
    status = _attr(record, "status")
    email = _attr(record, "email")
    whatsapp = _attr(record, "whatsapp")

    if (
        type(row_number) is not int
        or row_number < 2
        or type(cliente) is not str
        or not cliente.strip()
        or not _is_money(valor)
        or valor <= 0
        or not _is_plain_date(vencimento)
        or status != INITIAL_STATUS
        or (email is not None and type(email) is not str)
        or (whatsapp is not None and type(whatsapp) is not str)
    ):
        raise ImportConfirmationExecutionError("invalid_plan")

    return AccountDraft(
        row_number=row_number,
        cliente=cliente,
        valor=valor,
        vencimento=vencimento,
        status=INITIAL_STATUS,
        email=email,
        whatsapp=whatsapp,
    )


# ---------------------------------------------------------------- identidade


def _identity(cliente: str, valor: Decimal, vencimento: date) -> IdentityKey:
    # Mesma expressão de ``import_confirmation_plan.identity_key`` (v1).
    return (" ".join(cliente.split()).casefold(), valor, vencimento)


def _draft_identity(draft: AccountDraft) -> IdentityKey:
    return _identity(draft.cliente, draft.valor, draft.vencimento)


def _existing_identity_set(values: Any) -> frozenset[IdentityKey]:
    if isinstance(values, (str, bytes, bytearray, dict)):
        raise ImportConfirmationExecutionError("invalid_repository")

    try:
        iterator = iter(values)
    except TypeError:
        iterator = None

    if iterator is None:
        raise ImportConfirmationExecutionError("invalid_repository")

    identities = set()

    for count, item in enumerate(iterator, start=1):
        if count > MAX_EXISTING_IDENTITIES:
            raise ImportConfirmationExecutionError("invalid_repository")

        if type(item) is not tuple or len(item) != 3:
            raise ImportConfirmationExecutionError("invalid_repository")

        cliente, valor, vencimento = item

        if (
            type(cliente) is not str
            or not _is_money(valor)
            or not _is_plain_date(vencimento)
        ):
            raise ImportConfirmationExecutionError("invalid_repository")

        identities.add(_identity(cliente, valor, vencimento))

    return frozenset(identities)


def _is_money(value: Any) -> bool:
    return (
        type(value) is Decimal
        and value.is_finite()
        and value.as_tuple().exponent == _CENT
    )


def _is_plain_date(value: Any) -> bool:
    return type(value) is date and not isinstance(value, datetime)
