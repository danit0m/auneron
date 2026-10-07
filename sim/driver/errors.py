"""Erros do Driver. `HarnessError` invalida o run (Design Freeze V1.1 sec. 18)."""

from __future__ import annotations


class HarnessError(RuntimeError):
    """Falha do instrumento (nao do produto): o run e INVALID."""


class MissingRef(HarnessError):
    """Referencia da agenda (rec_ref/MO/MP/ESC) sem mapeamento no StateStore."""


class Ambiguous(RuntimeError):
    """Resultado ambiguo (timeout, conexao perdida, 5xx): exige reconciliacao."""

    def __init__(self, detail: str, last_status=None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.last_status = last_status
