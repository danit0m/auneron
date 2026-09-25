from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalStateError
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.policy_authority_consumption import (
    PolicyAuthorityConsumption,
)
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.policy_account_mark_overdue_execution_service import (
    PolicyAccountMarkOverdueExecutionService,
)
from app.services.policy_authority_grant_service import (
    PolicyAuthorityGrantService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


def _user(db_session: Session, *, email: str, role: str = "administrator") -> User:
    user = User(
        name="Policy Execution Test",
        email=email,
        password_hash="not-used",
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _account(
    db_session: Session,
    *,
    due_date: date,
    email: str,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Policy Execution",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=due_date,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _active_grant(
    db_session: Session,
    *,
    granter_email: str = "policy-exec-granter@example.com",
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> PolicyAuthorityGrant:
    granter = _user(db_session, email=granter_email)
    service = PolicyAuthorityGrantService(db_session)
    result = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=(
            expires_at
            if expires_at is not None
            else datetime.now(timezone.utc) + timedelta(hours=24)
        ),
        now=now,
    )
    return result.grant


def test_execution_consumes_grant_and_mutates_account(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    grant = _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="exec-happy@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    result = service.execute(account_id=account.id, due_date=due_date)

    assert result.duplicate is False
    assert result.policy_authority_grant_id == grant.id
    assert result.output["new_status"] == "atrasado"

    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "atrasado"

    consumption = db_session.get(
        PolicyAuthorityConsumption,
        result.policy_authority_consumption_id,
    )
    assert consumption.policy_authority_grant_id == grant.id
    assert consumption.target_account_id == account.id
    assert consumption.consumer_actor_type == "system"

    invocation = db_session.get(
        SkillInvocation, result.invocation_id
    )
    assert invocation.status == "succeeded"
    assert invocation.actor_type == "system"

    events = (
        db_session.execute(
            select(AccountEvent).where(
                AccountEvent.account_id == account.id
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_type == "system"
    assert events[0].actor_user_id is None
    assert events[0].new_status == "atrasado"


def test_execution_replay_returns_duplicate_without_reexecuting(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="exec-replay@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    first = service.execute(account_id=account.id, due_date=due_date)
    second = service.execute(account_id=account.id, due_date=due_date)

    assert first.duplicate is False
    assert second.duplicate is True
    assert (
        second.policy_authority_consumption_id
        == first.policy_authority_consumption_id
    )
    assert second.invocation_id == first.invocation_id

    consumptions = (
        db_session.execute(select(PolicyAuthorityConsumption))
        .scalars()
        .all()
    )
    assert len(consumptions) == 1


def test_execution_fails_closed_when_no_active_grant(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="exec-no-grant@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalAuthorizationError):
        service.execute(account_id=account.id, due_date=due_date)

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"


def test_execution_fails_closed_when_grant_expired(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    backdated_now = datetime.now(timezone.utc) - timedelta(hours=2)
    _active_grant(
        db_session,
        expires_at=backdated_now + timedelta(hours=1),
        now=backdated_now,
    )
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="exec-expired-grant@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalAuthorizationError):
        service.execute(account_id=account.id, due_date=due_date)

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert (
        db_session.execute(
            select(PolicyAuthorityConsumption)
        ).scalar_one_or_none()
        is None
    )


def test_execution_fails_closed_when_policy_version_mismatch(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    granter = _user(db_session, email="exec-stale-version@example.com")
    # Grant com policy_version desatualizada em relação à Policy
    # Definition corrente -- via inserção direta, já que
    # PolicyAuthorityGrantService nunca produziria isso (sempre lê a
    # versão corrente do código).
    stale_grant = PolicyAuthorityGrant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        policy_version="0",
        skill_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.skill_key,
        scope_type="deployment_wide",
        state="active",
        granted_by_user_id=granter.id,
        granted_by_reference=f"user:{granter.id}",
        granted_by_role="administrator",
        valid_from=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(stale_grant)
    db_session.commit()

    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="exec-stale-version-account@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalStateError):
        service.execute(account_id=account.id, due_date=due_date)

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"


def test_execution_fails_when_evidence_does_not_satisfy_policy(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="exec-not-open@example.com",
        status="pago",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalStateError):
        service.execute(account_id=account.id, due_date=due_date)


def test_execution_fails_when_account_vencimento_diverges_from_requested_due_date(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    real_due_date = date.today() - timedelta(days=5)
    requested_due_date = real_due_date - timedelta(days=1)
    account = _account(
        db_session,
        due_date=real_due_date,
        email="exec-due-date-mismatch@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalConsumptionConflictError):
        service.execute(
            account_id=account.id, due_date=requested_due_date
        )


def test_execution_rollback_is_atomic_on_failure(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    # vencimento futuro -- passa pelas checagens de identidade mas
    # falha em "conta não está atrasada", depois de já ter resolvido
    # grant/catálogo -- exercita rollback tardio no fluxo.
    account = _account(
        db_session,
        due_date=date.today() + timedelta(days=5),
        email="exec-atomic-failure@example.com",
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalStateError):
        service.execute(
            account_id=account.id,
            due_date=date.today() + timedelta(days=5),
        )

    assert (
        db_session.execute(select(SkillInvocation)).scalar_one_or_none()
        is None
    )
    assert (
        db_session.execute(
            select(PolicyAuthorityConsumption)
        ).scalar_one_or_none()
        is None
    )
    assert (
        db_session.execute(select(AccountEvent)).scalar_one_or_none()
        is None
    )
    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
