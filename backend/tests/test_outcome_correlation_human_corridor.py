"""
DW-5 V1 -- Human-governed corridor reconstruction in outcome_correlation.py.

Prova que get_outcome_episode() reconstroi o corredor humano
(account.mark_overdue via HumanAccountMarkOverdueMaterializationService /
HumanAccountMarkOverdueExecutionService) de forma inteiramente aditiva e
independente do corredor agente ja existente: identidade positiva pelo
WorkItem.work_key exato (nunca inferida pela ausencia do corredor
agente), validacao semantica fail-closed do ponteiro
WorkItem->ApprovalRequest (presenca + existencia referencial +
consistencia de conta/skill), projecao pura da BusinessEffectVerification
do DW-3 (nunca uma segunda verificacao), e coexistencia real de
autoridades (Episode 004) representada sem que uma sobrescreva a outra.

Casos felizes usam o corredor real via HTTP + execucao direta do
service, igual a test_business_effect_verification.py /
test_nba_policy_prior_effect_review.py. Casos de anomalia semantica
manipulam diretamente WorkItem.context_data, unica forma de alcancar
esses estados (o materializer real nunca produz um ponteiro invalido).
O corredor agente reusa exatamente os helpers de insercao direta de
test_outcome_correlation.py (_insert_proposal/_insert_approval_request/
_insert_work_item_and_execution), para provar coexistencia real.
"""

import os
from dataclasses import fields as dataclass_fields
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.outcome_correlation import OutcomeEpisodeResult
from app.core.outcome_correlation import get_outcome_episode
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalRequest
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.skill import AgentSkillBinding
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.user import User
from app.models.work import WorkItem
from app.models.work_skill_execution import WorkSkillExecution
from app.services.business_effect_verification_service import (
    BusinessEffectVerificationService,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanAccountMarkOverdueExecutionService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)
from scripts.register_account_mark_paid_skill import (
    main as register_account_mark_paid_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"
APPROVER_EMAIL = "approver.outcome.human@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-Outcome-Humano-123!"
MANIFEST_DIGEST = "1" * 64


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
        name="Aprovador Outcome Humano",
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


def _authenticated_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _make_account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Outcome Corredor Humano",
        email=email,
        whatsapp=None,
        valor=1000,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _materialize(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    days_overdue: int = 10,
) -> tuple[Account, date, WorkItem, ApprovalRequest]:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=days_overdue)
    account = _make_account(
        db_session, email=email, vencimento=due_date
    )

    materialize = client.post(
        "/recommendations/mark-overdue/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}/materialize"
    )
    assert materialize.status_code == 201, materialize.text

    work_item_id = materialize.json()["work_item"]["id"]
    request_id = (
        materialize.json()["approval_request"]["request_id"]
    )
    db_session.expire_all()
    work_item = db_session.get(WorkItem, work_item_id)
    approval_request = db_session.get(ApprovalRequest, request_id)
    return account, due_date, work_item, approval_request


