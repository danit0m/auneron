from app.core import work_skill_observability


def test_work_skill_observability_keeps_only_safe_fields(monkeypatch) -> None:
    captured = {}

    def fake_info(message, *, extra):
        captured["message"] = message
        captured["extra"] = extra

    monkeypatch.setattr(
        work_skill_observability.work_skill_observability_logger,
        "info",
        fake_info,
    )
    work_skill_observability.log_work_skill_execution_event(
        "work.skill_execution.test",
        work_item_id=10,
        skill_version_id=20,
        outcome="ready",
        payload={"secret": "x"},
        input_digest="a" * 64,
        dispatch_key="secret-key",
        idempotency_key="secret-idem",
        actor_reference="system:work:10",
        raw_output={"secret": "y"},
        exception="secret exception",
    )
    extra = captured["extra"]
    assert extra["work_item_id"] == 10
    assert extra["skill_version_id"] == 20
    assert extra["outcome"] == "ready"
    for forbidden in (
        "payload", "input_digest", "dispatch_key", "idempotency_key",
        "actor_reference", "raw_output", "exception",
    ):
        assert forbidden not in extra


def test_work_skill_observability_always_includes_request_id(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        work_skill_observability,
        "get_request_id",
        lambda: "work-skill-request-1",
    )
    monkeypatch.setattr(
        work_skill_observability.work_skill_observability_logger,
        "info",
        lambda message, *, extra: captured.update({"extra": extra}),
    )
    work_skill_observability.log_work_skill_execution_event(
        "work.skill_execution.test",
        work_item_id=1,
    )
    assert captured["extra"]["request_id"] == "work-skill-request-1"


def test_work_skill_observability_allowlist_excludes_secrets() -> None:
    allowed = work_skill_observability._ALLOWED_FIELDS
    for forbidden in (
        "input_payload", "output_payload", "input_digest", "output_digest",
        "dispatch_key", "idempotency_key", "actor_reference", "password",
        "token", "exception",
    ):
        assert forbidden not in allowed
