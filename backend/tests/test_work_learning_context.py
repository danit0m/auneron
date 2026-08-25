from dataclasses import asdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkValidationError
from app.models.user import User
from app.repositories.work_learning_context_repository import (
    WorkLearningContextCandidate,
)
from app.services.work_learning_context import WorkLearningContextService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25b-service",
)


class RecordingRepository:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.calls = []

    def list_outcome_candidates(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.candidates)


def _developer(db: Session) -> User:
    user = User(
        name="Learning Context Developer",
        email="learning.service.developer@example.com",
        password_hash="not-used",
        role="developer",
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _global_work(db: Session):
    return WorkManagerService(db).create(
        work_type="task",
        title="Learning Context Target",
        work_key="learning.context.service.target",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25b-service",
        actor=SYSTEM_ACTOR,
    ).work_item


def test_resolver_returns_only_safe_bounded_metadata(
    db_session: Session,
) -> None:
    authority = _developer(db_session)
    target = _global_work(db_session)
    now = datetime.now(timezone.utc)
    candidates = [
        WorkLearningContextCandidate(
            memory_id=index,
            source_work_item_id=100 + index,
            work_skill_execution_id=200 + index,
            skill_version_id=300,
            terminal_status="failed",
            evaluation_code="execution_failed",
            learning_signal="negative",
            observed_at=now - timedelta(seconds=index),
        )
        for index in range(1, 5)
    ]
    repository = RecordingRepository(candidates)
    service = WorkLearningContextService(
        db_session,
        repository=repository,
    )

    before_version = target.version
    items = service.resolve(
        target.id,
        skill_version_id=300,
        authority_user_id=authority.id,
        limit=2,
        as_of=now,
    )

    assert len(items) == 2
    assert repository.calls == [{
        "target_work_item_id": target.id,
        "skill_version_id": 300,
        "scope_type": "global",
        "account_id": None,
        "subject_user_id": None,
        "as_of": now,
        "limit": 2,
    }]

    expected_fields = {
        "memory_id",
        "source_work_item_id",
        "work_skill_execution_id",
        "skill_version_id",
        "terminal_status",
        "evaluation_code",
        "learning_signal",
        "observed_at",
    }
    assert set(asdict(items[0])) == expected_fields
    serialized = repr(items)
    for forbidden in (
        "input_payload",
        "output_payload",
        "memory_content",
        "context_data",
        "evidence_text",
        "dispatch_key",
        "approval_payload",
        "actor_reference",
    ):
        assert forbidden not in serialized

    db_session.refresh(target)
    assert target.version == before_version
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_item_id", 0),
        ("skill_version_id", 0),
        ("authority_user_id", 0),
        ("limit", 0),
        ("limit", 11),
    ],
)
def test_resolver_rejects_invalid_bounded_inputs(
    db_session: Session,
    field: str,
    value: int,
) -> None:
    authority = _developer(db_session)
    target = _global_work(db_session)
    payload = {
        "work_item_id": target.id,
        "skill_version_id": 10,
        "authority_user_id": authority.id,
        "limit": 5,
    }
    payload[field] = value

    with pytest.raises(WorkValidationError):
        WorkLearningContextService(
            db_session,
            repository=RecordingRepository([]),
        ).resolve(
            payload.pop("work_item_id"),
            **payload,
        )


def test_resolver_rejects_naive_as_of(
    db_session: Session,
) -> None:
    authority = _developer(db_session)
    target = _global_work(db_session)

    with pytest.raises(WorkValidationError):
        WorkLearningContextService(
            db_session,
            repository=RecordingRepository([]),
        ).resolve(
            target.id,
            skill_version_id=10,
            authority_user_id=authority.id,
            as_of=datetime(2026, 8, 25, 12, 0, 0),
        )
