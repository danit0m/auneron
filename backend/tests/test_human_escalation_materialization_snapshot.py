"""
VALUE-2.3 -- provenance de recommendation_snapshot_id no corredor de
escalonamento, espelhando 1:1 o precedente ja provado do corredor de
mark_overdue (DW-6.4B): get_verified() em todo ponto de
escrita/associacao, first-association-wins em replay/corrida.
"""

import threading
from datetime import date
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.nba_policy import get_nba_decision
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.nba_recommendation_snapshot import NbaRecommendationSnapshot
from app.models.user import User
from app.models.work import WorkItem
from app.services.human_escalation_materialization_service import (
    HumanEscalationSnapshotConflictError,
    HumanEscalationSnapshotReferenceInvalidError,
)
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotIntegrityError,
)
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotService,
)
from app.services.work_service import WorkActor

HOOK_RELEASE_TIMEOUT_S = 10.0


def _account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "atrasado",
) -> Account:
    account = Account(
        cliente="Cliente Escalation Snapshot Provenance",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _user(db_session: Session, *, email: str) -> User:
    from app.core.authentication import hash_password

    user = User(
        name="Escalation Snapshot Provenance Test",
        email=email,
        password_hash=hash_password("not-used-Aa1!"),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _actor(user: User) -> WorkActor:
    return WorkActor(
        actor_type="user",
        actor_reference=f"user:{user.id}",
        actor_user_id=user.id,
    )


def _snapshot_selecting_escalation(db_session, *, account, due_date):
    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )
    assert "escalate_to_human" in evidence.decision.selected_actions
    return NbaRecommendationSnapshotService(db_session).persist(evidence)


def test_materialize_with_valid_snapshot_threads_association(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-valid@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="snapshot-valid-actor@example.com")
    snapshot = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )

    result = HumanEscalationMaterializationService(db_session).materialize(
        account=account,
        due_date=due_date,
        actor=_actor(actor_user),
        recommendation_snapshot_id=snapshot.id,
    )

    assert result.created is True
    assert (
        result.work_item.context_data["recommendation_snapshot_id"]
        == snapshot.id
    )


def test_materialize_snapshot_wrong_account_conflicts(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-wrong-account@example.com", vencimento=due_date
    )
    other_account = _account(
        db_session,
        email="snapshot-wrong-account-other@example.com",
        vencimento=due_date,
    )
    actor_user = _user(
        db_session, email="snapshot-wrong-account-actor@example.com"
    )
    snapshot = _snapshot_selecting_escalation(
        db_session, account=other_account, due_date=due_date
    )

    with pytest.raises(HumanEscalationSnapshotConflictError):
        HumanEscalationMaterializationService(db_session).materialize(
            account=account,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot.id,
        )


def test_materialize_snapshot_not_recommending_escalation_conflicts(
    db_session: Session,
) -> None:
    """
    Mesma identidade (account_id, due_date) do inicio ao fim -- o
    snapshot e tirado enquanto a conta esta 'pago' (NBA nao recomenda
    nada, escalonamento ineligible por account_paid), depois a conta e
    revertida administrativamente para 'atrasado' (mesmo tipo de
    edicao coberta pela Governance Gap G1 ja registrada no roadmap) --
    agora elegivel para escalonamento, mas o snapshot antigo continua
    sem 'escalate_to_human' em selected_actions.
    """

    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session,
        email="snapshot-not-recommending@example.com",
        vencimento=due_date,
        status="pago",
    )
    actor_user = _user(
        db_session, email="snapshot-not-recommending-actor@example.com"
    )

    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )
    assert "escalate_to_human" not in evidence.decision.selected_actions
    snapshot = NbaRecommendationSnapshotService(db_session).persist(evidence)

    account.status = "atrasado"
    db_session.commit()

    with pytest.raises(HumanEscalationSnapshotConflictError):
        HumanEscalationMaterializationService(db_session).materialize(
            account=account,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot.id,
        )


def test_materialize_nonexistent_snapshot_id_invalid(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-nonexistent@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-nonexistent-actor@example.com"
    )

    with pytest.raises(HumanEscalationSnapshotReferenceInvalidError):
        HumanEscalationMaterializationService(db_session).materialize(
            account=account,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=999999,
        )


