"""Reverificacao periodica da exposicao do Ollama (modo recusa).

A partida confere uma vez que o Ollama escuta so em loopback. Esta tarefa repete a MESMA conferencia
a cada `exposure_recheck_seconds`. Se o Ollama passar a escutar fora de loopback, ou a conferencia
ficar impossivel (ou falhar por qualquer excecao), o Gateway entra em modo recusa: o app responde
503 sem chamar o Ollama. Quando a conferencia volta a passar, sai do modo recusa sozinho.
Uma unica tarefa asyncio (a conferencia bloqueante roda no executor do laco); cancelada no desligamento.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .config import Config
from .exposure import ExposureError, check_ollama_not_exposed
from .glog import AuditLog

REASON_EXPOSED = "ollama_exposed"
REASON_UNVERIFIABLE = "exposure_unverifiable"
REASON_RECOVERED = "exposure_recovered"


class ExposureWatch:
    def __init__(
        self,
        cfg: Config,
        log: AuditLog,
        *,
        check: Callable[[], str] | None = None,
        interval: float | None = None,
    ) -> None:
        self.log = log
        self.interval = cfg.exposure_recheck_seconds if interval is None else interval
        self._check = check or (
            lambda: check_ollama_not_exposed(cfg.ollama_port, on_unverifiable=cfg.exposure_unverifiable)
        )
        self.refusing = False
        self.reason: str | None = None

    def evaluate(self) -> str | None:
        """None = ok; senao o motivo da recusa. Qualquer excecao = nao verificavel (falha fechada)."""
        try:
            self._check()
        except ExposureError as exc:
            return REASON_UNVERIFIABLE if "nao verificavel" in str(exc) else REASON_EXPOSED
        except Exception:
            return REASON_UNVERIFIABLE
        return None

    def apply(self, reason: str | None) -> None:
        if reason is None:
            if self.refusing:
                self.refusing = False
                self.reason = None
                self.log.event("internal", code=REASON_RECOVERED)
            return
        if not self.refusing or reason != self.reason:
            self.log.event("internal", code=reason)
        self.refusing = True
        self.reason = reason

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.interval)
            self.apply(await loop.run_in_executor(None, self.evaluate))
