"""
DW-3 V1 -- Business Effect Verification.

Prova, contra os dois corredores governados reais (account.mark_overdue,
account.mark_paid), que o servico verifica o efeito de negocio commitado
a partir de Account + AccountEvent -- nunca de WorkSkillExecution.status
-- com o contrato fail-closed congelado: 0 eventos correspondentes ->
pending; exatamente 1 evento consistente -> verified; qualquer
divergencia (status, conta, ou mais de 1 evento correspondente) ->
contradicted; identidade de verificacao ausente (target_account_id nulo
ou Account inexistente) -> unverifiable. Resultados terminais sao
imutaveis: uma nova chamada nunca reinterpreta o passado usando o
estado empresarial atual.

Os casos felizes (verified) usam os execution services reais via HTTP +
chamada direta de execute() -- nunca mock da mutacao de negocio. Os
casos-limite (pending/contradicted/unverifiable) partem de uma execucao
real e manipulam diretamente o estado subsequente (Account/AccountEvent/
ApprovalRequest.target_account_id), simulando divergencias que o
Discovery encontrou como estruturalmente possiveis hoje (ausencia de
FK/UniqueConstraint entre execucao e AccountEvent).
"""

import hashlib
import os
import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import SessionLocal
from app.database.database import engine
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.business_effect_verification import (
    BusinessEffectVerification,
)
from app.models.skill import SkillInvocation
from app.models.user import User
from app.repositories.business_effect_verification_repository import (
    BusinessEffectVerificationRepository,
)
from app.repositories.skill_repository import SkillRepository
from app.services.account_mark_paid_execution_service import (
    AccountMarkPaidExecutionService,
)
from app.services.business_effect_verification_service import (
    BusinessEffectVerificationService,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanAccountMarkOverdueExecutionService,
)
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)
from scripts.register_account_mark_paid_skill import (
    main as register_account_mark_paid_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"
APPROVER_EMAIL = "approver.bev@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-BEV-123!"

WATCHER_TIMEOUT_S = 5.0
WATCHER_POLL_INTERVAL_S = 0.02
HOOK_RELEASE_TIMEOUT_S = 10.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _create_approver(db_session: Session) -> User:
    existing = (
        db_session.query(User)
        .filter(User.email == APPROVER_EMAIL)
        .one_or_none()
    )
    if existing is not None:
        return existing

    user = User(
        name="Aprovador BEV",
        email=APPROVER_EMAIL,
        password_hash=hash_password(APPROVER_PASSWORD),
        role="manager",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _approver_client(db_session: Session) -> TestClient:
    _create_approver(db_session)
    test_client = TestClient(app)
    test_client.headers.update(
        {API_KEY_HEADER_NAME: os.environ["API_KEY"]}
    )
    login = test_client.post(
        "/auth/login",
        json={
            "email": APPROVER_EMAIL,
            "password": APPROVER_PASSWORD,
        },
    )
    assert login.status_code == 200, login.text
    return test_client


def _make_account(
    db_session: Session,
    *,
    email: str,
    status: str = "aberto",
    vencimento: date | None = None,
) -> Account:
    account = Account(
        cliente="Cliente Business Effect Verification",
        email=email,
        whatsapp=None,
        valor=777,
        vencimento=vencimento or date(2026, 6, 1),
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _authenticated_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _execute_mark_overdue(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
) -> tuple[Account, int]:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=5)
    account = _make_account(
        db_session,
        email=email,
        status="aberto",
        vencimento=due_date,
    )

    materialize = client.post(
        "/recommendations/mark-overdue/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}/materialize"
    )
    assert materialize.status_code == 201, materialize.text
    request_id = (
        materialize.json()["approval_request"]["request_id"]
    )

    approver = _approver_client(db_session)
    decide = approver.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    authority = _authenticated_user(db_session)
    service = HumanAccountMarkOverdueExecutionService(
        db_session
    )
    result = service.execute(
        account_id=account.id,
        due_date=due_date,
        authority_user_id=authority.id,
    )
    return account, result.approval_consumption_id


def _mark_paid_version_id(db_session: Session) -> int:
    register_account_mark_paid_skill()
    repository = SkillRepository(db_session)
    skill = repository.find_skill_by_key(
        "account.mark_paid"
    )
    versions = [
        version
        for version in repository.list_versions(skill.id)
        if version.status == "published"
    ]
    assert len(versions) == 1
    return versions[0].id


def _execute_mark_paid(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
) -> tuple[Account, int]:
    account = _make_account(
        db_session, email=email, status="aberto"
    )
    version_id = _mark_paid_version_id(db_session)

    request = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account.id,
                "expected_status": "aberto",
            }
        },
        headers={
            "Idempotency-Key": f"bev-mark-paid-{account.id}"
        },
    )
    assert request.status_code == 201, request.text
    request_id = request.json()["request"]["request_id"]

    approver = _approver_client(db_session)
    decide = approver.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    authority = _authenticated_user(db_session)
    service = AccountMarkPaidExecutionService(db_session)
    result = service.execute(
        account_id=account.id,
        approval_request_id=request_id,
        expected_status="aberto",
        authority_user_id=authority.id,
    )
    return account, result.approval_consumption_id


