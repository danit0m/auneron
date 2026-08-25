from collections import deque
from hashlib import sha256
from math import ceil
from threading import RLock
from time import monotonic

from app.core.config import settings


class WorkSkillDispatchRateLimiter:
    """In-process defense-in-depth for governed Work -> Skill submissions."""

    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = {}
        self._lock = RLock()

    @staticmethod
    def _identity_key(authority_user_id: int) -> str:
        if (
            isinstance(authority_user_id, bool)
            or not isinstance(authority_user_id, int)
            or authority_user_id <= 0
        ):
            raise ValueError(
                "authority_user_id inválido para rate limit."
            )
        return sha256(
            str(authority_user_id).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _prune(
        bucket: deque[float],
        *,
        now: float,
        window_seconds: int,
    ) -> None:
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def consume(
        self,
        *,
        authority_user_id: int,
        now: float | None = None,
    ) -> int | None:
        effective_now = monotonic() if now is None else now
        key = self._identity_key(authority_user_id)
        max_requests = settings.work_skill_dispatch_max_requests
        window_seconds = settings.work_skill_dispatch_window_seconds

        with self._lock:
            bucket = self._requests.get(key)
            if bucket is None:
                bucket = deque()
                self._requests[key] = bucket

            self._prune(
                bucket,
                now=effective_now,
                window_seconds=window_seconds,
            )
            if len(bucket) >= max_requests:
                retry_after = ceil(
                    (bucket[0] + window_seconds) - effective_now
                )
                return max(1, retry_after)

            bucket.append(effective_now)
        return None

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()


work_skill_dispatch_rate_limiter = WorkSkillDispatchRateLimiter()
