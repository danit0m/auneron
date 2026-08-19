import json
import logging
from io import StringIO
from pathlib import Path

from app.core import skill_observability
from app.core.observability import JsonLogFormatter
from app.core.skill_observability import log_skill_runtime_event


def test_skill_runtime_event_is_structured_and_bounded() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(
        stream
    )
    handler.setFormatter(
        JsonLogFormatter()
    )
    logger = (
        skill_observability
        .skill_observability_logger
    )
    old_level = logger.level
    old_propagate = logger.propagate

    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    try:
        log_skill_runtime_event(
            "skill.runtime.finished",
            invocation_id=7,
            skill_version_id=3,
            actor_type="user",
            status="succeeded",
            duration_ms=12,
            output_bytes=18,
            input_payload={
                "password": "should-not-log"
            },
            idempotency_key=(
                "private-idempotency-key"
            ),
            actor_reference="user:999",
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    payload = json.loads(
        stream.getvalue()
    )

    assert payload["event"] == (
        "skill.runtime.finished"
    )
    assert payload["invocation_id"] == 7
    assert payload["skill_version_id"] == 3
    assert payload["status"] == "succeeded"
    assert "input_payload" not in payload
    assert "idempotency_key" not in payload
    assert "actor_reference" not in payload
    assert "should-not-log" not in stream.getvalue()
    assert (
        "private-idempotency-key"
        not in stream.getvalue()
    )


def test_skill_observability_source_has_no_raw_runtime_fields() -> None:
    source = Path(
        skill_observability.__file__
    ).read_text(
        encoding="utf-8"
    )

    assert '"input_payload",' not in source
    assert '"idempotency_key",' not in source
    assert '"actor_reference",' not in source
    assert '"raw_output",' not in source
