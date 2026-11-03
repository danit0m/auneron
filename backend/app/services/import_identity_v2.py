"""
Identidade de importação v2 (AREA2 TI2, CONTRACT-046 §A5): plano e
coordenação puros da confirmação com referência de título.

Escopo: a instalação single-company atual; não há tenant nem organização.
O caminho v1 (``import_confirmation_plan`` / ``import_confirmation_execution``)
não é alterado nem importado: ausência de versão continua sendo v1.

Identidade v2 de uma linha = (origem, referencia). Por linha, contra o banco:

- referência já existe com o mesmo payload (cliente normalizado, valor,
  vencimento) -> ``existing_same``: nada é inserido, auditoria
  ``title_reference_matched``; o título existente nunca é atualizado;
- referência já existe com payload diferente -> ``reference_conflict``:
  409 para o arquivo inteiro, nada é gravado;
- referência nova, mas a tupla coincide com conta NÃO referenciada ->
  ``legacy_match_review``: 409, sem override (o operador atribui a
  referência certa ao título legado pela operação TI1 e refaz o preview);
- referência nova e tupla coincidente só com contas referenciadas ->
  criada, com aviso ``coincident_referenced_tuple``.

Todas as linhas ``existing_same`` -> 409 ``nothing_to_import``, sem recibo.
O recibo v2 cobre só as contas criadas (contagem, total e referências).

Este módulo NÃO autentica, NÃO autoriza, NÃO acessa banco, rede, disco ou
logs e NÃO implementa as garantias de transação, lock e unicidade: ele as
exige do adaptador (``import_confirmation_repository_v2``) e as exercita
contra fakes. O parser é injetado pelo chamador. Erros carregam apenas
códigos fixos, números de linha e ids de conta; nunca a referência, o
cliente ou outros valores do arquivo (podem ser dados pessoais).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable
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


IDENTITY_VERSION = 2
INITIAL_STATUS = "aberto"
FILE_FORMATS = ("csv", "xlsx")
REFERENCE_FIELD = "referencia"
CANONICAL_FIELDS = (
    "cliente",
    "valor",
    "vencimento",
    "status",
    "email",
    "whatsapp",
    REFERENCE_FIELD,
)
IDEMPOTENCY_KEY_LENGTH = 36
MAX_REFERENCE_LENGTH = 128
MAX_RECORD_COUNT = 5000
MAX_EXISTING_IDENTITIES = 100_000

AUDIT_ACCOUNT_CREATED = "account.created_by_import"
AUDIT_IMPORT_CONFIRMED = "import.confirmed"
AUDIT_TITLE_REFERENCE_MATCHED = "title_reference_matched"
AUDIT_ACTIONS = (
    AUDIT_ACCOUNT_CREATED,
    AUDIT_IMPORT_CONFIRMED,
    AUDIT_TITLE_REFERENCE_MATCHED,
)

WARNING_COINCIDENT_REFERENCED_TUPLE = "coincident_referenced_tuple"

# Código -> status HTTP sugerido à rota. 500 = falha interna sem detalhe.
ERRORS = {
    # plano
    "invalid_parser": (500, "Configuração interna de importação inválida."),
    "invalid_format": (422, "Formato de arquivo inválido. Use csv ou xlsx."),
    "invalid_data": (422, "O conteúdo do arquivo deve ser enviado como bytes."),
    "empty_file": (422, "O arquivo está vazio."),
    "invalid_expected_hash": (
        422, "Hash esperado da pré-visualização inválido."
    ),
    "invalid_idempotency_key": (422, "Idempotency-Key ausente ou inválida."),
    "invalid_origin": (422, "Origem dos títulos ausente ou inválida."),
    "invalid_preview": (500, "Resultado do parser com formato inesperado."),
    "file_mismatch": (409, "O arquivo não corresponde à pré-visualização."),
    "content_mismatch": (409, "O conteúdo não corresponde à pré-visualização."),
    "invalid_rows": (
        422, "O arquivo possui linhas inválidas; nada será importado."
    ),
    "status_not_open": (422, "Toda conta importada deve ter status aberto."),
    "duplicate_reference": (
        422, "Há referências repetidas no arquivo; nada será importado."
    ),
    # execução
    "invalid_actor": (500, "Ator autenticado ausente ou inválido."),
    "invalid_plan": (500, "Plano de confirmação inválido."),
    "invalid_repository": (500, "Configuração interna de persistência inválida."),
    "idempotency_conflict": (
        409,
        "Idempotency-Key já usada com outro conteúdo ou versão; "
        "nada será importado.",
    ),
    "content_already_imported": (
        409, "Este conteúdo já foi importado; nada será importado."
    ),
    "reference_conflict": (
        409,
        "Referência já existe com outro cliente, valor ou vencimento; "
        "nada será importado.",
    ),
    "legacy_match_review": (
        409,
        "Título sem referência coincide com linha do arquivo; atribua a "
        "referência ao título existente ou revise o arquivo. Nada será "
        "importado.",
    ),
    "nothing_to_import": (
        409, "Todas as linhas já existem com a mesma referência."
    ),
}

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_TITLE_SOURCE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Mesmas categorias rejeitadas por ``normalize_title_reference`` (TI1).
_REJECTED_REFERENCE_CATEGORIES = frozenset(
    {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
)
_CENT = Decimal("0.01").as_tuple().exponent
_UNIT_METHODS = (
    "lock_account_writers",
    "find_receipt",
    "content_already_confirmed",
    "find_title_references",
    "find_account_tuples",
    "create_account",
    "create_title_reference",
    "append_audit_event",
    "insert_receipt",
)

_MISSING = object()

AccountReference = Union[int, str]
IdentityKey = tuple[str, Decimal, date]


class ImportIdentityV2Error(ValueError):
    """
    Rejeição do plano ou da confirmação inteira. Carrega código fixo,
    status HTTP sugerido e, nos conflitos, números de linha e ids de conta.
    """

    def __init__(
        self,
        code: str,
        *,
        row_numbers: Iterable[int] = (),
        account_ids: Iterable[AccountReference] = (),
    ) -> None:
        if code not in ERRORS:
            raise ValueError("Código de erro de importação v2 desconhecido.")

        self.code = code
        self.status_code, self.message = ERRORS[code]
        self.row_numbers = tuple(sorted(set(row_numbers)))
        self.account_ids = tuple(sorted(set(account_ids), key=_reference_order))

        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"ImportIdentityV2Error(code={self.code!r})"


# ---------------------------------------------------------------- DTOs


@dataclass(frozen=True)
class PlannedTitle:
    """Linha candidata v2. Valores do arquivo ficam fora do repr."""

    row_number: int
    cliente: str = field(repr=False)
    valor: Decimal = field(repr=False)
    vencimento: date = field(repr=False)
    status: str = field(repr=False)
    email: str | None = field(repr=False)
    whatsapp: str | None = field(repr=False)
    title_reference: str = field(repr=False)

    @property
    def identity(self) -> IdentityKey:
        return identity_key(self.cliente, self.valor, self.vencimento)


@dataclass(frozen=True)
class ImportPlanV2:
    """Intenção v2 sem efeitos. Repr expõe só formato e contagem."""

    file_kind: str
    record_count: int
    identity_version: int
    title_source: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    file_sha256: str = field(repr=False)
    content_sha256: str = field(repr=False)
    total: Decimal = field(repr=False)
    records: tuple[PlannedTitle, ...] = field(repr=False)


@dataclass(frozen=True)
class AccountDraftV2:
    """Conta a criar, sempre em aberto. Repr sem dados pessoais."""

    row_number: int
    cliente: str = field(repr=False)
    valor: Decimal = field(repr=False)
    vencimento: date = field(repr=False)
    status: str = field(repr=False)
    email: str | None = field(repr=False)
    whatsapp: str | None = field(repr=False)
    title_reference: str = field(repr=False)


@dataclass(frozen=True)
class TitleReferenceAssignment:
    """Linha lateral ``assigned_via='import_v2'``. Repr sem a referência."""

    account_reference: AccountReference
    title_source: str = field(repr=False)
    title_reference: str = field(repr=False)
    actor_reference: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    content_sha256: str = field(repr=False)


@dataclass(frozen=True)
class ImportReceiptV2:
    """Recibo v2: só contas criadas. Repr expõe só formato e contagem."""

    file_kind: str
    record_count: int
    identity_version: int
    actor_reference: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    content_sha256: str = field(repr=False)
    total: Decimal = field(repr=False)
    account_references: tuple[AccountReference, ...] = field(repr=False)


@dataclass(frozen=True)
class ImportAuditEventV2:
    """Evento de auditoria sem dados pessoais do arquivo."""

    action: str
    actor_reference: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    content_sha256: str = field(repr=False)
    account_reference: AccountReference | None = field(repr=False)
    row_number: int | None = field(repr=False)
    record_count: int | None = field(repr=False)


@dataclass(frozen=True)
class MatchedRow:
    row_number: int
    account_reference: AccountReference


@dataclass(frozen=True)
class RowWarning:
    row_number: int
    code: str
    account_references: tuple[AccountReference, ...]


@dataclass(frozen=True)
class ImportResultV2:
    """
    ``replayed`` indica recibo original devolvido. ``matched`` e
    ``warnings`` existem só na criação (o recibo não os guarda).
    """

    replayed: bool
    record_count: int
    receipt: Any = field(repr=False)
    matched: tuple[MatchedRow, ...] = field(default=(), repr=False)
    warnings: tuple[RowWarning, ...] = field(default=(), repr=False)


class ImportIdentityV2UnitOfWork(Protocol):
    def lock_account_writers(self) -> None: ...

    def find_receipt(self, actor_reference: str, idempotency_key: str) -> Any: ...

    def content_already_confirmed(
        self, content_sha256: str, identity_version: int
    ) -> bool: ...

    def find_title_references(
        self, title_source: str, references: frozenset[str]
    ) -> Iterable[tuple[str, AccountReference, str, Decimal, date]]: ...

    def find_account_tuples(
        self, candidates: frozenset[tuple[Decimal, date]]
    ) -> Iterable[tuple[AccountReference, str, Decimal, date, bool]]: ...

    def create_account(self, draft: AccountDraftV2) -> AccountReference: ...

    def create_title_reference(
        self, assignment: TitleReferenceAssignment
    ) -> None: ...

    def append_audit_event(self, event: ImportAuditEventV2) -> None: ...

    def insert_receipt(self, receipt: ImportReceiptV2) -> None: ...


class ImportIdentityV2Repository(Protocol):
    def transaction(
        self,
    ) -> AbstractContextManager[ImportIdentityV2UnitOfWork]: ...


def identity_key(cliente: str, valor: Decimal, vencimento: date) -> IdentityKey:
    """Mesma tupla normalizada da identidade v1 (aviso/conflito legado)."""

    return (" ".join(cliente.split()).casefold(), valor, vencimento)


# ---------------------------------------------------------------- plano


def plan_import_confirmation_v2(
    parse_import_preview: Callable[..., Any],
    data: bytes,
    file_format: str,
    *,
    origem: str,
    expected_file_sha256: str,
    expected_content_sha256: str,
    idempotency_key: str,
) -> ImportPlanV2:
    """
    Reparse dos bytes com as mesmas opções da rota de pré-visualização v2
    (``preview.<formato>``, ``identity_version=2``, ``origem``) e montagem
    do plano tudo-ou-nada. Não consulta banco.
    """

    if not callable(parse_import_preview):
        raise ImportIdentityV2Error("invalid_parser")

    if type(file_format) is not str or file_format not in FILE_FORMATS:
        raise ImportIdentityV2Error("invalid_format")

    if type(data) is bytearray:
        data = bytes(data)
    elif type(data) is not bytes:
        raise ImportIdentityV2Error("invalid_data")

    if not data:
        raise ImportIdentityV2Error("empty_file")

    _require_sha256(expected_file_sha256, "invalid_expected_hash")
    _require_sha256(expected_content_sha256, "invalid_expected_hash")
    _require_idempotency_key(idempotency_key)
    _require_title_source(origem, "invalid_origin")

    file_sha256 = hashlib.sha256(data).hexdigest()

    if file_sha256 != expected_file_sha256:
        raise ImportIdentityV2Error("file_mismatch")

    preview = parse_import_preview(
        f"preview.{file_format}",
        data,
        identity_version=IDENTITY_VERSION,
        origem=origem,
    )

    if (
        _plan_attr(preview, "identity_version") != IDENTITY_VERSION
        or _plan_attr(preview, "title_source") != origem
        or _plan_attr(preview, "file_kind") != file_format
        or _plan_attr(preview, "file_sha256") != file_sha256
    ):
        raise ImportIdentityV2Error("invalid_preview")

    content_sha256 = _plan_attr(preview, "content_sha256")

    if type(content_sha256) is not str or not _SHA256_HEX.fullmatch(
        content_sha256
    ):
        raise ImportIdentityV2Error("invalid_preview")

    rows = _plan_attr(preview, "rows")

    if type(rows) is not tuple or not rows:
        raise ImportIdentityV2Error("invalid_preview")

    if content_sha256 != expected_content_sha256:
        raise ImportIdentityV2Error("content_mismatch")

    for row in rows:
        issues = _plan_attr(row, "issues")

        if type(issues) is not tuple:
            raise ImportIdentityV2Error("invalid_preview")

        if issues:
            raise ImportIdentityV2Error("invalid_rows")

    records = tuple(_planned_title(row) for row in rows)
    numbers = [record.row_number for record in records]

    if any(left >= right for left, right in zip(numbers, numbers[1:])):
        raise ImportIdentityV2Error("invalid_preview")

    references = [record.title_reference for record in records]

    # Defesa: o parser já marca ``duplicate_reference`` como problema.
    if len(set(references)) != len(references):
        raise ImportIdentityV2Error("duplicate_reference")

    total = sum((record.valor for record in records), Decimal("0.00"))

    for name, expected in (
        ("total_rows", len(records)),
        ("valid_rows", len(records)),
        ("invalid_rows", 0),
    ):
        value = _plan_attr(preview, name)

        if type(value) is not int or value != expected:
            raise ImportIdentityV2Error("invalid_preview")

    valid_total = _plan_attr(preview, "valid_total")

    if type(valid_total) is not Decimal or valid_total != total:
        raise ImportIdentityV2Error("invalid_preview")

    return ImportPlanV2(
        file_kind=file_format,
        record_count=len(records),
        identity_version=IDENTITY_VERSION,
        title_source=origem,
        idempotency_key=idempotency_key,
        file_sha256=file_sha256,
        content_sha256=content_sha256,
        total=total,
        records=records,
    )


def _plan_attr(source: Any, name: str) -> Any:
    value = getattr(source, name, _MISSING)

    if value is _MISSING:
        raise ImportIdentityV2Error("invalid_preview")

    return value


def _planned_title(row: Any) -> PlannedTitle:
    row_number = _plan_attr(row, "row_number")
    values = _plan_attr(row, "values")

    if (
        type(row_number) is not int
        or row_number < 2
        or type(values) is not dict
        or set(values) != set(CANONICAL_FIELDS)
    ):
        raise ImportIdentityV2Error("invalid_preview")

    try:
        record = _valid_title(row_number, values)
    except _InvalidTitle as error:
        raise ImportIdentityV2Error(error.code) from None

    return PlannedTitle(**record)


# ---------------------------------------------------------------- execução


@dataclass(frozen=True, repr=False)
class _Snapshot:
    file_kind: str
    title_source: str
    idempotency_key: str
    content_sha256: str
    total: Decimal
    drafts: tuple[AccountDraftV2, ...]


def execute_import_confirmation_v2(
    repository: ImportIdentityV2Repository,
    plan: Any,
    *,
    actor_reference: str,
) -> ImportResultV2:
    """
    Executa o plano v2 numa única transação do adaptador, tudo ou nada.
    O resultado só é devolvido depois que a transação saiu sem exceção.
    """

    actor = _require_actor(actor_reference)
    snapshot = _snapshot_plan(plan)

    transaction = getattr(repository, "transaction", None)

    if not callable(transaction):
        raise ImportIdentityV2Error("invalid_repository")

    result = None

    with transaction() as unit:
        if any(not callable(getattr(unit, n, None)) for n in _UNIT_METHODS):
            raise ImportIdentityV2Error("invalid_repository")

        unit.lock_account_writers()
        stored = unit.find_receipt(actor, snapshot.idempotency_key)

        if stored is None:
            result = _create(unit, actor, snapshot)
        else:
            result = _replay(stored, actor, snapshot)

    if result is None:
        # Context manager que suprimiu exceção: falha fechada.
        raise ImportIdentityV2Error("invalid_repository")

    return result


def _replay(stored: Any, actor: str, snapshot: _Snapshot) -> ImportResultV2:
    """
    Recibo v1 com a mesma chave, ou v2 com outro digest ->
    ``idempotency_conflict``. Recibo v2 idêntico -> devolvido sem escrita.
    """

    actor_reference = getattr(stored, "actor_reference", _MISSING)
    idempotency_key = getattr(stored, "idempotency_key", _MISSING)

    if actor_reference != actor or idempotency_key != snapshot.idempotency_key:
        raise ImportIdentityV2Error("invalid_repository")

    version = getattr(stored, "identity_version", _MISSING)
    content = getattr(stored, "content_sha256", _MISSING)

    if type(version) is not int:
        raise ImportIdentityV2Error("invalid_repository")

    if version != IDENTITY_VERSION or content != snapshot.content_sha256:
        raise ImportIdentityV2Error("idempotency_conflict")

    record_count = getattr(stored, "record_count", _MISSING)
    total = getattr(stored, "total", _MISSING)
    references = getattr(stored, "account_references", _MISSING)

    # Recibo v2 cobre só as criadas: 1 <= contagem <= linhas do plano.
    if (
        type(record_count) is not int
        or not 1 <= record_count <= len(snapshot.drafts)
        or getattr(stored, "file_kind", None) not in FILE_FORMATS
        or not _is_money(total)
        or not Decimal("0.00") < total <= snapshot.total
        or type(references) is not tuple
        or len(references) != record_count
    ):
        raise ImportIdentityV2Error("invalid_repository")

    checked = tuple(_require_account_reference(ref) for ref in references)

    if len(set(checked)) != len(checked):
        raise ImportIdentityV2Error("invalid_repository")

    return ImportResultV2(replayed=True, record_count=record_count, receipt=stored)


def _create(unit: Any, actor: str, snapshot: _Snapshot) -> ImportResultV2:
    already = unit.content_already_confirmed(
        snapshot.content_sha256, IDENTITY_VERSION
    )

    if type(already) is not bool:
        raise ImportIdentityV2Error("invalid_repository")

    if already:
        raise ImportIdentityV2Error("content_already_imported")

    drafts = snapshot.drafts
    owners = _title_reference_owners(
        unit.find_title_references(
            snapshot.title_source,
            frozenset(draft.title_reference for draft in drafts),
        ),
        {draft.title_reference for draft in drafts},
    )

    matched: list[MatchedRow] = []
    new: list[AccountDraftV2] = []
    conflict_rows: list[int] = []
    conflict_accounts: list[AccountReference] = []

    for draft in drafts:
        owner = owners.get(draft.title_reference)

        if owner is None:
            new.append(draft)
        elif owner[1] == _draft_identity(draft):
            matched.append(MatchedRow(draft.row_number, owner[0]))
        else:
            conflict_rows.append(draft.row_number)
            conflict_accounts.append(owner[0])

    if conflict_rows:
        raise ImportIdentityV2Error(
            "reference_conflict",
            row_numbers=conflict_rows,
            account_ids=conflict_accounts,
        )

    coincident = _coincident_accounts(
        unit.find_account_tuples(
            frozenset((draft.valor, draft.vencimento) for draft in new)
        ) if new else ()
    )

    warnings: list[RowWarning] = []
    legacy_rows: list[int] = []
    legacy_accounts: list[AccountReference] = []

    for draft in new:
        unreferenced, referenced = coincident.get(_draft_identity(draft), ((), ()))

        if unreferenced:
            legacy_rows.append(draft.row_number)
            legacy_accounts.extend(unreferenced)
        elif referenced:
            warnings.append(
                RowWarning(
                    draft.row_number,
                    WARNING_COINCIDENT_REFERENCED_TUPLE,
                    tuple(sorted(referenced, key=_reference_order)),
                )
            )

    if legacy_rows:
        raise ImportIdentityV2Error(
            "legacy_match_review",
            row_numbers=legacy_rows,
            account_ids=legacy_accounts,
        )

    if not new:
        raise ImportIdentityV2Error("nothing_to_import")

    references = tuple(
        _require_account_reference(unit.create_account(draft)) for draft in new
    )

    if len(set(references)) != len(references):
        raise ImportIdentityV2Error("invalid_repository")

    for draft, reference in zip(new, references):
        unit.create_title_reference(
            TitleReferenceAssignment(
                account_reference=reference,
                title_source=snapshot.title_source,
                title_reference=draft.title_reference,
                actor_reference=actor,
                idempotency_key=snapshot.idempotency_key,
                content_sha256=snapshot.content_sha256,
            )
        )

    def event(action, reference=None, row_number=None, record_count=None):
        return ImportAuditEventV2(
            action=action,
            actor_reference=actor,
            idempotency_key=snapshot.idempotency_key,
            content_sha256=snapshot.content_sha256,
            account_reference=reference,
            row_number=row_number,
            record_count=record_count,
        )

    for draft, reference in zip(new, references):
        unit.append_audit_event(
            event(AUDIT_ACCOUNT_CREATED, reference, draft.row_number)
        )

    for row in matched:
        unit.append_audit_event(
            event(AUDIT_TITLE_REFERENCE_MATCHED, row.account_reference, row.row_number)
        )

    receipt = ImportReceiptV2(
        file_kind=snapshot.file_kind,
        record_count=len(new),
        identity_version=IDENTITY_VERSION,
        actor_reference=actor,
        idempotency_key=snapshot.idempotency_key,
        content_sha256=snapshot.content_sha256,
        total=sum((draft.valor for draft in new), Decimal("0.00")),
        account_references=references,
    )

    unit.insert_receipt(receipt)
    unit.append_audit_event(event(AUDIT_IMPORT_CONFIRMED, record_count=len(new)))

    return ImportResultV2(
        replayed=False,
        record_count=len(new),
        receipt=receipt,
        matched=tuple(matched),
        warnings=tuple(warnings),
    )


def _title_reference_owners(
    values: Any, requested: set[str]
) -> dict[str, tuple[AccountReference, IdentityKey]]:
    owners: dict[str, tuple[AccountReference, IdentityKey]] = {}

    for item in _bounded_items(values, 5):
        reference, account, cliente, valor, vencimento = item

        if (
            type(reference) is not str
            or reference not in requested
            or reference in owners
            or type(cliente) is not str
            or not _is_money(valor)
            or not _is_plain_date(vencimento)
        ):
            raise ImportIdentityV2Error("invalid_repository")

        owners[reference] = (
            _require_account_reference(account),
            identity_key(cliente, valor, vencimento),
        )

    return owners


def _coincident_accounts(
    values: Any,
) -> dict[IdentityKey, tuple[tuple[AccountReference, ...], tuple[AccountReference, ...]]]:
    grouped: dict[IdentityKey, tuple[list, list]] = {}

    for item in _bounded_items(values, 5):
        account, cliente, valor, vencimento, referenced = item

        if (
            type(cliente) is not str
            or not _is_money(valor)
            or not _is_plain_date(vencimento)
            or type(referenced) is not bool
        ):
            raise ImportIdentityV2Error("invalid_repository")

        unreferenced, with_reference = grouped.setdefault(
            identity_key(cliente, valor, vencimento), ([], [])
        )
        (with_reference if referenced else unreferenced).append(
            _require_account_reference(account)
        )

    return {
        key: (tuple(unreferenced), tuple(with_reference))
        for key, (unreferenced, with_reference) in grouped.items()
    }


def _bounded_items(values: Any, width: int):
    if isinstance(values, (str, bytes, bytearray, dict)):
        raise ImportIdentityV2Error("invalid_repository")

    try:
        iterator = iter(values)
    except TypeError:
        iterator = None

    if iterator is None:
        raise ImportIdentityV2Error("invalid_repository")

    for count, item in enumerate(iterator, start=1):
        if count > MAX_EXISTING_IDENTITIES:
            raise ImportIdentityV2Error("invalid_repository")

        if type(item) is not tuple or len(item) != width:
            raise ImportIdentityV2Error("invalid_repository")

        yield item


# ---------------------------------------------------------------- entradas


class _InvalidTitle(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _valid_title(row_number: int, values: Any) -> dict[str, Any]:
    """
    Campos comuns a plano e snapshot. Levanta ``_InvalidTitle`` com o
    código a usar no plano (``invalid_preview`` ou ``status_not_open``).
    """

    cliente = values["cliente"]
    valor = values["valor"]
    vencimento = values["vencimento"]
    status = values["status"]
    reference = values[REFERENCE_FIELD]

    if (
        type(cliente) is not str
        or not cliente.strip()
        or not _is_money(valor)
        or valor <= 0
        or not _is_plain_date(vencimento)
        or type(status) is not str
        or type(reference) is not str
        or not 1 <= len(reference) <= MAX_REFERENCE_LENGTH
        or reference != reference.strip()
        or reference != unicodedata.normalize("NFC", reference)
        or any(
            unicodedata.category(char) in _REJECTED_REFERENCE_CATEGORIES
            for char in reference
        )
    ):
        raise _InvalidTitle("invalid_preview")

    for name in ("email", "whatsapp"):
        if values[name] is not None and type(values[name]) is not str:
            raise _InvalidTitle("invalid_preview")

    if status != INITIAL_STATUS:
        raise _InvalidTitle("status_not_open")

    return {
        "row_number": row_number,
        "cliente": cliente,
        "valor": valor,
        "vencimento": vencimento,
        "status": status,
        "email": values["email"],
        "whatsapp": values["whatsapp"],
        "title_reference": reference,
    }


def _snapshot_plan(plan: Any) -> _Snapshot:
    """Copia e revalida o plano por duck typing, falhando fechado."""

    def attr(source, name):
        value = getattr(source, name, _MISSING)

        if value is _MISSING:
            raise ImportIdentityV2Error("invalid_plan")

        return value

    file_kind = attr(plan, "file_kind")
    version = attr(plan, "identity_version")
    title_source = attr(plan, "title_source")
    idempotency_key = attr(plan, "idempotency_key")
    content_sha256 = attr(plan, "content_sha256")
    record_count = attr(plan, "record_count")
    total = attr(plan, "total")
    records = attr(plan, "records")

    if (
        type(file_kind) is not str
        or file_kind not in FILE_FORMATS
        or type(version) is not int
        or version != IDENTITY_VERSION
        or type(title_source) is not str
        or not _TITLE_SOURCE.fullmatch(title_source)
        or type(idempotency_key) is not str
        or not _UUID4.fullmatch(idempotency_key)
        or type(content_sha256) is not str
        or not _SHA256_HEX.fullmatch(content_sha256)
        or type(records) is not tuple
        or not records
        or len(records) > MAX_RECORD_COUNT
        or type(record_count) is not int
        or record_count != len(records)
    ):
        raise ImportIdentityV2Error("invalid_plan")

    drafts = []

    for record in records:
        values = {
            "cliente": attr(record, "cliente"),
            "valor": attr(record, "valor"),
            "vencimento": attr(record, "vencimento"),
            "status": attr(record, "status"),
            "email": attr(record, "email"),
            "whatsapp": attr(record, "whatsapp"),
            REFERENCE_FIELD: attr(record, "title_reference"),
        }
        row_number = attr(record, "row_number")

        if type(row_number) is not int or row_number < 2:
            raise ImportIdentityV2Error("invalid_plan")

        try:
            drafts.append(AccountDraftV2(**_valid_title(row_number, values)))
        except _InvalidTitle:
            raise ImportIdentityV2Error("invalid_plan") from None

    numbers = [draft.row_number for draft in drafts]
    references = {draft.title_reference for draft in drafts}

    if (
        any(left >= right for left, right in zip(numbers, numbers[1:]))
        or len(references) != len(drafts)
        or not _is_money(total)
        or total != sum((draft.valor for draft in drafts), Decimal("0.00"))
    ):
        raise ImportIdentityV2Error("invalid_plan")

    return _Snapshot(
        file_kind=file_kind,
        title_source=title_source,
        idempotency_key=idempotency_key,
        content_sha256=content_sha256,
        total=total,
        drafts=tuple(drafts),
    )


def _require_sha256(value: Any, code: str) -> None:
    if type(value) is not str or not _SHA256_HEX.fullmatch(value):
        raise ImportIdentityV2Error(code)


def _require_idempotency_key(value: Any) -> None:
    if (
        type(value) is not str
        or len(value) != IDEMPOTENCY_KEY_LENGTH
        or not _UUID4.fullmatch(value)
    ):
        raise ImportIdentityV2Error("invalid_idempotency_key")


def _require_title_source(value: Any, code: str) -> None:
    if type(value) is not str or not _TITLE_SOURCE.fullmatch(value):
        raise ImportIdentityV2Error(code)


def _require_actor(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_REFERENCE_LENGTH
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ImportIdentityV2Error("invalid_actor")

    return value


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

    raise ImportIdentityV2Error("invalid_repository")


def _reference_order(value: Any) -> tuple[int, Any]:
    # Ordem estável entre ids inteiros e textuais.
    return (0, value) if type(value) is int else (1, f"{value}")


def _draft_identity(draft: AccountDraftV2) -> IdentityKey:
    return identity_key(draft.cliente, draft.valor, draft.vencimento)


def _is_money(value: Any) -> bool:
    return (
        type(value) is Decimal
        and value.is_finite()
        and value.as_tuple().exponent == _CENT
    )


def _is_plain_date(value: Any) -> bool:
    return type(value) is date and not isinstance(value, datetime)
