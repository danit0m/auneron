from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.skill import SkillInvocation
from app.repositories.skill_repository import SkillRepository
from app.services.skill_service import SkillService


def _published_version(
    db_session: Session,
):
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key="runtime.repository-test",
        provider="auneron.core",
        display_name="Runtime repository",
        description="Skill para validar repository do runtime.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.runtime:repository_test"
        ),
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(
        draft.id
    ).version


def _invocation(
    version_id: int,
    *,
    key: str,
    reference: str = "repository-test",
) -> SkillInvocation:
    return SkillInvocation(
        skill_version_id=version_id,
        actor_type="system",
        actor_reference=reference,
        actor_user_id=None,
        idempotency_key=key,
        request_fingerprint="c" * 64,
        input_digest="d" * 64,
        status="running",
        output_payload=None,
        output_digest=None,
        output_bytes=None,
        error_code=None,
        duration_ms=None,
        started_at=datetime.now(timezone.utc),
        finished_at=None,
    )


def test_repository_adds_and_gets_invocation(
    db_session: Session,
) -> None:
    version = _published_version(db_session)
    repository = SkillRepository(db_session)
    invocation = _invocation(
        version.id,
        key="repository-add",
    )

    persisted = repository.add_invocation(
        invocation
    )
    db_session.commit()

    assert persisted.id is not None
    assert repository.get_invocation(
        persisted.id
    ) is persisted


def test_repository_finds_idempotent_invocation(
    db_session: Session,
) -> None:
    version = _published_version(db_session)
    repository = SkillRepository(db_session)
    invocation = _invocation(
        version.id,
        key="repository-find",
    )
    repository.add_invocation(invocation)
    db_session.commit()

    found = (
        repository.find_invocation_by_idempotency(
            version_id=version.id,
            actor_type="system",
            actor_reference="repository-test",
            idempotency_key="repository-find",
        )
    )

    assert found is not None
    assert found.id == invocation.id


def test_repository_idempotency_scope_includes_actor(
    db_session: Session,
) -> None:
    version = _published_version(db_session)
    repository = SkillRepository(db_session)

    first = _invocation(
        version.id,
        key="scoped-key",
        reference="actor-a",
    )
    second = _invocation(
        version.id,
        key="scoped-key",
        reference="actor-b",
    )

    repository.add_invocation(first)
    repository.add_invocation(second)
    db_session.commit()

    found_a = (
        repository.find_invocation_by_idempotency(
            version_id=version.id,
            actor_type="system",
            actor_reference="actor-a",
            idempotency_key="scoped-key",
        )
    )
    found_b = (
        repository.find_invocation_by_idempotency(
            version_id=version.id,
            actor_type="system",
            actor_reference="actor-b",
            idempotency_key="scoped-key",
        )
    )

    assert found_a is not None
    assert found_b is not None
    assert found_a.id != found_b.id


def test_repository_lists_invocations_newest_first(
    db_session: Session,
) -> None:
    version = _published_version(db_session)
    repository = SkillRepository(db_session)

    for index in range(3):
        repository.add_invocation(
            _invocation(
                version.id,
                key=f"history-{index}",
            )
        )
        db_session.commit()

    history = repository.list_invocations_for_version(
        version.id,
        limit=2,
    )

    assert len(history) == 2
    assert history[0].id > history[1].id


def test_repository_locks_invocation(
    db_session: Session,
) -> None:
    version = _published_version(db_session)
    repository = SkillRepository(db_session)
    invocation = _invocation(
        version.id,
        key="repository-lock",
    )
    repository.add_invocation(invocation)
    db_session.commit()

    locked = repository.lock_invocation(
        invocation.id
    )

    assert locked is not None
    assert locked.id == invocation.id
