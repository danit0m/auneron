import hashlib
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.memory import MemoryItem
from app.models.user import User
from app.models.work import WorkMemoryLink
from app.models.work_outcome_evaluation import WorkOutcomeEvaluation
from app.models.work_skill_execution import WorkSkillExecution
from app.repositories.work_learning_context_repository import (
    WorkLearningContextRepository,
)
from app.services.memory_service import MemoryService
from app.services.skill_service import SkillService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25b-repository",
)


def _user(
    db: Session,
    *,
    suffix: str,
    role: str = "developer",
) -> User:
    user = User(
        name="Learning Context Repository",
        email=f"learning.repo.{suffix}@example.com",
        password_hash="not-used",
        role=role,
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _account(
    db: Session,
    *,
    suffix: str,
) -> Account:
    account = Account(
        cliente=f"Learning Context {suffix}",
        valor=Decimal("1000.00"),
        vencimento=datetime(2026, 12, 31).date(),
        status="aberto",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _version(
    db: Session,
    *,
    suffix: str,
):
    service = SkillService(db)
    skill = service.register_skill(
        skill_key=f"learning.context.{suffix}",
        provider="auneron.core",
        display_name="Learning Context",
        description="25B repository test",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.learning:"
            + suffix.replace("-", "_")
        ),
        execution_mode="read_only",
        input_schema={
            "type": "object",
            "additionalProperties": True,
        },
        output_schema={"type": "object"},
    )
    return service.publish_version(draft.id).version


def _work(
    db: Session,
    *,
    suffix: str,
    scope_type: str,
    account_id: int | None = None,
    subject_user_id: int | None = None,
):
    return WorkManagerService(db).create(
        work_type="task",
        title=f"Learning Context {suffix}",
        work_key=f"learning.context.{suffix}",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
        origin_type="system",
        origin_reference="system:test:25b-repository",
        actor=SYSTEM_ACTOR,
    ).work_item


def _candidate(
    db: Session,
    *,
    source_work,
    version_id: int,
    authority_user_id: int,
    suffix: str,
    finished_at: datetime,
    evaluation_status: str = "completed",
    memory_status: str = "active",
    create_link: bool = True,
):
    execution = WorkSkillExecution(
        work_item_id=source_work.id,
        skill_version_id=version_id,
        approval_request_id=None,
        approval_consumption_id=None,
        skill_invocation_id=None,
        authority_user_id=authority_user_id,
        authority_role="developer",
        actor_type="system",
        actor_reference=f"system:work:{source_work.id}",
        dispatch_key=f"learning-context-{suffix}",
        execution_mode="read_only",
        input_digest=hashlib.sha256(
            f"input:{suffix}".encode("utf-8")
        ).hexdigest(),
        status="failed",
        last_error_code="skill_runtime_failed",
        dispatch_attempts=1,
        started_at=finished_at - timedelta(seconds=1),
        finished_at=finished_at,
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)

    memory = MemoryService(db).remember(
        memory_type="observation",
        title="Work Skill terminal outcome",
        content=f"Safe deterministic outcome {execution.id}",
        memory_key=f"work-skill-outcome:{execution.id}:v1",
        scope_type=source_work.scope_type,
        account_id=source_work.account_id,
        subject_user_id=source_work.subject_user_id,
        source_type="derived",
        source_reference=f"work-skill-execution:{execution.id}",
        confidence=Decimal("1.000"),
        importance=Decimal("0.500"),
        valid_from=finished_at,
        valid_until=None,
        context_data={},
    ).memory

    if memory_status != "active":
        memory.status = memory_status
        db.commit()
        db.refresh(memory)

    evaluation = WorkOutcomeEvaluation(
        work_skill_execution_id=execution.id,
        terminal_status="failed",
        evaluation_code="execution_failed",
        learning_signal="negative",
        evaluator_version="deterministic_v1",
        evaluation_digest=hashlib.sha256(
            f"evaluation:{suffix}".encode("utf-8")
        ).hexdigest(),
        status=evaluation_status,
        memory_item_id=memory.id,
        attempts=1,
        last_error_code=None,
        evaluated_at=finished_at,
        completed_at=(
            finished_at
            if evaluation_status == "completed"
            else None
        ),
    )
    db.add(evaluation)
    db.commit()

    if create_link:
        db.add(
            WorkMemoryLink(
                work_item_id=source_work.id,
                memory_id=memory.id,
                relation="outcome",
                created_by_user_id=None,
            )
        )
        db.commit()

    return execution, memory


def test_repository_filters_exact_scope_skill_and_durable_contract(
    db_session: Session,
) -> None:
    authority = _user(db_session, suffix="authority")
    account = _account(db_session, suffix="primary")
    other_account = _account(db_session, suffix="other")
    version = _version(db_session, suffix="primary")
    other_version = _version(db_session, suffix="other")
    target = _work(
        db_session,
        suffix="target",
        scope_type="account",
        account_id=account.id,
    )
    now = datetime.now(timezone.utc)

    older = _work(
        db_session,
        suffix="older",
        scope_type="account",
        account_id=account.id,
    )
    older_execution, _ = _candidate(
        db_session,
        source_work=older,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="older",
        finished_at=now - timedelta(minutes=2),
    )

    newer = _work(
        db_session,
        suffix="newer",
        scope_type="account",
        account_id=account.id,
    )
    newer_execution, _ = _candidate(
        db_session,
        source_work=newer,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="newer",
        finished_at=now - timedelta(minutes=1),
    )

    wrong_scope = _work(
        db_session,
        suffix="wrong-scope",
        scope_type="account",
        account_id=other_account.id,
    )
    _candidate(
        db_session,
        source_work=wrong_scope,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="wrong-scope",
        finished_at=now - timedelta(seconds=50),
    )

    wrong_version = _work(
        db_session,
        suffix="wrong-version",
        scope_type="account",
        account_id=account.id,
    )
    _candidate(
        db_session,
        source_work=wrong_version,
        version_id=other_version.id,
        authority_user_id=authority.id,
        suffix="wrong-version",
        finished_at=now - timedelta(seconds=40),
    )

    incomplete = _work(
        db_session,
        suffix="incomplete",
        scope_type="account",
        account_id=account.id,
    )
    _candidate(
        db_session,
        source_work=incomplete,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="incomplete",
        finished_at=now - timedelta(seconds=30),
        evaluation_status="memory_recorded",
    )

    archived = _work(
        db_session,
        suffix="archived",
        scope_type="account",
        account_id=account.id,
    )
    _candidate(
        db_session,
        source_work=archived,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="archived",
        finished_at=now - timedelta(seconds=20),
        memory_status="archived",
    )

    no_link = _work(
        db_session,
        suffix="no-link",
        scope_type="account",
        account_id=account.id,
    )
    _candidate(
        db_session,
        source_work=no_link,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="no-link",
        finished_at=now - timedelta(seconds=10),
        create_link=False,
    )

    _candidate(
        db_session,
        source_work=target,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="target-self",
        finished_at=now - timedelta(seconds=5),
    )

    rows = WorkLearningContextRepository(
        db_session
    ).list_outcome_candidates(
        target_work_item_id=target.id,
        skill_version_id=version.id,
        scope_type="account",
        account_id=account.id,
        subject_user_id=None,
        as_of=now,
        limit=10,
    )

    assert [
        row.work_skill_execution_id
        for row in rows
    ] == [
        newer_execution.id,
        older_execution.id,
    ]
    assert all(row.skill_version_id == version.id for row in rows)
    assert all(row.terminal_status == "failed" for row in rows)
    assert all(row.evaluation_code == "execution_failed" for row in rows)
    assert all(row.learning_signal == "negative" for row in rows)


def test_repository_overfetch_is_bounded_to_limit_plus_one(
    db_session: Session,
) -> None:
    authority = _user(db_session, suffix="bounded")
    version = _version(db_session, suffix="bounded")
    target = _work(
        db_session,
        suffix="bounded-target",
        scope_type="global",
    )
    now = datetime.now(timezone.utc)

    for index in range(5):
        source = _work(
            db_session,
            suffix=f"bounded-{index}",
            scope_type="global",
        )
        _candidate(
            db_session,
            source_work=source,
            version_id=version.id,
            authority_user_id=authority.id,
            suffix=f"bounded-{index}",
            finished_at=now - timedelta(seconds=index + 1),
        )

    rows = WorkLearningContextRepository(
        db_session
    ).list_outcome_candidates(
        target_work_item_id=target.id,
        skill_version_id=version.id,
        scope_type="global",
        account_id=None,
        subject_user_id=None,
        as_of=now,
        limit=2,
    )

    assert len(rows) == 3

def test_repository_rejects_incoherent_deterministic_mapping(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        suffix="mapping-authority",
    )
    version = _version(
        db_session,
        suffix="mapping-version",
    )
    target = _work(
        db_session,
        suffix="mapping-target",
        scope_type="global",
    )
    source = _work(
        db_session,
        suffix="mapping-source",
        scope_type="global",
    )
    now = datetime.now(timezone.utc)

    execution, _ = _candidate(
        db_session,
        source_work=source,
        version_id=version.id,
        authority_user_id=authority.id,
        suffix="mapping-incoherent",
        finished_at=now - timedelta(seconds=1),
    )

    evaluation = (
        db_session.query(WorkOutcomeEvaluation)
        .filter(
            WorkOutcomeEvaluation.work_skill_execution_id
            == execution.id
        )
        .one()
    )
    evaluation.evaluation_code = "execution_cancelled"
    evaluation.learning_signal = "neutral"
    db_session.commit()

    rows = WorkLearningContextRepository(
        db_session
    ).list_outcome_candidates(
        target_work_item_id=target.id,
        skill_version_id=version.id,
        scope_type="global",
        account_id=None,
        subject_user_id=None,
        as_of=now,
        limit=10,
    )

    assert rows == []
