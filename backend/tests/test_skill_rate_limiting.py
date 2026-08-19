import pytest
from fastapi import HTTPException

from app.api.routes.skills import _enforce_skill_rate_limit
from app.core.config import settings
from app.core.skill_rate_limiting import SkillRateLimiter
from app.core.skill_rate_limiting import skill_rate_limiter


def test_skill_rate_limiter_blocks_after_configured_budget(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "skill_rate_limit_user_max_requests",
        2,
    )
    monkeypatch.setattr(
        settings,
        "skill_rate_limit_window_seconds",
        60,
    )
    limiter = SkillRateLimiter()

    assert limiter.consume(
        user_id=41
    ) is None
    assert limiter.consume(
        user_id=41
    ) is None

    retry_after = limiter.consume(
        user_id=41
    )
    assert retry_after is not None
    assert retry_after > 0


def test_skill_rate_limiter_isolated_by_user(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "skill_rate_limit_user_max_requests",
        1,
    )
    limiter = SkillRateLimiter()

    assert limiter.consume(
        user_id=1
    ) is None
    assert limiter.consume(
        user_id=1
    ) is not None
    assert limiter.consume(
        user_id=2
    ) is None


def test_route_rate_limit_returns_retry_after(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "skill_rate_limit_user_max_requests",
        1,
    )
    skill_rate_limiter.reset()

    _enforce_skill_rate_limit(
        user_id=9,
        version_id=4,
    )

    with pytest.raises(
        HTTPException
    ) as captured:
        _enforce_skill_rate_limit(
            user_id=9,
            version_id=4,
        )

    assert captured.value.status_code == 429
    assert int(
        captured.value.headers["Retry-After"]
    ) > 0
    assert captured.value.detail["code"] == (
        "skill_rate_limited"
    )


def test_skill_rate_limiter_does_not_store_raw_user_identity() -> None:
    limiter = SkillRateLimiter()
    limiter.consume(
        user_id=987654321
    )

    assert "987654321" not in repr(
        limiter._requests
    )
