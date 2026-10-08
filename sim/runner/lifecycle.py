"""
Ciclo de vida do run (D-1.5E-3): maquina de estados PURA, aplicada por REPLAY do ledger.

    CREATED -> PREFLIGHTED -> RUNNING -> COMPLETED
                                     \\-> ABORTED / INVALID   (tambem a partir de CREATED e PREFLIGHTED)

`COMPLETED`, `ABORTED` e `INVALID` sao TERMINAIS: nenhum evento os deixa, nenhuma transicao volta a `RUNNING`.
Dias andam estritamente 0,1,2...: `DAY_STARTED(d)` exige `d == ultimo_fechado + 1` e nenhum dia aberto
(isso torna impossivel pular, repetir ou retomar um dia: R0). `COMPLETED` exige todos os dias fechados, o
fechamento do run e o pacote selado.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from sim.runner.errors import LifecycleError

CREATED, PREFLIGHTED, RUNNING = "CREATED", "PREFLIGHTED", "RUNNING"
COMPLETED, ABORTED, INVALID = "COMPLETED", "ABORTED", "INVALID"
TERMINAL = frozenset({COMPLETED, ABORTED, INVALID})
LIVE = frozenset({CREATED, PREFLIGHTED, RUNNING})

# eventos que so existem em um estado (ou conjunto)
EVENT_STATES = {
    "RIM_VERIFIED": {CREATED},
    "PREFLIGHT_PASSED": {CREATED},
    "PROBE_DR1": {PREFLIGHTED, RUNNING},
    "D0_AUTHORIZED": {PREFLIGHTED},
    "RUN_STARTED": {PREFLIGHTED},
    "DAY_STARTED": {RUNNING},
    "DAY_CLOSED": {RUNNING},
    "FLOOR_ARMED": {RUNNING},
    "RESTART_DONE": {RUNNING},
    "AUTHORITY_NOTE": {RUNNING},
    "BALANCE_DIVERGENCE": {RUNNING},
    "DISK_WARNING": {RUNNING},
    "RUN_FINALIZING": {RUNNING},
    "PACKAGE_SEALED": {RUNNING},
    "COMPLETED": {RUNNING},
    "ABORT_INTENT": LIVE,
    "ABORTED": LIVE,
    "INVALID": LIVE,
    # anotacoes POS-RUN (nunca mudam o estado de execucao)
    "PARTIAL_PACKAGE_SEALED": {ABORTED, INVALID},
    "EVALUATED": {COMPLETED},
    "INVALIDATED_POST_HOC": {COMPLETED},
    "DESIGNATED_CANONICAL": {COMPLETED},
}
RESERVED_EVENTS = frozenset({"OPENED", "D0_AUTHORIZED"})       # so pelos metodos proprios do ledger
ALL_EVENTS = frozenset(EVENT_STATES) | {"OPENED"}


@dataclass
class Replay:
    last_day: int = 179
    state: str | None = None
    seen: set = field(default_factory=set)
    last_closed: int = -1
    open_day: int | None = None
    abort_intent: bool = False

    def apply(self, event: str, payload: dict) -> str | None:
        if event not in ALL_EVENTS:
            raise LifecycleError(f"evento desconhecido: {event!r}")
        if event == "OPENED":
            if self.state is not None:
                raise LifecycleError("OPENED so pode ser o primeiro evento")
            self.last_day = int(payload["timeline"]["last_day"])
            self.state = CREATED
            self.seen.add(event)
            return self.state
        if self.state is None:
            raise LifecycleError(f"{event} antes de OPENED")
        if self.state in TERMINAL and event not in _POST_RUN:
            raise LifecycleError(f"estado terminal {self.state}: {event} proibido (nada sai de um estado terminal)")
        allowed = EVENT_STATES[event]
        if self.state not in allowed:
            raise LifecycleError(f"{event} proibido em {self.state}")
        handler = getattr(self, "_on_" + event.lower(), None)
        if handler is not None:
            handler(payload)
        if event == "PREFLIGHT_PASSED":
            self.state = PREFLIGHTED
        elif event == "RUN_STARTED":
            self.state = RUNNING
        elif event == "COMPLETED":
            self.state = COMPLETED
        elif event in ("ABORTED", "INVALID"):
            self.state = event
        self.seen.add(event)
        return self.state

    # -- guardas por evento ------------------------------------------------------------------------------
    def _on_run_started(self, payload: dict) -> None:
        if "D0_AUTHORIZED" not in self.seen:
            raise LifecycleError("RUN_STARTED sem D0_AUTHORIZED (interlock)")
        if "RUN_STARTED" in self.seen:
            raise LifecycleError("RUN_STARTED duplicado")

    def _on_d0_authorized(self, payload: dict) -> None:
        if "D0_AUTHORIZED" in self.seen:
            raise LifecycleError("D0_AUTHORIZED e de uso unico")
        if not isinstance(payload.get("token_sha256"), str):
            raise LifecycleError("D0_AUTHORIZED sem token_sha256")

    def _on_day_started(self, payload: dict) -> None:
        day = payload.get("day")
        if self.open_day is not None:
            raise LifecycleError(f"dia {self.open_day} ainda aberto (R0: sem pular nem sobrepor dias)")
        if "RUN_FINALIZING" in self.seen:
            raise LifecycleError("DAY_STARTED apos RUN_FINALIZING")
        if day != self.last_closed + 1:
            raise LifecycleError(f"dia fora de ordem: esperado {self.last_closed + 1}, recebido {day} (R0: sem resume)")
        if day > self.last_day:
            raise LifecycleError(f"dia {day} alem de last_day={self.last_day}")
        self.open_day = day

    def _on_day_closed(self, payload: dict) -> None:
        if self.open_day is None or payload.get("day") != self.open_day:
            raise LifecycleError(f"DAY_CLOSED({payload.get('day')}) sem DAY_STARTED correspondente")
        self.last_closed, self.open_day = self.open_day, None

    def _on_run_finalizing(self, payload: dict) -> None:
        if self.open_day is not None or self.last_closed != self.last_day:
            raise LifecycleError("RUN_FINALIZING exige todos os dias 0..last_day fechados")
        if "RUN_FINALIZING" in self.seen:
            raise LifecycleError("RUN_FINALIZING duplicado")

    def _on_package_sealed(self, payload: dict) -> None:
        if "RUN_FINALIZING" not in self.seen:
            raise LifecycleError("PACKAGE_SEALED antes de RUN_FINALIZING")
        if not isinstance(payload.get("digest"), str):
            raise LifecycleError("PACKAGE_SEALED sem digest")

    def _on_completed(self, payload: dict) -> None:
        if "PACKAGE_SEALED" not in self.seen or self.open_day is not None or self.last_closed != self.last_day:
            raise LifecycleError("COMPLETED exige dias 0..last_day fechados e pacote selado")

    def _on_evaluated(self, payload: dict) -> None:
        if "EVALUATED" in self.seen:
            raise LifecycleError("EVALUATED duplicado")

    def _on_designated_canonical(self, payload: dict) -> None:
        if "EVALUATED" not in self.seen or "INVALIDATED_POST_HOC" in self.seen:
            raise LifecycleError("DESIGNATED_CANONICAL exige run avaliado e nao invalidado")


_POST_RUN = frozenset({"PARTIAL_PACKAGE_SEALED", "EVALUATED", "INVALIDATED_POST_HOC", "DESIGNATED_CANONICAL"})


def replay(events: list, last_day: int | None = None) -> Replay:
    """Reaplica `[(event, payload)]` desde o inicio; levanta LifecycleError na primeira violacao."""
    state = Replay()
    for event, payload in events:
        state.apply(event, payload)
    if last_day is not None and state.last_day != last_day:
        raise LifecycleError("last_day do ledger difere do esperado")
    return state
