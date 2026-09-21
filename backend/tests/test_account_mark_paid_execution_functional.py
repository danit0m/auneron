"""
account.mark_paid Pre-Pilot Safety Delta V1 -- cobertura funcional real
do corredor AccountMarkPaidExecutionService, executando request ->
decision -> execute de ponta a ponta via TestClient, nao inspecao de
codigo-fonte (ver tests/test_account_mark_paid_execution.py para o
contrato estatico, que permanece separado).

Prova o guard estrutural approver != executor introduzido neste mesmo
ciclo, junto com as garantias ja existentes que nunca haviam sido
exercitadas por execucao: stale concurrent authority, cross-wiring de
approval_request_id, estados de aprovacao invalidos e a fronteira real
de RBAC do corredor.

Terminologia deliberadamente amendada (Pre-Pilot Safety Gate V2): este
arquivo nao afirma isolamento de escopo por conta via ACL -- essa ACL
nao existe no sistema. "Scope" aqui se refere exclusivamente a
existencia do recurso (Account) verificada por
authorize_skill_execution().

KD-2/N2 (Governed Action Model V1): o caso "Account inexistente na
execucao" (antes FINDING-MARK-PAID-001) foi corrigido -- a rota
execute-mark-paid agora captura SkillScopeNotFoundError e traduz para
HTTP 403 governado, mesmo padrao ja usado pelas rotas de
account.mark_overdue. Prova disso esta em
test_execute_rejects_approved_authority_referencing_deleted_account.
"""

import os
from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalRequest
from app.models.approval import ApprovalDecision
from app.models.skill import SkillInvocation
from app.models.user import User
from app.repositories.skill_repository import SkillRepository
from app.services.account_mark_paid_execution_service import (
    CONSUMER_ACTOR_TYPE,
)
from app.services.account_mark_paid_execution_service import (
    CONSUMER_REFERENCE,
)
from app.services.skill_service import SkillService
from scripts.register_account_mark_paid_skill import (
    main as register_account_mark_paid_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"

# account.mark_paid e risk_level="high" -- ApprovalService.decide() exige
# decisor != solicitante para risco alto/critico. Todo teste que precisa
# de uma decisao "approved" real usa um segundo usuario para decidir,
# nunca o client autenticado (developer.test@example.com) que solicitou.
APPROVER_EMAIL = "approver.mark-paid@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-MarkPaid-123!"


def _create_user(
    db_session: Session,
    *,
    email: str,
    password: str,
    role: str = "manager",
) -> User:
    user = User(
        name=f"Usuario Teste ({email})",
        email=email,
        password_hash=hash_password(password),
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(email: str, password: str) -> TestClient:
    test_client = TestClient(app)
    test_client.headers.update(
        {API_KEY_HEADER_NAME: os.environ["API_KEY"]}
    )
    login = test_client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return test_client


def _approver_client(db_session: Session) -> TestClient:
    _create_user(
        db_session,
        email=APPROVER_EMAIL,
        password=APPROVER_PASSWORD,
        role="manager",
    )
    return _login(APPROVER_EMAIL, APPROVER_PASSWORD)


def _current_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _set_role(db_session: Session, role: str) -> User:
    user = _current_user(db_session)
    user.role = role
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_account(
    db_session: Session,
    *,
    email: str,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Mark Paid Execution Teste",
        email=email,
        whatsapp=None,
        valor=850,
        vencimento=date(2026, 6, 15),
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _mark_paid_version_id(db_session: Session) -> int:
    register_account_mark_paid_skill()
    repository = SkillRepository(db_session)
    skill = repository.find_skill_by_key("account.mark_paid")
    versions = [
        version
        for version in repository.list_versions(skill.id)
        if version.status == "published"
    ]
    assert len(versions) == 1
    return versions[0].id


def _other_skill_version_id(
    db_session: Session, *, skill_key: str
) -> int:
    """
    Publica uma Skill completamente nao relacionada (execution_mode
    read_only, sem capabilities) para provar cross-wiring de
    approval_request_id contra um skill_version_id que nao e
    account.mark_paid.
    """
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Skill nao relacionada (cross-wiring)",
        description="Skill usada apenas para provar cross-skill rejection.",
    )
    handler_name = skill_key.replace(".", "_").replace("-", "_")
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=f"app.skills.cross_wiring:{handler_name}",
        execution_mode="read_only",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
            "additionalProperties": False,
        },
    )
    return service.publish_version(draft.id, capabilities=()).version.id


