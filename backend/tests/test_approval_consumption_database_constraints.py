from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.skill_service import SkillService


def _chain(
    db_session: Session,
):
    user = User(
        name="Consumption DB",
        email="consumption.db@example.com",
        password_hash="not-used",
        role="manager",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    skills = SkillService(
        db_session
    )
    skill = skills.register_skill(
        skill_key="consumption.db",
        provider="auneron.core",
        display_name="Consumption DB",
        description="Constraints 24D",
    )
    draft = skills.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.consumption:db",
        execution_mode="mutating",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    version = skills.publish_version(
        draft.id
    ).version

    approvals = ApprovalService(
        db_session
    )
    created = approvals.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type="agent",
            actor_reference="agent:db",
        ),
        input_payload={},
        idempotency_key="consumption-db-approval",
    )
    decided = approvals.decide(
        created.request.id,
        decider_user_id=user.id,
        decision="approved",
    )

    now = datetime.now(
        timezone.utc
    )
    invocation = SkillInvocation(
        skill_version_id=version.id,
        actor_type="agent",
        actor_reference="agent:db",
        actor_user_id=None,
        idempotency_key=(
            f"approval:{created.request.id}"
        ),
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        status="succeeded",
        output_payload={
            "value": {},
        },
        output_digest="c" * 64,
        output_bytes=2,
        error_code=None,
        duration_ms=1,
        started_at=now,
        finished_at=now,
    )
    db_session.add(invocation)
    db_session.commit()
    db_session.refresh(invocation)

    return (
        user,
        created.request,
        decided.decision,
        invocation,
    )


def _consumption(
    *,
    user: User,
    request: ApprovalRequest,
    decision: ApprovalDecision,
    invocation: SkillInvocation | None,
    status: str,
) -> ApprovalConsumption:
    now = datetime.now(
        timezone.utc
    )
    return ApprovalConsumption(
        approval_request_id=request.id,
        approval_decision_id=decision.id,
        skill_invocation_id=(
            invocation.id
            if invocation is not None
            else None
        ),
        consumer_actor_type="agent",
        consumer_reference="agent:db",
        authority_user_id=user.id,
        authority_reference=f"user:{user.id}",
        authority_role=user.role,
        runtime_idempotency_key=(
            f"approval:{request.id}"
        ),
        request_fingerprint=request.request_fingerprint,
        input_digest=request.input_digest,
        status=status,
        error_code=(
            "preflight_failed"
            if status == "failed"
            else None
        ),
        reserved_at=now,
        finalized_at=(
            now
            if status in {
                "consumed",
                "failed",
            }
            else None
        ),
    )


def test_sensitive_elevation_evidence_defaults_false(
    db_session: Session,
) -> None:
    _, _, decision, _ = _chain(
        db_session
    )
    db_session.refresh(
        decision
    )
    assert (
        decision.sensitive_elevation_verified
        is False
    )


def test_database_rejects_user_as_consumption_actor(
    db_session: Session,
) -> None:
    user, request, decision, _ = _chain(
        db_session
    )
    item = _consumption(
        user=user,
        request=request,
        decision=decision,
        invocation=None,
        status="reserved",
    )
    item.consumer_actor_type = "user"
    db_session.add(item)

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_database_rejects_invalid_consumption_state_shape(
    db_session: Session,
) -> None:
    user, request, decision, invocation = _chain(
        db_session
    )
    item = _consumption(
        user=user,
        request=request,
        decision=decision,
        invocation=invocation,
        status="reserved",
    )
    db_session.add(item)

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_database_allows_only_one_consumption_per_approval(
    db_session: Session,
) -> None:
    user, request, decision, _ = _chain(
        db_session
    )
    first = _consumption(
        user=user,
        request=request,
        decision=decision,
        invocation=None,
        status="reserved",
    )
    db_session.add(first)
    db_session.commit()

    duplicate = _consumption(
        user=user,
        request=request,
        decision=decision,
        invocation=None,
        status="reserved",
    )
    db_session.add(duplicate)

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_consumed_approval_restricts_invocation_delete(
    db_session: Session,
) -> None:
    user, request, decision, invocation = _chain(
        db_session
    )
    item = _consumption(
        user=user,
        request=request,
        decision=decision,
        invocation=invocation,
        status="consumed",
    )
    db_session.add(item)
    db_session.commit()

    db_session.delete(
        invocation
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()
