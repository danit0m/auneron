"""
Outcome Intelligence V1 -- os 8 testes conceituais obrigatorios do
Correlation Contract (congelado com Tomaz em 15/09/2026).

Usa insercao direta via ORM para os elos que precisam de controle fino
(numero de attempt, timestamps exatos), no mesmo espirito de
_insert_raw_proposal em test_f1_overdue_system_principal.py, e o fluxo
real (OverdueDetectionService + run_receivables_monitor) onde a
integracao real importa mais que o controle fino (teste 1).
"""

import json
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from app.core.authentication import hash_password
from app.core.authority_provenance import (
    SYSTEM_PRINCIPAL_CANONICAL_EMAIL,
)
from app.core.outcome_correlation import (
    _EPISODE_ATTEMPT_KEY_PATTERN as reexported_attempt_pattern,
)
from app.core.outcome_correlation import get_outcome_episode
from app.core.receivables_monitor_maintenance import (
    run_receivables_monitor,
)
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.knowledge import Knowledge
from app.models.skill import AgentSkillBinding
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.user import User
from app.models.work import WorkItem
from app.models.work_skill_execution import WorkSkillExecution
from app.services.overdue_detection_service import (
    OverdueDetectionService,
    _EPISODE_ATTEMPT_KEY_PATTERN as original_attempt_pattern,
)

MANIFEST_DIGEST = "1" * 64


