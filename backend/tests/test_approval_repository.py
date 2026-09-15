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


def _pending_request(
    version_id: int,
    *,
    idempotency_key: str,
    required_permission: str = "approval:decide",
    risk_level: str = "low",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    effective_created_at = (
        created_at
        if created_at is not None
        else datetime.now(timezone.utc)
    )
    return ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=version_id,
        requester_actor_type="system",
        requester_reference="system:approval-count-test",
        requester_user_id=None,
        idempotency_key=idempotency_key,
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        risk_level=risk_level,
        required_permission=required_permission,
        status="pending",
        target_account_id=None,
        target_user_id=None,
        expires_at=(
            expires_at
            if expires_at is not None
            else effective_created_at
            + timedelta(hours=1)
        ),
        resolved_at=None,
        created_at=effective_created_at,
    )


def test_count_requests_matches_list_requests_length_for_same_filters(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    for index in range(3):
        repository.add_request(
            _pending_request(
                version.id,
                idempotency_key=(
                    f"count-parity-{index}"
                ),
            )
        )
    db_session.commit()

    count = repository.count_requests(
        statuses=("pending",),
        required_permissions=(
            "approval:decide",
        ),
    )
    listed = repository.list_requests(
        statuses=("pending",),
        required_permissions=(
            "approval:decide",
        ),
        limit=51,
    )

    assert count == len(listed) == 3


def test_count_requests_excludes_expired_pending(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    now = datetime.now(timezone.utc)
    repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="count-expired-1",
            created_at=now
            - timedelta(hours=2),
            expires_at=now
            - timedelta(hours=1),
        )
    )
    db_session.commit()

    count = repository.count_requests(
        statuses=("pending",),
        required_permissions=(
            "approval:decide",
        ),
        now=now,
    )

    assert count == 0


def test_list_requests_excludes_expired_pending_when_status_filter_includes_pending(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    now = datetime.now(timezone.utc)
    repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="list-expired-1",
            created_at=now
            - timedelta(hours=2),
            expires_at=now
            - timedelta(hours=1),
        )
    )
    db_session.commit()

    listed = repository.list_requests(
        statuses=("pending",),
        required_permissions=(
            "approval:decide",
        ),
        limit=51,
        now=now,
    )

    assert listed == []


def test_count_requests_does_not_filter_expiry_for_non_pending_statuses(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    now = datetime.now(timezone.utc)
    request = repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="count-approved-old",
            created_at=now
            - timedelta(days=40),
            expires_at=now
            - timedelta(days=39),
        )
    )
    request.status = "approved"
    request.resolved_at = (
        now - timedelta(days=39)
    )
    db_session.commit()

    count = repository.count_requests(
        statuses=("approved",),
        required_permissions=(
            "approval:decide",
        ),
        now=now,
    )

    assert count == 1


def test_list_requests_mixed_status_filter_only_excludes_expired_pending_rows(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    now = datetime.now(timezone.utc)

    expired_pending = repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="mixed-pending-expired",
            created_at=now
            - timedelta(hours=2),
            expires_at=now
            - timedelta(hours=1),
        )
    )
    old_approved = repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="mixed-approved-old",
            created_at=now
            - timedelta(days=40),
            expires_at=now
            - timedelta(days=39),
        )
    )
    old_approved.status = "approved"
    old_approved.resolved_at = (
        now - timedelta(days=39)
    )
    db_session.commit()

    listed_ids = {
        request.id
        for request in repository.list_requests(
            statuses=(
                "pending",
                "approved",
            ),
            required_permissions=(
                "approval:decide",
            ),
            limit=51,
            now=now,
        )
    }

    assert (
        expired_pending.id
        not in listed_ids
    )
    assert old_approved.id in listed_ids


def test_count_requests_zero_when_no_matching_requests(
    db_session: Session,
) -> None:
    repository = ApprovalRepository(
        db_session
    )

    count = repository.count_requests(
        statuses=("pending",),
        required_permissions=(
            "approval:decide",
        ),
    )

    assert count == 0


def test_count_requests_respects_required_permissions_scope(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="scope-decide-1",
            required_permission=(
                "approval:decide"
            ),
        )
    )
    repository.add_request(
        _pending_request(
            version.id,
            idempotency_key=(
                "scope-sensitive-1"
            ),
            required_permission=(
                "approval:decide_sensitive"
            ),
        )
    )
    db_session.commit()

    count = repository.count_requests(
        statuses=("pending",),
        required_permissions=(
            "approval:decide",
        ),
    )

    assert count == 1


def test_count_requests_returns_zero_for_empty_required_permissions(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    repository = ApprovalRepository(
        db_session
    )
    repository.add_request(
        _pending_request(
            version.id,
            idempotency_key="scope-empty-1",
        )
    )
    db_session.commit()

    count = repository.count_requests(
        statuses=("pending",),
        required_permissions=(),
    )

    assert count == 0
