from collections import deque
from hashlib import sha256
from math import ceil
from threading import RLock
from time import monotonic

from app.core.config import settings


class SkillRateLimiter:
    """
    Sliding-window defense-in-depth for explicit Skill API calls.

    State is intentionally in-process and keyed only by a SHA-256 user
    identity. Distributed deployments must also rate-limit at the
    reverse-proxy/gateway or through shared storage.
    """

    def __init__(self) -> None:
        self._requests: dict[
            str,
            deque[float],
        ] = {}
        self._lock = RLock()

    @staticmethod
    def _identity_key(
        user_id: int,
    ) -> str:
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
        ):
            raise ValueError(
                "user_id inválido para rate limit."
            )

        return sha256(
            str(user_id).encode("utf-8")
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
        user_id: int,
    ) -> int | None:
        now = monotonic()
        key = self._identity_key(
            user_id
        )
        max_requests = (
            settings
            .skill_rate_limit_user_max_requests
        )
        window_seconds = (
            settings
            .skill_rate_limit_window_seconds
        )

        with self._lock:
            bucket = self._requests.get(
                key
            )
            if bucket is None:
                bucket = deque()
                self._requests[key] = bucket

            self._prune(
                bucket,
                now=now,
                window_seconds=window_seconds,
            )

            if len(bucket) >= max_requests:
                retry_after = ceil(
                    (
                        bucket[0]
                        + window_seconds
                    )
                    - now
                )
                return max(
                    1,
                    retry_after,
                )

            bucket.append(now)

        return None

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()


skill_rate_limiter = SkillRateLimiter()
