from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalElevationRequiredError
from app.core.approval_errors import ApprovalExpiredError
from app.core.approval_errors import ApprovalIdempotencyConflictError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService


def _user(
    db_session: Session,
    *,
    email: str,
    role: str,
) -> User:
    user = User(
        name="Usuário de Aprovação",
        email=email,
        password_hash="not-used-by-approval-tests",
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _published_version(
    db_session: Session,
    *,
    skill_key: str,
    execution_mode: str = "read_only",
    capabilities: tuple[
        CapabilityInput,
        ...,
    ] = (),
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Approval service",
        description="Skill para testes do serviço de aprovação.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.approval:service_test"
        ),
        execution_mode=execution_mode,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(
        draft.id,
        capabilities=capabilities,
    ).version


def test_create_request_binds_exact_action_without_runtime_execution(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="approval.foundation-read",
    )
    service = ApprovalService(
        db_session
    )

    result = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="system",
            actor_reference="system:planner",
        ),
        input_payload={
            "query": "saldo",
            "nested": {
                "value": 1,
            },
        },
        idempotency_key="approval-read-1",
    )

    assert result.duplicate is False
    assert result.request.status == "pending"
    assert result.request.risk_level == "low"
    assert (
        result.request.required_permission
        == "approval:decide"
    )
    assert len(result.request.input_digest) == 64
    assert len(result.request.request_fingerprint) == 64
    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )
    assert (
        db_session.query(
            ApprovalDecision
        ).count()
        == 0
    )
    assert not hasattr(
        result.request,
        "input_payload",
    )


def test_create_request_is_idempotent_and_conflict_safe(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="approval.idempotency",
    )
    service = ApprovalService(
        db_session
    )
    requester = ApprovalRequester(
        actor_type="agent",
        actor_reference="agent:finance",
    )

    first = service.create_skill_execution_request(
        version_id=version.id,
        requester=requester,
        input_payload={"value": 1},
        idempotency_key="same-key",
    )
    duplicate = service.create_skill_execution_request(
        version_id=version.id,
        requester=requester,
        input_payload={"value": 1},
        idempotency_key="same-key",
    )

    assert duplicate.duplicate is True
    assert (
        duplicate.request.id
        == first.request.id
    )

    with pytest.raises(
        ApprovalIdempotencyConflictError
    ):
        service.create_skill_execution_request(
            version_id=version.id,
            requester=requester,
            input_payload={"value": 2},
            idempotency_key="same-key",
        )


def test_mutating_request_requires_separate_human_decider(
    db_session: Session,
) -> None:
    requester_user = _user(
        db_session,
        email="requester.manager@example.com",
        role="manager",
    )
    other_manager = _user(
        db_session,
        email="decider.manager@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="approval.mutating",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="accounts.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    service = ApprovalService(
        db_session
    )

    created = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="user",
            actor_reference=(
                f"user:{requester_user.id}"
            ),
            actor_user_id=requester_user.id,
        ),
        input_payload={"value": 1},
        idempotency_key="mutating-1",
    )

    assert created.request.risk_level == "high"

    with pytest.raises(
        ApprovalAuthorizationError
    ):
        service.decide(
            created.request.id,
            decider_user_id=requester_user.id,
            decision="approved",
        )

    decided = service.decide(
        created.request.id,
        decider_user_id=other_manager.id,
        decision="approved",
        decision_note="Alteração revisada.",
    )

    assert decided.request.status == "approved"
    assert decided.decision.decision == "approved"
    assert (
        decided.decision.permission_used
        == "approval:decide"
    )


def test_external_request_requires_sensitive_approval_permission(
    db_session: Session,
) -> None:
    manager = _user(
        db_session,
        email="approval.manager@example.com",
        role="manager",
    )
    executive = _user(
        db_session,
        email="approval.executive@example.com",
        role="executive",
    )
    version = _published_version(
        db_session,
        skill_key="approval.external",
        execution_mode="external",
        capabilities=(
            CapabilityInput(
                capability_key="crm.sync",
                access_mode="execute",
                resource_scope="external",
            ),
        ),
    )
    service = ApprovalService(
        db_session
    )

    created = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="integration",
            actor_reference="integration:crm",
        ),
        input_payload={"operation": "sync"},
        idempotency_key="external-1",
    )

    assert created.request.risk_level == "critical"
    assert (
        created.request.required_permission
        == "approval:decide_sensitive"
    )

    with pytest.raises(
        ApprovalAuthorizationError
    ):
        service.decide(
            created.request.id,
            decider_user_id=manager.id,
            decision="approved",
        )

    with pytest.raises(
        ApprovalElevationRequiredError
    ):
        service.decide(
            created.request.id,
            decider_user_id=executive.id,
            decision="approved",
        )

    decided = service.decide(
        created.request.id,
        decider_user_id=executive.id,
        decision="approved",
        sensitive_elevation_verified=True,
    )

    assert decided.request.status == "approved"
    assert (
        decided.decision.permission_used
        == "approval:decide_sensitive"
    )
    assert (
        decided.decision.sensitive_elevation_verified
        is True
    )



def test_expired_request_becomes_terminal_without_decision(
    db_session: Session,
) -> None:
    manager = _user(
        db_session,
        email="expiry.manager@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="approval.expiry",
    )
    service = ApprovalService(
        db_session
    )
    now = datetime.now(
        timezone.utc
    )

    created = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="system",
            actor_reference="system:expiry",
        ),
        input_payload={},
        idempotency_key="expiry-1",
        now=now,
    )

    with pytest.raises(
        ApprovalExpiredError
    ):
        service.decide(
            created.request.id,
            decider_user_id=manager.id,
            decision="approved",
            now=(
                created.request.expires_at
                + timedelta(seconds=1)
            ),
        )

    db_session.expire_all()
    persisted = db_session.get(
        ApprovalRequest,
        created.request.id,
    )
    assert persisted is not None
    assert persisted.status == "expired"
    assert persisted.resolved_at is not None
    assert (
        db_session.query(
            ApprovalDecision
        ).count()
        == 0
    )


def test_terminal_decision_is_immutable(
    db_session: Session,
) -> None:
    manager = _user(
        db_session,
        email="terminal.manager@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="approval.terminal",
    )
    service = ApprovalService(
        db_session
    )

    created = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="agent",
            actor_reference="agent:terminal",
        ),
        input_payload={},
        idempotency_key="terminal-1",
    )
    service.decide(
        created.request.id,
        decider_user_id=manager.id,
        decision="rejected",
    )

    with pytest.raises(
        ApprovalStateError
    ):
        service.decide(
            created.request.id,
            decider_user_id=manager.id,
            decision="approved",
        )

    assert (
        db_session.query(
            ApprovalDecision
        ).count()
        == 1
    )


def test_non_user_requester_cannot_spoof_user_identity(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="approval.actor-boundary",
    )
    service = ApprovalService(
        db_session
    )

    with pytest.raises(
        ApprovalValidationError
    ):
        service.create_skill_execution_request(
            version_id=version.id,
            requester=ApprovalRequester(
                actor_type="agent",
                actor_reference="agent:spoof",
                actor_user_id=99,
            ),
            input_payload={},
            idempotency_key="spoof-1",
        )
