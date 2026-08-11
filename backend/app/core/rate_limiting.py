from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from threading import RLock
from time import monotonic

from fastapi import Request

from app.core.config import settings


@dataclass(frozen=True)
class RateLimitRule:
    max_failures: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitTarget:
    scope: str
    key_hash: str
    rule: RateLimitRule


class AuthRateLimiter:
    def __init__(self) -> None:
        self._failures: dict[
            tuple[str, str],
            deque[float],
        ] = {}
        self._lock = RLock()

    def _bucket(
        self,
        target: RateLimitTarget,
    ) -> deque[float]:
        key = (
            target.scope,
            target.key_hash,
        )

        bucket = self._failures.get(key)

        if bucket is None:
            bucket = deque()
            self._failures[key] = bucket

        return bucket

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

    def retry_after(
        self,
        targets: tuple[
            RateLimitTarget,
            ...,
        ],
    ) -> int | None:
        now = monotonic()
        longest_retry_after = 0

        with self._lock:
            for target in targets:
                bucket = self._bucket(
                    target
                )
                self._prune(
                    bucket,
                    now=now,
                    window_seconds=(
                        target.rule.window_seconds
                    ),
                )

                if (
                    len(bucket)
                    < target.rule.max_failures
                ):
                    continue

                retry_after = ceil(
                    (
                        bucket[0]
                        + target.rule.window_seconds
                    )
                    - now
                )
                longest_retry_after = max(
                    longest_retry_after,
                    retry_after,
                )

        if longest_retry_after <= 0:
            return None

        return longest_retry_after

    def record_failure(
        self,
        targets: tuple[
            RateLimitTarget,
            ...,
        ],
    ) -> int | None:
        now = monotonic()

        with self._lock:
            for target in targets:
                bucket = self._bucket(
                    target
                )
                self._prune(
                    bucket,
                    now=now,
                    window_seconds=(
                        target.rule.window_seconds
                    ),
                )
                bucket.append(now)

        return self.retry_after(
            targets
        )

    def clear(
        self,
        targets: tuple[
            RateLimitTarget,
            ...,
        ],
        *,
        scopes: set[str] | None = None,
    ) -> None:
        with self._lock:
            for target in targets:
                if (
                    scopes is not None
                    and target.scope
                    not in scopes
                ):
                    continue

                self._failures.pop(
                    (
                        target.scope,
                        target.key_hash,
                    ),
                    None,
                )

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


auth_rate_limiter = AuthRateLimiter()


def _hash_identity(
    value: str,
) -> str:
    return sha256(
        value.encode("utf-8")
    ).hexdigest()


def _client_identity(
    request: Request,
) -> str:
    forwarded_for = request.headers.get(
        "x-forwarded-for"
    )

    if forwarded_for:
        first_hop = (
            forwarded_for
            .split(",", 1)[0]
            .strip()
        )

        if first_hop:
            return first_hop

    if request.client is not None:
        return request.client.host

    return "unknown-client"


def login_rate_limit_targets(
    request: Request,
    email: str,
) -> tuple[
    RateLimitTarget,
    RateLimitTarget,
]:
    normalized_email = (
        email.strip().lower()
    )
    client_identity = _client_identity(
        request
    )

    return (
        RateLimitTarget(
            scope="login_account",
            key_hash=_hash_identity(
                normalized_email
            ),
            rule=RateLimitRule(
                max_failures=(
                    settings
                    .auth_login_account_max_failures
                ),
                window_seconds=(
                    settings
                    .auth_login_window_seconds
                ),
            ),
        ),
        RateLimitTarget(
            scope="login_ip",
            key_hash=_hash_identity(
                client_identity
            ),
            rule=RateLimitRule(
                max_failures=(
                    settings
                    .auth_login_ip_max_failures
                ),
                window_seconds=(
                    settings
                    .auth_login_window_seconds
                ),
            ),
        ),
    )


def elevation_rate_limit_targets(
    request: Request,
    user_id: int,
) -> tuple[
    RateLimitTarget,
    RateLimitTarget,
]:
    client_identity = _client_identity(
        request
    )

    return (
        RateLimitTarget(
            scope="elevation_user",
            key_hash=_hash_identity(
                str(user_id)
            ),
            rule=RateLimitRule(
                max_failures=(
                    settings
                    .auth_elevation_user_max_failures
                ),
                window_seconds=(
                    settings
                    .auth_elevation_window_seconds
                ),
            ),
        ),
        RateLimitTarget(
            scope="elevation_ip",
            key_hash=_hash_identity(
                client_identity
            ),
            rule=RateLimitRule(
                max_failures=(
                    settings
                    .auth_elevation_ip_max_failures
                ),
                window_seconds=(
                    settings
                    .auth_elevation_window_seconds
                ),
            ),
        ),
    )
