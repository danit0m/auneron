"""
Clock Controller (SIM-CLOCK-1/2): offset relativo gravado no volume Docker
NATIVO via container `auneron-sim-clock`; so avanca (nunca retrocede).

Propriedade mecanica: o offset do libfaketime e um INTEIRO de segundos, logo
cada `set` tem erro de arredondamento de ate 0,5 s. A guarda anti-retrocesso
compara o alvo com o "agora virtual" corrente com tolerancia de
ROUNDING_TOLERANCE_S = 1 s: reaplicar praticamente o mesmo instante NAO e
retrocesso; qualquer retrocesso material (> 1 s) e recusado.
"""

from __future__ import annotations

import json
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.guard import require_container


class ClockController:
    CONTAINER = "auneron-sim-clock"
    ROUNDING_TOLERANCE_S = 1

    def __init__(self, runner, config, state_file: Path) -> None:
        require_container(self.CONTAINER, config)
        self.runner = runner
        self.config = config
        self.state_file = state_file
        clock = config["clock"]
        self.d0 = date.fromisoformat(clock["d0"])
        self.tz = timezone(timedelta(minutes=int(clock["utc_offset_minutes"])))

    def virtual(self, day: int, hhmm: str) -> datetime:
        hours, minutes = map(int, hhmm.split(":"))
        return datetime.combine(self.d0 + timedelta(days=day), datetime.min.time(), self.tz).replace(
            hour=hours, minute=minutes)

    def _last(self):
        if self.state_file.exists():
            return datetime.fromisoformat(json.loads(self.state_file.read_text())["target"])
        return None

    def set(self, target: datetime) -> dict:
        last = self._last()
        if last is not None and target < last:
            raise GuardViolation(f"relogio nao pode retroceder: {target} < {last}")
        real = time.time()
        if last is not None and target.timestamp() < self.expected_at(real) - self.ROUNDING_TOLERANCE_S:
            # mesmo alvo reaplicado depois de tempo real decorrido tambem e retrocesso
            # (tolerancia = arredondamento do offset inteiro)
            raise GuardViolation(f"relogio nao pode retroceder: {target} < agora virtual")
        offset = int(round(target.timestamp() - real))
        script = f"printf '%s' '{offset:+d}' > /sim_clock/now.tmp && mv /sim_clock/now.tmp /sim_clock/now"
        result = self.runner.run(["docker", "exec", self.CONTAINER, "sh", "-c", script])
        if result.code != 0:
            raise RuntimeError(f"falha ao gravar o relogio: {result.err[-200:]}")
        self.state_file.write_text(json.dumps({"target": target.isoformat(), "real": real, "offset": offset}),
                                   encoding="utf-8")
        return {"target": target.isoformat(), "offset_s": offset, "set_real_utc": real}

    def expected_at(self, real_epoch: float) -> float:
        """Instante virtual esperado (epoch) para um instante real do host."""
        data = json.loads(self.state_file.read_text())
        return real_epoch + data["offset"]

    def expected_now(self) -> datetime:
        return datetime.fromtimestamp(self.expected_at(time.time()), self.tz)