def test_materialize_replay_same_snapshot_is_idempotent(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-replay-same@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-replay-same-actor@example.com"
    )
    snapshot = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )

    service = HumanEscalationMaterializationService(db_session)
    first = service.materialize(
        account=account,
        due_date=due_date,
        actor=_actor(actor_user),
        recommendation_snapshot_id=snapshot.id,
    )
    second = service.materialize(
        account=account,
        due_date=due_date,
        actor=_actor(actor_user),
        recommendation_snapshot_id=snapshot.id,
    )

    assert second.duplicate is True
    assert second.work_item.id == first.work_item.id


def test_materialize_replay_different_snapshot_conflicts(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-replay-diff@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-replay-diff-actor@example.com"
    )
    snapshot_a = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )
    snapshot_b = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )
    assert snapshot_a.id != snapshot_b.id

    service = HumanEscalationMaterializationService(db_session)
    service.materialize(
        account=account,
        due_date=due_date,
        actor=_actor(actor_user),
        recommendation_snapshot_id=snapshot_a.id,
    )

    with pytest.raises(HumanEscalationSnapshotConflictError):
        service.materialize(
            account=account,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot_b.id,
        )


def test_materialize_retroactive_attach_to_legacy_work_item_conflicts(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-retroactive@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-retroactive-actor@example.com"
    )
    snapshot = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )

    service = HumanEscalationMaterializationService(db_session)
    service.materialize(
        account=account, due_date=due_date, actor=_actor(actor_user)
    )

    with pytest.raises(HumanEscalationSnapshotConflictError):
        service.materialize(
            account=account,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot.id,
        )


def test_materialize_snapshot_due_date_mismatch_conflicts(
    db_session: Session,
) -> None:
    """
    Mesma conta, mas o snapshot foi tirado para um due_date DIFERENTE
    do episodio que esta sendo materializado agora -- o snapshot
    referenciado precisa pertencer ao MESMO (account_id, due_date),
    nunca so a mesma conta.
    """

    vencimento = date.today() - timedelta(days=10)
    snapshot_due_date = date.today() - timedelta(days=25)
    account = _account(
        db_session,
        email="snapshot-due-date-mismatch@example.com",
        vencimento=vencimento,
    )
    actor_user = _user(
        db_session, email="snapshot-due-date-mismatch-actor@example.com"
    )

    # snapshot tirado para uma due_date diferente do vencimento real
    # da conta -- ainda assim uma chamada valida a get_nba_decision,
    # so representa um episodio diferente.
    evidence = get_nba_decision(
        db_session, account=account, due_date=snapshot_due_date
    )
    snapshot = NbaRecommendationSnapshotService(db_session).persist(evidence)
    assert snapshot.due_date == snapshot_due_date
    assert snapshot.account_id == account.id

    with pytest.raises(HumanEscalationSnapshotConflictError):
        HumanEscalationMaterializationService(db_session).materialize(
            account=account,
            due_date=vencimento,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot.id,
        )


def test_materialize_corrupted_snapshot_digest_raises_conflict(
    db_session: Session,
) -> None:
    """
    Corrompe snapshot_digest diretamente no banco (simulando
    adulteracao/corrupcao) -- get_verified() deve detectar a
    divergencia via hmac.compare_digest, levantar
    NbaRecommendationSnapshotIntegrityError internamente, e
    _validate_snapshot_reference deve converte-la explicitamente em
    HumanEscalationSnapshotConflictError (nunca deixar a excecao de
    integridade vazar crua para o chamador).
    """

    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-corrupted@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-corrupted-actor@example.com"
    )
    snapshot = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )

    stored = db_session.get(NbaRecommendationSnapshot, snapshot.id)
    stored.snapshot_digest = "0" * 64
    db_session.commit()

    with pytest.raises(HumanEscalationSnapshotConflictError):
        HumanEscalationMaterializationService(db_session).materialize(
            account=account,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot.id,
        )

    # confirma diretamente, sem passar pelo corredor de escalonamento,
    # que a causa raiz e de fato a excecao de integridade do 6.4A --
    # nao um efeito colateral de outra validacao.
    with pytest.raises(NbaRecommendationSnapshotIntegrityError):
        NbaRecommendationSnapshotService(db_session).get_verified(
            snapshot.id
        )


def _tagged_session(role: str) -> Session:
    session = SessionLocal()
    session.connection().info["role"] = role
    return session