def _materialize_and_approve(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    days_overdue: int = 10,
) -> tuple[Account, date, WorkItem, ApprovalRequest]:
    account, due_date, work_item, approval_request = _materialize(
        client, db_session, email=email, days_overdue=days_overdue
    )
    approver = _approver_client(db_session)
    decide = approver.post(
        f"/approvals/{approval_request.id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text
    db_session.expire_all()
    approval_request = db_session.get(
        ApprovalRequest, approval_request.id
    )
    return account, due_date, work_item, approval_request


def _materialize_approve_execute(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    days_overdue: int = 10,
) -> tuple[Account, date, WorkItem, ApprovalRequest, int]:
    account, due_date, work_item, approval_request = (
        _materialize_and_approve(
            client,
            db_session,
            email=email,
            days_overdue=days_overdue,
        )
    )
    authority = _authenticated_user(db_session)
    service = HumanAccountMarkOverdueExecutionService(db_session)
    result = service.execute(
        account_id=account.id,
        due_date=due_date,
        authority_user_id=authority.id,
    )
    db_session.expire_all()
    approval_request = db_session.get(
        ApprovalRequest, approval_request.id
    )
    return (
        account,
        due_date,
        work_item,
        approval_request,
        result.approval_consumption_id,
    )


def _corrupt_approval_request_id(
    db_session: Session, work_item: WorkItem, value
) -> None:
    context = dict(work_item.context_data or {})
    if value is None:
        context.pop("approval_request_id", None)
    else:
        context["approval_request_id"] = value
    work_item.context_data = context
    db_session.commit()
    db_session.refresh(work_item)


# --- agent-corridor direct-insertion helpers, mirroring
#     test_outcome_correlation.py exactly ------------------------------


def _seed_agent_overdue_skill(
    db_session: Session, *, created_by_user_id: int
) -> SkillVersion:
    skill = SkillDefinition(
        skill_key="account.mark_overdue",
        provider="auneron.core.agent-fixture",
        display_name="Marcar conta como atrasada (agente, fixture)",
        description="Fixture da suite de teste para o corredor agente.",
        status="active",
        created_by_user_id=created_by_user_id,
    )
    db_session.add(skill)
    db_session.flush()

    version = SkillVersion(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.account:mark_overdue",
        execution_mode="mutating",
        manifest_digest=MANIFEST_DIGEST,
        status="published",
        published_at=_utc_now(),
        created_by_user_id=created_by_user_id,
    )
    db_session.add(version)
    db_session.flush()

    db_session.add(
        SkillCapability(
            skill_version_id=version.id,
            capability_key="account.status.mark_overdue",
            access_mode="write",
            resource_scope="account",
            required=True,
        )
    )
    db_session.add(
        AgentSkillBinding(
            agent_name="OverdueDetectionAgent",
            skill_version_id=version.id,
            priority=100,
            enabled=True,
            created_by_user_id=created_by_user_id,
        )
    )
    db_session.commit()
    return version


def _insert_agent_proposal(
    db_session: Session,
    *,
    account_id: int,
    due_date: date,
    attempt: int,
    authority_user_id: int,
) -> AuthenticatedAdvisoryProposal:
    proposal = AuthenticatedAdvisoryProposal(
        authority_user_id=authority_user_id,
        auth_session_id=None,
        authority_source="system_principal",
        protocol="system_advisory_v1",
        idempotency_key=(
            f"conta_vencida:{account_id}:{due_date.isoformat()}:"
            f"attempt:{attempt}"
        ),
        snapshot_payload={"account_id": account_id},
        snapshot_digest="a" * 64,
        agent_count=1,
        binding_count=1,
        snapshot_bytes=10,
    )
    db_session.add(proposal)
    db_session.commit()
    db_session.refresh(proposal)
    return proposal


def _insert_agent_approval_request(
    db_session: Session,
    *,
    proposal_id: int,
    binding_id: int,
    skill_version_id: int,
    account_id: int,
    status: str = "pending",
) -> ApprovalRequest:
    now = _utc_now()
    request = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=skill_version_id,
        requester_actor_type="agent",
        requester_reference="agent:OverdueDetectionAgent",
        requester_user_id=None,
        idempotency_key=f"advisory:{proposal_id}:{binding_id}",
        request_fingerprint="b" * 64,
        input_digest="c" * 64,
        risk_level="medium",
        required_permission="approval:decide",
        status=status,
        target_account_id=account_id,
        target_user_id=None,
        expires_at=now + timedelta(hours=24),
        resolved_at=now if status != "pending" else None,
    )
    db_session.add(request)
    db_session.commit()
    db_session.refresh(request)
    return request


# --- 1. Human identity -----------------------------------------------


