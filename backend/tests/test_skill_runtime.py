import json
import time
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.skill_errors import SkillExecutionError
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.skill_errors import SkillHandlerNotAllowedError
from app.core.skill_errors import SkillIdempotencyConflictError
from app.core.skill_errors import SkillInputValidationError
from app.core.skill_errors import SkillInvocationInProgressError
from app.core.skill_errors import SkillOutputLimitError
from app.core.skill_errors import SkillOutputValidationError
from app.core.skill_errors import SkillRuntimeBusyError
from app.core.skill_errors import SkillSchemaError
from app.core.skill_errors import SkillValidationError
from app.models.skill import SkillInvocation
from app.services.isolated_skill_executor import IsolatedSkillExecutor
from app.services.skill_runtime import BoundedSkillExecutor
from app.services.skill_runtime import SkillHandlerRegistry
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import SkillRuntimeService
from app.services.skill_runtime import _canonical_json
from app.services.skill_runtime import _digest_bytes
from app.services.skill_runtime import _fingerprint
from app.services.skill_runtime_context import WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
from app.services.skill_runtime_context import normalize_work_learning_runtime_context
from app.services.skill_service import SkillService


def _published_version(
    db_session: Session,
    *,
    skill_key: str = "runtime.execute-test",
    handler_reference: str = (
        "app.skills.runtime:execute_test"
    ),
    runtime_kind: str = "internal_python",
    execution_mode: str = "read_only",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    timeout_seconds: int = 20,
    max_output_bytes: int = 32768,
    manifest: dict[str, Any] | None = None,
):
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Runtime execute",
        description="Skill para validar execução 23C.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind=runtime_kind,
        handler_reference=handler_reference,
        execution_mode=execution_mode,
        input_schema=(
            input_schema
            if input_schema is not None
            else {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "integer",
                    },
                },
                "required": ["value"],
                "additionalProperties": False,
            }
        ),
        output_schema=(
            output_schema
            if output_schema is not None
            else {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "integer",
                    },
                },
                "required": ["result"],
                "additionalProperties": False,
            }
        ),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        manifest=manifest,
    )
    return service.publish_version(
        draft.id
    ).version


def _actor() -> SkillInvocationActor:
    return SkillInvocationActor(
        actor_type="system",
        actor_reference="pytest-runtime",
    )


def _runtime(
    db_session: Session,
    *,
    version,
    handler,
    max_workers: int = 2,
    trusted_for_autonomy: bool = False,
    autonomy_entrypoint: str | None = None,
    runtime_context_protocol: str | None = None,
    isolated_executor: IsolatedSkillExecutor | None = None,
):
    registry = SkillHandlerRegistry()
    registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=handler,
        trusted_for_autonomy=trusted_for_autonomy,
        autonomy_entrypoint=autonomy_entrypoint,
        runtime_context_protocol=runtime_context_protocol,
    )
    executor = BoundedSkillExecutor(
        max_workers=max_workers
    )
    service = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        executor=executor,
        isolated_executor=isolated_executor,
    )
    return service, executor


def _learning_context(
    skill_version_id: int,
    *,
    memory_id: int = 11,
    execution_id: int = 13,
    terminal_status: str = "succeeded",
    evaluation_code: str = "execution_succeeded",
    learning_signal: str = "positive",
):
    return {
        "protocol": WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
        "items": [
            {
                "memory_id": memory_id,
                "source_work_item_id": 12,
                "work_skill_execution_id": execution_id,
                "skill_version_id": skill_version_id,
                "terminal_status": terminal_status,
                "evaluation_code": evaluation_code,
                "learning_signal": learning_signal,
                "observed_at": "2026-08-25T12:30:45.123456Z",
            }
        ],
    }


def _invocations(
    db_session: Session,
) -> list[SkillInvocation]:
    return list(
        db_session.query(
            SkillInvocation
        ).order_by(
            SkillInvocation.id.asc()
        )
    )