def test_concurrent_materialize_same_snapshot_reconciles_without_conflict(
    db_session: Session,
) -> None:
    """
    Corrida real (duas sessoes, dois threads) sobre o MESMO episodio,
    MESMO ator, MESMO recommendation_snapshot_id -- o fingerprint de
    WorkManagerService.create() e identico para ambos, entao o
    perdedor da corrida reconcilia como duplicate=True SEM nunca
    levantar WorkConflictError (provenance compativel).
    """

    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-race-same@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-race-same-actor@example.com"
    )
    snapshot = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )
    account_id = account.id
    snapshot_id = snapshot.id

    t1_at_insert = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def before_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "INSERT" in upper and "WORK_ITEMS" in upper:
            if not t1_at_insert.is_set():
                t1_at_insert.set()
                released = release_t1.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
                if not released:
                    raise AssertionError(
                        "release_t1 não sinalizado a tempo."
                    )

    event.listen(engine, "before_cursor_execute", before_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:

        def t1_worker() -> None:
            account_ref = t1_session.get(Account, account_id)
            try:
                outcome["t1"] = HumanEscalationMaterializationService(
                    t1_session
                ).materialize(
                    account=account_ref,
                    due_date=due_date,
                    actor=_actor(actor_user),
                    recommendation_snapshot_id=snapshot_id,
                )
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_at_insert.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert acquired, (
            "T1 não alcançou o INSERT em work_items a tempo -- "
            "corrida real não comprovada."
        )

        t2_session = _tagged_session("T2")
        account_ref_t2 = t2_session.get(Account, account_id)
        outcome["t2"] = HumanEscalationMaterializationService(
            t2_session
        ).materialize(
            account=account_ref_t2,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot_id,
        )

        release_t1.set()
        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive()
    finally:
        event.remove(engine, "before_cursor_execute", before_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, outcome.get("t1_error")
    assert outcome["t2"].created is True
    assert outcome["t1"].duplicate is True
    assert outcome["t1"].work_item.id == outcome["t2"].work_item.id

    db_session.expire_all()
    work_item_count = (
        db_session.query(WorkItem)
        .filter(WorkItem.account_id == account_id)
        .count()
    )
    assert work_item_count == 1


def test_concurrent_materialize_divergent_snapshot_conflicts(
    db_session: Session,
) -> None:
    """
    Mesma corrida real, mas T1 e T2 usam recommendation_snapshot_id
    DIFERENTES -- o fingerprint diverge, o perdedor passa pelo
    tratamento de WorkConflictError recem-adicionado em
    HumanEscalationMaterializationService.materialize() e
    _reconcile_existing_association() deve rejeitar com
    HumanEscalationSnapshotConflictError (provenance incompativel) --
    nunca aceitar silenciosamente o vencedor como se fosse a mesma
    materializacao.
    """

    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="snapshot-race-diff@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="snapshot-race-diff-actor@example.com"
    )
    snapshot_a = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )
    snapshot_b = _snapshot_selecting_escalation(
        db_session, account=account, due_date=due_date
    )
    assert snapshot_a.id != snapshot_b.id
    account_id = account.id

    t1_at_insert = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def before_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "INSERT" in upper and "WORK_ITEMS" in upper:
            if not t1_at_insert.is_set():
                t1_at_insert.set()
                released = release_t1.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
                if not released:
                    raise AssertionError(
                        "release_t1 não sinalizado a tempo."
                    )

    event.listen(engine, "before_cursor_execute", before_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:

        def t1_worker() -> None:
            account_ref = t1_session.get(Account, account_id)
            try:
                outcome["t1"] = HumanEscalationMaterializationService(
                    t1_session
                ).materialize(
                    account=account_ref,
                    due_date=due_date,
                    actor=_actor(actor_user),
                    recommendation_snapshot_id=snapshot_a.id,
                )
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_at_insert.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert acquired, (
            "T1 não alcançou o INSERT em work_items a tempo -- "
            "corrida real não comprovada."
        )

        t2_session = _tagged_session("T2")
        account_ref_t2 = t2_session.get(Account, account_id)
        outcome["t2"] = HumanEscalationMaterializationService(
            t2_session
        ).materialize(
            account=account_ref_t2,
            due_date=due_date,
            actor=_actor(actor_user),
            recommendation_snapshot_id=snapshot_b.id,
        )

        release_t1.set()
        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive()
    finally:
        event.remove(engine, "before_cursor_execute", before_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert outcome["t2"].created is True

    assert "t1_error" in outcome, (
        "T1 deveria falhar ao encontrar provenance divergente do "
        "vencedor real da corrida."
    )
    assert isinstance(
        outcome["t1_error"], HumanEscalationSnapshotConflictError
    )

    db_session.expire_all()
    work_item_count = (
        db_session.query(WorkItem)
        .filter(WorkItem.account_id == account_id)
        .count()
    )
    assert work_item_count == 1
