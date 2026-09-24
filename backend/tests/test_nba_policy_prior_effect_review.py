"""
DW-4 V1 -- Prior Effect Contradiction Review (R4).

Prova que nba_policy.get_nba_decision() consome, exclusivamente para o
episodio exato (account_id, due_date) consultado, a
BusinessEffectVerification do corredor real account.mark_overdue --
nunca de outro episodio, nunca de outra conta, nunca de outra skill,
nunca de WorkOutcomeEvaluation/learning_signal. Somente result ==
"contradicted" produz requires_human_review=True + human_review_reasons
== ("prior_effect_contradiction",) + "prior_effect_contradiction_review"
em applied_rules. Todos os demais estados (ausencia, pending,
unverifiable, verified) produzem exatamente o mesmo resultado que hoje:
sem revisao. R4 nunca altera decision_type/selected_actions/
recommendable_actions -- prova comparando com o resultado das regras
R0-R3 puras.

Casos felizes (verified/pending/contradicted a partir de dados reais)
usam o corredor real via HTTP + HumanAccountMarkOverdueExecutionService,
igual a test_business_effect_verification.py -- nunca fabricam uma BEV
positiva diretamente. O caso de anomalia de cardinalidade e o de skill
incorreta manipulam diretamente o estado subsequente, pois as UNIQUE
constraints tornam esses estados inatingiveis pelo corredor real --
exatamente como o Discovery/Design Freeze documentou.
"""

import hashlib
import inspect
import os
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session

