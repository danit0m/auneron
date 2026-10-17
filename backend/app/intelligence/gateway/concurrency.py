import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode


class ConcurrencyLimiter:
    """
    Bounded number of simultaneous in-flight provider calls. Rejects
    immediately (fail-closed) instead of queueing without bound.
    """

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1.")

        self._max = max_concurrent
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    @property
    def max_concurrent(self) -> int:
        return self._max

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._semaphore.acquire(blocking=False):
            raise GatewayError(
                GatewayErrorCode.CONCURRENCY_LIMIT,
                "Concurrent call limit reached.",
            )

        try:
            yield
        finally:
            self._semaphore.release()
