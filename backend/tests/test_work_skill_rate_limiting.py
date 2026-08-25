from app.core.config import settings
from app.core.work_skill_rate_limiting import WorkSkillDispatchRateLimiter


def test_work_dispatch_limiter_blocks_after_budget(monkeypatch) -> None:
    monkeypatch.setattr(settings, "work_skill_dispatch_max_requests", 2)
    monkeypatch.setattr(settings, "work_skill_dispatch_window_seconds", 60)
    limiter = WorkSkillDispatchRateLimiter()
    assert limiter.consume(authority_user_id=41, now=10.0) is None
    assert limiter.consume(authority_user_id=41, now=11.0) is None
    assert limiter.consume(authority_user_id=41, now=12.0) == 58


def test_work_dispatch_limiter_isolated_by_authority(monkeypatch) -> None:
    monkeypatch.setattr(settings, "work_skill_dispatch_max_requests", 1)
    limiter = WorkSkillDispatchRateLimiter()
    assert limiter.consume(authority_user_id=1, now=1.0) is None
    assert limiter.consume(authority_user_id=1, now=2.0) is not None
    assert limiter.consume(authority_user_id=2, now=2.0) is None


def test_work_dispatch_limiter_does_not_store_raw_identity() -> None:
    limiter = WorkSkillDispatchRateLimiter()
    limiter.consume(authority_user_id=987654321, now=1.0)
    assert "987654321" not in repr(limiter._requests)


def test_work_dispatch_limiter_rejects_invalid_authority() -> None:
    limiter = WorkSkillDispatchRateLimiter()
    for value in (True, 0, -1, "1"):
        try:
            limiter.consume(
                authority_user_id=value,  # type: ignore[arg-type]
                now=1.0,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid authority should fail")
