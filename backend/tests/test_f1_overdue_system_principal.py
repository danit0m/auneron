"""
F1 -- Overdue Governed UX / system_principal provenance.

Covers the M12 test matrix from the F1 Mechanical Gate: I6 (system is
non-interactive), the protocol/source DB constraint, the
system_principal advisory corridor end to end (entry-to-effect),
retry after a terminal ApprovalRequest, due-date changes opening a
new episode, concurrent proposal creation, the legacy orphan staying
untouched, and the V12 transient dispatch cursor not being blocked by
a permanent orphan.
"""

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database.database import engine

from app.core.authentication import NonInteractivePrincipalError
from app.core.authentication import authenticate_user
from app.core.authentication import create_session
from app.core.authentication import hash_password
from app.core.authentication import require_user_session
from app.core.authority_provenance import SYSTEM_PRINCIPAL_CANONICAL_EMAIL
from app.core.authority_provenance import SystemPrincipalIntegrityError
from app.core.authority_provenance import SystemPrincipalProvenance
from app.core.authority_provenance import SystemPrincipalUnavailableError
from app.core.authority_provenance import resolve_system_principal
from app.models.account import Account
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.skill import AgentSkillBinding
from app.models.user import User
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.authenticated_advisory_proposal_repository import (
    AuthenticatedAdvisoryProposalRepository,
)
from app.services.approval_service import ApprovalService
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovalBridgeService,
)
from app.services.overdue_detection_service import OverdueDetectionService

MANIFEST_DIGEST = (
    "9dcee461a5606d7def14d8884e06aac5415ca76d19388acbc7b1e77c96fa1b79"
)