def _request_mark_paid(
    client: TestClient,
    version_id: int,
    *,
    account_id: int,
    expected_status: str,
    key: str,
):
    return client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account_id,
                "expected_status": expected_status,
            }
        },
        headers={"Idempotency-Key": key},
    )


def _decide(approver_client: TestClient, request_id: int, decision: str):
    response = approver_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": decision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _execute(
    client: TestClient,
    account_id: int,
    *,
    approval_request_id: int,
    expected_status: str,
):
    return client.post(
        f"/accounts/{account_id}/execute-mark-paid",
        json={
            "approval_request_id": approval_request_id,
            "expected_status": expected_status,
        },
    )


def _counts(db_session: Session) -> tuple[int, int, int]:
    consumption_count = db_session.execute(
        select(func.count(ApprovalConsumption.id))
    ).scalar_one()
    invocation_count = db_session.execute(
        select(func.count(SkillInvocation.id))
    ).scalar_one()
    event_count = db_session.execute(
        select(func.count(AccountEvent.id))
    ).scalar_one()
    return consumption_count, invocation_count, event_count


def _approve_mark_paid_request(
    client: TestClient,
    db_session: Session,
    *,
    account: Account,
    expected_status: str,
    idempotency_key: str,
) -> int:
    version_id = _mark_paid_version_id(db_session)
    created = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status=expected_status,
        key=idempotency_key,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]
    approver = _approver_client(db_session)
    _decide(approver, request_id, "approved")
    return request_id


# ---------------------------------------------------------------------
# 1. Caminho canonico
# ---------------------------------------------------------------------


def test_canonical_success_mutates_account_and_produces_single_chain(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.canonical@example.com",
        status="aberto",
    )
    executor = _current_user(db_session)
    version_id = _mark_paid_version_id(db_session)

    created = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status="aberto",
        key="mark-paid-canonical-1",
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]

    approver = _approver_client(db_session)
    decision_payload = _decide(approver, request_id, "approved")
    decision_id = decision_payload["decision"]["decision_id"]

    before_consumptions, before_invocations, before_events = _counts(
        db_session
    )

    # Executor = mesmo usuario do requester (client), diferente do
    # approver -- preserva o modelo ja validado nos pilotos.
    response = _execute(
        client,
        account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["invocation_status"] == "succeeded"
    assert body["output"]["previous_status"] == "aberto"
    assert body["output"]["new_status"] == "pago"
    assert body["output"]["changed"] is True

    db_session.expire_all()
    reloaded = db_session.get(Account, account.id)
    assert reloaded.status == "pago"

    after_consumptions, after_invocations, after_events = _counts(
        db_session
    )
    assert after_consumptions == before_consumptions + 1
    assert after_invocations == before_invocations + 1
    assert after_events == before_events + 1

    # A cadeia persistida corresponde exatamente aos IDs retornados na
    # resposta -- nao apenas "alguma linha recem-criada".
    consumption = db_session.get(
        ApprovalConsumption, body["approval_consumption_id"]
    )
    assert consumption is not None
    assert consumption.approval_request_id == request_id
    assert consumption.approval_decision_id == decision_id
    assert consumption.skill_invocation_id == body["invocation_id"]
    assert consumption.status == "consumed"
    assert consumption.consumer_actor_type == CONSUMER_ACTOR_TYPE
    assert consumption.consumer_reference == CONSUMER_REFERENCE
    assert consumption.authority_user_id == executor.id
    assert consumption.authority_reference == f"user:{executor.id}"
    assert consumption.authority_role == executor.role
    assert consumption.finalized_at is not None
    assert consumption.error_code is None

    invocation = db_session.get(SkillInvocation, body["invocation_id"])
    assert invocation is not None
    assert invocation.skill_version_id == version_id
    assert invocation.status == "succeeded"
    assert invocation.actor_type == CONSUMER_ACTOR_TYPE
    assert invocation.actor_reference == CONSUMER_REFERENCE
    assert invocation.actor_user_id is None
    assert invocation.error_code is None
    assert invocation.output_payload["account_id"] == account.id
    assert invocation.output_payload["previous_status"] == "aberto"
    assert invocation.output_payload["new_status"] == "pago"
    assert invocation.output_payload["changed"] is True

    decision = db_session.get(ApprovalDecision, decision_id)
    assert decision is not None
    assert decision.approval_request_id == request_id
    assert decision.decision == "approved"

    event = db_session.execute(
        select(AccountEvent).where(AccountEvent.account_id == account.id)
    ).scalar_one()
    assert event.event_type == "status_changed"
    assert event.previous_status == "aberto"
    assert event.new_status == "pago"
    assert event.actor_type == "user"
    assert event.actor_user_id == executor.id
    assert event.actor_reference == f"user:{executor.id}"


# ---------------------------------------------------------------------
# 2. approver == executor
# ---------------------------------------------------------------------


def test_approver_cannot_execute_own_decision(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.approver-executor@example.com",
        status="aberto",
    )
    version_id = _mark_paid_version_id(db_session)
    created = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status="aberto",
        key="mark-paid-approver-executor-1",
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]

    approver = _approver_client(db_session)
    _decide(approver, request_id, "approved")

    before = _counts(db_session)

    # O proprio approver tenta executar sua propria decisao.
    response = _execute(
        approver,
        account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "approval_forbidden"

    db_session.expire_all()
    reloaded = db_session.get(Account, account.id)
    assert reloaded.status == "aberto"
    assert _counts(db_session) == before


# ---------------------------------------------------------------------
# 3. Autoridades concorrentes aprovadas -- primeira sucede, segunda
#    fica stale.
# ---------------------------------------------------------------------


def test_second_approved_authority_is_rejected_after_first_execution(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.stale@example.com",
        status="aberto",
    )
    version_id = _mark_paid_version_id(db_session)

    request_a = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status="aberto",
        key="mark-paid-stale-authority-a",
    )
    assert request_a.status_code == 201, request_a.text
    request_a_id = request_a.json()["request"]["request_id"]

    request_b = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status="aberto",
        key="mark-paid-stale-authority-b",
    )
    assert request_b.status_code == 201, request_b.text
    request_b_id = request_b.json()["request"]["request_id"]
    assert request_b_id != request_a_id

    approver = _approver_client(db_session)
    _decide(approver, request_a_id, "approved")
    _decide(approver, request_b_id, "approved")

    # execute A -> sucede
    response_a = _execute(
        client,
        account.id,
        approval_request_id=request_a_id,
        expected_status="aberto",
    )
    assert response_a.status_code == 200, response_a.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "pago"
    after_a = _counts(db_session)
    assert after_a[0] == 1
    assert after_a[1] == 1
    assert after_a[2] == 1

    # execute B -> stale, rejeitado
    response_b = _execute(
        client,
        account.id,
        approval_request_id=request_b_id,
        expected_status="aberto",
    )
    assert response_b.status_code == 409, response_b.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "pago"
    after_b = _counts(db_session)
    assert after_b == after_a