def test_runtime_executes_allowlisted_handler_and_persists_success(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"] * 2
        },
    )

    try:
        result = service.invoke(
            version.id,
            actor=_actor(),
            input_payload={"value": 7},
            idempotency_key="success-1",
        )
    finally:
        executor.shutdown()

    assert result.output == {
        "result": 14
    }
    assert result.duplicate is False
    assert result.invocation.status == "succeeded"
    assert result.invocation.output_payload == {
        "value": {
            "result": 14
        }
    }
    assert result.invocation.output_digest is not None
    assert result.invocation.output_bytes > 0
    assert result.invocation.error_code is None
    assert result.invocation.finished_at is not None


def test_runtime_replays_success_without_executing_twice(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.idempotent-replay",
    )
    calls = {
        "count": 0
    }

    def handler(payload):
        calls["count"] += 1
        return {
            "result": payload["value"]
        }

    service, executor = _runtime(
        db_session,
        version=version,
        handler=handler,
    )

    try:
        first = service.invoke(
            version.id,
            actor=_actor(),
            input_payload={"value": 3},
            idempotency_key="replay-1",
        )
        second = service.invoke(
            version.id,
            actor=_actor(),
            input_payload={"value": 3},
            idempotency_key="replay-1",
        )
    finally:
        executor.shutdown()

    assert calls["count"] == 1
    assert first.invocation.id == second.invocation.id
    assert second.duplicate is True
    assert second.output == {
        "result": 3
    }


def test_runtime_rejects_idempotency_key_with_different_input(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.idempotent-conflict",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"]
        },
    )

    try:
        service.invoke(
            version.id,
            actor=_actor(),
            input_payload={"value": 1},
            idempotency_key="conflict-1",
        )

        with pytest.raises(
            SkillIdempotencyConflictError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 2},
                idempotency_key="conflict-1",
            )
    finally:
        executor.shutdown()

    assert len(_invocations(db_session)) == 1


def test_runtime_persists_input_validation_rejection(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.input-rejected",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": 1
        },
    )

    try:
        with pytest.raises(
            SkillInputValidationError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": "invalid"},
                idempotency_key="invalid-input-1",
            )
    finally:
        executor.shutdown()

    history = _invocations(
        db_session
    )

    assert len(history) == 1
    assert history[0].status == "rejected"
    assert (
        history[0].error_code
        == "input_validation_failed"
    )
    assert history[0].output_payload is None


def test_runtime_rejects_remote_schema_reference_without_fetch(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.remote-ref",
        input_schema={
            "$ref": "https://example.invalid/schema.json"
        },
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": 1
        },
    )

    try:
        with pytest.raises(
            SkillSchemaError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                idempotency_key="remote-ref-1",
            )
    finally:
        executor.shutdown()

    invocation = _invocations(
        db_session
    )[0]

    assert invocation.status == "rejected"
    assert (
        invocation.error_code
        == "input_schema_invalid"
    )


def test_runtime_rejects_handler_not_in_allowlist(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.handler-allowlist",
    )
    registry = SkillHandlerRegistry()
    executor = BoundedSkillExecutor(
        max_workers=1
    )
    service = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        executor=executor,
    )

    try:
        with pytest.raises(
            SkillHandlerNotAllowedError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                idempotency_key="not-allowed-1",
            )
    finally:
        executor.shutdown()

    invocation = _invocations(
        db_session
    )[0]

    assert invocation.status == "rejected"
    assert (
        invocation.error_code
        == "handler_not_allowed"
    )


def test_runtime_sanitizes_handler_exception(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.handler-error",
    )

    def handler(_):
        raise RuntimeError(
            "SECRET_INTERNAL_DETAIL"
        )

    service, executor = _runtime(
        db_session,
        version=version,
        handler=handler,
    )

    try:
        with pytest.raises(
            SkillExecutionError
        ) as captured:
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                idempotency_key="error-1",
            )
    finally:
        executor.shutdown()

    assert (
        "SECRET_INTERNAL_DETAIL"
        not in str(captured.value)
    )

    invocation = _invocations(
        db_session
    )[0]

    assert invocation.status == "failed"
    assert (
        invocation.error_code
        == "execution_failed"
    )


