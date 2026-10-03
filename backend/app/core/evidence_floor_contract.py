"""
VALUE-3.4D-1 -- contrato OPERACIONAL estrito do activation floor.

O `Settings` (VALUE-3.3B/3.4B) e deliberadamente tolerante: aceita epoch
numerico ("0", "1790000000"), separador espaco, offset sem dois-pontos,
sem segundos, valores futuros e obsoletos. Este modulo define o contrato
que o PROCEDIMENTO de ativacao exige -- sem alterar o contrato do
Settings. Todo valor aceito aqui e aceito pelo Settings com o mesmo
instante; o inverso e falso (de proposito).

Contrato (todas as condicoes):

* ISO-8601 estrito, ASCII, sem espacos: ``YYYY-MM-DDThh:mm:ss`` +
  fracao opcional (1 a 6 digitos) + ``Z`` ou ``+hh:mm``/``-hh:mm``.
  Digitos sao ``[0-9]`` (nunca ``\\d``, que casa digitos Unicode) e a
  comparacao e ``fullmatch`` (nunca ``$``, que tolera ``\\n`` final).
* Calendario valido (mes 13, 30 de fevereiro, hora 24, segundo 60 e
  offset >= 24h sao rejeitados).
* Timezone obrigatorio (o formato ja o exige; checagem defensiva).
* NAO futuro e idade maxima de 30 minutos, ambos contra o relogio
  AUTORITATIVO do PostgreSQL (``clock_timestamp()``) -- o mesmo relogio
  que carimba ``account_events.occurred_at`` e ``work_items.created_at``
  (``now()``) -- nunca contra o relogio do host. Frescor + "nao futuro"
  ja impedem epoch/backfill acidental; por isso NAO ha faixa de anos.

A saida canonica e UTC com microssegundos e ``Z``
(``YYYY-MM-DDTHH:MM:SS.ffffffZ``): uma unica representacao para
registro e comparacao.
"""

from __future__ import annotations

import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone

MAX_FLOOR_AGE = timedelta(minutes=30)

_STRICT_FLOOR = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})"
)


class FloorContractError(ValueError):
    """Violacao do contrato estrito, com `code` estavel para o operador."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def parse_strict(raw: str) -> datetime:
    if not isinstance(raw, str):
        raise FloorContractError("not_a_string")

    if _STRICT_FLOOR.fullmatch(raw) is None:
        raise FloorContractError(
            "format",
            "esperado YYYY-MM-DDThh:mm:ss[.ffffff](Z|+hh:mm|-hh:mm)",
        )

    try:
        value = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as error:
        raise FloorContractError(
            "calendar", "data, hora ou offset inexistente"
        ) from error

    if value.tzinfo is None or value.utcoffset() is None:
        raise FloorContractError("naive", "timezone obrigatorio")

    return value


def canonical_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise FloorContractError("naive", "timezone obrigatorio")

    return value.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def validate_for_activation(
    raw: str,
    db_now: datetime,
    *,
    max_age: timedelta = MAX_FLOOR_AGE,
) -> str:
    """Retorna o T0 canonico; levanta FloorContractError se violar."""
    if db_now.tzinfo is None:
        raise FloorContractError("db_now_naive", "relogio sem timezone")

    value = parse_strict(raw)

    if value > db_now:
        raise FloorContractError("future", "floor posterior ao relogio do DB")

    if db_now - value > max_age:
        raise FloorContractError(
            "stale", "floor mais antigo que 30 minutos no relogio do DB"
        )

    return canonical_utc(value)


def floor_state(floor: datetime | None, now: datetime) -> str:
    """unset | armed (floor <= agora) | future (floor > agora)."""
    if floor is None:
        return "unset"

    return "future" if floor > now else "armed"


def floor_age_seconds(
    floor: datetime | None, now: datetime
) -> int | None:
    """Idade diagnostica do floor (agora - floor), em segundos inteiros."""
    if floor is None:
        return None

    return int((now - floor).total_seconds())
