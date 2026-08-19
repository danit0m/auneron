from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.skill_errors import SkillExecutionError
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.skill_runtime import BoundedSkillExecutor
from app.services.skill_runtime import SkillHandlerRegistry
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import SkillRuntimeService
from app.services.skill_service import SkillService


def _published_version(
    db_session: Session,
    *,
    skill_key: str,
):
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Runtime transaction",
        description="Skill para testes transacionais do 23C.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.runtime:transaction_test"
        ),
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(
        draft.id
    ).version


def test_failed_handler_leaves_terminal_audit_and_session_usable(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.transaction-failure",
    )
    registry = SkillHandlerRegistry()

    def failing_handler(_):
        raise ValueError(
            "internal detail"
        )

    registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=failing_handler,
    )
    executor = BoundedSkillExecutor(
        max_workers=1
    )
    runtime = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        executor=executor,
    )

    try:
        with pytest.raises(
            SkillExecutionError
        ):
            runtime.invoke(
                version.id,
                actor=SkillInvocationActor(
                    actor_type="system",
                    actor_reference="transaction-test",
                ),
                input_payload={},
                idempotency_key="failure-1",
            )
    finally:
        executor.shutdown()

    invocation = (
        db_session.query(
            SkillInvocation
        ).one()
    )

    assert invocation.status == "failed"
    assert invocation.error_code == "execution_failed"

    assert (
        runtime.get_invocation(
            invocation.id
        ).id
        == invocation.id
    )


def test_invocation_history_restricts_version_delete(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.version-retention",
    )
    invocation = SkillInvocation(
        skill_version_id=version.id,
        actor_type="system",
        actor_reference="retention-test",
        actor_user_id=None,
        idempotency_key="retention-1",
        request_fingerprint="e" * 64,
        input_digest="f" * 64,
        status="succeeded",
        output_payload={"value": {}},
        output_digest="a" * 64,
        output_bytes=2,
        error_code=None,
        duration_ms=1,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(invocation)
    db_session.commit()

    db_session.delete(version)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()

    assert (
        db_session.get(
            SkillInvocation,
            invocation.id,
        )
        is not None
    )


def test_actor_user_delete_nulls_attribution_but_preserves_history(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="runtime.actor-retention",
    )
    user = User(
        name="Runtime Actor",
        email="runtime.actor@example.com",
        password_hash=hash_password(
            "Runtime-Test-Password-123!"
        ),
        role="developer",
        active=True,
    )
    db_session.add(user)
    db_session.commit()

    invocation = SkillInvocation(
        skill_version_id=version.id,
        actor_type="user",
        actor_reference="runtime.actor@example.com",
        actor_user_id=user.id,
        idempotency_key="actor-retention-1",
        request_fingerprint="1" * 64,
        input_digest="2" * 64,
        status="succeeded",
        output_payload={"value": {}},
        output_digest="3" * 64,
        output_bytes=2,
        error_code=None,
        duration_ms=1,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(invocation)
    db_session.commit()

    invocation_id = invocation.id
    db_session.delete(user)
    db_session.commit()
    db_session.expire_all()

    persisted = db_session.get(
        SkillInvocation,
        invocation_id,
    )

    assert persisted is not None
    assert persisted.actor_user_id is None
    assert (
        persisted.actor_reference
        == "runtime.actor@example.com"
    )
