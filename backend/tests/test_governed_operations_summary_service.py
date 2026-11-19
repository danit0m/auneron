from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.account_payment import AccountPayment
from app.models.approval import ApprovalRequest
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.user import User
from app.services.skill_service import SkillService
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
#
# Fonte da data de pagamento: account_payments.paid_on (data efetiva).
# O evento 'pago' (account_events) so indica que o titulo foi pago; sem
# paid_on a situacao e DESCONHECIDA (contada a parte).
# ---------------------------------------------------------------------


def _payment(
    db_session: Session,
    *,
    account: Account,
    paid_on: date,
) -> AccountPayment:
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key=f"kpi1.payment.{account.id}",
        provider="auneron.core",
        display_name="KPI-1 payment",
        description="Skill para testes de data efetiva do KPI-1.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.approval:kpi1_test",
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    version = service.publish_version(draft.id).version
    now = datetime.now(timezone.utc)
    approval = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=version.id,
        requester_actor_type="system",
        requester_reference=f"system:kpi1-{account.id}",
        requester_user_id=None,
        idempotency_key=f"kpi1-{account.id}",
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        risk_level="low",
        required_permission="approval:decide",
        status="pending",
        target_account_id=None,
        target_user_id=None,
        expires_at=now + timedelta(hours=1),
        resolved_at=None,
        created_at=now,
    )
    db_session.add(approval)
    db_session.commit()
    payment = AccountPayment(
        account_id=account.id,
        paid_on=paid_on,
        approval_request_id=approval.id,
    )
    db_session.add(payment)
    db_session.commit()
    return payment