def _get_verification(
    db_session: Session, approval_consumption_id: int
) -> BusinessEffectVerification:
    return db_session.execute(
        select(BusinessEffectVerification).where(
            BusinessEffectVerification.approval_consumption_id
            == approval_consumption_id
        )
    ).scalar_one()


def _only_account_event(
    db_session: Session, account_id: int
) -> AccountEvent:
    events = (
        db_session.execute(
            select(AccountEvent).where(
                AccountEvent.account_id == account_id
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    return events[0]


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _create_non_v1_consumed_consumption(
    db_session: Session, *, suffix: str
) -> int:
    """
    Constroi um ApprovalConsumption 'consumed' para uma skill FORA do
    escopo V1 (nem account.mark_overdue, nem account.mark_paid) -- o
    unico jeito de provar que a query de recovery filtra por skill_key,
    ja que este ambiente so registra as duas skills reais. A skill/
    versao usa o SkillService real (mesmo codigo de producao dos scripts
    de registro); as linhas de aprovacao/consumo sao inseridas
    diretamente, pois nao ha execution service para uma skill
    descartavel.
    """
    skill_key = f"account.bev_test_non_v1_{suffix}"
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.test",
        display_name="BEV non-V1 test skill",
        description=(
            "Skill descartavel usada apenas para provar que o "
            "recovery do Business Effect Verification exclui "
            "skills fora do escopo V1."
        ),
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.test:noop",
        execution_mode="mutating",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    publication = service.publish_version(
        draft.id,
        capabilities=[
            CapabilityInput(
                capability_key=(
                    f"account.status.bev_test_non_v1_{suffix}"
                ),
                access_mode="write",
                resource_scope="account",
                required=True,
            ),
        ],
    )
    version_id = publication.version.id

    now = _utc_now()
    request = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=version_id,
        requester_actor_type="system",
        requester_reference=f"system:bev-test-non-v1-{suffix}",
        requester_user_id=None,
        idempotency_key=f"bev-test-non-v1-{suffix}",
        request_fingerprint=_hex64(f"request-{suffix}"),
        input_digest=_hex64(f"input-{suffix}"),
        risk_level="low",
        required_permission="approval:decide",
        status="approved",
        target_account_id=None,
        target_user_id=None,
        expires_at=now + timedelta(hours=1),
        resolved_at=now,
    )
    db_session.add(request)
    db_session.commit()
    db_session.refresh(request)

    decision = ApprovalDecision(
        approval_request_id=request.id,
        decision="approved",
        decided_by_user_id=None,
        decided_by_reference="system:bev-test-reviewer",
        decided_by_role="manager",
        permission_used="approval:decide",
        decision_note=None,
        sensitive_elevation_verified=False,
    )
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)

    invocation = SkillInvocation(
        skill_version_id=version_id,
        actor_type="system",
        actor_reference=f"system:bev-test-non-v1-{suffix}",
        actor_user_id=None,
        idempotency_key=f"bev-test-non-v1-{suffix}",
        request_fingerprint=_hex64(f"invocation-{suffix}"),
        input_digest=_hex64(f"input-{suffix}"),
        status="succeeded",
        output_payload={"noop": True},
        output_digest=_hex64(f"output-{suffix}"),
        output_bytes=2,
        error_code=None,
        duration_ms=0,
        started_at=now,
        finished_at=now,
    )
    db_session.add(invocation)
    db_session.commit()
    db_session.refresh(invocation)

    consumption = ApprovalConsumption(
        approval_request_id=request.id,
        approval_decision_id=decision.id,
        skill_invocation_id=invocation.id,
        consumer_actor_type="system",
        consumer_reference=f"system:bev-test-non-v1-{suffix}",
        authority_user_id=None,
        authority_reference="system:bev-test-non-v1",
        authority_role="manager",
        runtime_idempotency_key=f"bev-test-non-v1-{suffix}",
        request_fingerprint=request.request_fingerprint,
        input_digest=request.input_digest,
        status="consumed",
        error_code=None,
        reserved_at=now,
        finalized_at=now,
    )
    db_session.add(consumption)
    db_session.commit()
    db_session.refresh(consumption)
    return consumption.id


def _tagged_session(role: str) -> Session:
    session = SessionLocal()
    session.connection().info["role"] = role
    return session


def _backend_pid(session: Session) -> int:
    return session.execute(
        text("SELECT pg_backend_pid()")
    ).scalar_one()


def _watcher_confirms_blocked(pid: int) -> bool:
    """
    Conexao independente, autocommit, exclusiva para polling -- nunca
    participa da transacao critica. Confirma objetivamente que `pid`
    esta em wait_event_type='Lock', nao apenas lento. Mesmo mecanismo de
    test_human_account_mark_overdue_execution_concurrency.py.
    """
    connection = engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        deadline = time.monotonic() + WATCHER_TIMEOUT_S
        while time.monotonic() < deadline:
            row = connection.execute(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE pid = :pid"
                ),
                {"pid": pid},
            ).first()
            if row is not None and row[0] == "Lock":
                return True
            time.sleep(WATCHER_POLL_INTERVAL_S)
        return False
    finally:
        connection.close()


