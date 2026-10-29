"""
Transporte limitado e serialização privada da pré-visualização de
importação (MVP-FIN-IMPORT-01 / TASK-013).

Pré-requisitos reutilizáveis para uma rota futura. Este módulo não é um
endpoint: não registra rota, não aplica autenticação, não acessa banco,
rede, disco ou logs. Depende apenas da biblioteca padrão e recebe o
resultado do parser por duck typing, sem importá-lo.

Erros carregam apenas códigos e mensagens fixos, sem ecoar payload,
valores de cabeçalhos ou representações de objetos recebidos.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable
from datetime import date
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from typing import Protocol


MAX_RETURNED_ROWS = 100

# Cabeçalhos de contrato para a rota futura. Nada aqui os aplica a HTTP.
PREVIEW_RESPONSE_HEADERS = MappingProxyType({"Cache-Control": "no-store"})

CANONICAL_FIELDS = (
    "cliente",
    "valor",
    "vencimento",
    "status",
    "email",
    "whatsapp",
)
REQUIRED_VALUE_FIELDS = ("cliente", "valor", "vencimento", "status")
OPTIONAL_VALUE_FIELDS = ("email", "whatsapp")
FILE_KINDS = ("csv", "xlsx")

TRANSPORT_ERRORS = MappingProxyType({
    "invalid_content_length": (
        400,
        "Cabeçalho Content-Length inválido.",
    ),
    "file_too_large": (
        413,
        "O arquivo excede o tamanho máximo permitido.",
    ),
    "invalid_stream": (
        500,
        "Fluxo de dados da requisição inválido.",
    ),
    "invalid_configuration": (
        500,
        "Configuração interna de importação inválida.",
    ),
    "invalid_preview": (
        500,
        "Pré-visualização com formato inesperado.",
    ),
    "invalid_option": (
        500,
        "Opção de serialização inválida.",
    ),
})

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_ISSUE_CODE = re.compile(r"^[a-z][a-z_]{0,63}$")
_ASCII_DIGITS = re.compile(r"^[0-9]+$")

_MISSING = object()


class ImportTransportError(Exception):
    """
    Rejeição de transporte ou serialização. Carrega apenas código fixo,
    status HTTP sugerido e mensagem fixa.
    """

    def __init__(self, code: str) -> None:
        if code not in TRANSPORT_ERRORS:
            raise ValueError("Código de erro de transporte desconhecido.")

        self.code = code
        self.status_code, self.message = TRANSPORT_ERRORS[code]

        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"ImportTransportError(code={self.code!r})"


class PreviewIssueLike(Protocol):
    field: str | None
    code: str
    message: str


class PreviewRowLike(Protocol):
    row_number: int
    values: dict[str, Any] | None
    issues: tuple[PreviewIssueLike, ...]
    is_valid: bool


class ImportPreviewLike(Protocol):
    file_kind: str
    file_sha256: str
    content_sha256: str
    column_mapping: dict[str, int]
    ignored_columns: tuple[int, ...]
    rows: tuple[PreviewRowLike, ...]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    valid_total: Decimal


# ---------------------------------------------------------------- corpo


async def read_bounded_body(
    chunks: AsyncIterable[bytes],
    content_length: str | None,
    *,
    max_bytes: int,
) -> bytes:
    """
    Lê o corpo em memória contando os bytes recebidos.

    Content-Length declarado acima do limite é rejeitado antes de consumir
    qualquer chunk. Sem Content-Length, ou com valor subdeclarado, a leitura
    para no primeiro chunk que ultrapassa ``max_bytes``, sem consumir o
    próximo. Cancelamento e erros do fluxo propagam sem tratamento.
    """

    if type(max_bytes) is not int or max_bytes <= 0:
        raise ImportTransportError("invalid_configuration")

    if content_length is not None:
        if _declared_length_exceeds(content_length, max_bytes):
            raise ImportTransportError("file_too_large")

    if not hasattr(type(chunks), "__aiter__"):
        raise ImportTransportError("invalid_stream")

    parts = []
    received = 0

    async for chunk in chunks:
        if type(chunk) is bytearray:
            chunk = bytes(chunk)
        elif type(chunk) is not bytes:
            raise ImportTransportError("invalid_stream")

        received += len(chunk)

        if received > max_bytes:
            raise ImportTransportError("file_too_large")

        parts.append(chunk)

    return b"".join(parts)


def _declared_length_exceeds(content_length: Any, max_bytes: int) -> bool:
    """
    Valida o Content-Length (somente dígitos ASCII, sem sinal ou espaços)
    e indica se o valor declarado excede o limite.
    """

    if type(content_length) is not str:
        raise ImportTransportError("invalid_content_length")

    if not _ASCII_DIGITS.fullmatch(content_length):
        raise ImportTransportError("invalid_content_length")

    # Compara pelo número de dígitos antes de converter, evitando int()
    # sobre valores gigantes declarados pelo cliente.
    significant = content_length.lstrip("0")

    if len(significant) > len(_digits(max_bytes)):
        return True

    return int(significant or "0") > max_bytes


def _digits(value: int) -> str:
    return f"{value:d}"


# ---------------------------------------------------------------- resposta


def serialize_preview(
    preview: ImportPreviewLike,
    *,
    include_values: bool = False,
) -> dict[str, Any]:
    """
    Converte a pré-visualização em dict JSON-safe.

    Totais refletem todas as linhas; apenas as primeiras
    ``MAX_RETURNED_ROWS`` linhas são devolvidas. ``values`` só aparece com
    ``include_values=True`` (bool estrito) e apenas para linhas válidas,
    restrito aos campos canônicos. Não lê cabeçalho original nem nome de
    arquivo e não altera o objeto recebido.
    """

    if type(include_values) is not bool:
        raise ImportTransportError("invalid_option")

    file_kind = _attr(preview, "file_kind")

    if type(file_kind) is not str or file_kind not in FILE_KINDS:
        raise ImportTransportError("invalid_preview")

    rows = _attr(preview, "rows")

    if not isinstance(rows, (tuple, list)):
        raise ImportTransportError("invalid_preview")

    total_rows = _count(_attr(preview, "total_rows"))
    valid_rows = _count(_attr(preview, "valid_rows"))
    invalid_rows = _count(_attr(preview, "invalid_rows"))

    if total_rows != len(rows) or valid_rows + invalid_rows != total_rows:
        raise ImportTransportError("invalid_preview")

    returned = [
        _serialize_row(row, include_values)
        for row in rows[:MAX_RETURNED_ROWS]
    ]

    return {
        "file_kind": file_kind,
        "file_sha256": _sha256(_attr(preview, "file_sha256")),
        "content_sha256": _sha256(_attr(preview, "content_sha256")),
        "column_mapping": _mapping(_attr(preview, "column_mapping")),
        "ignored_columns": _indices(_attr(preview, "ignored_columns")),
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "valid_total": _money(_attr(preview, "valid_total")),
        "returned_rows": len(returned),
        "rows_truncated": total_rows > len(returned),
        "rows": returned,
    }


def _serialize_row(row: Any, include_values: bool) -> dict[str, Any]:
    row_number = _attr(row, "row_number")

    if type(row_number) is not int or row_number < 1:
        raise ImportTransportError("invalid_preview")

    valid = _attr(row, "is_valid")

    if type(valid) is not bool:
        raise ImportTransportError("invalid_preview")

    issues = _attr(row, "issues")

    if not isinstance(issues, (tuple, list)) or valid == bool(issues):
        raise ImportTransportError("invalid_preview")

    serialized: dict[str, Any] = {
        "row_number": row_number,
        "valid": valid,
        "issues": [_serialize_issue(issue) for issue in issues],
    }

    if include_values:
        serialized["values"] = (
            _serialize_values(_attr(row, "values")) if valid else None
        )

    return serialized


def _serialize_issue(issue: Any) -> dict[str, Any]:
    field = _attr(issue, "field")
    code = _attr(issue, "code")
    message = _attr(issue, "message")

    if field is not None and (
        type(field) is not str or field not in CANONICAL_FIELDS
    ):
        raise ImportTransportError("invalid_preview")

    if type(code) is not str or not _ISSUE_CODE.fullmatch(code):
        raise ImportTransportError("invalid_preview")

    if type(message) is not str:
        raise ImportTransportError("invalid_preview")

    return {"field": field, "code": code, "message": message}


def _serialize_values(values: Any) -> dict[str, Any]:
    if type(values) is not dict:
        raise ImportTransportError("invalid_preview")

    for field in CANONICAL_FIELDS:
        if field not in values:
            raise ImportTransportError("invalid_preview")

    serialized = {
        "cliente": _text(values["cliente"]),
        "valor": _money(values["valor"]),
        "vencimento": _iso_date(values["vencimento"]),
        "status": _text(values["status"]),
    }

    for field in OPTIONAL_VALUE_FIELDS:
        value = values[field]
        serialized[field] = None if value is None else _text(value)

    return serialized


def _attr(source: Any, name: str) -> Any:
    value = getattr(source, name, _MISSING)

    if value is _MISSING:
        raise ImportTransportError("invalid_preview")

    return value


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ImportTransportError("invalid_preview")

    return value


def _sha256(value: Any) -> str:
    if type(value) is not str or not _SHA256_HEX.fullmatch(value):
        raise ImportTransportError("invalid_preview")

    return value


def _mapping(value: Any) -> dict[str, int]:
    if type(value) is not dict:
        raise ImportTransportError("invalid_preview")

    if not set(REQUIRED_VALUE_FIELDS) <= set(value) <= set(CANONICAL_FIELDS):
        raise ImportTransportError("invalid_preview")

    mapping = {}

    for field in CANONICAL_FIELDS:
        if field in value:
            mapping[field] = _index(value[field])

    return mapping


def _indices(value: Any) -> list[int]:
    if not isinstance(value, (tuple, list)):
        raise ImportTransportError("invalid_preview")

    return [_index(index) for index in value]


def _index(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ImportTransportError("invalid_preview")

    return value


def _money(value: Any) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise ImportTransportError("invalid_preview")

    return f"{value:f}"


def _iso_date(value: Any) -> str:
    # datetime é subclasse de date; o parser devolve apenas date.
    if type(value) is not date or isinstance(value, datetime):
        raise ImportTransportError("invalid_preview")

    return value.isoformat()


def _text(value: Any) -> str:
    if type(value) is not str:
        raise ImportTransportError("invalid_preview")

    return value
