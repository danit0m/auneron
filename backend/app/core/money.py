from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP
from typing import Any


MONEY_QUANTUM = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")
MAX_MONEY = Decimal("999999999999.99")


def to_money(
    value: Any,
    *,
    default: Decimal | None = None,
) -> Decimal:
    """
    Converte um valor numérico para Decimal com duas casas.

    A conversão de float passa primeiro por str para evitar carregar
    artefatos binários para a camada financeira.
    """

    if value is None or (
        isinstance(value, str)
        and not value.strip()
    ):
        if default is None:
            raise ValueError(
                "O valor monetário não foi informado."
            )

        value = default

    if isinstance(value, bool):
        raise ValueError(
            "Valor booleano não é um valor monetário válido."
        )

    try:
        amount = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value).strip())
        )

    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(
            f"Valor monetário inválido: {value!r}."
        ) from error

    if not amount.is_finite():
        raise ValueError(
            "O valor monetário deve ser finito."
        )

    try:
        normalized = amount.quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    except InvalidOperation as error:
        raise ValueError(
            f"Valor monetário fora do intervalo suportado: {value!r}."
        ) from error

    if abs(normalized) > MAX_MONEY:
        raise ValueError(
            "O valor monetário excede o limite de NUMERIC(14, 2)."
        )

    return normalized


def money_or_zero(value: Any) -> Decimal:
    """
    Converte um valor para Decimal e devolve zero quando ele é inválido.

    Deve ser usado somente em contextos de decisão tolerantes a payloads
    incompletos. Entradas de usuário e migrações devem usar ``to_money``.
    """

    try:
        return to_money(
            value,
            default=ZERO_MONEY,
        )

    except ValueError:
        return ZERO_MONEY


def parse_localized_money(value: Any) -> Decimal:
    """
    Converte textos monetários brasileiros ou internacionais.

    Exemplos aceitos:
    - 12400
    - 12400.00
    - 12.400,00
    - 12,400.00
    - R$ 12.400,00
    """

    text = (
        ""
        if value is None
        else str(value)
    ).strip()

    text = (
        text
        .replace("R$", "")
        .replace("BRL", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .strip()
    )

    if not text:
        raise ValueError(
            "O campo valor está vazio."
        )

    comma_position = text.rfind(",")
    dot_position = text.rfind(".")

    if comma_position >= 0 and dot_position >= 0:
        if comma_position > dot_position:
            text = text.replace(".", "")
            text = _replace_last_separator(
                text,
                ",",
                ".",
            )
        else:
            text = text.replace(",", "")
            text = _keep_last_separator(
                text,
                ".",
            )

    elif "," in text:
        text = _normalize_single_separator(
            text,
            ",",
        )

    elif "." in text:
        text = _normalize_single_separator(
            text,
            ".",
        )

    return to_money(text)


def money_to_json_number(value: Decimal) -> float:
    """
    Converte Decimal para número JSON apenas na fronteira da API.
    """

    return float(value)


def _normalize_single_separator(
    text: str,
    separator: str,
) -> str:
    parts = text.split(separator)

    if len(parts) == 2:
        integer_part, fractional_part = parts

        if (
            len(fractional_part) == 3
            and integer_part.lstrip("+-").isdigit()
            and fractional_part.isdigit()
        ):
            return integer_part + fractional_part

        if separator == ",":
            return ".".join(parts)

        return text

    fractional_part = parts[-1]

    if len(fractional_part) in {1, 2}:
        integer_part = "".join(parts[:-1])
        return f"{integer_part}.{fractional_part}"

    return "".join(parts)


def _replace_last_separator(
    text: str,
    old: str,
    new: str,
) -> str:
    head, separator, tail = text.rpartition(old)

    if not separator:
        return text

    return (
        head.replace(old, "")
        + new
        + tail
    )


def _keep_last_separator(
    text: str,
    separator: str,
) -> str:
    head, found, tail = text.rpartition(separator)

    if not found:
        return text

    return (
        head.replace(separator, "")
        + separator
        + tail
    )