# --- casos felizes (corredores reais) ---------------------------------


def test_mark_overdue_valid_effect_is_verified(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="overdue.verified@example.com",
    )
    service = BusinessEffectVerificationService(db_session)

    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "verified"
    assert outcome.verification.skill_key == (
        "account.mark_overdue"
    )
    assert (
        outcome.verification.target_account_id == account.id
    )
    assert (
        outcome.verification.expected_status == "atrasado"
    )
    assert outcome.verification.account_event_id is not None
    assert (
        outcome.verification.account_status_observed
        == "atrasado"
    )
    assert outcome.verification.checked_at is not None


def test_mark_paid_valid_effect_is_verified_without_external_claim(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.verified@example.com",
    )
    service = BusinessEffectVerificationService(db_session)

    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "verified"
    assert outcome.verification.skill_key == (
        "account.mark_paid"
    )
    assert (
        outcome.verification.target_account_id == account.id
    )
    assert outcome.verification.expected_status == "pago"

    # Guardrail DW-3: nenhum texto do artefato pode afirmar
    # confirmacao de pagamento externo (banco/ERP/cliente).
    forbidden_terms = (
        "banco",
        "bank",
        "erp",
        "external",
        "externo",
        "cliente pagou",
        "customer paid",
    )
    verification = outcome.verification
    haystack = " ".join(
        str(value).lower()
        for value in (
            verification.skill_key,
            verification.expected_status,
            verification.result,
            verification.account_event_key_searched,
        )
    )
    for term in forbidden_terms:
        assert term not in haystack


# --- pending / recuperação -------------------------------------------


