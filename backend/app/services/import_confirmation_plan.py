"""
Planejamento puro da confirmação de importação CSV/XLSX
(MVP-FIN-IMPORT-01 / TASK-024).

Refaz o parse dos mesmos bytes com o parser injetado, confere os hashes
da pré-visualização e devolve uma intenção imutável de criação de contas.
Não acessa banco, rede, disco ou logs; depende apenas da biblioteca
padrão e não importa o parser (recebido explicitamente pelo chamador).

O plano NÃO grava nada, NÃO concede permissão e NÃO assegura
idempotência durável nem serialização entre escritores: a validação da
Idempotency-Key aqui é apenas de formato. Recibo persistente, transação
atômica e proteção concorrente comum a todos os caminhos de criação de
contas são outro contrato, ainda não implementado.

Erros carregam apenas códigos e mensagens fixos, sem ecoar bytes, hashes,
chaves ou valores do arquivo. Rejeições do próprio parser propagam como
a exceção dele (código fixo, sem conteúdo).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any


FILE_FORMATS = ("csv", "xlsx")
INITIAL_STATUS = "aberto"
CANONICAL_FIELDS = (
    "cliente",
    "valor",
    "vencimento",
    "status",
    "email",
    "whatsapp",
)
IDEMPOTENCY_KEY_LENGTH = 36
MAX_EXISTING_IDENTITIES = 100_000

CONFIRMATION_ERROR_MESSAGES = {
    "invalid_parser": "Configuração interna de importação inválida.",
    "invalid_format": "Formato de arquivo inválido. Use csv ou xlsx.",
    "invalid_data": "O conteúdo do arquivo deve ser enviado como bytes.",
    "empty_file": "O arquivo está vazio.",
    "invalid_expected_hash": "Hash esperado da pré-visualização inválido.",
    "invalid_idempotency_key": "Idempotency-Key ausente ou inválida.",
    "invalid_preview": "Resultado do parser com formato inesperado.",
    "file_mismatch": "O arquivo não corresponde à pré-visualização.",
    "content_mismatch": "O conteúdo não corresponde à pré-visualização.",
    "invalid_rows": "O arquivo possui linhas inválidas; nada será importado.",
    "status_not_open": "Toda conta importada deve ter status aberto.",
    "duplicate_identity": "Há contas repetidas no arquivo.",
    "invalid_existing_identity": "Identidade existente informada é inválida.",
    "existing_identity_conflict": (
        "O arquivo contém conta já existente; nada será importado."
    ),
}

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
# UUIDv4 canônico, apenas minúsculas, para que a mesma chave tenha uma
# única representação textual.
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CENT = Decimal("0.01").as_tuple().exponent

_MISSING = object()


class ImportConfirmationError(ValueError):
    """
    Rejeição do plano inteiro. Carrega apenas um código fixo.
    """

    def __init__(self, code: str) -> None:
        if code not in CONFIRMATION_ERROR_MESSAGES:
            raise ValueError("Código de erro de confirmação desconhecido.")

        self.code = code
        self.message = CONFIRMATION_ERROR_MESSAGES[code]

        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"ImportConfirmationError(code={self.code!r})"


IdentityKey = tuple[str, Decimal, date]


@dataclass(frozen=True)
class PlannedAccount:
    """
    Conta candidata, normalizada pelo parser. Campos com dados pessoais
    ou financeiros ficam fora do repr.
    """

    row_number: int
    cliente: str = field(repr=False)
    valor: Decimal = field(repr=False)
    vencimento: date = field(repr=False)
    status: str = field(repr=False)
    email: str | None = field(repr=False)
    whatsapp: str | None = field(repr=False)

    @property
    def identity(self) -> IdentityKey:
        return identity_key(self.cliente, self.valor, self.vencimento)


@dataclass(frozen=True)
class ImportConfirmationPlan:
    """
    Intenção determinística de criação, sem efeitos. O repr expõe somente
    formato e contagem; chave, hashes, total e registros ficam ocultos.
    """

    file_kind: str
    record_count: int
    idempotency_key: str = field(repr=False)
    file_sha256: str = field(repr=False)
    content_sha256: str = field(repr=False)
    total: Decimal = field(repr=False)
    records: tuple[PlannedAccount, ...] = field(repr=False)


def identity_key(cliente: str, valor: Decimal, vencimento: date) -> IdentityKey:
    """
    Identidade v1 de uma conta, idêntica à chave de duplicidade do parser
    (``_validate_rows``): colapso de espaços Unicode + casefold, valor
    Decimal exato e vencimento date. Não equivale a ``lower()`` do SQL.
    """

    return (" ".join(cliente.split()).casefold(), valor, vencimento)


def plan_import_confirmation(
    parse_import_preview: Callable[..., Any],
    data: bytes,
    file_format: str,
    *,
    expected_file_sha256: str,
    expected_content_sha256: str,
    idempotency_key: str,
    existing_identities: Iterable[tuple[str, Decimal, date]] = (),
) -> ImportConfirmationPlan:
    """
    Reparse dos bytes com as mesmas opções default da rota HTTP de
    pré-visualização (nome ``preview.<formato>``, sem mapeamento manual e
    sem delimitador) e montagem do plano tudo-ou-nada.

    ``existing_identities`` recebe triplos ``(cliente, valor, vencimento)``
    já conhecidos pelo chamador; qualquer colisão bloqueia o plano inteiro.
    Este módulo não consulta banco para obtê-los.
    """

    if not callable(parse_import_preview):
        raise ImportConfirmationError("invalid_parser")

    if type(file_format) is not str or file_format not in FILE_FORMATS:
        raise ImportConfirmationError("invalid_format")

    if type(data) is bytearray:
        data = bytes(data)
    elif type(data) is not bytes:
        raise ImportConfirmationError("invalid_data")

    if not data:
        raise ImportConfirmationError("empty_file")

    _require_sha256(expected_file_sha256)
    _require_sha256(expected_content_sha256)
    _require_idempotency_key(idempotency_key)

    existing = _existing_identity_set(existing_identities)
    file_sha256 = hashlib.sha256(data).hexdigest()

    # Comparação antecipada: arquivo trocado não chega ao parser.
    if file_sha256 != expected_file_sha256:
        raise ImportConfirmationError("file_mismatch")

    preview = parse_import_preview(f"preview.{file_format}", data)

    rows = _check_preview(preview, file_format, file_sha256)

    if _attr(preview, "content_sha256") != expected_content_sha256:
        raise ImportConfirmationError("content_mismatch")

    if any(_row_issues(row) for row in rows):
        raise ImportConfirmationError("invalid_rows")

    records = tuple(_planned_account(row) for row in rows)
    _check_row_order(records)

    identities = [record.identity for record in records]

    if len(set(identities)) != len(identities):
        raise ImportConfirmationError("duplicate_identity")

    if existing.intersection(identities):
        raise ImportConfirmationError("existing_identity_conflict")

    total = sum((record.valor for record in records), Decimal("0.00"))
    _check_totals(preview, records, total)

    return ImportConfirmationPlan(
        file_kind=file_format,
        record_count=len(records),
        idempotency_key=idempotency_key,
        file_sha256=file_sha256,
        content_sha256=expected_content_sha256,
        total=total,
        records=records,
    )


# ---------------------------------------------------------------- entradas


def _require_sha256(value: Any) -> None:
    if type(value) is not str or not _SHA256_HEX.fullmatch(value):
        raise ImportConfirmationError("invalid_expected_hash")


def _require_idempotency_key(value: Any) -> None:
    if (
        type(value) is not str
        or len(value) != IDEMPOTENCY_KEY_LENGTH
        or not _UUID4.fullmatch(value)
    ):
        raise ImportConfirmationError("invalid_idempotency_key")


def _existing_identity_set(values: Any) -> frozenset[IdentityKey]:
    if isinstance(values, (str, bytes, bytearray, dict)):
        raise ImportConfirmationError("invalid_existing_identity")

    try:
        iterator = iter(values)
    except TypeError as error:
        raise ImportConfirmationError("invalid_existing_identity") from error

    identities = set()

    for count, item in enumerate(iterator, start=1):
        if count > MAX_EXISTING_IDENTITIES:
            raise ImportConfirmationError("invalid_existing_identity")

        if type(item) is not tuple or len(item) != 3:
            raise ImportConfirmationError("invalid_existing_identity")

        cliente, valor, vencimento = item

        if (
            type(cliente) is not str
            or not cliente.strip()
            or not _is_money(valor)
            or not _is_plain_date(vencimento)
        ):
            raise ImportConfirmationError("invalid_existing_identity")

        identities.add(identity_key(cliente, valor, vencimento))

    return frozenset(identities)


# ---------------------------------------------------------------- parser


def _attr(source: Any, name: str) -> Any:
    value = getattr(source, name, _MISSING)

    if value is _MISSING:
        raise ImportConfirmationError("invalid_preview")

    return value


def _check_preview(preview: Any, file_format: str, file_sha256: str) -> tuple:
    if _attr(preview, "file_kind") != file_format:
        raise ImportConfirmationError("invalid_preview")

    if _attr(preview, "file_sha256") != file_sha256:
        raise ImportConfirmationError("invalid_preview")

    content_sha256 = _attr(preview, "content_sha256")

    if type(content_sha256) is not str or not _SHA256_HEX.fullmatch(
        content_sha256
    ):
        raise ImportConfirmationError("invalid_preview")

    rows = _attr(preview, "rows")

    if type(rows) is not tuple or not rows:
        raise ImportConfirmationError("invalid_preview")

    return rows


def _row_issues(row: Any) -> tuple:
    issues = _attr(row, "issues")

    if type(issues) is not tuple:
        raise ImportConfirmationError("invalid_preview")

    return issues


def _planned_account(row: Any) -> PlannedAccount:
    row_number = _attr(row, "row_number")

    if type(row_number) is not int or row_number < 2:
        raise ImportConfirmationError("invalid_preview")

    values = _attr(row, "values")

    if type(values) is not dict or set(values) != set(CANONICAL_FIELDS):
        raise ImportConfirmationError("invalid_preview")

    cliente = values["cliente"]
    valor = values["valor"]
    vencimento = values["vencimento"]
    status = values["status"]

    if type(cliente) is not str or not cliente.strip():
        raise ImportConfirmationError("invalid_preview")

    if not _is_money(valor) or valor <= 0:
        raise ImportConfirmationError("invalid_preview")

    if not _is_plain_date(vencimento):
        raise ImportConfirmationError("invalid_preview")

    if type(status) is not str:
        raise ImportConfirmationError("invalid_preview")

    if status != INITIAL_STATUS:
        raise ImportConfirmationError("status_not_open")

    for name in ("email", "whatsapp"):
        if values[name] is not None and type(values[name]) is not str:
            raise ImportConfirmationError("invalid_preview")

    return PlannedAccount(
        row_number=row_number,
        cliente=cliente,
        valor=valor,
        vencimento=vencimento,
        status=status,
        email=values["email"],
        whatsapp=values["whatsapp"],
    )


def _check_row_order(records: tuple[PlannedAccount, ...]) -> None:
    numbers = [record.row_number for record in records]

    if any(left >= right for left, right in zip(numbers, numbers[1:])):
        raise ImportConfirmationError("invalid_preview")


def _check_totals(
    preview: Any,
    records: tuple[PlannedAccount, ...],
    total: Decimal,
) -> None:
    """
    Confere os agregados do parser contra a contagem própria, falhando
    fechado diante de um parser inconsistente.
    """

    count = len(records)

    for name, expected in (
        ("total_rows", count),
        ("valid_rows", count),
        ("invalid_rows", 0),
    ):
        value = _attr(preview, name)

        if type(value) is not int or value != expected:
            raise ImportConfirmationError("invalid_preview")

    valid_total = _attr(preview, "valid_total")

    if type(valid_total) is not Decimal or valid_total != total:
        raise ImportConfirmationError("invalid_preview")


def _is_money(value: Any) -> bool:
    return (
        type(value) is Decimal
        and value.is_finite()
        and value.as_tuple().exponent == _CENT
    )


def _is_plain_date(value: Any) -> bool:
    # datetime é subclasse de date; o parser devolve apenas date.
    return type(value) is date and not isinstance(value, datetime)
