import inspect
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.skill import SkillInvocation
from app.repositories.skill_repository import SkillRepository
from app.services.skill_runtime import SkillRuntimeService
from app.services.skill_service import SkillService


def _version(
    db_session: Session,
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key="maintenance.stale-runtime",
        provider="auneron.core",
        display_name="Stale runtime maintenance",
        description=(
            "Skill usada para testar recuperação 23E."
        ),
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.maintenance:stale_test"
        ),
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        timeout_seconds=300,
    )
    return service.publish_version(
        draft.id
    ).version


def _running(
    db_session: Session,
    *,
    version_id: int,
    started_at: datetime,
    key: str,
) -> SkillInvocation:
    invocation = SkillInvocation(
        skill_version_id=version_id,
        actor_type="system",
        actor_reference="maintenance-test",
        actor_user_id=None,
        idempotency_key=key,
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        status="running",
        output_payload=None,
        output_digest=None,
        output_bytes=None,
        error_code=None,
        duration_ms=None,
        started_at=started_at,
        finished_at=None,
    )
    db_session.add(
        invocation
    )
    db_session.commit()
    return invocation


def test_stale_recovery_terminalizes_only_old_running_rows(
    db_session: Session,
) -> None:
    version = _version(
        db_session
    )
    now = datetime.now(
        timezone.utc
    )
    old = _running(
        db_session,
        version_id=version.id,
        started_at=now - timedelta(
            seconds=700
        ),
        key="stale-old",
    )
    fresh = _running(
        db_session,
        version_id=version.id,
        started_at=now - timedelta(
            seconds=100
        ),
        key="stale-fresh",
    )

    recovered = SkillRuntimeService(
        db_session
    ).recover_stale_invocations(
        now=now,
        stale_after_seconds=600,
        limit=10,
    )

    assert [
        item.id
        for item in recovered
    ] == [old.id]

    db_session.refresh(old)
    db_session.refresh(fresh)

    assert old.status == "failed"
    assert old.error_code == (
        "stale_running_recovered"
    )
    assert old.finished_at == now
    assert old.duration_ms == 700000
    assert old.output_payload is None

    assert fresh.status == "running"
    assert fresh.finished_at is None


def test_stale_recovery_never_reexecutes_handler(
    db_session: Session,
) -> None:
    version = _version(
        db_session
    )
    now = datetime.now(
        timezone.utc
    )
    invocation = _running(
        db_session,
        version_id=version.id,
        started_at=now - timedelta(
            seconds=800
        ),
        key="stale-no-replay",
    )

    runtime = SkillRuntimeService(
        db_session
    )
    recovered = runtime.recover_stale_invocations(
        now=now,
        stale_after_seconds=600,
    )

    assert recovered[0].id == invocation.id
    assert recovered[0].status == "failed"


def test_repository_stale_recovery_uses_skip_locked() -> None:
    source = inspect.getsource(
        SkillRepository
        .lock_stale_running_invocations
    )

    assert ".with_for_update(skip_locked=True)" in source
    assert 'SkillInvocation.status == "running"' in source