# ---------------------------------------------------------------------
# 4 / 6. input_digest binding -- variar account_id ou expected_status
#    diverge do aprovado. Nota mecanica: ambos os casos disparam a
#    MESMA verificacao (input_digest mismatch em
#    account_mark_paid_execution_service.py, linha ~230), nao dois
#    ramos distintos -- account_id e expected_status sao os dois unicos
#    componentes do input_payload assinado. Documentado, nao escondido.
# ---------------------------------------------------------------------


def test_execute_with_different_account_than_approved_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    approved_account = _make_account(
        db_session,
        email="cliente.mark-paid.cross-account-approved@example.com",
        status="aberto",
    )
    other_account = _make_account(
        db_session,
        email="cliente.mark-paid.cross-account-other@example.com",
        status="aberto",
    )
    request_id = _approve_mark_paid_request(
        client,
        db_session,
        account=approved_account,
        expected_status="aberto",
        idempotency_key="mark-paid-cross-account-1",
    )
    before = _counts(db_session)

    response = _execute(
        client,
        other_account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(Account, approved_account.id).status == "aberto"
    assert db_session.get(Account, other_account.id).status == "aberto"
    assert _counts(db_session) == before


def test_execute_with_different_expected_status_than_approved_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.payload-mismatch@example.com",
        status="aberto",
    )
    request_id = _approve_mark_paid_request(
        client,
        db_session,
        account=account,
        expected_status="aberto",
        idempotency_key="mark-paid-payload-mismatch-1",
    )
    before = _counts(db_session)

    response = _execute(
        client,
        account.id,
        approval_request_id=request_id,
        expected_status="atrasado",
    )

    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _counts(db_session) == before


# ---------------------------------------------------------------------
# 5. Cross-skill/version
# ---------------------------------------------------------------------


