"""Limite de taxa por janela deslizante."""

from __future__ import annotations

import collections
import math
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._clock = clock
        self._events: collections.deque[float] = collections.deque()

    def try_acquire(self) -> int | None:
        """Retorna None se permitido (e registra); senao, segundos sugeridos de espera."""
        now = self._clock()
        while self._events and now - self._events[0] >= self.window:
            self._events.popleft()
        if len(self._events) >= self.max_events:
            return max(1, math.ceil(self.window - (now - self._events[0])))
        self._events.append(now)
        return None