def _paid_account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
    paid_on: date,
    event_at: datetime | None = None,
) -> Account:
    account = _account(
        db_session, email=email, vencimento=vencimento, status="pago"
    )
    _payment(db_session, account=account, paid_on=paid_on)
    _paid_event(
        db_session,
        account=account,
        occurred_at=event_at
        or _business_datetime(paid_on, 12, 0),
    )
    return account


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
    (data efetiva) depois do vencimento -- deve contar."""

    vencimento = date.today() - timedelta(days=20)
    account = _paid_account(
        db_session,
        email="kpi1-account-2-equivalent@example.com",
        vencimento=vencimento,
        paid_on=vencimento + timedelta(days=5),
    )

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    eligible, unknown = _kpi1_ids(db_session, period_days=30)
    assert account.id in eligible
    assert account.id not in unknown
    assert result.eligible_accounts_identified >= 1


def test_kpi1_excludes_account_paid_before_due_date(
    db_session: Session,
) -> None:
    vencimento = date.today() - timedelta(days=10)
    account = _paid_account(
        db_session,
        email="kpi1-paid-before@example.com",
        vencimento=vencimento,
        paid_on=vencimento - timedelta(days=1),
    )

    eligible, unknown = _kpi1_ids(db_session, period_days=30)
    assert account.id not in eligible
    assert account.id not in unknown


def test_kpi1_excludes_account_paid_exactly_on_due_date(
    db_session: Session,
) -> None:
    vencimento = date.today() - timedelta(days=10)
    account = _paid_account(
        db_session,
        email="kpi1-paid-on-due-date@example.com",
        vencimento=vencimento,
        paid_on=vencimento,
    )

    eligible, unknown = _kpi1_ids(db_session, period_days=30)
    assert account.id not in eligible
    assert account.id not in unknown


def test_kpi1_late_registration_is_not_paid_after_due_date(
    db_session: Session,
) -> None:
    """paid_on 10/11, evento registrado em 14/11, vencimento 12/11: a
    data efetiva e anterior ao vencimento; o registro tardio do evento
    nao pode tornar a conta elegivel (antes, o evento definia a data)."""

    vencimento = date.today() - timedelta(days=10)
    account = _paid_account(
        db_session,
        email="kpi1-late-registration@example.com",
        vencimento=vencimento,
        paid_on=vencimento - timedelta(days=2),
        event_at=_business_datetime(
            vencimento + timedelta(days=2), 12, 0
        ),
    )

    eligible, unknown = _kpi1_ids(db_session, period_days=30)
    assert account.id not in eligible
    assert account.id not in unknown


def test_kpi1_paid_without_payment_record_is_unknown(
    db_session: Session,
) -> None:
    """Pago (evento) sem account_payments: nem elegivel nem nao
    elegivel; contado a parte."""

    vencimento = date.today() - timedelta(days=10)
    account = _account(
        db_session,
        email="kpi1-paid-no-payment-record@example.com",
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

    eligible, unknown = _kpi1_ids(db_session, period_days=30)
    assert account.id not in eligible
    assert account.id in unknown

    before = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )
    assert before.eligible_accounts_unknown_payment_date >= 1


def test_kpi1_unknown_count_is_exposed_separately(
    db_session: Session,
) -> None:
    vencimento = date.today() - timedelta(days=10)
    service = GovernedOperationsSummaryService(db_session)
    baseline = service.compute(period_days=30)

    account = _account(
        db_session,
        email="kpi1-unknown-count@example.com",
        vencimento=vencimento,
        status="pago",
    )
    _paid_event(
        db_session,
        account=account,
        occurred_at=_business_datetime(vencimento, 10, 0),
    )

    after = service.compute(period_days=30)
    assert (
        after.eligible_accounts_unknown_payment_date
        == baseline.eligible_accounts_unknown_payment_date + 1
    )
    assert (
        after.eligible_accounts_identified
        == baseline.eligible_accounts_identified
    )


def test_kpi1_event_timestamp_no_longer_decides(
    db_session: Session,
) -> None:
    """Evento as 23:50 (fuso de negocio) no dia do vencimento, mas
    paid_on posterior: vale a data efetiva."""

    vencimento = date.today() - timedelta(days=10)
    account = _paid_account(
        db_session,
        email="kpi1-event-not-decisive@example.com",
        vencimento=vencimento,
        paid_on=vencimento + timedelta(days=1),
        event_at=_business_datetime(vencimento, 23, 50),
    )

    eligible, _ = _kpi1_ids(db_session, period_days=30)
    assert account.id in eligible


def _kpi1_ids(
    db_session: Session, *, period_days: int
) -> tuple[set[int], set[int]]:
    from app.core.receivable_lifecycle import business_today

    today = business_today()
    return GovernedOperationsSummaryService(db_session).kpi1_account_ids(
        vencimento_start=today - timedelta(days=period_days),
        vencimento_end=today,
    )


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

    result = GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )

    assert result.autonomous_dispositions == 0
    assert result.human_governed_dispositions == 0


AGENT_KEY = "account_event:effect:account.mark_overdue:approval:"
MANUAL_KEY = "account_event:effect:human_account_mark_overdue:approval:"
L3_KEY = (
    "account_event:effect:policy_account_mark_overdue:"
    "account_mark_overdue:v1:"
)


def _overdue_with_key(
    db_session: Session,
    *,
    tag: str,
    key: str | None,
    status: str = "atrasado",
    new_status: str = "atrasado",
) -> None:
    account = _account(
        db_session,
        email=f"kpi23-{tag}@example.com",
        vencimento=date.today() - timedelta(days=5),
        status=status,
    )
    _governed_event(
        db_session,
        account=account,
        idempotency_key=key,
        occurred_at=datetime.now(timezone.utc) - timedelta(days=1),
        new_status=new_status,
    )


def _compute(db_session: Session):
    return GovernedOperationsSummaryService(db_session).compute(
        period_days=30,
    )


def test_origin_manual_human_counts_only_as_manual(
    db_session: Session,
) -> None:
    _overdue_with_key(db_session, tag="o-manual", key=f"{MANUAL_KEY}801")

    result = _compute(db_session)

    assert result.manual_human_dispositions == 1
    assert result.agent_initiated_human_approved_dispositions == 0
    assert result.human_governed_dispositions == 1
    assert result.autonomous_dispositions == 0
    assert result.autonomous_disposition_rate == 0.0


def test_origin_agent_with_human_approval_counts_as_human_governed(
    db_session: Session,
) -> None:
    _overdue_with_key(db_session, tag="o-agent", key=f"{AGENT_KEY}802")

    result = _compute(db_session)

    assert result.agent_initiated_human_approved_dispositions == 1
    assert result.manual_human_dispositions == 0
    assert result.human_governed_dispositions == 1
    assert result.autonomous_dispositions == 0
    assert result.autonomous_disposition_rate == 0.0


def test_origin_autonomous_l3_is_unchanged_and_not_human(
    db_session: Session,
) -> None:
    _overdue_with_key(db_session, tag="o-l3", key=f"{L3_KEY}803")

    result = _compute(db_session)

    assert result.autonomous_dispositions == 1
    assert result.human_governed_dispositions == 0
    assert result.manual_human_dispositions == 0
    assert result.agent_initiated_human_approved_dispositions == 0
    assert result.autonomous_disposition_rate == 1.0


def test_origin_out_of_scope_counts_nowhere(db_session: Session) -> None:
    _overdue_with_key(db_session, tag="o-none", key=None)
    _overdue_with_key(
        db_session,
        tag="o-paid",
        key="account_event:approval:804",
        status="pago",
        new_status="pago",
    )
    _overdue_with_key(
        db_session,
        tag="o-other",
        key="account_event:effect:account.mark_paid:approval:805",
    )

    result = _compute(db_session)

    assert result.autonomous_dispositions == 0
    assert result.human_governed_dispositions == 0
    assert result.manual_human_dispositions == 0
    assert result.agent_initiated_human_approved_dispositions == 0
    assert result.autonomous_disposition_rate is None


def test_origins_combined_without_double_counting(
    db_session: Session,
) -> None:
    _overdue_with_key(db_session, tag="c-l3", key=f"{L3_KEY}811")
    _overdue_with_key(db_session, tag="c-m1", key=f"{MANUAL_KEY}812")
    _overdue_with_key(db_session, tag="c-m2", key=f"{MANUAL_KEY}813")
    _overdue_with_key(db_session, tag="c-a1", key=f"{AGENT_KEY}814")
    _overdue_with_key(db_session, tag="c-none", key=None)

    result = _compute(db_session)

    assert result.autonomous_dispositions == 1
    assert result.manual_human_dispositions == 2
    assert result.agent_initiated_human_approved_dispositions == 1
    assert result.human_governed_dispositions == 3
    assert (
        result.human_governed_dispositions
        == result.manual_human_dispositions
        + result.agent_initiated_human_approved_dispositions
    )
    assert result.autonomous_disposition_rate == 0.25


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
