import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.work_skill_execution import WorkSkillExecution
from app.services.skill_service import SkillService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:24e2-db",
)


def _configured(
    db_session: Session,
    *,
    suffix: str,
):
    user = User(
        name="Work Skill DB",
        email=f"work.db.{suffix}@example.com",
        password_hash="not-used",
        role="analyst",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    skill_service = SkillService(
        db_session
    )
    skill = skill_service.register_skill(
        skill_key=f"work.db.{suffix}",
        provider="auneron.core",
        display_name="Work Skill DB",
        description="Constraint test",
    )
    draft = skill_service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.db:"
            + suffix.replace(
                "-",
                "_",
            )
        ),
        execution_mode="read_only",
        input_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "integer",
                },
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
        },
    )
    version = skill_service.publish_version(
        draft.id
    ).version

    work_service = WorkManagerService(
        db_session
    )
    item = work_service.create(
        work_type="task",
        title="Work DB constraint",
        work_key=f"work.db.{suffix}",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:24e2-db",
        actor=SYSTEM_ACTOR,
    ).work_item
    item = work_service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item

    configured = WorkSkillExecutionService(
        db_session
    ).configure(
        item.id,
        version_id=version.id,
        authority_user_id=user.id,
        input_payload={"value": 1},
    )

    return (
        user,
        version,
        item,
        configured.execution,
    )


def test_database_allows_only_one_execution_per_work(
    db_session: Session,
) -> None:
    user, version, item, execution = _configured(
        db_session,
        suffix="unique-work",
    )

    duplicate = WorkSkillExecution(
        work_item_id=item.id,
        skill_version_id=version.id,
        approval_request_id=None,
        approval_consumption_id=None,
        skill_invocation_id=None,
        authority_user_id=user.id,
        authority_role=user.role,
        actor_type="system",
        actor_reference=f"system:work:{item.id}",
        dispatch_key=f"work:{item.id}:skill:duplicate",
        execution_mode="read_only",
        input_digest="a" * 64,
        status="ready",
        last_error_code=None,
        dispatch_attempts=0,
        started_at=None,
        finished_at=None,
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    assert execution.id is not None


def test_database_rejects_non_system_execution_actor(
    db_session: Session,
) -> None:
    _, _, _, execution = _configured(
        db_session,
        suffix="actor",
    )

    execution.actor_type = "agent"

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_database_restricts_deleting_linked_work(
    db_session: Session,
) -> None:
    _, _, item, execution = _configured(
        db_session,
        suffix="restrict-work",
    )

    db_session.delete(item)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    assert (
        db_session.get(
            WorkSkillExecution,
            execution.id,
        )
        is not None
    )


def test_database_sets_deleted_authority_to_null(
    db_session: Session,
) -> None:
    user, _, _, execution = _configured(
        db_session,
        suffix="authority-null",
    )
    execution_id = execution.id

    db_session.delete(user)
    db_session.commit()
    db_session.expire_all()

    persisted = db_session.get(
        WorkSkillExecution,
        execution_id,
    )
    assert persisted is not None
    assert persisted.authority_user_id is None
    assert persisted.authority_role == "analyst"