def test_missing_account_event_yields_pending_then_verified(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.pending@example.com",
    )
    event = _only_account_event(db_session, account.id)
    event_snapshot = {
        "account_id": event.account_id,
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "actor_reference": event.actor_reference,
        "actor_user_id": event.actor_user_id,
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "idempotency_key": event.idempotency_key,
    }
    db_session.delete(event)
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    first = service.verify(consumption_id)
    assert first.verification.result == "pending"
    assert first.verification.checked_at is None
    assert first.verification.account_event_id is None
    pending_id = first.verification.id

    db_session.add(AccountEvent(**event_snapshot))
    db_session.commit()

    second = service.verify(consumption_id)
    assert second.verification.result == "verified"
    assert second.verification.id == pending_id

    stored = _get_verification(db_session, consumption_id)
    assert stored.result == "verified"


# --- contradicted -------------------------------------------------------


def test_account_status_diverges_yields_contradicted(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="overdue.contradicted-status@example.com",
    )
    account.status = "aberto"
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "contradicted"
    assert (
        outcome.verification.account_status_observed
        == "aberto"
    )


def test_account_event_new_status_diverges_yields_contradicted(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.contradicted-event@example.com",
    )
    event = _only_account_event(db_session, account.id)
    event.new_status = "atrasado"
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "contradicted"


def test_account_event_points_to_other_account_yields_contradicted(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.contradicted-account@example.com",
    )
    other_account = _make_account(
        db_session,
        email="paid.other-account@example.com",
        status="aberto",
    )
    event = _only_account_event(db_session, account.id)
    event.account_id = other_account.id
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "contradicted"


def test_multiple_matching_events_fail_closed_to_contradicted(
    client: TestClient, db_session: Session
) -> None:
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.multiple-events@example.com",
    )
    original = _only_account_event(db_session, account.id)
    db_session.add(
        AccountEvent(
            account_id=account.id,
            event_type="status_changed",
            actor_type="user",
            actor_reference=original.actor_reference,
            actor_user_id=original.actor_user_id,
            previous_status=original.previous_status,
            new_status=original.new_status,
            idempotency_key=original.idempotency_key,
        )
    )
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "contradicted"
    assert outcome.verification.account_event_id is None


# --- unverifiable -------------------------------------------------------


def test_null_target_account_id_yields_unverifiable(
    client: TestClient, db_session: Session
) -> None:
    from app.models.approval import ApprovalConsumption

    _, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.null-target@example.com",
    )
    consumption = db_session.get(
        ApprovalConsumption, consumption_id
    )
    request = db_session.get(
        ApprovalRequest, consumption.approval_request_id
    )
    request.target_account_id = None
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "unverifiable"
    assert outcome.verification.account_event_id is None
    assert (
        outcome.verification.account_status_observed is None
    )


def test_missing_target_account_yields_unverifiable(
    client: TestClient, db_session: Session
) -> None:
    from app.models.approval import ApprovalConsumption

    _, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="paid.missing-account@example.com",
    )
    consumption = db_session.get(
        ApprovalConsumption, consumption_id
    )
    request = db_session.get(
        ApprovalRequest, consumption.approval_request_id
    )
    request.target_account_id = 999_999_999
    db_session.commit()

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(consumption_id)

    assert outcome.verification.result == "unverifiable"


# --- imutabilidade / replay ---------------------------------------------