def test_runtime_rejects_output_that_violates_schema(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.output-schema",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": str(payload["value"])
        },
    )

    try:
        with pytest.raises(
            SkillOutputValidationError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 5},
                idempotency_key="output-schema-1",
            )
    finally:
        executor.shutdown()

    invocation = _invocations(
        db_session
    )[0]

    assert invocation.status == "failed"
    assert (
        invocation.error_code
        == "output_validation_failed"
    )
    assert invocation.output_payload is None


def test_runtime_enforces_output_byte_limit(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.output-limit",
        output_schema={"type": "string"},
        max_output_bytes=1024,
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda _: "x" * 2048,
    )

    try:
        with pytest.raises(
            SkillOutputLimitError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                idempotency_key="output-limit-1",
            )
    finally:
        executor.shutdown()

    invocation = _invocations(
        db_session
    )[0]

    assert invocation.status == "failed"
    assert (
        invocation.error_code
        == "output_limit_or_json_invalid"
    )


def test_runtime_enforces_published_timeout(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.timeout",
        timeout_seconds=1,
    )

    def handler(payload):
        time.sleep(1.2)
        return {
            "result": payload["value"]
        }

    service, executor = _runtime(
        db_session,
        version=version,
        handler=handler,
        max_workers=1,
    )

    try:
        with pytest.raises(
            SkillExecutionTimeoutError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 8},
                idempotency_key="timeout-1",
            )
    finally:
        executor.shutdown()

    invocation = _invocations(
        db_session
    )[0]

    assert invocation.status == "timed_out"
    assert invocation.error_code == "timeout"
    assert invocation.finished_at is not None


def test_mutating_runtime_requires_idempotency_key(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.mutating-key",
        execution_mode="mutating",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"]
        },
    )

    try:
        with pytest.raises(
            SkillValidationError
        ):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
            )
    finally:
        executor.shutdown()

    assert _invocations(db_session) == []


def test_plugin_runtime_executes_only_registered_adapter(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.plugin-adapter",
        runtime_kind="plugin",
        handler_reference="example.plugin:run/summary",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"] + 10
        },
    )

    try:
        result = service.invoke(
            version.id,
            actor=_actor(),
            input_payload={"value": 2},
            idempotency_key="plugin-1",
        )
    finally:
        executor.shutdown()

    assert result.output == {
        "result": 12
    }


def test_bounded_executor_rejects_when_all_slots_are_occupied() -> None:
    executor = BoundedSkillExecutor(
        max_workers=1
    )
    entered = Event()
    release = Event()

    def blocking_handler(payload):
        entered.set()
        release.wait(timeout=2)
        return payload

    first_future = executor._executor.submit(
        lambda: None
    )
    first_future.result(timeout=1)

    acquired = executor._semaphore.acquire(
        blocking=False
    )
    assert acquired is True

    try:
        with pytest.raises(
            SkillRuntimeBusyError
        ):
            executor.execute(
                blocking_handler,
                {},
                timeout_seconds=1,
            )
    finally:
        executor._semaphore.release()
        release.set()
        executor.shutdown()



def test_runtime_replays_terminal_failure_without_executing_twice(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.failure-replay",
    )
    calls = {
        "count": 0
    }

    def handler(_):
        calls["count"] += 1
        raise RuntimeError(
            "internal"
        )

    service, executor = _runtime(
        db_session,
        version=version,
        handler=handler,
    )

    try:
        for _ in range(2):
            with pytest.raises(
                SkillExecutionError
            ):
                service.invoke(
                    version.id,
                    actor=_actor(),
                    input_payload={"value": 1},
                    idempotency_key="failure-replay-1",
                )
    finally:
        executor.shutdown()

    assert calls["count"] == 1
    assert len(_invocations(db_session)) == 1


