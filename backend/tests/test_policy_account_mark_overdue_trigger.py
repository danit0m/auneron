"""
DW-7.4 -- Connected Autonomous Trigger Mechanism.

Todos os testes chamam `run_policy_account_mark_overdue_trigger()` --
a unidade que integra deteccao + despacho -- nunca
`PolicyAccountMarkOverdueExecutionService.execute()` diretamente com um
(account_id, due_date) escolhido a mao. Isso prova mecanicamente a
fiacao deteccao->despacho->taxonomia; nao substitui o futuro Connected
Autonomous Trigger Pilot (processo real, MAINTENANCE_ENABLED=true, sem
chamada de teste).
"""

import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy import event
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.policy_account_mark_overdue_trigger_maintenance import (
    run_policy_account_mark_overdue_trigger,
)
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.policy_authority_consumption import (
    PolicyAuthorityConsumption,
)
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.user import User
from app.services.policy_authority_grant_service import (
    PolicyAuthorityGrantService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)

HOOK_RELEASE_TIMEOUT_S = 10.0


def _granter(db_session: Session, *, email: str) -> User:
    user = User(
        name="Trigger Test Granter",
        email=email,
        password_hash="not-used",
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
    email: str = "trigger-granter@example.com",
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> PolicyAuthorityGrant:
    granter = _granter(db_session, email=email)
    result = PolicyAuthorityGrantService(db_session).create_grant(
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


def _account(
    db_session: Session, *, due_date: date, email: str
) -> Account:
    account = Account(
        cliente="Cliente Trigger",
        email=email,
        whatsapp=None,
        valor=750,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_trigger_detects_and_executes_eligible_account_with_active_grant(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session, due_date=due_date, email="trigger-positive@example.com"
    )

    result = run_policy_account_mark_overdue_trigger()

    assert result.candidates_detected == 1
    assert result.executions_attempted == 1
    assert result.executions_succeeded == 1
    assert result.executions_duplicate == 0
    assert result.executions_authority_unavailable == 0
    assert result.executions_failed_other == 0

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "atrasado"
    assert (
        db_session.execute(
            select(PolicyAuthorityConsumption)
        ).scalar_one_or_none()
        is not None
    )


def test_trigger_reports_authority_unavailable_with_zero_effect_when_no_grant(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=5)
    account = _account(
        db_session,
        due_date=due_date,
        email="trigger-no-grant@example.com",
    )

    result = run_policy_account_mark_overdue_trigger()

    assert result.candidates_detected == 1
    assert result.executions_authority_unavailable == 1
    assert result.executions_succeeded == 0

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert (
        db_session.execute(
            select(PolicyAuthorityConsumption)
        ).scalar_one_or_none()
        is None
    )


def test_trigger_reports_authority_unavailable_when_grant_expired(
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
        email="trigger-expired-grant@example.com",
    )

    result = run_policy_account_mark_overdue_trigger()

    assert result.executions_authority_unavailable == 1
    assert result.executions_succeeded == 0

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"


def test_trigger_reconciliation_after_success_produces_zero_additional_effect(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    _account(
        db_session,
        due_date=due_date,
        email="trigger-reconciliation@example.com",
    )

    first = run_policy_account_mark_overdue_trigger()
    assert first.executions_succeeded == 1

    consumption_count_after_first = db_session.execute(
        select(PolicyAuthorityConsumption)
    ).scalars().all()
    assert len(consumption_count_after_first) == 1

    second = run_policy_account_mark_overdue_trigger()

    # A conta saiu da query de elegibilidade (status != 'aberto') --
    # zero segundo efeito, sem exigir duplicate=true (critério
    # corrigido: a propriedade obrigatória é "nenhum segundo efeito",
    # não um mecanismo específico).
    assert second.candidates_detected == 0
    assert second.executions_succeeded == 0
    assert second.executions_duplicate == 0

    consumption_count_after_second = db_session.execute(
        select(PolicyAuthorityConsumption)
    ).scalars().all()
    assert len(consumption_count_after_second) == 1

    event_count = db_session.execute(
        select(AccountEvent)
    ).scalars().all()
    assert len(event_count) == 1


def test_trigger_respects_batch_limit_and_deterministic_ordering(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    _active_grant(db_session)
    due_date = date.today() - timedelta(days=5)
    accounts = [
        _account(
            db_session,
            due_date=due_date,
            email=f"trigger-batch-{i}@example.com",
        )
        for i in range(3)
    ]

    result = run_policy_account_mark_overdue_trigger(limit=2)

    assert result.candidates_detected == 2
    assert result.executions_succeeded == 2

    db_session.expire_all()
    ordered_ids = sorted(a.id for a in accounts)
    first_two = ordered_ids[:2]
    last_one = ordered_ids[2]
    for account_id in first_two:
        assert db_session.get(Account, account_id).status == "atrasado"
    assert db_session.get(Account, last_one).status == "aberto"


def test_trigger_one_candidate_failure_does_not_abort_batch(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=5)
    # Sem grant -- ambas as contas falham com authority_unavailable,
    # mas AMBAS precisam ser tentadas (nenhuma aborta o lote).
    account_a = _account(
        db_session, due_date=due_date, email="trigger-batch-fail-a@example.com"
    )
    account_b = _account(
        db_session, due_date=due_date, email="trigger-batch-fail-b@example.com"
    )

    result = run_policy_account_mark_overdue_trigger()

    assert result.candidates_detected == 2
    assert result.executions_attempted == 2
    assert result.executions_authority_unavailable == 2

    db_session.expire_all()
    assert db_session.get(Account, account_a.id).status == "aberto"
    assert db_session.get(Account, account_b.id).status == "aberto"


def test_trigger_rollback_releases_locks_between_candidates(
    db_session: Session,
) -> None:
    """
    Achado central do PRE-APPLY: execute() nao faz rollback em todos os
    caminhos fail-closed -- o runner precisa faze-lo incondicionalmente
    apos QUALQUER excecao, para nao reter o lock do Grant durante todo
    o resto do lote. Prova: duas contas elegiveis, grant expirado (ambas
    falham no MESMO grant); pausa a sessao do runner logo ANTES do
    segundo SELECT ... FOR UPDATE sobre policy_authority_grants (ou
    seja, depois que o primeiro candidato ja rolou de volta); confirma,
    de uma sessao concorrente separada, que o lock do grant esta
    genuinamente livre nesse instante (SELECT ... FOR UPDATE NOWAIT
    nao levanta erro de lock).
    """
    register_account_mark_overdue_skill()
    backdated_now = datetime.now(timezone.utc) - timedelta(hours=2)
    grant = _active_grant(
        db_session,
        expires_at=backdated_now + timedelta(hours=1),
        now=backdated_now,
    )
    due_date = date.today() - timedelta(days=5)
    _account(
        db_session, due_date=due_date, email="trigger-lock-a@example.com"
    )
    _account(
        db_session, due_date=due_date, email="trigger-lock-b@example.com"
    )

    grant_lock_calls = {"count": 0}
    paused = threading.Event()
    release = threading.Event()
    outcome: dict[str, object] = {}

    def before_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "TRIGGER":
            return
        upper = statement.upper()
        if (
            "FOR UPDATE" in upper
            and "POLICY_AUTHORITY_GRANTS" in upper
        ):
            grant_lock_calls["count"] += 1
            if grant_lock_calls["count"] == 2:
                paused.set()
                released = release.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
                if not released:
                    raise AssertionError(
                        "release não sinalizado a tempo."
                    )

    event.listen(engine, "before_cursor_execute", before_hook)

    trigger_session = SessionLocal()
    trigger_session.connection().info["role"] = "TRIGGER"

    watcher_session: Session | None = None
    try:
        def trigger_worker() -> None:
            from app.services.policy_account_mark_overdue_execution_service import (
                PolicyAccountMarkOverdueExecutionService,
            )

            candidates = trigger_session.execute(
                select(Account.id, Account.vencimento)
                .where(
                    Account.status == "aberto",
                    Account.vencimento < date.today(),
                )
                .order_by(Account.id.asc())
            ).all()
            service = PolicyAccountMarkOverdueExecutionService(
                trigger_session
            )
            succeeded = duplicate = authority_unavailable = failed = 0
            for account_id, vencimento in candidates:
                try:
                    result = service.execute(
                        account_id=account_id, due_date=vencimento
                    )
                    if result.duplicate:
                        duplicate += 1
                    else:
                        succeeded += 1
                except Exception:  # noqa: BLE001
                    trigger_session.rollback()
                    authority_unavailable += 1
            outcome["authority_unavailable"] = authority_unavailable

        trigger_thread = threading.Thread(target=trigger_worker)
        trigger_thread.start()

        reached = paused.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert reached, (
            "Runner não alcançou o segundo lock do Grant a tempo -- "
            "primeiro candidato pode não ter sido processado/"
            "rolado de volta."
        )

        watcher_session = SessionLocal()
        watcher_session.execute(
            text("SET LOCAL lock_timeout = '2s'")
        )
        # Se o primeiro candidato NÃO tivesse feito rollback, este
        # FOR UPDATE NOWAIT levantaria erro de lock imediatamente.
        locked_row = watcher_session.execute(
            select(PolicyAuthorityGrant)
            .where(PolicyAuthorityGrant.id == grant.id)
            .with_for_update(nowait=True)
        ).scalar_one_or_none()
        assert locked_row is not None
        watcher_session.rollback()

        release.set()
        trigger_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not trigger_thread.is_alive()
    finally:
        event.remove(engine, "before_cursor_execute", before_hook)
        trigger_session.close()
        if watcher_session is not None:
            watcher_session.close()

    assert outcome["authority_unavailable"] == 2