def test_terminal_replay_is_stable_no_new_row(
    client: TestClient, db_session: Session
) -> None:
    _, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="overdue.replay@example.com",
    )
    service = BusinessEffectVerificationService(db_session)

    first = service.verify(consumption_id)
    second = service.verify(consumption_id)

    assert first.verification.id == second.verification.id
    assert second.duplicate is True

    rows = (
        db_session.execute(
            select(BusinessEffectVerification).where(
                BusinessEffectVerification
                .approval_consumption_id
                == consumption_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_verified_survives_legitimate_later_account_change(
    client: TestClient, db_session: Session
) -> None:
    account, overdue_consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="overdue.then-paid@example.com",
    )
    service = BusinessEffectVerificationService(db_session)
    first = service.verify(overdue_consumption_id)
    assert first.verification.result == "verified"
    original_event_id = (
        first.verification.account_event_id
    )

    version_id = _mark_paid_version_id(db_session)
    paid_request = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account.id,
                "expected_status": "atrasado",
            }
        },
        headers={
            "Idempotency-Key": (
                f"bev-then-paid-{account.id}"
            )
        },
    )
    assert paid_request.status_code == 201, paid_request.text
    paid_request_id = (
        paid_request.json()["request"]["request_id"]
    )
    approver = _approver_client(db_session)
    decide = approver.post(
        f"/approvals/{paid_request_id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text
    authority = _authenticated_user(db_session)
    AccountMarkPaidExecutionService(db_session).execute(
        account_id=account.id,
        approval_request_id=paid_request_id,
        expected_status="atrasado",
        authority_user_id=authority.id,
    )

    replay = service.verify(overdue_consumption_id)
    assert replay.verification.result == "verified"
    assert (
        replay.verification.account_event_id
        == original_event_id
    )
    assert (
        replay.verification.account_status_observed
        == "atrasado"
    )


def test_concurrent_pending_transition_is_serialized_by_row_lock(
    client: TestClient, db_session: Session
) -> None:
    """
    Prova, sob concorrencia real (duas conexoes/threads Postgres
    genuinamente sobrepostas), que duas transacoes nao podem transicionar
    a MESMA linha PENDING simultaneamente. UNIQUE(approval_consumption_id)
    protege apenas contra duas LINHAS; o ponto de serializacao real e o
    SELECT ... FOR UPDATE de lock_by_consumption_id(). T2 so e iniciada
    DEPOIS que o hook confirma que T1 ja detem o lock da proof --
    elimina a possibilidade de T2 vencer a corrida acidentalmente.
    """
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="race.pending@example.com",
    )
    event_row = _only_account_event(db_session, account.id)
    event_snapshot = {
        "account_id": event_row.account_id,
        "event_type": event_row.event_type,
        "actor_type": event_row.actor_type,
        "actor_reference": event_row.actor_reference,
        "actor_user_id": event_row.actor_user_id,
        "previous_status": event_row.previous_status,
        "new_status": event_row.new_status,
        "idempotency_key": event_row.idempotency_key,
    }
    db_session.delete(event_row)
    db_session.commit()

    setup = BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )
    assert setup.verification.result == "pending"
    pending_row_id = setup.verification.id

    db_session.add(AccountEvent(**event_snapshot))
    db_session.commit()

    t1_has_lock = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def after_hook(
        conn, cursor, statement, parameters, context, executemany
    ):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if (
            "FOR UPDATE" in upper
            and "BUSINESS_EFFECT_VERIFICATIONS" in upper
        ):
            if not t1_has_lock.is_set():
                t1_has_lock.set()
                released = release_t1.wait(
                    timeout=HOOK_RELEASE_TIMEOUT_S
                )
                if not released:
                    raise AssertionError(
                        "release_t1 nao foi sinalizado a tempo."
                    )

    event.listen(engine, "after_cursor_execute", after_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = BusinessEffectVerificationService(t1_session)
            try:
                outcome["t1_result"] = service.verify(consumption_id)
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_has_lock.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert acquired, (
            "T1 nao alcancou/adquiriu o lock da proof a tempo."
        )

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        assert pid_t1 != pid_t2

        def t2_worker() -> None:
            service = BusinessEffectVerificationService(t2_session)
            try:
                outcome["t2_result"] = service.verify(consumption_id)
            except Exception as error:  # noqa: BLE001
                t2_session.rollback()
                outcome["t2_error"] = error

        t2_thread = threading.Thread(target=t2_worker)
        t2_thread.start()

        blocked = _watcher_confirms_blocked(pid_t2)
        assert blocked, (
            "Watcher nao observou wait_event_type='Lock' para T2 -- "
            "concorrencia real nao foi comprovada; o teste falha em "
            "vez de inferir por tempo."
        )

        release_t1.set()

        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive(), "T1 nao concluiu a tempo."
        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive(), "T2 nao concluiu a tempo."
    finally:
        event.remove(engine, "after_cursor_execute", after_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, outcome.get("t1_error")
    assert "t2_error" not in outcome, outcome.get("t2_error")

    t1_result = outcome["t1_result"]
    t2_result = outcome["t2_result"]
    assert t1_result.verification.id == pending_row_id
    assert t1_result.verification.result == "verified"
    assert t1_result.duplicate is False

    assert t2_result.verification.id == pending_row_id
    assert t2_result.verification.result == "verified"
    assert t2_result.duplicate is True

    db_session.expire_all()
    rows = (
        db_session.execute(
            select(BusinessEffectVerification).where(
                BusinessEffectVerification.approval_consumption_id
                == consumption_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].result == "verified"


def test_concurrent_insert_race_does_not_produce_two_proofs(
    client: TestClient, db_session: Session
) -> None:
    """
    Prova, sob concorrencia real, que repository.add() -> IntegrityError
    -> rollback -> reread e genuinamente exercitado -- nao apenas
    inferido a partir de uma linha concorrente pre-commitada antes de
    verify() ser chamado pela primeira vez (achado da Patch Review: o
    teste anterior nunca alcancava o INSERT, so o curto-circuito de
    leitura). T1 e pausada logo apos seu INSERT, ainda sem commit; T2
    tenta inserir a mesma approval_consumption_id e bloqueia de verdade
    no indice UNIQUE ate T1 resolver.
    """
    account, consumption_id = _execute_mark_paid(
        client,
        db_session,
        email="race.insert@example.com",
    )

    t1_inserted = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def after_hook(
        conn, cursor, statement, parameters, context, executemany
    ):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if (
            "INSERT INTO" in upper
            and "BUSINESS_EFFECT_VERIFICATIONS" in upper
        ):
            if not t1_inserted.is_set():
                t1_inserted.set()
                released = release_t1.wait(
                    timeout=HOOK_RELEASE_TIMEOUT_S
                )
                if not released:
                    raise AssertionError(
                        "release_t1 nao foi sinalizado a tempo."
                    )

    event.listen(engine, "after_cursor_execute", after_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = BusinessEffectVerificationService(t1_session)
            try:
                outcome["t1_result"] = service.verify(consumption_id)
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        inserted = t1_inserted.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert inserted, "T1 nao alcancou o INSERT a tempo."

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        assert pid_t1 != pid_t2
        t2_session.execute(text("SET LOCAL lock_timeout = '30s'"))

        def t2_worker() -> None:
            service = BusinessEffectVerificationService(t2_session)
            try:
                outcome["t2_result"] = service.verify(consumption_id)
            except Exception as error:  # noqa: BLE001
                t2_session.rollback()
                outcome["t2_error"] = error

        t2_thread = threading.Thread(target=t2_worker)
        t2_thread.start()

        blocked = _watcher_confirms_blocked(pid_t2)
        assert blocked, (
            "Watcher nao observou wait_event_type='Lock' para T2 -- "
            "concorrencia real de insercao nao foi comprovada."
        )

        release_t1.set()

        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive(), "T1 nao concluiu a tempo."
        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive(), "T2 nao concluiu a tempo."
    finally:
        event.remove(engine, "after_cursor_execute", after_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, outcome.get("t1_error")
    assert "t2_error" not in outcome, outcome.get("t2_error")

    t1_result = outcome["t1_result"]
    t2_result = outcome["t2_result"]
    assert t1_result.duplicate is False
    assert t2_result.duplicate is True
    assert t2_result.verification.id == t1_result.verification.id

    db_session.expire_all()
    rows = (
        db_session.execute(
            select(BusinessEffectVerification).where(
                BusinessEffectVerification
                .approval_consumption_id
                == consumption_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


# --- recovery: seleção real do repository (não fake) ---------------------


def test_recovery_candidates_are_filtered_correctly(
    client: TestClient, db_session: Session
) -> None:
    """
    Prova, contra o banco real (não _FakeRepository), que
    list_recovery_candidate_consumption_ids() seleciona exatamente:
    ausência de proof -> candidato; PENDING -> candidato; e exclui
    VERIFIED/CONTRADICTED/UNVERIFIABLE e qualquer skill fora do escopo
    V1 -- mesmo quando a ApprovalConsumption está 'consumed'.
    """
    repository = BusinessEffectVerificationRepository(db_session)
    service = BusinessEffectVerificationService(db_session)

    _, absent_id = _execute_mark_paid(
        client, db_session, email="recovery.absent@example.com"
    )

    account_pending, pending_id = _execute_mark_paid(
        client, db_session, email="recovery.pending@example.com"
    )
    pending_event = _only_account_event(
        db_session, account_pending.id
    )
    db_session.delete(pending_event)
    db_session.commit()
    pending_outcome = service.verify(pending_id)
    assert pending_outcome.verification.result == "pending"

    _, verified_id = _execute_mark_overdue(
        client, db_session, email="recovery.verified@example.com"
    )
    verified_outcome = service.verify(verified_id)
    assert verified_outcome.verification.result == "verified"

    account_contra, contradicted_id = _execute_mark_overdue(
        client,
        db_session,
        email="recovery.contradicted@example.com",
    )
    account_contra.status = "aberto"
    db_session.commit()
    contradicted_outcome = service.verify(contradicted_id)
    assert contradicted_outcome.verification.result == "contradicted"

    _, unverifiable_id = _execute_mark_paid(
        client,
        db_session,
        email="recovery.unverifiable@example.com",
    )
    unverifiable_consumption = db_session.get(
        ApprovalConsumption, unverifiable_id
    )
    unverifiable_request = db_session.get(
        ApprovalRequest,
        unverifiable_consumption.approval_request_id,
    )
    unverifiable_request.target_account_id = None
    db_session.commit()
    unverifiable_outcome = service.verify(unverifiable_id)
    assert unverifiable_outcome.verification.result == "unverifiable"

    non_v1_id = _create_non_v1_consumed_consumption(
        db_session, suffix="recovery-filter"
    )

    candidate_ids = set(
        repository.list_recovery_candidate_consumption_ids(
            limit=1000
        )
    )

    assert absent_id in candidate_ids
    assert pending_id in candidate_ids
    assert verified_id not in candidate_ids
    assert contradicted_id not in candidate_ids
    assert unverifiable_id not in candidate_ids
    assert non_v1_id not in candidate_ids


def test_bev_verifies_policy_authority_consumption_path(
    db_session: Session,
) -> None:
    """
    DW-7.3 -- BEV aceita policy_authority_consumption_id como fonte
    alternativa de identidade (XOR com approval_consumption_id), sem
    alterar a lógica de _evaluate_effect (nunca usa
    SkillInvocation.status como verdade, sempre relê Account/
    AccountEvent).
    """
    from app.core.policy_definitions import (
        ACCOUNT_MARK_OVERDUE_POLICY_V1,
    )
    from app.services.policy_account_mark_overdue_execution_service import (
        PolicyAccountMarkOverdueExecutionService,
    )
    from app.services.policy_authority_grant_service import (
        PolicyAuthorityGrantService,
    )

    register_account_mark_overdue_skill()
    granter = User(
        name="BEV Policy Granter",
        email="bev-policy-granter@example.com",
        password_hash="not-used",
        role="administrator",
        active=True,
    )
    db_session.add(granter)
    db_session.commit()
    db_session.refresh(granter)
    PolicyAuthorityGrantService(db_session).create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_utc_now() + timedelta(hours=24),
    )

    due_date = date.today() - timedelta(days=3)
    account = Account(
        cliente="Cliente BEV Policy",
        email="cliente-bev-policy@example.com",
        whatsapp=None,
        valor=650,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    execution = PolicyAccountMarkOverdueExecutionService(
        db_session
    ).execute(account_id=account.id, due_date=due_date)

    service = BusinessEffectVerificationService(db_session)
    outcome = service.verify(
        policy_authority_consumption_id=(
            execution.policy_authority_consumption_id
        )
    )

    assert outcome.verification.result == "verified"
    assert (
        outcome.verification.policy_authority_consumption_id
        == execution.policy_authority_consumption_id
    )
    assert outcome.verification.approval_consumption_id is None
    assert outcome.verification.skill_key == "account.mark_overdue"

    # Replay: segunda chamada não reavalia (resultado terminal).
    replay = service.verify(
        policy_authority_consumption_id=(
            execution.policy_authority_consumption_id
        )
    )
    assert replay.duplicate is True
    assert replay.verification.id == outcome.verification.id
