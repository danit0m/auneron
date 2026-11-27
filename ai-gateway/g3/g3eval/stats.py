"""Small exact statistics, stdlib only."""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile(values: Sequence[float], pct: int) -> float | None:
    """Nearest-rank percentile (same rule as the 18/11 evaluator)."""
    if not values:
        return None

    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _binom_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0

    if k >= n:
        return 1.0

    return sum(
        math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1)
    )


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided CI for a binomial proportion."""
    if n <= 0 or k < 0 or k > n:
        raise ValueError("invalid counts")

    low = 0.0
    high = 1.0

    if k > 0:
        a, b = 0.0, 1.0

        for _ in range(80):
            mid = (a + b) / 2

            if 1 - _binom_cdf(k - 1, n, mid) < alpha / 2:
                a = mid
            else:
                b = mid

        low = (a + b) / 2

    if k < n:
        a, b = 0.0, 1.0

        for _ in range(80):
            mid = (a + b) / 2

            if _binom_cdf(k, n, mid) > alpha / 2:
                a = mid
            else:
                b = mid

        high = (a + b) / 2

    return low, high


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from the discordant counts.

    b = only arm 1 correct, c = only arm 2 correct. Under H0 the discordant
    pairs split 50/50: two-sided p = min(1, 2 * P(X <= min(b, c))).
    """
    if b < 0 or c < 0:
        raise ValueError("negative count")

    n = b + c

    if n == 0:
        return 1.0

    tail = _binom_cdf(min(b, c), n, 0.5)
    return min(1.0, 2 * tail)


def mcnemar_power(
    n_pairs: int, p_only1: float, p_only2: float, alpha: float = 0.05
) -> float:
    """Exact power: probability that mcnemar_exact < alpha when the
    per-pair probabilities of the two discordant outcomes are given."""
    if p_only1 < 0 or p_only2 < 0 or p_only1 + p_only2 > 1:
        raise ValueError("invalid probabilities")

    power = 0.0
    p_conc = 1 - p_only1 - p_only2

    for b in range(n_pairs + 1):
        for c in range(n_pairs - b + 1):
            if mcnemar_exact(b, c) >= alpha:
                continue

            rest = n_pairs - b - c
            power += (
                math.comb(n_pairs, b)
                * math.comb(n_pairs - b, c)
                * p_only1**b
                * p_only2**c
                * p_conc**rest
            )

    return power
