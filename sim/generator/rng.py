"""
Sub-streams deterministicos (Design Freeze v2, secao 9).

Cada componente tem seu proprio gerador derivado de
sha256("<seed>:<componente>:<indices>"): adicionar o cliente N+1 nao altera
os bytes dos clientes 1..N. Sorteios ponderados usam pesos INTEIROS (sem
aritmetica de ponto flutuante na decisao).
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence
from typing import TypeVar

T = TypeVar("T")


def substream(seed: int, component: str, *index: object) -> random.Random:
    key = f"{seed}:{component}:" + ":".join(str(item) for item in index)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def pick_weighted(rng: random.Random, options: Sequence[tuple[int, T]]) -> T:
    total = sum(weight for weight, _ in options)
    if total <= 0:
        raise ValueError("pesos invalidos")
    roll = rng.randrange(total)
    acc = 0
    for weight, value in options:
        acc += weight
        if roll < acc:
            return value
    raise AssertionError("inalcancavel")


def chance_bps(rng: random.Random, bps: int) -> bool:
    """Verdadeiro com probabilidade bps/10000 (inteiro)."""
    return rng.randrange(10000) < bps