def test_execute_rejects_approval_request_from_unrelated_skill(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.cross-skill@example.com",
        status="aberto",
    )
    foreign_version_id = _other_skill_version_id(
        db_session, skill_key="cross-wiring.unrelated-skill"
    )
    created = client.post(
        f"/approvals/skill-executions/{foreign_version_id}",
        json={"input_payload": {"value": 1}},
        headers={"Idempotency-Key": "mark-paid-cross-skill-1"},
    )
    assert created.status_code == 201, created.text
    foreign_request_id = created.json()["request"]["request_id"]

    approver = _approver_client(db_session)
    _decide(approver, foreign_request_id, "approved")

    before = _counts(db_session)

    response = _execute(
        client,
        account.id,
        approval_request_id=foreign_request_id,
        expected_status="aberto",
    )

    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _counts(db_session) == before


# ---------------------------------------------------------------------
# 7. Estados de aprovacao invalidos: pending / rejected / expired
# ---------------------------------------------------------------------


def test_execute_rejects_pending_approval(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.pending@example.com",
        status="aberto",
    )
    version_id = _mark_paid_version_id(db_session)
    created = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status="aberto",
        key="mark-paid-pending-1",
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]

    before = _counts(db_session)

    response = _execute(
        client,
        account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _counts(db_session) == before


def test_execute_rejects_rejected_approval(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.rejected@example.com",
        status="aberto",
    )
    version_id = _mark_paid_version_id(db_session)
    created = _request_mark_paid(
        client,
        version_id,
        account_id=account.id,
        expected_status="aberto",
        key="mark-paid-rejected-1",
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]

    approver = _approver_client(db_session)
    _decide(approver, request_id, "rejected")

    before = _counts(db_session)

    response = _execute(
        client,
        account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _counts(db_session) == before


def test_execute_rejects_expired_approval(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.expired@example.com",
        status="aberto",
    )
    request_id = _approve_mark_paid_request(
        client,
        db_session,
        account=account,
        expected_status="aberto",
        idempotency_key="mark-paid-expired-1",
    )

    stored_request = db_session.get(ApprovalRequest, request_id)
    stored_request.expires_at = (
        stored_request.created_at + timedelta(milliseconds=1)
    )
    db_session.commit()

    before = _counts(db_session)

    response = _execute(
        client,
        account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _counts(db_session) == before


# ---------------------------------------------------------------------
# 8. RBAC no nivel da rota -- este teste prova a dependencia
#    require_permission("approval:decide") da propria rota
#    POST /accounts/{id}/execute-mark-paid, NAO uma falha interna de
#    authorize_skill_execution(). Ver achado registrado: nenhum role
#    que possua approval:decide carece de skill:execute /
#    skill:execute_mutating / clients.manage no catalogo atual de
#    ROLE_PERMISSIONS, entao esse ramo interno nao e alcancavel por
#    nenhuma combinacao real de role hoje -- nao fabricamos um role
#    artificial para forca-lo.
# ---------------------------------------------------------------------


def test_execute_requires_approval_decide_permission_at_route_level(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.rbac@example.com",
        status="aberto",
    )
    request_id = _approve_mark_paid_request(
        client,
        db_session,
        account=account,
        expected_status="aberto",
        idempotency_key="mark-paid-rbac-1",
    )

    _set_role(db_session, "viewer")

    before = _counts(db_session)

    response = _execute(
        client,
        account.id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 403, response.text

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _counts(db_session) == before


# ---------------------------------------------------------------------
# KD-2/N2 -- autoridade aprovada referenciando Account removida entre
# a aprovacao e a execucao (antes FINDING-MARK-PAID-001). A remocao da
# Account e preparacao do cenario, nao efeito do corredor -- o
# baseline e capturado DEPOIS da remocao/commit e imediatamente antes
# do POST de execucao, para que os deltas provem exclusivamente o
# comportamento de mark_paid. "Account unchanged" nao e uma
# pos-condicao aplicavel aqui, ja que a propria Account foi removida
# para criar a condicao.
# ---------------------------------------------------------------------


def test_execute_rejects_approved_authority_referencing_deleted_account(
    client: TestClient,
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.mark-paid.deleted-account@example.com",
        status="aberto",
    )
    request_id = _approve_mark_paid_request(
        client,
        db_session,
        account=account,
        expected_status="aberto",
        idempotency_key="mark-paid-deleted-account-1",
    )

    account_id = account.id
    db_session.delete(account)
    db_session.commit()
    assert db_session.get(Account, account_id) is None

    before = _counts(db_session)

    response = _execute(
        client,
        account_id,
        approval_request_id=request_id,
        expected_status="aberto",
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == (
        "Recurso inexistente ou não acessível."
    )

    assert _counts(db_session) == before
