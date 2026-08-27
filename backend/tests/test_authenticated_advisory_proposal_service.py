import ast
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.core.advisory_proposal_errors import (
    AdvisoryProposalIdempotencyConflictError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalValidationError,
)
from app.core.authority_provenance import AuthorityProvenance
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.orchestrator.advisory_envelope import (
    AuthenticatedAdvisoryEnvelope,
)
from app.orchestrator.decision import DecisionSignal
from app.orchestrator.decision import OrchestrationDecision
from app.services.authenticated_advisory_proposal_service import (
    AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
)
from app.services.authenticated_advisory_proposal_service import (
    AuthenticatedAdvisoryProposalService,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisoryAgentSkillSet,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBinding,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBindingPlan,
)


@pytest.fixture(autouse=True)
def clear_advisory_proposals(db_session):
    db_session.execute(
        delete(AuthenticatedAdvisoryProposal)
    )
    db_session.commit()
    yield
    db_session.rollback()
    db_session.execute(
        delete(AuthenticatedAdvisoryProposal)
    )
    db_session.commit()


def _binding(
    *,
    agent_name: str,
    binding_id: int,
    priority: int,
) -> AdvisorySkillBinding:
    return AdvisorySkillBinding(
        agent_name=agent_name,
        binding_id=binding_id,
        skill_version_id=1000 + binding_id,
        skill_id=2000 + binding_id,
        binding_priority=priority,
        execution_mode="sync",
        runtime_kind="python",
    )


def _envelope(
    *,
    user_id: int = 11,
    session_id: int = 21,
    request_id: str | None = "req-first",
    decision_name: str = "advisory_candidate",
    agents: tuple[str, ...] = ("analyst", "reviewer"),
) -> AuthenticatedAdvisoryEnvelope:
    decision = OrchestrationDecision(
        decision_name=decision_name,
        selected_agents=agents,
        reason="sensitive reason must never persist",
        confidence=0.9876,
        signals=(
            DecisionSignal(
                name="private_signal",
                value={"secret": "never-persist"},
                description="sensitive signal",
            ),
        ),
    )

    plan_agents = tuple(
        AdvisoryAgentSkillSet(
            agent_name=agent_name,
            bindings=(
                _binding(
                    agent_name=agent_name,
                    binding_id=index * 10 + 1,
                    priority=index,
                ),
                _binding(
                    agent_name=agent_name,
                    binding_id=index * 10 + 2,
                    priority=index + 10,
                ),
            ),
        )
        for index, agent_name in enumerate(
            agents,
            start=1,
        )
    )

    return AuthenticatedAdvisoryEnvelope(
        decision=decision,
        plan=AdvisorySkillBindingPlan(
            decision_name=decision_name,
            agents=plan_agents,
        ),
        authority=AuthorityProvenance(
            authority_user_id=user_id,
            auth_session_id=session_id,
            request_id=request_id,
        ),
    )