def _make_admin(db_session, *, email: str = "admin.f1@example.com") -> User:
    admin = User(
        name="Admin F1",
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


def _make_account(
    db_session,
    *,
    days_overdue: int,
    cliente: str = "Cliente F1 Teste",
) -> Account:
    """
    days_overdue has no default on purpose: authenticated_advisory_
    proposals is not truncated by the shared clean_database fixture
    (accounts is, so account.id resets to 1 every test), so each test
    in this file must use a due date no other test in the file uses,
    or their episode keys (account_id + due_date) collide against
    leftover rows from earlier tests in the same session.
    """
    account = Account(
        cliente=cliente,
        email="cliente.f1@example.com",
        whatsapp=None,
        valor=100,
        vencimento=date.today() - timedelta(days=days_overdue),
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _seed_overdue_skill(db_session, *, created_by_user_id: int) -> SkillVersion:
    """
    Replicates the real account.mark_overdue skill/version/capability/
    binding shape (values taken from the dev-seeded row) so the
    orchestrator's OverdueDetectionAgent binding resolves for real
    inside a test, instead of mocking the skill catalog.
    """
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


# ---------------------------------------------------------------------
# I6 -- system is non-interactive
# ---------------------------------------------------------------------


def test_i6_a_authenticate_user_rejects_system_role(db_session) -> None:
    password = "Senha-Teste-Auneron-123!"
    principal = User(
        name="Sistema",
        email="i6a.system@example.com",
        password_hash=hash_password(password),
        role="system",
        active=True,
    )
    db_session.add(principal)
    db_session.commit()

    result = authenticate_user(
        db_session,
        email="i6a.system@example.com",
        password=password,
    )

    assert result is None


def test_i6_b_create_session_rejects_system_role(db_session) -> None:
    principal = User(
        name="Sistema",
        email="i6b.system@example.com",
        password_hash=hash_password("whatever"),
        role="system",
        active=True,
    )
    db_session.add(principal)
    db_session.commit()

    with pytest.raises(NonInteractivePrincipalError):
        create_session(db_session, principal)


def test_i6_c_existing_authsession_for_system_role_is_rejected_401(
    db_session,
) -> None:
    """
    Even a pre-existing AuthSession belonging to a role=system User
    (e.g. a legacy row created before I6 shipped) must not be treated
    as a valid login by require_user_session.
    """
    principal = User(
        name="Sistema",
        email="i6c.system@example.com",
        password_hash=hash_password("whatever"),
        role="system",
        active=True,
    )
    db_session.add(principal)
    db_session.flush()

    from app.models.auth_session import AuthSession
    from app.core.authentication import hash_session_token
    from app.core.authentication import utc_now

    legacy_session = AuthSession(
        user_id=principal.id,
        token_hash=hash_session_token("i6c-legacy-token"),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(legacy_session)
    db_session.commit()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        require_user_session(
            session_token="i6c-legacy-token",
            db=db_session,
        )

    assert excinfo.value.status_code == 401


def test_resolve_system_principal_fails_closed_when_missing(db_session) -> None:
    with pytest.raises(SystemPrincipalUnavailableError):
        resolve_system_principal(db_session)


def test_resolve_system_principal_fails_closed_when_role_mismatched(
    db_session,
) -> None:
    wrong_role = User(
        name="Nao e system",
        email=SYSTEM_PRINCIPAL_CANONICAL_EMAIL,
        password_hash=hash_password("whatever"),
        role="administrator",
        active=True,
    )
    db_session.add(wrong_role)
    db_session.commit()

    with pytest.raises(SystemPrincipalIntegrityError):
        resolve_system_principal(db_session)


def test_resolve_system_principal_succeeds_for_canonical_row(db_session) -> None:
    principal_user = _make_system_principal(db_session)

    resolved = resolve_system_principal(db_session)

    assert resolved.authority_user_id == principal_user.id
    assert resolved.source == "system_principal"


# ---------------------------------------------------------------------
# V13 -- protocol/source DB constraint
# ---------------------------------------------------------------------


def _insert_raw_proposal(db_session, **overrides):
    import json as _json

    defaults = dict(
        authority_user_id=1,
        auth_session_id=None,
        authority_source="system_principal",
        protocol="system_advisory_v1",
        idempotency_key="conta_vencida:1:2026-01-01:attempt:1",
        snapshot_payload={"a": 1},
        snapshot_digest="a" * 64,
        agent_count=1,
        binding_count=1,
        snapshot_bytes=10,
    )
    defaults.update(overrides)
    raw_payload = defaults["snapshot_payload"]
    defaults["snapshot_payload"] = (
        raw_payload
        if isinstance(raw_payload, str)
        else _json.dumps(raw_payload)
    )
    db_session.execute(
        text(
            """
            INSERT INTO authenticated_advisory_proposals (
                authority_user_id, auth_session_id, authority_source,
                protocol, idempotency_key, snapshot_payload,
                snapshot_digest, agent_count, binding_count,
                snapshot_bytes
            ) VALUES (
                :authority_user_id, :auth_session_id, :authority_source,
                :protocol, :idempotency_key,
                CAST(:snapshot_payload AS jsonb), :snapshot_digest,
                :agent_count, :binding_count, :snapshot_bytes
            )
            """
        ),
        defaults,
    )


def test_provenance_constraint_accepts_human_shape(db_session) -> None:
    admin = _make_admin(db_session, email="v13.human@example.com")
    from app.models.auth_session import AuthSession
    from app.core.authentication import hash_session_token
    from app.core.authentication import utc_now

    session_row = AuthSession(
        user_id=admin.id,
        token_hash=hash_session_token("v13-human-token"),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(session_row)
    db_session.flush()

    _insert_raw_proposal(
        db_session,
        authority_user_id=admin.id,
        auth_session_id=session_row.id,
        authority_source="authenticated_http_session",
        protocol="authenticated_advisory_v1",
        idempotency_key="cliente_criado:v13-human",
    )
    db_session.commit()


def test_provenance_constraint_accepts_system_shape(db_session) -> None:
    principal = _make_system_principal(db_session)

    _insert_raw_proposal(
        db_session,
        authority_user_id=principal.id,
        auth_session_id=None,
        authority_source="system_principal",
        protocol="system_advisory_v1",
        idempotency_key="conta_vencida:v13-system:2026-01-01:attempt:1",
    )
    db_session.commit()


@pytest.mark.parametrize(
    "overrides,idempotency_key",
    [
        (
            {
                "authority_source": "system_principal",
                "protocol": "authenticated_advisory_v1",
                "auth_session_id": None,
            },
            "conta_vencida:900:2026-02-01:attempt:1",
        ),
        (
            {
                "authority_source": "authenticated_http_session",
                "protocol": "authenticated_advisory_v1",
                "auth_session_id": None,
            },
            "conta_vencida:901:2026-02-01:attempt:1",
        ),
        (
            {
                "authority_source": "authenticated_http_session",
                "protocol": "system_advisory_v1",
                "auth_session_id": 1,
            },
            "conta_vencida:902:2026-02-01:attempt:1",
        ),
    ],
)
def test_provenance_constraint_rejects_hybrid_states(
    overrides,
    idempotency_key,
) -> None:
    """
    Uses its own short-lived connection/transaction, independent from
    the db_session fixture shared by every other test in this file --
    a CHECK violation deliberately aborts the transaction, and that
    must never bleed into a different test's session state.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                _insert_raw_proposal(
                    connection,
                    idempotency_key=idempotency_key,
                    **overrides,
                )
        finally:
            # pytest.raises already consumed the exception, so the
            # transaction context manager never sees it fail --
            # roll back explicitly instead of letting it try to commit
            # an aborted Postgres transaction.
            transaction.rollback()


# ---------------------------------------------------------------------
# system_principal corridor -- entry to effect
# ---------------------------------------------------------------------


def test_entry_to_effect_overdue_account_reaches_atrasado(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=101)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    scan_result = OverdueDetectionService(db_session).run_scan()

    assert scan_result.principal_available is True
    assert scan_result.accounts_checked == 1
    assert scan_result.proposals_created == 1
    assert scan_result.approvals_requested == 1
    assert scan_result.failures == 0

    proposal = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account.id}:{account.vencimento}:attempt:1"
        )
        .one()
    )
    assert proposal.authority_source == "system_principal"
    assert proposal.auth_session_id is None

    repo = ApprovalRepository(db_session)
    request = repo.find_request_by_idempotency(
        requester_actor_type="agent",
        requester_reference="agent:OverdueDetectionAgent",
        idempotency_key=f"advisory:{proposal.id}:1",
    )
    assert request is not None
    assert request.status == "pending"

    ApprovalService(db_session).decide(
        request.id,
        decider_user_id=admin.id,
        decision="approved",
    )
    db_session.commit()

    principal = resolve_system_principal(db_session)
    AuthenticatedAdvisoryProposalApprovalBridgeService(db_session).dispatch_approved_system(
        proposal_id=proposal.id,
        principal=principal,
        binding_id=1,
        input_payload={
            "account_id": account.id,
            "expected_status": "aberto",
            "expected_due_date": str(account.vencimento),
        },
        approval_request_id=request.id,
    )
    db_session.commit()

    db_session.refresh(account)
    assert account.status == "atrasado"

    # The scan must never touch Account.status itself -- reconfirm the
    # only writer of "atrasado" is the effect above, not the scanner.
    second_scan = OverdueDetectionService(db_session).run_scan()
    assert second_scan.accounts_checked == 0


def test_scan_does_not_duplicate_pending_attempt(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=102)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    first = OverdueDetectionService(db_session).run_scan()
    second = OverdueDetectionService(db_session).run_scan()

    assert first.proposals_created == 1
    assert second.proposals_created == 0
    assert second.proposals_reused == 1
    assert second.attempts_advanced == 0

    episode_proposals = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key.like(
                f"conta_vencida:{account.id}:{account.vencimento}:%"
            )
        )
        .count()
    )
    assert episode_proposals == 1


def test_rejected_attempt_opens_new_attempt_on_next_scan(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=103)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    OverdueDetectionService(db_session).run_scan()

    proposal = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key.like(
                f"conta_vencida:{account.id}:%:attempt:1"
            )
        )
        .one()
    )
    repo = ApprovalRepository(db_session)
    request = repo.find_request_by_idempotency(
        requester_actor_type="agent",
        requester_reference="agent:OverdueDetectionAgent",
        idempotency_key=f"advisory:{proposal.id}:1",
    )

    ApprovalService(db_session).decide(
        request.id,
        decider_user_id=admin.id,
        decision="rejected",
    )
    db_session.commit()

    second = OverdueDetectionService(db_session).run_scan()

    assert second.attempts_advanced == 1
    assert second.proposals_created == 1

    attempt_2 = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account.id}:{account.vencimento}:attempt:2"
        )
        .one_or_none()
    )
    assert attempt_2 is not None


def test_due_date_change_opens_a_new_episode(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=104)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    OverdueDetectionService(db_session).run_scan()

    old_due = account.vencimento
    account.vencimento = old_due - timedelta(days=3)
    db_session.commit()

    second = OverdueDetectionService(db_session).run_scan()

    assert second.proposals_created == 1

    new_episode = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account.id}:{account.vencimento}:attempt:1"
        )
        .one_or_none()
    )
    assert new_episode is not None

    old_episode = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account.id}:{old_due}:attempt:1"
        )
        .one_or_none()
    )
    assert old_episode is not None


def test_concurrent_system_proposal_creation_is_treated_as_duplicate(
    db_session,
) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=105)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    first = OverdueDetectionService(db_session).run_scan()
    # A second, independent scan call replays the same idempotency_key
    # -- this exercises the same "existing row found" branch that a
    # true concurrent INSERT/IntegrityError would also land on inside
    # SystemAdvisoryProposalService.create().
    second = OverdueDetectionService(db_session).run_scan()

    assert first.proposals_created == 1
    assert second.proposals_reused == 1

    episode_proposals = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key.like(
                f"conta_vencida:{account.id}:{account.vencimento}:%"
            )
        )
        .count()
    )
    assert episode_proposals == 1


# ---------------------------------------------------------------------
# A5 -- active legacy pending proposals block a competing F1 attempt
# ---------------------------------------------------------------------


def _insert_legacy_proposal_with_request(
    db_session,
    *,
    account,
    admin,
    session_row=None,
    request_status: str = "pending",
    request_expires_at=None,
    binding_id: int = 1,
) -> tuple[AuthenticatedAdvisoryProposal, "ApprovalRequest"]:
    """
    Reproduces the exact shape the pre-F1 /accounts/detect-overdue
    route used to persist: idempotency_key "conta_vencida:{account_id}"
    (no due_date/attempt), authority_source=authenticated_http_session,
    with a correlated ApprovalRequest (agent:OverdueDetectionAgent).
    """
    from app.core.authentication import utc_now
    from app.models.approval import ApprovalRequest

    payload = {
        "decision_name": "CONTA_VENCIDA_DETECTADA",
        "selected_agents": ["OverdueDetectionAgent"],
        "agents": [
            {
                "agent_name": "OverdueDetectionAgent",
                "bindings": [
                    {
                        "binding_id": binding_id,
                        "skill_version_id": 1,
                        "skill_id": 1,
                        "binding_priority": 100,
                        "execution_mode": "mutating",
                        "runtime_kind": "internal_python",
                    }
                ],
            }
        ],
    }

    # authenticated_http_session provenance requires a positive
    # auth_session_id at the DB level -- session_row=None simulates a
    # *dangling* reference (the session existed once, then got
    # cleaned up/deleted), not a NULL one. There is no FK enforcing
    # this, exactly like the real dev-database legacy orphan.
    dangling_session_id = 999_000 + account.id
    proposal = AuthenticatedAdvisoryProposal(
        authority_user_id=admin.id,
        auth_session_id=(
            session_row.id
            if session_row is not None
            else dangling_session_id
        ),
        authority_source="authenticated_http_session",
        protocol="authenticated_advisory_v1",
        idempotency_key=f"conta_vencida:{account.id}",
        snapshot_payload=payload,
        snapshot_digest="b" * 64,
        agent_count=1,
        binding_count=1,
        snapshot_bytes=len(str(payload)),
    )
    db_session.add(proposal)
    db_session.flush()

    request = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=1,
        requester_actor_type="agent",
        requester_reference="agent:OverdueDetectionAgent",
        requester_user_id=None,
        idempotency_key=f"advisory:{proposal.id}:{binding_id}",
        request_fingerprint=f"{proposal.id}".rjust(64, "c"),
        input_digest="d" * 64,
        risk_level="high",
        required_permission="approval:decide",
        status=request_status,
        target_account_id=account.id,
        target_user_id=None,
        expires_at=(
            request_expires_at
            if request_expires_at is not None
            else utc_now() + timedelta(hours=1)
        ),
        resolved_at=(
            None if request_status == "pending" else utc_now()
        ),
    )
    db_session.add(request)
    db_session.commit()

    return proposal, request


def test_a5_active_legacy_pending_blocks_new_attempt(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=107)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    from app.models.auth_session import AuthSession
    from app.core.authentication import hash_session_token, utc_now

    session_row = AuthSession(
        user_id=admin.id,
        token_hash=hash_session_token("a5-active-token"),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(session_row)
    db_session.flush()

    _insert_legacy_proposal_with_request(
        db_session,
        account=account,
        admin=admin,
        session_row=session_row,
        request_status="pending",
    )

    result = OverdueDetectionService(db_session).run_scan()

    assert result.legacy_active_blocked == 1
    assert result.proposals_created == 0

    new_attempt = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account.id}:{account.vencimento}:attempt:1"
        )
        .one_or_none()
    )
    assert new_attempt is None


def test_a5_expired_legacy_pending_allows_new_attempt(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=108)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    from app.models.auth_session import AuthSession
    from app.core.authentication import hash_session_token, utc_now

    session_row = AuthSession(
        user_id=admin.id,
        token_hash=hash_session_token("a5-expired-token"),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(session_row)
    db_session.flush()

    # A request cannot be inserted already-expired (expires_at must be
    # > created_at at the DB level) -- give it a real, short-lived
    # expiry instead, then simulate time having passed by calling
    # run_scan(now=...) with a timestamp past that expiry.
    _, legacy_request = _insert_legacy_proposal_with_request(
        db_session,
        account=account,
        admin=admin,
        session_row=session_row,
        request_status="pending",
        request_expires_at=utc_now() + timedelta(seconds=1),
    )

    result = OverdueDetectionService(db_session).run_scan(
        now=utc_now() + timedelta(hours=2),
    )

    assert result.legacy_active_blocked == 0
    assert result.proposals_created == 1

    db_session.refresh(legacy_request)
    assert legacy_request.status == "expired"

    new_attempt = (
        db_session.query(AuthenticatedAdvisoryProposal)
        .filter(
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account.id}:{account.vencimento}:attempt:1"
        )
        .one_or_none()
    )
    assert new_attempt is not None


def test_a5_terminal_legacy_allows_new_attempt(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=109)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    _insert_legacy_proposal_with_request(
        db_session,
        account=account,
        admin=admin,
        session_row=None,
        request_status="rejected",
    )

    result = OverdueDetectionService(db_session).run_scan()

    assert result.legacy_active_blocked == 0
    assert result.proposals_created == 1


def test_a5_approved_legacy_with_consumption_blocks(db_session) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=110)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    from app.models.auth_session import AuthSession
    from app.core.authentication import hash_session_token, utc_now
    from app.models.approval import ApprovalConsumption

    session_row = AuthSession(
        user_id=admin.id,
        token_hash=hash_session_token("a5-approved-consumed-token"),
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(session_row)
    db_session.flush()

    _, legacy_request = _insert_legacy_proposal_with_request(
        db_session,
        account=account,
        admin=admin,
        session_row=session_row,
        request_status="pending",
    )

    decision_result = ApprovalService(db_session).decide(
        legacy_request.id,
        decider_user_id=admin.id,
        decision="approved",
    )
    db_session.commit()

    consumption = ApprovalConsumption(
        approval_request_id=legacy_request.id,
        approval_decision_id=decision_result.decision.id,
        consumer_actor_type="agent",
        consumer_reference="agent:OverdueDetectionAgent",
        authority_user_id=admin.id,
        authority_reference=f"user:{admin.id}",
        authority_role="administrator",
        runtime_idempotency_key=f"effect:account.mark_overdue:approval:{legacy_request.id}",
        request_fingerprint=legacy_request.request_fingerprint,
        input_digest=legacy_request.input_digest,
        status="reserved",
    )
    db_session.add(consumption)
    db_session.commit()

    result = OverdueDetectionService(db_session).run_scan()

    assert result.legacy_active_blocked == 1
    assert result.proposals_created == 0


def test_a5_approved_legacy_permanent_orphan_allows_new_attempt(
    db_session,
) -> None:
    """
    approved + no consumption + original AuthSession missing ==
    LEGACY_PERMANENT_ORPHAN (A2) -- F1 is free to open attempt:1.
    """
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=111)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    _insert_legacy_proposal_with_request(
        db_session,
        account=account,
        admin=admin,
        session_row=None,  # auth_session_id stays NULL -- "missing"
        request_status="approved",
    )

    result = OverdueDetectionService(db_session).run_scan()

    assert result.legacy_active_blocked == 0
    assert result.proposals_created == 1


def test_a5_legacy_without_correlatable_request_fails_closed(
    db_session,
) -> None:
    admin = _make_admin(db_session)
    _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=112)
    _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    # A legacy proposal exists (correct decision/binding shape so it
    # actually reaches the ApprovalRequest correlation step) but no
    # ApprovalRequest was ever created for it -- e.g. the old route
    # persisted the proposal, then crashed/failed before requesting
    # Approval.
    _insert_raw_proposal(
        db_session,
        authority_user_id=admin.id,
        auth_session_id=999_000 + account.id,
        authority_source="authenticated_http_session",
        protocol="authenticated_advisory_v1",
        idempotency_key=f"conta_vencida:{account.id}",
        snapshot_payload={
            "decision_name": "CONTA_VENCIDA_DETECTADA",
            "selected_agents": ["OverdueDetectionAgent"],
            "agents": [
                {
                    "agent_name": "OverdueDetectionAgent",
                    "bindings": [
                        {
                            "binding_id": 1,
                            "skill_version_id": 1,
                            "skill_id": 1,
                            "binding_priority": 100,
                            "execution_mode": "mutating",
                            "runtime_kind": "internal_python",
                        }
                    ],
                }
            ],
        },
    )
    db_session.commit()

    result = OverdueDetectionService(db_session).run_scan()

    assert result.failures == 1
    assert result.proposals_created == 0
    assert result.legacy_active_blocked == 0


# ---------------------------------------------------------------------
# Legacy orphan stays untouched / V12 transient cursor
# ---------------------------------------------------------------------


def test_legacy_orphan_is_never_selected_by_system_episode_lookup(
    db_session,
) -> None:
    principal = _make_system_principal(db_session)
    account = _make_account(db_session, days_overdue=106)

    legacy_key = f"conta_vencida:{account.id}"
    _insert_raw_proposal(
        db_session,
        authority_user_id=principal.id,
        auth_session_id=None,
        authority_source="system_principal",
        protocol="system_advisory_v1",
        idempotency_key=legacy_key,
    )
    db_session.commit()

    repo = AuthenticatedAdvisoryProposalRepository(db_session)
    latest = repo.find_latest_episode_attempt(
        authority_user_id=principal.id,
        account_id=account.id,
        due_date=str(account.vencimento),
    )

    assert latest is None


def test_v12_permanent_orphan_does_not_block_later_candidates(
    db_session,
) -> None:
    """
    With an effective batch_size of 1, list_approved_agent_requests_
    without_consumption(after_id=...) must let the caller step past a
    request that keeps failing (the orphan), instead of returning the
    exact same row forever.
    """
    admin = _make_admin(db_session)
    version = _seed_overdue_skill(db_session, created_by_user_id=admin.id)

    from app.models.approval import ApprovalRequest
    from app.core.authentication import utc_now

    orphan = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=version.id,
        requester_actor_type="agent",
        requester_reference="agent:OverdueDetectionAgent",
        requester_user_id=None,
        idempotency_key="advisory:9001:1",
        request_fingerprint="f" * 64,
        input_digest="a" * 64,
        risk_level="high",
        required_permission="approval:decide",
        status="approved",
        target_account_id=None,
        target_user_id=None,
        expires_at=utc_now() + timedelta(hours=1),
        resolved_at=utc_now(),
    )
    db_session.add(orphan)
    db_session.flush()

    valid = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=version.id,
        requester_actor_type="agent",
        requester_reference="agent:OverdueDetectionAgent",
        requester_user_id=None,
        idempotency_key="advisory:9002:1",
        request_fingerprint="e" * 64,
        input_digest="b" * 64,
        risk_level="high",
        required_permission="approval:decide",
        status="approved",
        target_account_id=None,
        target_user_id=None,
        expires_at=utc_now() + timedelta(hours=1),
        resolved_at=utc_now(),
    )
    db_session.add(valid)
    db_session.commit()

    repo = ApprovalRepository(db_session)

    first_page = repo.list_approved_agent_requests_without_consumption(
        limit=1,
        after_id=0,
    )
    assert len(first_page) == 1
    assert first_page[0].id == orphan.id

    second_page = repo.list_approved_agent_requests_without_consumption(
        limit=1,
        after_id=orphan.id,
    )
    assert len(second_page) == 1
    assert second_page[0].id == valid.id

    third_page = repo.list_approved_agent_requests_without_consumption(
        limit=1,
        after_id=valid.id,
    )
    assert third_page == []
