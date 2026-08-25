from sqlalchemy.orm import Session

import pytest

from app.models.approval import ApprovalDecision
from app.models.user import User
from app.repositories.approval_repository import ApprovalRepository
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.skill_service import SkillService


def _user(
    db_session: Session,
) -> User:
    user = User(
        name="Decisor Transacional",
        email="approval.transaction@example.com",
        password_hash="not-used-by-approval-tests",
        role="manager",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _published_version(
    db_session: Session,
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key="approval.transaction",
        provider="auneron.core",
        display_name="Approval transaction",
        description="Skill para teste transacional de aprovação.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.approval:transaction_test"
        ),
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(
        draft.id
    ).version


def test_decision_failure_rolls_back_terminal_mutation(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decider = _user(
        db_session
    )
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    service = ApprovalService(
        db_session,
        repository=repository,
    )

    created = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="system",
            actor_reference="system:transaction",
        ),
        input_payload={},
        idempotency_key="transaction-1",
    )

    def fail_add_decision(
        _: ApprovalDecision,
    ) -> ApprovalDecision:
        raise RuntimeError(
            "forced decision persistence failure"
        )

    monkeypatch.setattr(
        repository,
        "add_decision",
        fail_add_decision,
    )

    with pytest.raises(
        RuntimeError,
        match="forced decision persistence failure",
    ):
        service.decide(
            created.request.id,
            decider_user_id=decider.id,
            decision="approved",
        )

    db_session.expire_all()
    persisted = service.get_request(
        created.request.id
    )
    assert persisted.status == "pending"
    assert persisted.resolved_at is None
    assert (
        db_session.query(
            ApprovalDecision
        ).count()
        == 0
    )


def test_service_session_remains_usable_after_rollback(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decider = _user(
        db_session
    )
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    service = ApprovalService(
        db_session,
        repository=repository,
    )

    first = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="system",
            actor_reference="system:rollback",
        ),
        input_payload={"value": 1},
        idempotency_key="rollback-1",
    )

    original = repository.add_decision

    def fail_once(
        decision: ApprovalDecision,
    ) -> ApprovalDecision:
        monkeypatch.setattr(
            repository,
            "add_decision",
            original,
        )
        raise RuntimeError(
            "forced once"
        )

    monkeypatch.setattr(
        repository,
        "add_decision",
        fail_once,
    )

    with pytest.raises(
        RuntimeError,
        match="forced once",
    ):
        service.decide(
            first.request.id,
            decider_user_id=decider.id,
            decision="approved",
        )

    second = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="system",
            actor_reference="system:rollback",
        ),
        input_payload={"value": 2},
        idempotency_key="rollback-2",
    )

    decided = service.decide(
        second.request.id,
        decider_user_id=decider.id,
        decision="approved",
    )
    assert decided.request.status == "approved"