def _canonical_digest(payload: dict) -> str:
    canonical = json.dumps(
        [
            AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
            payload,
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_idempotency_scope_and_key_normalization(
    db_session,
):
    service = AuthenticatedAdvisoryProposalService(
        db_session
    )

    first = service.create(
        envelope=_envelope(
            user_id=7,
            session_id=9,
        ),
        idempotency_key="  Same.Key:ABC  ",
    )
    second = service.create(
        envelope=_envelope(
            user_id=7,
            session_id=10,
        ),
        idempotency_key="same.key:abc",
    )

    assert first.created is True
    assert second.created is True
    assert first.proposal.id != second.proposal.id
    assert first.proposal.idempotency_key == "same.key:abc"

    with pytest.raises(
        AdvisoryProposalValidationError
    ):
        service.create(
            envelope=_envelope(
                session_id=11,
            ),
            idempotency_key="contains spaces",
        )


def test_snapshot_persists_only_safe_ordered_advisory_metadata(
    db_session,
):
    proposal = AuthenticatedAdvisoryProposalService(
        db_session
    ).create(
        envelope=_envelope(),
        idempotency_key="safe-snapshot",
    ).proposal

    payload = proposal.snapshot_payload

    assert set(payload) == {
        "decision_name",
        "selected_agents",
        "agents",
    }
    assert payload["selected_agents"] == [
        "analyst",
        "reviewer",
    ]
    assert [
        agent["agent_name"]
        for agent in payload["agents"]
    ] == [
        "analyst",
        "reviewer",
    ]

    first_binding = payload["agents"][0]["bindings"][0]
    assert set(first_binding) == {
        "binding_id",
        "skill_version_id",
        "skill_id",
        "binding_priority",
        "execution_mode",
        "runtime_kind",
    }

    serialized = json.dumps(
        payload,
        sort_keys=True,
    )

    for forbidden in (
        "sensitive reason",
        "private_signal",
        "never-persist",
        "confidence",
        "signals",
        "reason",
        "event_name",
        "payload",
        "role",
        "permissions",
        "scope",
        "elevation",
        "credentials",
        "tokens",
    ):
        assert forbidden not in serialized


def test_authority_reference_metadata_and_request_id_semantics(
    db_session,
):
    service = AuthenticatedAdvisoryProposalService(
        db_session
    )

    first = service.create(
        envelope=_envelope(
            user_id=44,
            session_id=55,
            request_id="request-one",
        ),
        idempotency_key="authority-reference",
    )
    second = service.create(
        envelope=_envelope(
            user_id=44,
            session_id=55,
            request_id="request-two",
        ),
        idempotency_key="authority-reference",
    )

    proposal = second.proposal

    assert proposal.authority_user_id == 44
    assert proposal.auth_session_id == 55
    assert proposal.authority_source == "authenticated_http_session"
    assert proposal.request_id == "request-one"
    assert second.duplicate is True
    assert second.proposal.id == first.proposal.id


def test_canonical_snapshot_digest_is_deterministic_and_protocol_bound(
    db_session,
):
    first = AuthenticatedAdvisoryProposalService(
        db_session
    ).create(
        envelope=_envelope(
            session_id=31,
        ),
        idempotency_key="digest-one",
    ).proposal

    second = AuthenticatedAdvisoryProposalService(
        db_session
    ).create(
        envelope=_envelope(
            session_id=32,
        ),
        idempotency_key="digest-two",
    ).proposal

    assert first.snapshot_payload == second.snapshot_payload
    assert first.snapshot_digest == second.snapshot_digest
    assert first.snapshot_digest == _canonical_digest(
        first.snapshot_payload
    )
    assert first.protocol == AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL


def test_agent_and_binding_order_and_membership_are_preserved_exactly(
    db_session,
):
    proposal = AuthenticatedAdvisoryProposalService(
        db_session
    ).create(
        envelope=_envelope(
            agents=("zeta", "alpha"),
        ),
        idempotency_key="ordered",
    ).proposal

    assert proposal.snapshot_payload[
        "selected_agents"
    ] == ["zeta", "alpha"]

    assert [
        agent["agent_name"]
        for agent in proposal.snapshot_payload["agents"]
    ] == ["zeta", "alpha"]

    assert [
        binding["binding_id"]
        for binding
        in proposal.snapshot_payload["agents"][0]["bindings"]
    ] == [11, 12]


def test_agent_binding_and_snapshot_bounds_fail_closed(
    db_session,
):
    service = AuthenticatedAdvisoryProposalService(
        db_session
    )

    with pytest.raises(
        AdvisoryProposalValidationError
    ):
        service.create(
            envelope=_envelope(
                session_id=61,
                agents=tuple(
                    f"agent-{index}"
                    for index in range(33)
                ),
            ),
            idempotency_key="too-many-agents",
        )

    one_agent = "one"
    bindings = tuple(
        _binding(
            agent_name=one_agent,
            binding_id=index + 1,
            priority=index,
        )
        for index in range(513)
    )
    decision = OrchestrationDecision(
        decision_name="binding-bound",
        selected_agents=(one_agent,),
        reason="not persisted",
        confidence=0.5,
        signals=(),
    )
    envelope = AuthenticatedAdvisoryEnvelope(
        decision=decision,
        plan=AdvisorySkillBindingPlan(
            decision_name="binding-bound",
            agents=(
                AdvisoryAgentSkillSet(
                    agent_name=one_agent,
                    bindings=bindings,
                ),
            ),
        ),
        authority=AuthorityProvenance(
            authority_user_id=1,
            auth_session_id=62,
        ),
    )

    with pytest.raises(
        AdvisoryProposalValidationError
    ):
        service.create(
            envelope=envelope,
            idempotency_key="too-many-bindings",
        )

    large_bindings = tuple(
        AdvisorySkillBinding(
            agent_name=one_agent,
            binding_id=index + 1,
            skill_version_id=10000 + index,
            skill_id=20000 + index,
            binding_priority=index,
            execution_mode="x" * 64,
            runtime_kind="y" * 64,
        )
        for index in range(512)
    )
    large_envelope = AuthenticatedAdvisoryEnvelope(
        decision=decision,
        plan=AdvisorySkillBindingPlan(
            decision_name="binding-bound",
            agents=(
                AdvisoryAgentSkillSet(
                    agent_name=one_agent,
                    bindings=large_bindings,
                ),
            ),
        ),
        authority=AuthorityProvenance(
            authority_user_id=1,
            auth_session_id=63,
        ),
    )

    with pytest.raises(
        AdvisoryProposalValidationError
    ):
        service.create(
            envelope=large_envelope,
            idempotency_key="snapshot-too-large",
        )


def test_same_identity_and_digest_returns_duplicate_without_second_row(
    db_session,
):
    service = AuthenticatedAdvisoryProposalService(
        db_session
    )
    envelope = _envelope()

    first = service.create(
        envelope=envelope,
        idempotency_key="duplicate",
    )
    second = service.create(
        envelope=envelope,
        idempotency_key="duplicate",
    )

    rows = db_session.query(
        AuthenticatedAdvisoryProposal
    ).all()

    assert first.created is True
    assert first.duplicate is False
    assert second.created is False
    assert second.duplicate is True
    assert second.proposal.id == first.proposal.id
    assert len(rows) == 1


def test_same_identity_with_different_digest_raises_idempotency_conflict(
    db_session,
):
    service = AuthenticatedAdvisoryProposalService(
        db_session
    )

    service.create(
        envelope=_envelope(),
        idempotency_key="conflict",
    )

    with pytest.raises(
        AdvisoryProposalIdempotencyConflictError
    ):
        service.create(
            envelope=_envelope(
                decision_name="different-decision",
            ),
            idempotency_key="conflict",
        )


class _RaceRepository:
    def __init__(
        self,
        existing: AuthenticatedAdvisoryProposal,
    ) -> None:
        self.existing = existing
        self.lookups = 0

    def find_by_idempotency(self, **_):
        self.lookups += 1
        if self.lookups == 1:
            return None
        return self.existing

    def add(self, _):
        raise IntegrityError(
            "insert",
            {},
            Exception("unique race"),
        )


def test_integrity_race_with_same_digest_recovers_as_duplicate(
    db_session,
):
    existing = AuthenticatedAdvisoryProposalService(
        db_session
    ).create(
        envelope=_envelope(
            session_id=71,
        ),
        idempotency_key="race-same",
    ).proposal

    db_session.expunge(existing)

    result = AuthenticatedAdvisoryProposalService(
        db_session,
        repository=_RaceRepository(existing),
    ).create(
        envelope=_envelope(
            session_id=71,
        ),
        idempotency_key="race-same",
    )

    assert result.created is False
    assert result.duplicate is True
    assert result.proposal.snapshot_digest == existing.snapshot_digest


def test_integrity_race_with_different_digest_fails_closed(
    db_session,
):
    existing = AuthenticatedAdvisoryProposalService(
        db_session
    ).create(
        envelope=_envelope(
            session_id=72,
        ),
        idempotency_key="race-different",
    ).proposal

    db_session.expunge(existing)

    with pytest.raises(
        AdvisoryProposalIdempotencyConflictError
    ):
        AuthenticatedAdvisoryProposalService(
            db_session,
            repository=_RaceRepository(existing),
        ).create(
            envelope=_envelope(
                session_id=72,
                decision_name="different-race-content",
            ),
            idempotency_key="race-different",
        )


def test_proposal_modules_have_no_mutating_domain_or_route_imports():
    repo_root = Path(__file__).resolve().parents[1]
    paths = [
        repo_root
        / "app/models/authenticated_advisory_proposal.py",
        repo_root
        / "app/repositories/authenticated_advisory_proposal_repository.py",
        repo_root
        / "app/services/authenticated_advisory_proposal_service.py",
    ]

    forbidden_prefixes = (
        "app.api",
        "app.agents",
        "app.services.work",
        "app.services.approval",
        "app.services.governed",
        "app.services.memory",
    )

    for path in paths:
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig")
        )
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                imports.extend(
                    alias.name
                    for alias in node.names
                )

        assert not any(
            imported.startswith(forbidden_prefixes)
            for imported in imports
        )


def test_documentation_preserves_advisory_only_and_future_reauthorization():
    repo_root = Path(__file__).resolve().parents[1]
    text = (
        repo_root
        / "docs/orchestrator/AUTHENTICATED_ADVISORY_PROPOSALS.md"
    ).read_text(
        encoding="utf-8-sig"
    )

    normalized_text = " ".join(text.split())

    required = (
        "does not grant",
        "excluded from the semantic idempotency identity",
        "never persists",
        "must reload the current user",
        "reauthorize current scope and the exact Skill",
        "fail closed",
    )

    for phrase in required:
        assert phrase in normalized_text