def test_runtime_reports_equivalent_running_idempotency_as_in_progress(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.in-progress",
    )
    actor = _actor()
    payload, input_bytes = _canonical_json(
        {"value": 4},
        field_name="input_payload",
        max_bytes=64 * 1024,
    )
    invocation = SkillInvocation(
        skill_version_id=version.id,
        actor_type=actor.actor_type,
        actor_reference=actor.actor_reference,
        actor_user_id=actor.actor_user_id,
        idempotency_key="running-1",
        request_fingerprint=_fingerprint(
            version=version,
            actor=actor,
            normalized_input=payload,
        ),
        input_digest=_digest_bytes(
            input_bytes
        ),
        status="running",
        started_at=version.published_at,
    )
    db_session.add(invocation)
    db_session.commit()

    registry = SkillHandlerRegistry()
    executor = BoundedSkillExecutor(
        max_workers=1
    )
    service = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        executor=executor,
    )

    try:
        with pytest.raises(
            SkillInvocationInProgressError
        ):
            service.invoke(
                version.id,
                actor=actor,
                input_payload={"value": 4},
                idempotency_key="running-1",
            )
    finally:
        executor.shutdown()

    assert len(_invocations(db_session)) == 1

def test_runtime_history_is_newest_first_and_bounded(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.history",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"]
        },
    )

    try:
        for value in range(3):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": value},
                idempotency_key=f"history-{value}",
            )

        history = service.list_invocations(
            version.id,
            limit=2,
        )
    finally:
        executor.shutdown()

    assert len(history) == 2
    assert history[0].id > history[1].id


def test_contextless_fingerprint_remains_legacy_compatible(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.contextless-fingerprint",
    )
    actor = _actor()
    normalized_input = {"value": 3}

    legacy_envelope = {
        "actor": {
            "actor_reference": actor.actor_reference,
            "actor_type": actor.actor_type,
            "actor_user_id": actor.actor_user_id,
        },
        "input": normalized_input,
        "manifest_digest": version.manifest_digest,
        "skill_version_id": version.id,
    }
    expected = _digest_bytes(
        json.dumps(
            legacy_envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )

    assert _fingerprint(
        version=version,
        actor=actor,
        normalized_input=normalized_input,
    ) == expected


def test_contextful_fingerprint_binds_protocol_and_context_digest(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.context-fingerprint",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    actor = _actor()
    first = normalize_work_learning_runtime_context(
        _learning_context(version.id, memory_id=11),
        expected_skill_version_id=version.id,
    )
    second = normalize_work_learning_runtime_context(
        _learning_context(version.id, memory_id=21),
        expected_skill_version_id=version.id,
    )

    first_fingerprint = _fingerprint(
        version=version,
        actor=actor,
        normalized_input={"value": 3},
        runtime_context_protocol=first.protocol,
        runtime_context_digest=first.digest,
    )
    second_fingerprint = _fingerprint(
        version=version,
        actor=actor,
        normalized_input={"value": 3},
        runtime_context_protocol=second.protocol,
        runtime_context_digest=second.digest,
    )

    assert first_fingerprint != second_fingerprint


def test_runtime_context_requires_isolated_read_only_internal_python(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.context-isolated-required",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {"result": payload["value"]},
    )

    try:
        with pytest.raises(SkillValidationError):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                runtime_context=_learning_context(version.id),
            )
    finally:
        executor.shutdown()

    assert _invocations(db_session) == []


def test_runtime_context_requires_manifest_opt_in(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.context-manifest-required",
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {"result": payload["value"]},
    )

    try:
        with pytest.raises(SkillHandlerNotAllowedError):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                isolated=True,
                runtime_context=_learning_context(version.id),
            )
    finally:
        executor.shutdown()

    assert _invocations(db_session) == []


def test_runtime_context_rejects_mutating_version_before_invocation(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.context-mutating-rejected",
        execution_mode="mutating",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {"result": payload["value"]},
    )

    try:
        with pytest.raises(SkillHandlerNotAllowedError):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                idempotency_key="context-mutating-1",
                isolated=True,
                runtime_context=_learning_context(version.id),
            )
    finally:
        executor.shutdown()

    assert _invocations(db_session) == []


