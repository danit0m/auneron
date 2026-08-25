from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.user import User
from app.services.skill_service import SkillService


def _published_version(
    db_session: Session,
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key="approval.database",
        provider="auneron.core",
        display_name="Approval database",
        description="Skill para testes de constraints de aprovação.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.approval:database_test"
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
    *,
    suffix: str,
    **overrides,
) -> ApprovalRequest:
    now = datetime.now(
        timezone.utc
    )
    values = {
        "action_type": "skill_execution",
        "skill_version_id": version_id,
        "requester_actor_type": "system",
        "requester_reference": f"system:{suffix}",
        "requester_user_id": None,
        "idempotency_key": f"db-{suffix}",
        "request_fingerprint": "a" * 64,
        "input_digest": "b" * 64,
        "risk_level": "low",
        "required_permission": "approval:decide",
        "status": "pending",
        "target_account_id": None,
        "target_user_id": None,
        "expires_at": now + timedelta(hours=1),
        "resolved_at": None,
        "created_at": now,
    }
    values.update(
        overrides
    )
    return ApprovalRequest(
        **values
    )


def test_database_rejects_invalid_risk_level(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    db_session.add(
        _request(
            version.id,
            suffix="risk",
            risk_level="unbounded",
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_database_rejects_non_user_requester_with_user_id(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    user = User(
        name="Usuário Constraint",
        email="approval.constraint@example.com",
        password_hash="not-used-by-approval-tests",
        role="manager",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    db_session.add(
        _request(
            version.id,
            suffix="actor",
            requester_actor_type="agent",
            requester_user_id=user.id,
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_database_rejects_terminal_status_without_resolution(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    db_session.add(
        _request(
            version.id,
            suffix="terminal",
            status="approved",
            resolved_at=None,
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_database_allows_only_one_decision_per_request(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    now = datetime.now(
        timezone.utc
    )
    request = _request(
        version.id,
        suffix="decision",
        status="approved",
        resolved_at=now,
        created_at=(
            now - timedelta(minutes=1)
        ),
        expires_at=(
            now + timedelta(hours=1)
        ),
    )
    db_session.add(request)
    db_session.commit()
    db_session.refresh(request)

    for index in range(2):
        db_session.add(
            ApprovalDecision(
                approval_request_id=request.id,
                decision="approved",
                decided_by_user_id=None,
                decided_by_reference=(
                    f"user:deleted-{index}"
                ),
                decided_by_role="manager",
                permission_used="approval:decide",
                decision_note=None,
                created_at=now,
            )
        )

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()


def test_database_restricts_skill_version_delete_with_approval_history(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session
    )
    request = _request(
        version.id,
        suffix="retention",
    )
    db_session.add(request)
    db_session.commit()

    db_session.delete(version)

    with pytest.raises(
        IntegrityError
    ):
        db_session.commit()

    db_session.rollback()
