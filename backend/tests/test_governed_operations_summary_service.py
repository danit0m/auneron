from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.user import User
from app.services.business_effect_verification_service import (
    BusinessEffectVerificationService,
)
from app.services.governed_operations_summary_service import (
    GovernedOperationsSummaryService,
)
from app.services.policy_account_mark_overdue_execution_service import (
    PolicyAccountMarkOverdueExecutionService,
)
from app.services.policy_authority_grant_service import (
    PolicyAuthorityGrantService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")


def _account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "aberto",
    valor: float = 900,
) -> Account:
    account = Account(
        cliente="Cliente Governed Summary",
        email=email,
        whatsapp=None,
        valor=valor,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _paid_event(
    db_session: Session,
    *,
    account: Account,
    occurred_at: datetime,
) -> AccountEvent:
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="system",
        actor_reference="test:governed-operations-summary",
        previous_status="aberto",
        new_status="pago",
        occurred_at=occurred_at,
    )
    db_session.add(event)
    db_session.commit()
    return event


def _governed_event(
    db_session: Session,
    *,
    account: Account,
    idempotency_key: str | None,
    occurred_at: datetime,
    new_status: str = "atrasado",
) -> AccountEvent:
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="system",
        actor_reference="test:governed-operations-summary",
        previous_status="aberto",
        new_status=new_status,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
    )
    db_session.add(event)
    db_session.commit()
    return event


