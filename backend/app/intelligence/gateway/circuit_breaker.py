import threading
import time
from collections.abc import Callable


class CircuitBreaker:
    """
    In-process availability breaker per key (model profile).

    closed -> open after `failure_threshold` consecutive availability
    failures; open -> half_open after `cooldown_seconds` (one probe
    allowed); a success closes it, a failure re-opens it. Only
    availability failures (timeout/transport/429/5xx) count: refusals,
    invalid schemas and budget rejections are not provider outages.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1.")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0.")

        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probing: set[str] = set()

    def state(self, key: str) -> str:
        with self._lock:
            return self._state_locked(key)

    def _state_locked(self, key: str) -> str:
        opened_at = self._opened_at.get(key)

        if opened_at is None:
            return "closed"

        if self._clock() - opened_at >= self._cooldown:
            return "half_open"

        return "open"

    def allow(self, key: str) -> bool:
        with self._lock:
            state = self._state_locked(key)

            if state == "closed":
                return True

            if state == "half_open" and key not in self._probing:
                self._probing.add(key)
                return True

            return False

    def cancel_probe(self, key: str) -> None:
        """Frees a half-open probe slot whose attempt never completed."""
        with self._lock:
            self._probing.discard(key)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            self._probing.discard(key)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._probing.discard(key)
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count

            if (
                count >= self._threshold
                or key in self._opened_at
            ):
                self._opened_at[key] = self._clock()