def test_runtime_context_requires_trusted_registry_dual_opt_in(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.context-registry-required",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    registry = SkillHandlerRegistry()
    registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=lambda payload: {"result": payload["value"]},
        trusted_for_autonomy=True,
        autonomy_entrypoint="isolated_skill_handlers:identity",
    )
    executor = BoundedSkillExecutor(max_workers=1)
    isolated_executor = IsolatedSkillExecutor(
        max_workers=1,
        kill_grace_seconds=1,
        python_path_entries=(
            str(Path(__file__).resolve().parent),
        ),
    )
    service = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        executor=executor,
        isolated_executor=isolated_executor,
    )

    try:
        with pytest.raises(SkillHandlerNotAllowedError):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 1},
                idempotency_key="context-registry-1",
                isolated=True,
                runtime_context=_learning_context(version.id),
            )
    finally:
        executor.shutdown()

    invocations = _invocations(db_session)
    assert len(invocations) == 1
    assert invocations[0].status == "rejected"
    assert invocations[0].error_code == "runtime_context_handler_not_allowed"


def test_runtime_executes_opted_in_isolated_context_handler_without_changing_input_digest(
    db_session: Session,
) -> None:
    output_schema = {
        "type": "object",
        "properties": {
            "result": {"type": "integer"},
            "context_protocol": {"type": "string"},
            "learning_signal": {"type": "string"},
            "context_items": {"type": "integer"},
        },
        "required": [
            "result",
            "context_protocol",
            "learning_signal",
            "context_items",
        ],
        "additionalProperties": False,
    }
    version = _published_version(
        db_session,
        skill_key="runtime.context-success",
        output_schema=output_schema,
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    isolated_executor = IsolatedSkillExecutor(
        max_workers=1,
        kill_grace_seconds=1,
        python_path_entries=(
            str(Path(__file__).resolve().parent),
        ),
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {"result": payload["value"]},
        trusted_for_autonomy=True,
        autonomy_entrypoint="isolated_skill_handlers:context_probe",
        runtime_context_protocol="work_learning_v1",
        isolated_executor=isolated_executor,
    )
    normalized_input, input_bytes = _canonical_json(
        {"value": 4},
        field_name="input_payload",
        max_bytes=64 * 1024,
    )

    try:
        result = service.invoke(
            version.id,
            actor=_actor(),
            input_payload=normalized_input,
            idempotency_key="context-success-1",
            isolated=True,
            runtime_context=_learning_context(version.id),
        )
    finally:
        executor.shutdown()

    assert result.output == {
        "result": 8,
        "context_protocol": "work_learning_v1",
        "learning_signal": "positive",
        "context_items": 1,
    }
    assert result.invocation.input_digest == _digest_bytes(input_bytes)
    assert result.invocation.status == "succeeded"


def test_runtime_idempotency_conflicts_when_context_digest_changes(
    db_session: Session,
) -> None:
    output_schema = {
        "type": "object",
        "properties": {
            "result": {"type": "integer"},
            "context_protocol": {"type": "string"},
            "learning_signal": {"type": "string"},
            "context_items": {"type": "integer"},
        },
        "required": [
            "result",
            "context_protocol",
            "learning_signal",
            "context_items",
        ],
        "additionalProperties": False,
    }
    version = _published_version(
        db_session,
        skill_key="runtime.context-idempotency",
        output_schema=output_schema,
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    isolated_executor = IsolatedSkillExecutor(
        max_workers=1,
        kill_grace_seconds=1,
        python_path_entries=(
            str(Path(__file__).resolve().parent),
        ),
    )
    service, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {"result": payload["value"]},
        trusted_for_autonomy=True,
        autonomy_entrypoint="isolated_skill_handlers:context_probe",
        runtime_context_protocol="work_learning_v1",
        isolated_executor=isolated_executor,
    )

    try:
        service.invoke(
            version.id,
            actor=_actor(),
            input_payload={"value": 4},
            idempotency_key="context-idempotency-1",
            isolated=True,
            runtime_context=_learning_context(version.id, memory_id=11),
        )
        with pytest.raises(SkillIdempotencyConflictError):
            service.invoke(
                version.id,
                actor=_actor(),
                input_payload={"value": 4},
                idempotency_key="context-idempotency-1",
                isolated=True,
                runtime_context=_learning_context(version.id, memory_id=21),
            )
    finally:
        executor.shutdown()

    assert len(_invocations(db_session)) == 1
