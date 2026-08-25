from datetime import datetime
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.models.account import Account
from app.models.user import User
from app.services.work_learning_context import WorkLearningContextService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25b-authorization",
)


class RecordingRepository:
    def __init__(self):
        self.calls = []

    def list_outcome_candidates(self, **kwargs):
        self.calls.append(kwargs)
        return []


def _user(
    db: Session,
    *,
    suffix: str,
    role: str,
    active: bool = True,
) -> User:
    user = User(
        name="Learning Context Authority",
        email=f"learning.auth.{suffix}@example.com",
        password_hash="not-used",
        role=role,
        active=active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _account(db: Session) -> Account:
    account = Account(
        cliente="Learning Context Account",
        valor=Decimal("1000.00"),
        vencimento=datetime(2026, 12, 31).date(),
        status="aberto",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


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
        work_key=f"learning.context.auth.{suffix}",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
        origin_type="system",
        origin_reference="system:test:25b-authorization",
        actor=SYSTEM_ACTOR,
    ).work_item


def test_global_context_requires_current_global_work_and_memory_read(
    db_session: Session,
) -> None:
    manager = _user(
        db_session,
        suffix="manager-global",
        role="manager",
    )
    target = _work(
        db_session,
        suffix="global-denied",
        scope_type="global",
    )
    repository = RecordingRepository()

    with pytest.raises(WorkNotFoundError):
        WorkLearningContextService(
            db_session,
            repository=repository,
        ).resolve(
            target.id,
            skill_version_id=1,
            authority_user_id=manager.id,
        )

    assert repository.calls == []


def test_account_viewer_is_authorized_from_persisted_target_scope(
    db_session: Session,
) -> None:
    viewer = _user(
        db_session,
        suffix="viewer-account",
        role="viewer",
    )
    account = _account(db_session)
    target = _work(
        db_session,
        suffix="account-allowed",
        scope_type="account",
        account_id=account.id,
    )
    repository = RecordingRepository()

    result = WorkLearningContextService(
        db_session,
        repository=repository,
    ).resolve(
        target.id,
        skill_version_id=44,
        authority_user_id=viewer.id,
        as_of=datetime.now(timezone.utc),
    )

    assert result == ()
    assert len(repository.calls) == 1
    assert repository.calls[0]["scope_type"] == "account"
    assert repository.calls[0]["account_id"] == account.id
    assert repository.calls[0]["subject_user_id"] is None


def test_user_scope_does_not_allow_cross_user_context_without_permission(
    db_session: Session,
) -> None:
    actor = _user(
        db_session,
        suffix="viewer-cross-user",
        role="viewer",
    )
    subject = _user(
        db_session,
        suffix="subject-cross-user",
        role="viewer",
    )
    target = _work(
        db_session,
        suffix="user-denied",
        scope_type="user",
        subject_user_id=subject.id,
    )
    repository = RecordingRepository()

    with pytest.raises(WorkNotFoundError):
        WorkLearningContextService(
            db_session,
            repository=repository,
        ).resolve(
            target.id,
            skill_version_id=1,
            authority_user_id=actor.id,
        )

    assert repository.calls == []


def test_authority_role_is_reloaded_from_current_persisted_user(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        suffix="role-reload",
        role="developer",
    )
    target = _work(
        db_session,
        suffix="role-reload-global",
        scope_type="global",
    )
    repository = RecordingRepository()
    service = WorkLearningContextService(
        db_session,
        repository=repository,
    )

    authority.role = "manager"
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(WorkNotFoundError):
        service.resolve(
            target.id,
            skill_version_id=1,
            authority_user_id=authority.id,
        )

    assert repository.calls == []


def test_inactive_authority_is_rejected_before_repository_query(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        suffix="inactive",
        role="developer",
        active=False,
    )
    target = _work(
        db_session,
        suffix="inactive-global",
        scope_type="global",
    )
    repository = RecordingRepository()

    with pytest.raises(WorkStateError):
        WorkLearningContextService(
            db_session,
            repository=repository,
        ).resolve(
            target.id,
            skill_version_id=1,
            authority_user_id=authority.id,
        )

    assert repository.calls == []

def test_authority_role_is_refreshed_from_database_identity(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        suffix="role-external-refresh",
        role="developer",
    )
    target = _work(
        db_session,
        suffix="role-external-refresh-global",
        scope_type="global",
    )
    repository = RecordingRepository()
    service = WorkLearningContextService(
        db_session,
        repository=repository,
    )

    with Session(bind=db_session.get_bind()) as external:
        persisted = external.get(User, authority.id)
        assert persisted is not None
        persisted.role = "manager"
        external.commit()

    assert authority.role == "developer"

    with pytest.raises(WorkNotFoundError):
        service.resolve(
            target.id,
            skill_version_id=1,
            authority_user_id=authority.id,
        )

    assert repository.calls == []


def test_target_work_scope_is_refreshed_from_database_identity(
    db_session: Session,
) -> None:
    viewer = _user(
        db_session,
        suffix="scope-external-refresh",
        role="viewer",
    )
    account = _account(db_session)
    target = _work(
        db_session,
        suffix="scope-external-refresh-account",
        scope_type="account",
        account_id=account.id,
    )
    repository = RecordingRepository()
    service = WorkLearningContextService(
        db_session,
        repository=repository,
    )

    with Session(bind=db_session.get_bind()) as external:
        persisted = external.get(type(target), target.id)
        assert persisted is not None
        persisted.scope_type = "global"
        persisted.account_id = None
        persisted.subject_user_id = None
        external.commit()

    assert target.scope_type == "account"
    assert target.account_id == account.id

    with pytest.raises(WorkNotFoundError):
        service.resolve(
            target.id,
            skill_version_id=1,
            authority_user_id=viewer.id,
        )

    assert repository.calls == []
