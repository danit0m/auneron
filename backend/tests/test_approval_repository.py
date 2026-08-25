import inspect
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.repositories.approval_repository import ApprovalRepository
from app.services.skill_service import SkillService


def _published_version(
    db_session: Session,
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key="approval.repository-test",
        provider="auneron.core",
        display_name="Approval repository",
        description="Skill para testes do repositório de aprovação.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.approval:repository_test"
        ),
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(
        draft.id
    ).version


def _request(
    version_id: int,
) -> ApprovalRequest:
    now = datetime.now(
        timezone.utc
    )
    return ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=version_id,
        requester_actor_type="system",
        requester_reference="system:approval-test",
        requester_user_id=None,
        idempotency_key="repository-1",
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        risk_level="low",
        required_permission="approval:decide",
        status="pending",
        target_account_id=None,
        target_user_id=None,
        expires_at=(
            now
            + timedelta(hours=1)
        ),
        resolved_at=None,
        created_at=now,
    )


def test_repository_roundtrip_and_lock(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    request = repository.add_request(
        _request(version.id)
    )
    db_session.commit()

    assert (
        repository.get_request(
            request.id
        ).id
        == request.id
    )
    assert (
        repository.lock_request(
            request.id
        ).id
        == request.id
    )
    assert (
        repository.find_request_by_idempotency(
            requester_actor_type="system",
            requester_reference="system:approval-test",
            idempotency_key="repository-1",
        ).id
        == request.id
    )


def test_repository_persists_one_decision(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    request = repository.add_request(
        _request(version.id)
    )
    db_session.flush()

    decision = repository.add_decision(
        ApprovalDecision(
            approval_request_id=request.id,
            decision="approved",
            decided_by_user_id=None,
            decided_by_reference="user:deleted",
            decided_by_role="manager",
            permission_used="approval:decide",
            decision_note=None,
            created_at=datetime.now(
                timezone.utc
            ),
        )
    )
    db_session.commit()

    assert (
        repository.get_decision(
            request.id
        ).id
        == decision.id
    )


def test_repository_does_not_own_transactions() -> None:
    source = inspect.getsource(
        ApprovalRepository
    )

    for forbidden in (
        ".commit(",
        ".rollback(",
        ".begin(",
        ".begin_nested(",
    ):
        assert forbidden not in source
