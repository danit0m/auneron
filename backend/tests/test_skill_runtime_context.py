import hashlib
import json
from datetime import datetime
from datetime import timezone

import pytest

from app.core.skill_errors import SkillValidationError
from app.services.skill_runtime_context import MAX_WORK_LEARNING_CONTEXT_ITEMS
from app.services.skill_runtime_context import WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
from app.services.skill_runtime_context import normalize_work_learning_runtime_context


def _item(
    *,
    skill_version_id: int = 7,
    terminal_status: str = "succeeded",
    evaluation_code: str = "execution_succeeded",
    learning_signal: str = "positive",
):
    return {
        "memory_id": 11,
        "source_work_item_id": 12,
        "work_skill_execution_id": 13,
        "skill_version_id": skill_version_id,
        "terminal_status": terminal_status,
        "evaluation_code": evaluation_code,
        "learning_signal": learning_signal,
        "observed_at": datetime(
            2026,
            8,
            25,
            12,
            30,
            45,
            123456,
            tzinfo=timezone.utc,
        ),
    }


def _context(**item_kwargs):
    return {
        "protocol": WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
        "items": [_item(**item_kwargs)],
    }


def test_runtime_context_normalizes_and_digests_canonical_safe_metadata() -> None:
    normalized = normalize_work_learning_runtime_context(
        _context(),
        expected_skill_version_id=7,
    )

    assert normalized.protocol == WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
    assert normalized.payload == {
        "protocol": "work_learning_v1",
        "items": [
            {
                "memory_id": 11,
                "source_work_item_id": 12,
                "work_skill_execution_id": 13,
                "skill_version_id": 7,
                "terminal_status": "succeeded",
                "evaluation_code": "execution_succeeded",
                "learning_signal": "positive",
                "observed_at": "2026-08-25T12:30:45.123456Z",
            }
        ],
    }

    expected_bytes = json.dumps(
        normalized.payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert normalized.canonical_bytes == expected_bytes
    assert normalized.digest == hashlib.sha256(expected_bytes).hexdigest()


def test_runtime_context_accepts_timezone_aware_iso_text_and_canonicalizes_utc() -> None:
    context = _context()
    context["items"][0]["observed_at"] = "2026-08-25T09:30:45.123456-03:00"

    normalized = normalize_work_learning_runtime_context(context)

    assert normalized.payload["items"][0]["observed_at"] == (
        "2026-08-25T12:30:45.123456Z"
    )


def test_runtime_context_rejects_unknown_or_sensitive_item_fields() -> None:
    context = _context()
    context["items"][0]["raw_error"] = "private"

    with pytest.raises(SkillValidationError):
        normalize_work_learning_runtime_context(context)


def test_runtime_context_rejects_unknown_outer_fields() -> None:
    context = _context()
    context["role"] = "admin"

    with pytest.raises(SkillValidationError):
        normalize_work_learning_runtime_context(context)


def test_runtime_context_rejects_incoherent_deterministic_mapping() -> None:
    with pytest.raises(SkillValidationError):
        normalize_work_learning_runtime_context(
            _context(
                terminal_status="failed",
                evaluation_code="execution_cancelled",
                learning_signal="neutral",
            )
        )


def test_runtime_context_rejects_cross_skill_context() -> None:
    with pytest.raises(SkillValidationError):
        normalize_work_learning_runtime_context(
            _context(skill_version_id=8),
            expected_skill_version_id=7,
        )


def test_runtime_context_rejects_naive_observed_at() -> None:
    context = _context()
    context["items"][0]["observed_at"] = datetime(2026, 8, 25, 12, 30)

    with pytest.raises(SkillValidationError):
        normalize_work_learning_runtime_context(context)


def test_runtime_context_rejects_more_than_ten_items() -> None:
    context = {
        "protocol": WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
        "items": [
            _item()
            for _ in range(MAX_WORK_LEARNING_CONTEXT_ITEMS + 1)
        ],
    }

    with pytest.raises(SkillValidationError):
        normalize_work_learning_runtime_context(context)