from app.core import nba_policy
from app.core.authentication import hash_password
from app.core.nba_policy import get_nba_decision
from app.core.security import API_KEY_HEADER_NAME
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
from app.repositories.skill_repository import SkillRepository
from app.services.business_effect_verification_service import (
    BusinessEffectVerificationService,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanAccountMarkOverdueExecutionService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"
APPROVER_EMAIL = "approver.r4@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-R4-123!"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _create_approver(db_session: Session) -> User:
    existing = (
        db_session.query(User)
        .filter(User.email == APPROVER_EMAIL)
        .one_or_none()
    )
    if existing is not None:
        return existing

    user = User(
        name="Aprovador R4",
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
        cliente="Cliente R4",
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


def _execute_mark_overdue(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    days_overdue: int = 10,
) -> tuple[Account, date, int]:
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

    approver = _approver_client(db_session)
    request_id = (
        materialize.json()["approval_request"]["request_id"]
    )
    decide = approver.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text

    authority = _authenticated_user(db_session)
    service = HumanAccountMarkOverdueExecutionService(db_session)
    result = service.execute(
        account_id=account.id,
        due_date=due_date,
        authority_user_id=authority.id,
    )
    return account, due_date, result.approval_consumption_id


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


def _mark_overdue_version_id(db_session: Session) -> int:
    register_account_mark_overdue_skill()
    repository = SkillRepository(db_session)
    skill = repository.find_skill_by_key(
        "account.mark_overdue"
    )
    versions = [
        version
        for version in repository.list_versions(skill.id)
        if version.status == "published"
    ]
    assert len(versions) == 1
    return versions[0].id


def _insert_duplicate_verification_same_episode(
    db_session: Session,
    *,
    account: Account,
    due_date: date,
    skill_version_id: int,
    suffix: str,
) -> BusinessEffectVerification:
    """
    Constroi uma SEGUNDA cadeia ApprovalRequest->ApprovalConsumption->
    BusinessEffectVerification para o MESMO episodio (mesma
    idempotency_key exata do materializer), usando um requester_reference
    diferente -- unica forma de alcancar esse estado, ja que
    ApprovalRequest.idempotency_key so e UNIQUE por
    (requester_actor_type, requester_reference, idempotency_key), nao
    globalmente. Em producao isso nunca ocorre porque
    uq_work_items_account_key barra um segundo requester antes que ele
    chegue a criar uma ApprovalRequest -- exatamente a ressalva de
    cardinalidade registrada no Design Freeze.
    """
    exact_key = (
        "human_mark_overdue_approval:v1:"
        f"{account.id}:{due_date.isoformat()}"
    )
    now = _utc_now()
    existing_event = _only_account_event(db_session, account.id)

    request = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=skill_version_id,
        requester_actor_type="user",
        requester_reference=f"user:dup-{suffix}",
        requester_user_id=None,
        idempotency_key=exact_key,
        request_fingerprint=_hex64(f"dup-request-{suffix}"),
        input_digest=_hex64(f"dup-input-{suffix}"),
        risk_level="high",
        required_permission="approval:decide",
        status="approved",
        target_account_id=account.id,
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
        decided_by_reference="system:dup-reviewer",
        decided_by_role="manager",
        permission_used="approval:decide",
        decision_note=None,
        sensitive_elevation_verified=False,
    )
    db_session.add(decision)
    db_session.commit()
    db_session.refresh(decision)

    invocation = SkillInvocation(
        skill_version_id=skill_version_id,
        actor_type="user",
        actor_reference=f"user:dup-{suffix}",
        actor_user_id=None,
        idempotency_key=f"dup-invocation-{suffix}",
        request_fingerprint=_hex64(f"dup-invocation-fp-{suffix}"),
        input_digest=_hex64(f"dup-input-{suffix}"),
        status="succeeded",
        output_payload={"duplicate": True},
        output_digest=_hex64(f"dup-output-{suffix}"),
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
        consumer_reference=f"system:dup-{suffix}",
        authority_user_id=None,
        authority_reference="system:dup",
        authority_role="manager",
        runtime_idempotency_key=f"dup-consumption-{suffix}",
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

    verification = BusinessEffectVerification(
        approval_consumption_id=consumption.id,
        skill_key="account.mark_overdue",
        target_account_id=account.id,
        expected_status="atrasado",
        account_event_id=existing_event.id,
        account_event_key_searched=f"dup-search-{suffix}",
        account_status_observed="atrasado",
        result="verified",
        checked_at=now,
    )
    db_session.add(verification)
    db_session.commit()
    db_session.refresh(verification)
    return verification


# --- 1-5: matriz de estados BEV -----------------------------------------


def test_no_bev_yields_no_review(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, _ = _execute_mark_overdue(
        client, db_session, email="r4.no-bev@example.com"
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()
    assert "prior_effect_contradiction_review" not in (
        result.applied_rules
    )


def test_pending_yields_no_review(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client, db_session, email="r4.pending@example.com"
    )
    event = _only_account_event(db_session, account.id)
    db_session.delete(event)
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "pending"

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()


def test_unverifiable_yields_no_review(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client, db_session, email="r4.unverifiable@example.com"
    )
    consumption = db_session.get(
        ApprovalConsumption, consumption_id
    )
    request = db_session.get(
        ApprovalRequest, consumption.approval_request_id
    )
    request.target_account_id = None
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "unverifiable"

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()


def test_verified_yields_no_review(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client, db_session, email="r4.verified@example.com"
    )

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "verified"

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()


def test_contradicted_triggers_review(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client, db_session, email="r4.contradicted@example.com"
    )
    account.status = "aberto"
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "contradicted"

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.requires_human_review is True
    assert result.human_review_reasons == (
        "prior_effect_contradiction",
    )
    assert (
        "prior_effect_contradiction_review"
        in result.applied_rules
    )


# --- 6-8: isolamento de episódio/conta/skill ------------------------------


def test_contradiction_different_due_date_does_not_leak(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="r4.other-due-date@example.com",
    )
    account.status = "aberto"
    db_session.commit()
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )

    other_due_date = due_date - timedelta(days=100)

    result = get_nba_decision(
        db_session, account=account, due_date=other_due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()


def test_contradiction_different_account_does_not_leak(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="r4.other-account-a@example.com",
    )
    account.status = "aberto"
    db_session.commit()
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )

    other_account = _make_account(
        db_session,
        email="r4.other-account-b@example.com",
        vencimento=due_date,
    )

    result = get_nba_decision(
        db_session, account=other_account, due_date=due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()


def test_wrong_skill_key_does_not_trigger_review(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client, db_session, email="r4.wrong-skill@example.com"
    )
    account.status = "aberto"
    db_session.commit()

    outcome = BusinessEffectVerificationService(
        db_session
    ).verify(consumption_id)
    assert outcome.verification.result == "contradicted"

    # Anomalia deliberada, inatingivel pelo corredor real: a mesma
    # proof, com skill_key mutada para fora do escopo consultado pelo
    # R4. Prova que o filtro skill_key == "account.mark_overdue" no
    # lookup e real, nao apenas assumido pela identidade do episodio.
    outcome.verification.skill_key = "account.mark_paid"
    db_session.commit()

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.requires_human_review is False
    assert result.human_review_reasons == ()


# --- 9: anomalia de cardinalidade -----------------------------------------


def test_cardinality_anomaly_fails_closed(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client, db_session, email="r4.cardinality@example.com"
    )
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )

    version_id = _mark_overdue_version_id(db_session)
    _insert_duplicate_verification_same_episode(
        db_session,
        account=account,
        due_date=due_date,
        skill_version_id=version_id,
        suffix="cardinality",
    )

    with pytest.raises(MultipleResultsFound):
        get_nba_decision(
            db_session, account=account, due_date=due_date
        )


# --- 10-11: invariantes de seleção operacional ----------------------------


def test_contradiction_does_not_change_selected_actions(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="r4.selected-actions@example.com",
        days_overdue=10,
    )

    before = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    account.status = "aberto"
    db_session.commit()
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )
    account.status = "atrasado"
    db_session.commit()

    after = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert after.requires_human_review is True
    assert before.decision.decision_type == (
        after.decision.decision_type
    )
    assert before.decision.selected_actions == (
        after.decision.selected_actions
    )
    assert (
        set(before.applied_rules)
        == set(after.applied_rules)
        - {"prior_effect_contradiction_review"}
    )


def test_contradiction_does_not_change_recommendable_actions(
    client: TestClient, db_session: Session
) -> None:
    account, due_date, consumption_id = _execute_mark_overdue(
        client,
        db_session,
        email="r4.recommendable-actions@example.com",
        days_overdue=10,
    )

    before = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    account.status = "aberto"
    db_session.commit()
    BusinessEffectVerificationService(db_session).verify(
        consumption_id
    )
    account.status = "atrasado"
    db_session.commit()

    after = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert after.requires_human_review is True
    assert before.action_space.recommendable_actions == (
        after.action_space.recommendable_actions
    )


# --- guardrail estrutural --------------------------------------------------


def test_r4_never_references_learning_signal_or_execution_status() -> (
    None
):
    """
    Guardrail DW-4: R4 nunca deve derivar de
    WorkOutcomeEvaluation/learning_signal/WorkSkillExecution.status --
    prova estrutural via inspecao do source, mesmo estilo do guardrail
    de isolamento do DW-3.
    """
    source = inspect.getsource(nba_policy)
    for forbidden in (
        "WorkOutcomeEvaluation",
        "learning_signal",
        "WorkSkillExecution",
    ):
        assert forbidden not in source
