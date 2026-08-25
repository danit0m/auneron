import os
from pathlib import Path

import pytest

from app.core.skill_errors import SkillExecutionError
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.skill_errors import SkillOutputLimitError
from app.core.skill_errors import SkillValidationError
from app.services.isolated_skill_executor import IsolatedSkillExecutor


TESTS_DIR = Path(__file__).resolve().parent


def _executor() -> IsolatedSkillExecutor:
    return IsolatedSkillExecutor(
        max_workers=1,
        kill_grace_seconds=1,
        python_path_entries=(str(TESTS_DIR),),
    )


def test_isolated_executor_runs_importable_handler() -> None:
    executor = _executor()

    result = executor.execute(
        "isolated_skill_handlers:double",
        {"value": 4},
        timeout_seconds=2,
        max_output_bytes=4096,
    )

    assert result == {"result": 8}


def test_timeout_kills_worker_before_delayed_side_effect(
    tmp_path: Path,
) -> None:
    executor = _executor()
    marker = tmp_path / "should-not-exist.txt"

    with pytest.raises(SkillExecutionTimeoutError):
        executor.execute(
            "isolated_skill_handlers:delayed_write",
            {
                "delay_seconds": 5,
                "path": str(marker),
            },
            timeout_seconds=1,
            max_output_bytes=4096,
        )

    assert marker.exists() is False


def test_application_secrets_are_not_forwarded(
    monkeypatch,
) -> None:
    monkeypatch.setenv("API_KEY", "private-api-key")
    monkeypatch.setenv("DATABASE_URL", "private-database-url")
    monkeypatch.setenv("TEST_API_KEY", "private-test-api-key")
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "private-test-database-url",
    )
    executor = _executor()

    result = executor.execute(
        "isolated_skill_handlers:environment_probe",
        {},
        timeout_seconds=2,
        max_output_bytes=4096,
    )

    assert result == {
        "api_key_present": False,
        "database_url_present": False,
        "test_api_key_present": False,
        "test_database_url_present": False,
    }


def test_handler_failure_is_sanitized() -> None:
    executor = _executor()

    with pytest.raises(
        SkillExecutionError,
        match="Handler isolado falhou",
    ) as captured:
        executor.execute(
            "isolated_skill_handlers:fail",
            {},
            timeout_seconds=2,
            max_output_bytes=4096,
        )

    assert "private worker detail" not in str(captured.value)


def test_output_ceiling_is_enforced_in_worker() -> None:
    executor = _executor()

    with pytest.raises(SkillOutputLimitError):
        executor.execute(
            "isolated_skill_handlers:oversized",
            {"size": 5000},
            timeout_seconds=2,
            max_output_bytes=1024,
        )


def test_entrypoint_validation_fails_closed() -> None:
    executor = _executor()

    with pytest.raises(SkillValidationError):
        executor.execute(
            "os:system('unsafe')",
            {},
            timeout_seconds=2,
            max_output_bytes=4096,
        )


def _runtime_context():
    return {
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


def test_isolated_executor_delivers_opted_in_side_band_runtime_context() -> None:
    executor = _executor()

    result = executor.execute(
        "isolated_skill_handlers:context_probe",
        {"value": 4},
        timeout_seconds=2,
        max_output_bytes=4096,
        runtime_context_protocol="work_learning_v1",
        runtime_context=_runtime_context(),
    )

    assert result == {
        "result": 8,
        "context_protocol": "work_learning_v1",
        "learning_signal": "positive",
        "context_items": 1,
    }


def test_isolated_executor_rejects_incomplete_or_unknown_runtime_context() -> None:
    executor = _executor()

    with pytest.raises(SkillValidationError):
        executor.execute(
            "isolated_skill_handlers:context_probe",
            {"value": 4},
            timeout_seconds=2,
            max_output_bytes=4096,
            runtime_context_protocol="work_learning_v1",
        )

    with pytest.raises(SkillValidationError):
        executor.execute(
            "isolated_skill_handlers:context_probe",
            {"value": 4},
            timeout_seconds=2,
            max_output_bytes=4096,
            runtime_context_protocol="unknown_v1",
            runtime_context=_runtime_context(),
        )