def test_exact_episode_key_resolves_human_work_item(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, _ = _materialize_approve_execute(
        client, db_session, email="id.match@example.com"
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage != "absent"
    assert result.human_execution.linkage != "absent"


def test_different_account_does_not_match(
    client: TestClient, db_session: Session
) -> None:
    _, due_date, _, _, _ = _materialize_approve_execute(
        client, db_session, email="id.other-account-a@example.com"
    )
    other_account = _make_account(
        db_session,
        email="id.other-account-b@example.com",
        vencimento=due_date,
    )

    result = get_outcome_episode(
        db_session, account=other_account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"
    assert result.human_execution.linkage == "absent"
    assert result.effect_verification.linkage == "absent"


def test_different_due_date_does_not_match(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, _ = _materialize_approve_execute(
        client, db_session, email="id.other-due-date@example.com"
    )
    other_due_date = due_date - timedelta(days=100)

    result = get_outcome_episode(
        db_session, account=account, due_date=other_due_date
    )

    assert result.human_approval.linkage == "absent"
    assert result.human_execution.linkage == "absent"
    assert result.effect_verification.linkage == "absent"


def test_absence_of_human_work_item_does_not_infer_human_corridor(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(
        db_session,
        email="id.no-human-evidence@example.com",
        vencimento=due_date,
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"
    assert result.human_execution.linkage == "absent"
    assert result.effect_verification.linkage == "absent"
    assert result.recommendation.linkage == "absent"
    assert result.approval.linkage == "absent"
    assert result.execution.linkage == "absent"


# --- 2. Semantic validation --------------------------------------------


def test_missing_approval_request_id_yields_absent(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, work_item, _ = _materialize(
        client, db_session, email="sem.missing-pointer@example.com"
    )
    _corrupt_approval_request_id(db_session, work_item, None)

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"


def test_nonexistent_approval_request_yields_absent(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, work_item, _ = _materialize(
        client, db_session, email="sem.nonexistent-pointer@example.com"
    )
    _corrupt_approval_request_id(db_session, work_item, 999_999_999)

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"


def test_wrong_target_account_id_yields_absent(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, work_item, _ = _materialize(
        client, db_session, email="sem.wrong-account-a@example.com"
    )
    _, _, _, other_request = _materialize(
        client, db_session, email="sem.wrong-account-b@example.com"
    )
    _corrupt_approval_request_id(
        db_session, work_item, other_request.id
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"


def test_wrong_skill_yields_absent(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, work_item, _ = _materialize(
        client, db_session, email="sem.wrong-skill@example.com"
    )

    register_account_mark_paid_skill()
    from app.repositories.skill_repository import SkillRepository

    repository = SkillRepository(db_session)
    mark_paid_skill = repository.find_skill_by_key(
        "account.mark_paid"
    )
    mark_paid_versions = [
        version
        for version in repository.list_versions(
            mark_paid_skill.id
        )
        if version.status == "published"
    ]
    mark_paid_version_id = mark_paid_versions[0].id

    mark_paid_account = _make_account(
        db_session,
        email="sem.wrong-skill-paid@example.com",
        vencimento=due_date,
    )
    request = client.post(
        f"/approvals/skill-executions/{mark_paid_version_id}",
        json={
            "input_payload": {
                "account_id": mark_paid_account.id,
                "expected_status": "aberto",
            }
        },
        headers={
            "Idempotency-Key": (
                f"outcome-wrong-skill-{mark_paid_account.id}"
            )
        },
    )
    assert request.status_code == 201, request.text
    mark_paid_request_id = request.json()["request"]["request_id"]

    _corrupt_approval_request_id(
        db_session, work_item, mark_paid_request_id
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"


# --- 3. Human approval ---------------------------------------------------


def test_pending_request_is_represented(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, approval_request = _materialize(
        client, db_session, email="approval.pending@example.com"
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "correlated"
    assert result.human_approval.status == approval_request.status
    assert result.human_approval.decided_at is None


def test_approved_decision_is_represented(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _ = _materialize_and_approve(
        client, db_session, email="approval.approved@example.com"
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.decided_at is not None
    assert (
        result.human_approval.decided_by_reference is not None
    )


def test_consumed_request_is_represented(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, _ = _materialize_approve_execute(
        client, db_session, email="approval.consumed@example.com"
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    sources = {item.source for item in result.human_approval.evidence}
    assert "approval_consumption" in sources
    assert "approval_decision" in sources


# --- 4. Human execution ---------------------------------------------------


def test_consumed_reconstructs_skill_invocation(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, _ = _materialize_approve_execute(
        client, db_session, email="execution.consumed@example.com"
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_execution.linkage == "correlated"
    assert result.human_execution.status == "succeeded"
    assert result.human_execution.finished_at is not None
    assert result.human_execution.evidence[0].source == (
        "skill_invocation"
    )
    assert result.human_execution.evidence[0].linkage == "direct"


def test_execution_success_does_not_imply_verified_effect(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, _ = _materialize_approve_execute(
        client,
        db_session,
        email="execution.not-verified-yet@example.com",
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_execution.status == "succeeded"
    assert result.effect_verification.linkage == "absent"
    assert result.effect_verification.result is None


# --- 5. Business Effect Verification --------------------------------------


def test_bev_pending_projected(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, consumption_id = (
        _materialize_approve_execute(
            client, db_session, email="bev.pending@example.com"
        )
    )
    events = (
        db_session.query(AccountEvent)
        .filter(AccountEvent.account_id == account.id)
        .all()
    )
    assert len(events) == 1
    db_session.delete(events[0])
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "pending"

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.effect_verification.linkage == "correlated"
    assert result.effect_verification.result == "pending"


def test_bev_verified_projected(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, consumption_id = (
        _materialize_approve_execute(
            client, db_session, email="bev.verified@example.com"
        )
    )

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "verified"

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.effect_verification.result == "verified"
    assert result.effect_verification.checked_at is not None


def test_bev_contradicted_projected(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, consumption_id = (
        _materialize_approve_execute(
            client, db_session, email="bev.contradicted@example.com"
        )
    )
    account.status = "aberto"
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "contradicted"

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.effect_verification.result == "contradicted"


def test_bev_unverifiable_is_absent_from_dw5_when_account_link_breaks(
    client: TestClient, db_session: Session
) -> None:
    """
    Achado real, nao bug de fixture: quando
    ApprovalRequest.target_account_id vira NULL, o DW-3 corretamente
    classifica a BEV como 'unverifiable' (identidade de verificacao
    ausente). Mas o MESMO campo tambem alimenta a validacao semantica
    do DW-5 (#4 do Design Freeze: target_account_id == account_id) --
    com o campo NULL, o DW-5 nao consegue mais confirmar que o
    ApprovalRequest pertence a esta conta, entao a cadeia inteira
    (human_approval/human_execution/effect_verification) cai para
    'absent' em vez de projetar 'unverifiable'. Isso e o comportamento
    fail-closed correto e esperado -- os dois mecanismos usam o mesmo
    campo para perguntas diferentes, e a resposta mais conservadora
    (ausencia total) prevalece.
    """
    account, due_date, _, approval_request, consumption_id = (
        _materialize_approve_execute(
            client, db_session, email="bev.unverifiable@example.com"
        )
    )
    approval_request.target_account_id = None
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "unverifiable"

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.human_approval.linkage == "absent"
    assert result.human_execution.linkage == "absent"
    assert result.effect_verification.linkage == "absent"
    assert result.effect_verification.result is None


def test_bev_account_event_fk_projected_not_rediscovered(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, consumption_id = (
        _materialize_approve_execute(
            client,
            db_session,
            email="bev.account-event-fk@example.com",
        )
    )
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    account_event_evidence = [
        item
        for item in result.effect_verification.evidence
        if item.source == "account_event"
    ]
    assert len(account_event_evidence) == 1
    assert account_event_evidence[0].linkage == "direct"


# --- 6. Coexistence -------------------------------------------------------


def test_agent_only_preserves_existing_behavior(
    db_session: Session,
) -> None:
    admin = _create_approver(db_session)
    due_date = date.today() - timedelta(days=10)
    account = _make_account(
        db_session,
        email="coexist.agent-only@example.com",
        vencimento=due_date,
        status="aberto",
    )
    version = _seed_agent_overdue_skill(
        db_session, created_by_user_id=admin.id
    )
    proposal = _insert_agent_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=1,
        authority_user_id=admin.id,
    )
    _insert_agent_approval_request(
        db_session,
        proposal_id=proposal.id,
        binding_id=1,
        skill_version_id=version.id,
        account_id=account.id,
        status="pending",
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.recommendation.linkage == "correlated"
    assert result.approval.linkage == "correlated"
    assert result.approval.status == "pending"
    assert result.human_approval.linkage == "absent"
    assert result.human_execution.linkage == "absent"
    assert result.effect_verification.linkage == "absent"


def test_human_only_has_recommendation_absent(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _, _, _ = _materialize_approve_execute(
        client, db_session, email="coexist.human-only@example.com"
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.recommendation.linkage == "absent"
    assert result.human_execution.linkage != "absent"


def test_agent_and_human_both_present_preserve_both_chains(
    client: TestClient, db_session: Session
) -> None:
    """
    Reproduz a coexistencia real observada no Episode 004: uma
    ApprovalRequest agent-originated pendente/nunca consumida coexiste
    com uma execucao humana bem-sucedida e uma BusinessEffectVerification
    'verified' para o MESMO episodio. Nenhum dos dois deve sobrescrever
    o outro.
    """
    admin = _create_approver(db_session)
    account, due_date, _, _, consumption_id = (
        _materialize_approve_execute(
            client,
            db_session,
            email="coexist.both-e004@example.com",
        )
    )
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )

    # O corredor humano ja registrou account.mark_overdue de verdade
    # (uq_skills_skill_key) -- reusa a mesma SkillVersion publicada em
    # vez de tentar semear uma segunda, o que violaria a constraint.
    from app.repositories.skill_repository import SkillRepository

    repository = SkillRepository(db_session)
    skill = repository.find_skill_by_key("account.mark_overdue")
    published = [
        candidate
        for candidate in repository.list_versions(skill.id)
        if candidate.status == "published"
    ]
    version = published[0]
    proposal = _insert_agent_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=1,
        authority_user_id=admin.id,
    )
    _insert_agent_approval_request(
        db_session,
        proposal_id=proposal.id,
        binding_id=1,
        skill_version_id=version.id,
        account_id=account.id,
        status="pending",
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    # cadeia agente: real, mas nunca consumida
    assert result.approval.linkage == "correlated"
    assert result.approval.status == "pending"
    assert result.execution.linkage == "absent"

    # cadeia humana: real, executada e verificada -- nao sobrescrita
    # pela evidencia agente, nem sobrescrevendo-a
    assert result.human_execution.status == "succeeded"
    assert result.effect_verification.result == "verified"


# --- 7. Contract -----------------------------------------------------------


def test_root_result_includes_new_fields_and_preserves_existing(
    db_session: Session,
) -> None:
    field_names = {
        field.name for field in dataclass_fields(OutcomeEpisodeResult)
    }

    assert {
        "episode",
        "detection",
        "recommendation",
        "approval",
        "execution",
        "payment",
        "derived",
        "amount",
    }.issubset(field_names)
    assert {
        "human_approval",
        "human_execution",
        "effect_verification",
    }.issubset(field_names)