def _make_account(
    db_session,
    *,
    vencimento: date,
    status: str = "aberto",
    cliente: str = "Cliente Outcome Teste",
    email: str = "cliente.outcome@example.com",
) -> Account:
    account = Account(
        cliente=cliente,
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


def _make_admin(db_session, *, email: str = "admin.outcome@example.com") -> User:
    admin = User(
        name="Admin Outcome",
        email=email,
        password_hash=hash_password("Senha-Teste-Auneron-123!"),
        role="administrator",
        active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


def _make_system_principal(db_session) -> User:
    principal = User(
        name="Sistema de vencimentos",
        email=SYSTEM_PRINCIPAL_CANONICAL_EMAIL,
        password_hash=hash_password("unusable-not-a-real-login"),
        role="system",
        active=True,
    )
    db_session.add(principal)
    db_session.commit()
    db_session.refresh(principal)
    return principal


def _seed_overdue_skill(db_session, *, created_by_user_id: int) -> SkillVersion:
    skill = SkillDefinition(
        skill_key="account.mark_overdue",
        provider="auneron.core",
        display_name="Marcar conta como atrasada",
        description="Governed pilot action account.mark_overdue.",
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
        published_at=datetime.now(timezone.utc),
        created_by_user_id=created_by_user_id,
    )
    db_session.add(version)
    db_session.flush()

    capability = SkillCapability(
        skill_version_id=version.id,
        capability_key="account.status.mark_overdue",
        access_mode="write",
        resource_scope="account",
        required=True,
    )
    db_session.add(capability)

    binding = AgentSkillBinding(
        agent_name="OverdueDetectionAgent",
        skill_version_id=version.id,
        priority=100,
        enabled=True,
        created_by_user_id=created_by_user_id,
    )
    db_session.add(binding)
    db_session.commit()

    return version


def _insert_proposal(
    db_session,
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


def _insert_approval_request(
    db_session,
    *,
    proposal_id: int,
    binding_id: int,
    skill_version_id: int,
    account_id: int,
    status: str = "approved",
) -> ApprovalRequest:
    now = datetime.now(timezone.utc)
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


def _insert_approval_decision(
    db_session,
    *,
    approval_request_id: int,
    decided_by_user_id: int,
    decided_by_reference: str = "admin.outcome@example.com",
) -> ApprovalDecision:
    decision = ApprovalDecision(
        approval_request_id=approval_request_id,
        decision="approved",
        decided_by_user_id=decided_by_user_id,
        decided_by_reference=decided_by_reference,
        decided_by_role="administrator",
        permission_used="approval:decide",
    )
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)
    return decision


def _insert_work_item_and_execution(
    db_session,
    *,
    account_id: int,
    proposal_id: int,
    skill_version_id: int,
) -> tuple[WorkItem, WorkSkillExecution]:
    work_item = WorkItem(
        work_type="task",
        title="Outcome teste -- execucao governada",
        scope_type="account",
        account_id=account_id,
        origin_type="agent",
        origin_reference=f"advisory_proposal:{proposal_id}",
    )
    db_session.add(work_item)
    db_session.commit()
    db_session.refresh(work_item)

    execution = WorkSkillExecution(
        work_item_id=work_item.id,
        skill_version_id=skill_version_id,
        authority_role="administrator",
        actor_type="system",
        actor_reference="system:work:outcome-test",
        dispatch_key=f"outcome-test-{work_item.id}",
        execution_mode="mutating",
        input_digest="d" * 64,
        status="configured",
    )
    db_session.add(execution)
    db_session.commit()
    db_session.refresh(execution)

    return work_item, execution


def _insert_knowledge(
    db_session,
    *,
    account_id: int,
    due_date: date,
    state: str = "overdue",
    created_at: datetime | None = None,
) -> Knowledge:
    knowledge = Knowledge(
        agent_name="ReceivablesMonitorAgent",
        event_name="receivable_lifecycle_changed",
        knowledge_type="receivable_lifecycle",
        severity="high",
        title="Pagamento atrasado -- teste",
        message="Vencido -- fixture de teste.",
        account_id=account_id,
        correlation_key=(
            f"receivable_lifecycle:v1:{account_id}:"
            f"{due_date.isoformat()}:{state}"
        ),
        resolved=False,
    )
    db_session.add(knowledge)
    db_session.commit()
    db_session.refresh(knowledge)

    if created_at is not None:
        db_session.execute(
            Knowledge.__table__.update()
            .where(Knowledge.id == knowledge.id)
            .values(created_at=created_at)
        )
        db_session.commit()
        db_session.refresh(knowledge)

    return knowledge


def _insert_account_event(
    db_session,
    *,
    account_id: int,
    occurred_at: datetime,
    new_status: str = "pago",
    previous_status: str = "aberto",
) -> AccountEvent:
    event = AccountEvent(
        account_id=account_id,
        event_type="status_changed",
        actor_type="system",
        actor_reference="system:outcome-test",
        previous_status=previous_status,
        new_status=new_status,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    db_session.execute(
        AccountEvent.__table__.update()
        .where(AccountEvent.id == event.id)
        .values(occurred_at=occurred_at)
    )
    db_session.commit()
    db_session.refresh(event)

    return event


# ---------------------------------------------------------------------
# 1. Isolamento entre episodios do mesmo cliente
# ---------------------------------------------------------------------


def test_episode_isolation_between_accounts_of_same_client(
    db_session,
) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    due_date_a = date.today() - timedelta(days=5)
    due_date_b = date.today() - timedelta(days=12)

    account_a = _make_account(
        db_session,
        vencimento=due_date_a,
        cliente="Cliente Isolamento A",
    )
    account_b = _make_account(
        db_session,
        vencimento=due_date_b,
        cliente="Cliente Isolamento B",
    )

    run_receivables_monitor()

    scan = OverdueDetectionService(db_session).run_scan()
    assert scan.accounts_checked == 2

    result_a = get_outcome_episode(
        db_session, account=account_a, due_date=due_date_a
    )
    result_b = get_outcome_episode(
        db_session, account=account_b, due_date=due_date_b
    )

    ids_a = {
        ev.id
        for ev in (
            result_a.detection.evidence
            + result_a.recommendation.evidence
        )
    }
    ids_b = {
        ev.id
        for ev in (
            result_b.detection.evidence
            + result_b.recommendation.evidence
        )
    }

    assert result_a.episode.account_id == account_a.id
    assert result_b.episode.account_id == account_b.id
    assert ids_a.isdisjoint(ids_b), (
        "evidencia do episodio A vazou para o episodio B (ou vice-versa)"
    )
    assert result_a.recommendation.linkage == "correlated"
    assert result_b.recommendation.linkage == "correlated"


# ---------------------------------------------------------------------
# 2. latest_proposal_attempt vs approval_source_attempt
# ---------------------------------------------------------------------


def test_execution_follows_approval_source_attempt_not_latest(
    db_session,
) -> None:
    admin = _make_admin(db_session)
    version = _seed_overdue_skill(
        db_session, created_by_user_id=admin.id
    )

    due_date = date.today() - timedelta(days=20)
    account = _make_account(db_session, vencimento=due_date)

    proposal_1 = _insert_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=1,
        authority_user_id=admin.id,
    )
    proposal_2 = _insert_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=2,
        authority_user_id=admin.id,
    )
    _insert_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=3,
        authority_user_id=admin.id,
    )
    # attempt 3 (a mais recente) nunca gerou ApprovalRequest --
    # simula a falha transitoria mencionada em accounts.py.

    approval_request = _insert_approval_request(
        db_session,
        proposal_id=proposal_2.id,
        binding_id=1,
        skill_version_id=version.id,
        account_id=account.id,
        status="approved",
    )
    _insert_approval_decision(
        db_session,
        approval_request_id=approval_request.id,
        decided_by_user_id=admin.id,
    )

    # WorkItem existe apenas para o proposal_id do attempt 2 -- se a
    # execucao (incorretamente) seguisse o attempt 3, isto ficaria
    # "absent" mesmo havendo uma execucao real disponivel.
    _insert_work_item_and_execution(
        db_session,
        account_id=account.id,
        proposal_id=proposal_2.id,
        skill_version_id=version.id,
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.recommendation.latest_proposal_attempt == 3
    assert result.approval.approval_source_attempt == 2
    assert result.approval.attempt_mismatch is True
    assert result.execution.linkage == "correlated"
    assert result.execution.sourced_from_attempt == 2

    work_item_ids = {
        ev.id
        for ev in result.execution.evidence
        if ev.source == "work_item"
    }
    assert work_item_ids, (
        "execucao deveria ter encontrado o WorkItem do attempt 2, "
        "nao ficar ausente"
    )

    # proposal_1 nunca deveria ser usado como fonte -- so entra como
    # candidato descartado pelo loop decrescente.
    approval_evidence_ids = {
        ev.id
        for ev in result.approval.evidence
        if ev.source == "approval_request"
    }
    assert approval_request.id in approval_evidence_ids
    assert proposal_1.id not in {
        ev.id
        for ev in result.recommendation.evidence
        if ev.linkage == "direct"
    }


# ---------------------------------------------------------------------
# 3. outcome_correlation_v1: "primeiro depois", nunca "mais proximo"
# ---------------------------------------------------------------------


def test_payment_correlation_picks_first_after_not_nearest(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    detection_ts = datetime.now(timezone.utc) - timedelta(days=8)

    _insert_knowledge(
        db_session,
        account_id=account.id,
        due_date=due_date,
        state="overdue",
        created_at=detection_ts,
    )

    near_but_before = _insert_account_event(
        db_session,
        account_id=account.id,
        occurred_at=detection_ts - timedelta(hours=1),
    )
    far_but_after = _insert_account_event(
        db_session,
        account_id=account.id,
        occurred_at=detection_ts + timedelta(days=10),
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.payment.linkage == "correlated"
    assert result.payment.occurred_at == far_but_after.occurred_at
    assert result.payment.occurred_at != near_but_before.occurred_at

    evidence_ids = {ev.id for ev in result.payment.evidence}
    assert far_but_after.id in evidence_ids
    assert near_but_before.id not in evidence_ids


# ---------------------------------------------------------------------
# 4. vencimento editado -> unresolved_due_date_change
# ---------------------------------------------------------------------


def test_due_date_change_yields_unresolved_not_correlated(
    db_session,
) -> None:
    original_due_date = date.today() - timedelta(days=15)
    account = _make_account(db_session, vencimento=original_due_date)

    _insert_knowledge(
        db_session,
        account_id=account.id,
        due_date=original_due_date,
        state="overdue",
    )

    account.vencimento = date.today() + timedelta(days=30)
    db_session.commit()

    paid_event = _insert_account_event(
        db_session,
        account_id=account.id,
        occurred_at=datetime.now(timezone.utc),
    )

    result = get_outcome_episode(
        db_session, account=account, due_date=original_due_date
    )

    assert result.payment.linkage == "unresolved_due_date_change"
    assert result.payment.occurred_at is None
    assert result.payment.evidence == ()

    candidate_ids = {
        ev.id for ev in result.payment.candidate_evidence
    }
    assert paid_event.id in candidate_ids


# ---------------------------------------------------------------------
# 5. Ausencia em cada estagio isoladamente
# ---------------------------------------------------------------------


def test_absence_no_recommendation_no_approval_no_execution(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=3)
    account = _make_account(db_session, vencimento=due_date)

    _insert_knowledge(
        db_session,
        account_id=account.id,
        due_date=due_date,
        state="overdue",
    )
    # Nenhuma Proposal criada para este episodio.

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.detection.linkage == "direct"
    assert result.recommendation.linkage == "absent"
    assert result.approval.linkage == "absent"
    assert result.execution.linkage == "absent"


def test_absence_proposal_without_approval_request(
    db_session,
) -> None:
    admin = _make_admin(db_session)
    due_date = date.today() - timedelta(days=4)
    account = _make_account(db_session, vencimento=due_date)

    _insert_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=1,
        authority_user_id=admin.id,
    )
    # Nenhum ApprovalRequest criado para esta Proposal.

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.recommendation.linkage == "correlated"
    assert result.approval.linkage == "absent"
    assert result.execution.linkage == "absent"


def test_absence_approved_without_execution(db_session) -> None:
    admin = _make_admin(db_session)
    version = _seed_overdue_skill(
        db_session, created_by_user_id=admin.id
    )
    due_date = date.today() - timedelta(days=6)
    account = _make_account(db_session, vencimento=due_date)

    proposal = _insert_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=1,
        authority_user_id=admin.id,
    )
    approval_request = _insert_approval_request(
        db_session,
        proposal_id=proposal.id,
        binding_id=1,
        skill_version_id=version.id,
        account_id=account.id,
        status="approved",
    )
    _insert_approval_decision(
        db_session,
        approval_request_id=approval_request.id,
        decided_by_user_id=admin.id,
    )
    # Nenhum WorkItem/WorkSkillExecution -- aprovado, mas nao
    # despachado ainda.

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.approval.linkage == "correlated"
    assert result.approval.status == "approved"
    assert result.execution.linkage == "absent"


def test_absence_no_payment(db_session) -> None:
    due_date = date.today() - timedelta(days=2)
    account = _make_account(db_session, vencimento=due_date)

    _insert_knowledge(
        db_session,
        account_id=account.id,
        due_date=due_date,
        state="overdue",
    )
    # Nenhum AccountEvent(pago) para esta conta.

    result = get_outcome_episode(
        db_session, account=account, due_date=due_date
    )

    assert result.payment.linkage == "absent"
    assert result.payment.occurred_at is None
    assert result.derived.days_detection_to_payment is None


# ---------------------------------------------------------------------
# 6. Reaproveitamento do regex existente (nao redefine copia)
# ---------------------------------------------------------------------


def test_reuses_same_attempt_key_pattern_object_as_f1() -> None:
    assert reexported_attempt_pattern is original_attempt_pattern


# ---------------------------------------------------------------------
# 7. Nenhuma linguagem causal proibida
# ---------------------------------------------------------------------


BANNED_TERMS = (
    "recuperado",
    "recuperou",
    "recuperados",
    "causou",
    "causada",
    "causada por",
)


def test_no_causal_language_anywhere_in_openapi_schema() -> None:
    schema_text = json.dumps(
        app.openapi(), ensure_ascii=False
    ).lower()

    for term in BANNED_TERMS:
        assert term not in schema_text, (
            f"termo causal proibido encontrado no schema: {term!r}"
        )


# ---------------------------------------------------------------------
# 8. Prova de read-only no banco
# ---------------------------------------------------------------------

_TRACKED_TABLES = (
    "accounts",
    "knowledge",
    "authenticated_advisory_proposals",
    "approval_requests",
    "approval_decisions",
    "work_items",
    "work_skill_executions",
    "skill_invocations",
    "account_events",
)


def _table_counts(db_session) -> dict[str, int]:
    from sqlalchemy import text

    return {
        table: db_session.execute(
            text(f"SELECT count(*) FROM {table}")
        ).scalar_one()
        for table in _TRACKED_TABLES
    }


def test_get_outcome_episode_writes_nothing(db_session) -> None:
    admin = _make_admin(db_session)
    version = _seed_overdue_skill(
        db_session, created_by_user_id=admin.id
    )
    due_date = date.today() - timedelta(days=7)
    account = _make_account(db_session, vencimento=due_date)

    _insert_knowledge(
        db_session,
        account_id=account.id,
        due_date=due_date,
        state="overdue",
    )
    proposal = _insert_proposal(
        db_session,
        account_id=account.id,
        due_date=due_date,
        attempt=1,
        authority_user_id=admin.id,
    )
    approval_request = _insert_approval_request(
        db_session,
        proposal_id=proposal.id,
        binding_id=1,
        skill_version_id=version.id,
        account_id=account.id,
        status="approved",
    )
    _insert_approval_decision(
        db_session,
        approval_request_id=approval_request.id,
        decided_by_user_id=admin.id,
    )
    _insert_work_item_and_execution(
        db_session,
        account_id=account.id,
        proposal_id=proposal.id,
        skill_version_id=version.id,
    )
    _insert_account_event(
        db_session,
        account_id=account.id,
        occurred_at=datetime.now(timezone.utc),
    )

    before = _table_counts(db_session)

    get_outcome_episode(db_session, account=account, due_date=due_date)
    get_outcome_episode(db_session, account=account, due_date=due_date)

    after = _table_counts(db_session)

    assert before == after, (
        "get_outcome_episode escreveu no banco -- deveria ser "
        "estritamente read-only"
    )