def _user(db_session: Session, *, email: str) -> User:
    from app.core.authentication import hash_password

    user = User(
        name="Governed Summary Test",
        email=email,
        password_hash=hash_password("not-used-Aa1!"),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _active_grant(
    db_session: Session,
    *,
    granter_email: str,
) -> PolicyAuthorityGrant:
    granter = _user(db_session, email=granter_email)
    service = PolicyAuthorityGrantService(db_session)
    result = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    return result.grant


def _business_datetime(business_date: date, hour: int, minute: int) -> datetime:
    return datetime(
        business_date.year,
        business_date.month,
        business_date.day,
        hour,
        minute,
        tzinfo=BUSINESS_TZ,
    ).astimezone(timezone.utc)


# ---------------------------------------------------------------------
# KPI-1
# ---------------------------------------------------------------------


def test_kpi1_counts_account_never_paid(db_session: Session) -> None:
    _account(
        db_session,
        email="kpi1-never-paid@example.com",
        vencimento=date.today() - timedelta(days=10),
        status="atrasado",
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.eligible_accounts_identified >= 1


def test_kpi1_counts_account_paid_after_due_date(
    db_session: Session,
) -> None:
    """Reproduz o caso real da Account 2: vencimento no periodo, paga
    depois do vencimento -- deve contar."""

    vencimento = date.today() - timedelta(days=20)
    account = _account(
        db_session,
        email="kpi1-account-2-equivalent@example.com",
        vencimento=vencimento,
        status="pago",
    )
    _paid_event(
        db_session,
        account=account,
        occurred_at=_business_datetime(
            vencimento + timedelta(days=5), 12, 0
        ),
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    eligible = _eligible_account_ids(db_session, period_days=30)
    assert account.id in eligible
    assert result.eligible_accounts_identified >= 1


def test_kpi1_excludes_account_paid_before_due_date(
    db_session: Session,
) -> None:
    vencimento = date.today() - timedelta(days=10)
    account = _account(
        db_session,
        email="kpi1-paid-before@example.com",
        vencimento=vencimento,
        status="pago",
    )
    _paid_event(
        db_session,
        account=account,
        occurred_at=_business_datetime(
            vencimento - timedelta(days=1), 12, 0
        ),
    )

    eligible = _eligible_account_ids(db_session, period_days=30)
    assert account.id not in eligible


def test_kpi1_excludes_account_paid_exactly_on_due_date(
    db_session: Session,
) -> None:
    vencimento = date.today() - timedelta(days=10)
    account = _account(
        db_session,
        email="kpi1-paid-on-due-date@example.com",
        vencimento=vencimento,
        status="pago",
    )
    _paid_event(
        db_session,
        account=account,
        occurred_at=_business_datetime(vencimento, 23, 50),
    )

    eligible = _eligible_account_ids(db_session, period_days=30)
    assert account.id not in eligible


def test_kpi1_respects_business_timezone_boundary(
    db_session: Session,
) -> None:
    """Pagamento as 23:50 America/Sao_Paulo no dia do vencimento nao
    pode contar, mesmo que em UTC ja seja o dia seguinte."""

    vencimento = date.today() - timedelta(days=10)
    payment_at = _business_datetime(vencimento, 23, 50)
    assert payment_at.astimezone(timezone.utc).date() > vencimento

    account = _account(
        db_session,
        email="kpi1-timezone-boundary@example.com",
        vencimento=vencimento,
        status="pago",
    )
    _paid_event(db_session, account=account, occurred_at=payment_at)

    eligible = _eligible_account_ids(db_session, period_days=30)
    assert account.id not in eligible


def test_kpi1_counts_account_paid_after_due_date_business_boundary(
    db_session: Session,
) -> None:
    vencimento = date.today() - timedelta(days=10)
    payment_at = _business_datetime(
        vencimento + timedelta(days=1), 0, 10
    )

    account = _account(
        db_session,
        email="kpi1-timezone-boundary-after@example.com",
        vencimento=vencimento,
        status="pago",
    )
    _paid_event(db_session, account=account, occurred_at=payment_at)

    eligible = _eligible_account_ids(db_session, period_days=30)
    assert account.id in eligible


def _eligible_account_ids(
    db_session: Session, *, period_days: int
) -> set[int]:
    from sqlalchemy import Date, cast, func, or_
    from app.core.config import settings
    from app.core.receivable_lifecycle import business_today

    today = business_today()
    start = today - timedelta(days=period_days)

    first_payment = (
        db_session.query(
            AccountEvent.account_id.label("account_id"),
            func.min(AccountEvent.occurred_at).label(
                "first_paid_at"
            ),
        )
        .filter(AccountEvent.new_status == "pago")
        .group_by(AccountEvent.account_id)
        .subquery()
    )
    first_paid_business_date = cast(
        func.timezone(
            settings.business_timezone,
            first_payment.c.first_paid_at,
        ),
        Date,
    )
    rows = (
        db_session.query(Account.id)
        .outerjoin(
            first_payment,
            first_payment.c.account_id == Account.id,
        )
        .filter(Account.vencimento >= start, Account.vencimento < today)
        .filter(
            or_(
                first_payment.c.first_paid_at.is_(None),
                first_paid_business_date > Account.vencimento,
            )
        )
        .all()
    )
    return {row.id for row in rows}


# ---------------------------------------------------------------------
# KPI-2 / KPI-3
# ---------------------------------------------------------------------


def test_kpi2_kpi3_classify_by_idempotency_prefix(
    db_session: Session,
) -> None:
    now = datetime.now(timezone.utc)

    autonomous_account = _account(
        db_session,
        email="kpi23-autonomous@example.com",
        vencimento=date.today() - timedelta(days=5),
        status="atrasado",
    )
    _governed_event(
        db_session,
        account=autonomous_account,
        idempotency_key=(
            "account_event:effect:policy_account_mark_overdue:"
            "account_mark_overdue:v1:999:2026-09-01"
        ),
        occurred_at=now - timedelta(days=1),
    )

    human_account = _account(
        db_session,
        email="kpi23-human@example.com",
        vencimento=date.today() - timedelta(days=5),
        status="atrasado",
    )
    _governed_event(
        db_session,
        account=human_account,
        idempotency_key=(
            "account_event:effect:human_account_mark_overdue:"
            "approval:998"
        ),
        occurred_at=now - timedelta(days=1),
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.autonomous_dispositions == 1
    assert result.human_governed_dispositions == 1
    assert result.autonomous_disposition_rate == 0.5


def test_kpi2_kpi3_exclude_out_of_scope_producers(
    db_session: Session,
) -> None:
    now = datetime.now(timezone.utc)

    admin_edit_account = _account(
        db_session,
        email="kpi23-admin-edit@example.com",
        vencimento=date.today() - timedelta(days=5),
        status="atrasado",
    )
    _governed_event(
        db_session,
        account=admin_edit_account,
        idempotency_key=None,
        occurred_at=now - timedelta(days=1),
    )

    mark_paid_account = _account(
        db_session,
        email="kpi23-mark-paid@example.com",
        vencimento=date.today() - timedelta(days=5),
        status="pago",
    )
    _governed_event(
        db_session,
        account=mark_paid_account,
        idempotency_key="account_event:approval:997",
        occurred_at=now - timedelta(days=1),
        new_status="pago",
    )

    legacy_agent_account = _account(
        db_session,
        email="kpi23-legacy-agent@example.com",
        vencimento=date.today() - timedelta(days=5),
        status="atrasado",
    )
    _governed_event(
        db_session,
        account=legacy_agent_account,
        idempotency_key=(
            "account_event:effect:account.mark_overdue:approval:996"
        ),
        occurred_at=now - timedelta(days=1),
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.autonomous_dispositions == 0
    assert result.human_governed_dispositions == 0


def test_period_window_is_half_open(db_session: Session) -> None:
    account = _account(
        db_session,
        email="kpi23-period-boundary@example.com",
        vencimento=date.today() - timedelta(days=5),
        status="atrasado",
    )

    period_days = 10
    frozen_now = datetime.now(timezone.utc)
    frozen_start = frozen_now - timedelta(days=period_days)

    _governed_event(
        db_session,
        account=account,
        idempotency_key=(
            "account_event:effect:human_account_mark_overdue:"
            "approval:995"
        ),
        occurred_at=frozen_start,
    )

    included = GovernedOperationsSummaryService(db_session).compute(
        period_days=period_days,
        now=frozen_now,
    )
    assert included.human_governed_dispositions == 1

    event = (
        db_session.query(AccountEvent)
        .filter(AccountEvent.account_id == account.id)
        .one()
    )
    event.occurred_at = frozen_now
    db_session.commit()

    excluded = GovernedOperationsSummaryService(db_session).compute(
        period_days=period_days,
        now=frozen_now,
    )
    assert excluded.human_governed_dispositions == 0


# ---------------------------------------------------------------------
# KPI-4 + integrity assertion (real corridor, real Grant/Consumption/BEV)
# ---------------------------------------------------------------------


def test_kpi4_buckets_and_kpi2_integrity(db_session: Session) -> None:
    register_account_mark_overdue_skill()
    grant = _active_grant(
        db_session, granter_email="kpi4-granter@example.com"
    )
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        email="kpi4-account@example.com",
        vencimento=due_date,
        status="aberto",
    )

    execution = PolicyAccountMarkOverdueExecutionService(
        db_session
    ).execute(account_id=account.id, due_date=due_date)

    BusinessEffectVerificationService(db_session).verify(
        policy_authority_consumption_id=(
            execution.policy_authority_consumption_id
        ),
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.autonomous_dispositions == 1
    assert result.verification_total == 1
    assert result.verification_verified == 1
    assert result.verification_checked_other == 0
    assert result.verification_not_yet_checked == 0
    assert result.verification_rate == 1.0
    assert result.verification_total == result.autonomous_dispositions


def test_kpi4_not_yet_checked_when_no_bev(db_session: Session) -> None:
    register_account_mark_overdue_skill()
    _active_grant(
        db_session, granter_email="kpi4-nocheck-granter@example.com"
    )
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        email="kpi4-nocheck-account@example.com",
        vencimento=due_date,
        status="aberto",
    )

    PolicyAccountMarkOverdueExecutionService(db_session).execute(
        account_id=account.id, due_date=due_date
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.verification_total == 1
    assert result.verification_verified == 0
    assert result.verification_not_yet_checked == 1
    assert result.verification_rate == 0.0


# ---------------------------------------------------------------------
# null handling
# ---------------------------------------------------------------------


def test_autonomous_disposition_rate_null_when_no_dispositions(
    db_session: Session,
) -> None:
    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.autonomous_dispositions == 0
    assert result.human_governed_dispositions == 0
    assert result.autonomous_disposition_rate is None


def test_verification_rate_null_when_no_consumptions(
    db_session: Session,
) -> None:
    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.verification_total == 0
    assert result.verification_rate is None


# ---------------------------------------------------------------------
# pending_overdue_accounts_now
# ---------------------------------------------------------------------


def test_pending_overdue_accounts_now_simple_snapshot(
    db_session: Session,
) -> None:
    _account(
        db_session,
        email="pending-now-open-overdue@example.com",
        vencimento=date.today() - timedelta(days=3),
        status="aberto",
    )
    _account(
        db_session,
        email="pending-now-open-future@example.com",
        vencimento=date.today() + timedelta(days=3),
        status="aberto",
    )
    _account(
        db_session,
        email="pending-now-already-flagged@example.com",
        vencimento=date.today() - timedelta(days=3),
        status="atrasado",
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.pending_overdue_accounts_now == 1
