from dataclasses import dataclass
from typing import Final


MTOK: Final[int] = 1_000_000
BPS: Final[int] = 10_000


@dataclass(frozen=True)
class PriceEntry:
    """Integer micro-USD per million tokens (1 USD = 1_000_000 micro)."""

    input_micro_per_mtok: int
    output_micro_per_mtok: int


# Lab values -- FakeProvider only. Real prices need a pinned,
# dated price entry approved by the PO before any paid call
# (docs/intelligence/GATEWAY_CONTRACT.md, "Lab values").
PRICE_TABLE: Final[dict[str, PriceEntry]] = {
    "fake-small": PriceEntry(
        input_micro_per_mtok=1_000_000,
        output_micro_per_mtok=4_000_000,
    ),
    "fake-large": PriceEntry(
        input_micro_per_mtok=5_000_000,
        output_micro_per_mtok=20_000_000,
    ),
}


def ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def cost_micro(
    input_tokens: int,
    output_tokens: int,
    price: PriceEntry,
) -> int:
    """cost = ceil(tokens * price / 1e6), integer arithmetic only."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts must be non-negative.")

    return ceil_div(
        input_tokens * price.input_micro_per_mtok
        + output_tokens * price.output_micro_per_mtok,
        MTOK,
    )


def input_token_upper_bound(*texts: str) -> int:
    """
    Provable, conservative upper bound: one token per UTF-8 byte.
    No tokenizer is a provable bound across providers; bytes are.
    """
    return sum(len(text.encode("utf-8")) for text in texts)


@dataclass(frozen=True)
class Reservation:
    micro: int
    tokens: int


def worst_case_reservation(
    *,
    input_upper_bound_tokens: int,
    max_output_tokens: int,
    price: PriceEntry,
    margin_bps: int,
) -> Reservation:
    """
    Worst-case hold for one attempt: priced input upper bound plus
    priced max output, inflated by margin_bps. Always >= 1 on both
    dimensions (the ledger rejects non-positive reservations).
    """
    if margin_bps < 0:
        raise ValueError("margin_bps must be non-negative.")

    base = cost_micro(
        input_upper_bound_tokens, max_output_tokens, price
    )
    micro = ceil_div(base * (BPS + margin_bps), BPS)
    tokens = ceil_div(
        (input_upper_bound_tokens + max_output_tokens)
        * (BPS + margin_bps),
        BPS,
    )

    return Reservation(micro=max(micro, 1), tokens=max(tokens, 1))
