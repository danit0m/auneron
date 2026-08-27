import ast
import hashlib
import json
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

import app.services.authenticated_advisory_proposal_consumption_service as consumption_module
from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionAuthorizationError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionStaleError,
)
from app.core.advisory_proposal_errors import AdvisoryProposalNotFoundError
from app.core.advisory_proposal_errors import AdvisoryProposalValidationError
from app.core.authentication import AuthenticatedSession
from app.core.authentication import utc_now
from app.models.auth_session import AuthSession
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.user import User
from app.repositories.authenticated_advisory_proposal_repository import (
    AuthenticatedAdvisoryProposalRepository,
)
from app.repositories.skill_repository import SkillRepository
from app.services.authenticated_advisory_proposal_consumption_service import (
    AuthenticatedAdvisoryProposalConsumptionService,
)
from app.services.authenticated_advisory_proposal_consumption_service import (
    AuthenticatedAdvisoryProposalConsumptionValidation,
)
from app.services.authenticated_advisory_proposal_service import (
    AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
)


PROPOSAL_ID = 101
AUTHORITY_USER_ID = 11
AUTH_SESSION_ID = 21
BINDING_ID = 41
VERSION_ID = 51
SKILL_ID = 61
AGENT_NAME = "analyst"
PRIORITY = 10
TOKEN_HASH = "a" * 64


def _canonical(
    payload: dict,
    *,
    protocol: str = AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
) -> bytes:
    return json.dumps(
        [protocol, payload],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _payload(
    *,
    binding_id: int = BINDING_ID,
    skill_version_id: int = VERSION_ID,
    skill_id: int = SKILL_ID,
    agent_name: str = AGENT_NAME,
    priority: int = PRIORITY,
    execution_mode: str = "read_only",
    runtime_kind: str = "internal_python",
) -> dict:
    return {
        "decision_name": "proposal_candidate",
        "selected_agents": [agent_name],
        "agents": [
            {
                "agent_name": agent_name,
                "bindings": [
                    {
                        "binding_id": binding_id,
                        "skill_version_id": skill_version_id,
                        "skill_id": skill_id,
                        "binding_priority": priority,
                        "execution_mode": execution_mode,
                        "runtime_kind": runtime_kind,
                    }
                ],
            }
        ],
    }


def _proposal(
    *,
    payload: dict | None = None,
    protocol: str = AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
    authority_user_id: int = AUTHORITY_USER_ID,
    auth_session_id: int = AUTH_SESSION_ID,
) -> AuthenticatedAdvisoryProposal:
    snapshot = payload or _payload()
    canonical = _canonical(
        snapshot,
        protocol=protocol,
    )
    return AuthenticatedAdvisoryProposal(
        id=PROPOSAL_ID,
        authority_user_id=authority_user_id,
        auth_session_id=auth_session_id,
        authority_source="authenticated_http_session",
        request_id="req-25k",
        idempotency_key="proposal-25k",
        protocol=protocol,
        snapshot_payload=snapshot,
        snapshot_digest=hashlib.sha256(
            canonical
        ).hexdigest(),
        agent_count=len(
            snapshot.get("selected_agents", [])
        ),
        binding_count=sum(
            len(agent.get("bindings", []))
            for agent in snapshot.get("agents", [])
            if isinstance(agent, dict)
        ),
        snapshot_bytes=len(canonical),
    )


def _rehash(
    proposal: AuthenticatedAdvisoryProposal,
) -> None:
    canonical = _canonical(
        proposal.snapshot_payload,
        protocol=proposal.protocol,
    )
    proposal.snapshot_digest = hashlib.sha256(
        canonical
    ).hexdigest()
    proposal.agent_count = len(
        proposal.snapshot_payload["selected_agents"]
    )
    proposal.binding_count = sum(
        len(agent["bindings"])
        for agent in proposal.snapshot_payload["agents"]
    )
    proposal.snapshot_bytes = len(canonical)


def _user(
    *,
    user_id: int = AUTHORITY_USER_ID,
    role: str = "analyst",
    active: bool = True,
) -> User:
    return User(
        id=user_id,
        name="25K authority",
        email=f"authority.{user_id}@example.com",
        password_hash="not-used",
        role=role,
        active=active,
    )


def _session(
    *,
    session_id: int = AUTH_SESSION_ID,
    user_id: int = AUTHORITY_USER_ID,
    token_hash: str = TOKEN_HASH,
    revoked: bool = False,
    expired: bool = False,
    elevated: bool = False,
) -> AuthSession:
    now = utc_now()
    return AuthSession(
        id=session_id,
        user_id=user_id,
        token_hash=token_hash,
        created_at=now - timedelta(minutes=5),
        expires_at=(
            now - timedelta(seconds=1)
            if expired
            else now + timedelta(hours=1)
        ),
        revoked_at=(
            now if revoked else None
        ),
        elevated_until=(
            now + timedelta(minutes=10)
            if elevated
            else None
        ),
    )


def _version(
    *,
    version_id: int = VERSION_ID,
    skill_id: int = SKILL_ID,
    status: str = "published",
    execution_mode: str = "read_only",
    runtime_kind: str = "internal_python",
):
    return SimpleNamespace(
        id=version_id,
        skill_id=skill_id,
        status=status,
        execution_mode=execution_mode,
        runtime_kind=runtime_kind,
    )


def _skill(
    *,
    skill_id: int = SKILL_ID,
    status: str = "active",
):
    return SimpleNamespace(
        id=skill_id,
        status=status,
    )


def _binding(
    *,
    binding_id: int = BINDING_ID,
    agent_name: str = AGENT_NAME,
    version_id: int = VERSION_ID,
    priority: int = PRIORITY,
    enabled: bool = True,
):
    return SimpleNamespace(
        id=binding_id,
        agent_name=agent_name,
        skill_version_id=version_id,
        priority=priority,
        enabled=enabled,
    )


def _harness(
    monkeypatch,
    *,
    proposal=None,
    passed_user=None,
    passed_session=None,
    current_user=None,
    current_session=None,
    binding=None,
    version=None,
    skill=None,
    grant=None,
    authorize_error: Exception | None = None,
):
    proposal = (
        _proposal()
        if proposal is None
        else proposal
    )
    passed_user = (
        _user()
        if passed_user is None
        else passed_user
    )
    passed_session = (
        _session()
        if passed_session is None
        else passed_session
    )
    current_user = (
        _user()
        if current_user is None
        else current_user
    )
    current_session = (
        _session()
        if current_session is None
        else current_session
    )
    binding = (
        _binding()
        if binding is None
        else binding
    )
    version = (
        _version()
        if version is None
        else version
    )
    skill = (
        _skill()
        if skill is None
        else skill
    )
    grant = (
        SimpleNamespace(
            skill=skill,
            version=version,
            capabilities=(),
            account_id=None,
            subject_user_id=None,
        )
        if grant is None
        else grant
    )

    db = MagicMock(spec=Session)
    db.no_autoflush = nullcontext()

    def get(model, object_id, **kwargs):
        if model is AuthSession:
            if (
                current_session is not None
                and object_id == current_session.id
            ):
                return current_session
            return None
        if model is User:
            if (
                current_user is not None
                and object_id == current_user.id
            ):
                return current_user
            return None
        raise AssertionError(
            f"Unexpected current-authority model lookup: {model}"
        )

    db.get.side_effect = get

    proposal_repository = MagicMock(
        spec=AuthenticatedAdvisoryProposalRepository
    )
    proposal_repository.get_by_id.return_value = proposal

    skill_repository = MagicMock(
        spec=SkillRepository
    )
    skill_repository.get_binding.return_value = binding
    skill_repository.get_version.return_value = version
    skill_repository.get_skill.return_value = skill

    authorize_calls = []

    def fake_authorize(**kwargs):
        authorize_calls.append(kwargs)
        if authorize_error is not None:
            raise authorize_error
        return grant

    monkeypatch.setattr(
        consumption_module,
        "authorize_skill_execution",
        fake_authorize,
    )

    service = AuthenticatedAdvisoryProposalConsumptionService(
        db,
        proposal_repository=proposal_repository,
        skill_repository=skill_repository,
    )
    authenticated = AuthenticatedSession(
        user=passed_user,
        session=passed_session,
    )

    return SimpleNamespace(
        db=db,
        proposal=proposal,
        current_user=current_user,
        current_session=current_session,
        binding=binding,
        version=version,
        skill=skill,
        grant=grant,
        service=service,
        authenticated=authenticated,
        proposal_repository=proposal_repository,
        skill_repository=skill_repository,
        authorize_calls=authorize_calls,
    )


def _validate(harness, *, input_payload=None):
    return harness.service.validate(
        proposal_id=PROPOSAL_ID,
        authenticated=harness.authenticated,
        binding_id=BINDING_ID,
        input_payload=(
            {}
            if input_payload is None
            else input_payload
        ),
    )


def test_invalid_proposal_id_fails_validation(monkeypatch) -> None:
    harness = _harness(monkeypatch)

    for value in (0, -1, True, "1"):
        with pytest.raises(
            AdvisoryProposalValidationError
        ):
            harness.service.validate(
                proposal_id=value,
                authenticated=harness.authenticated,
                binding_id=BINDING_ID,
                input_payload={},
            )


def test_invalid_binding_id_fails_validation(monkeypatch) -> None:
    harness = _harness(monkeypatch)

    for value in (0, -1, False, "41"):
        with pytest.raises(
            AdvisoryProposalValidationError
        ):
            harness.service.validate(
                proposal_id=PROPOSAL_ID,
                authenticated=harness.authenticated,
                binding_id=value,
                input_payload={},
            )


def test_missing_proposal_fails_opaque_not_found(monkeypatch) -> None:
    harness = _harness(monkeypatch)
    harness.proposal_repository.get_by_id.return_value = None

    with pytest.raises(
        AdvisoryProposalNotFoundError
    ):
        _validate(harness)

    assert harness.authorize_calls == []


def test_authenticated_user_mismatch_fails_opaque_not_found(
    monkeypatch,
) -> None:
    harness = _harness(
        monkeypatch,
        passed_user=_user(user_id=99),
        passed_session=_session(user_id=99),
    )

    with pytest.raises(
        AdvisoryProposalNotFoundError
    ):
        _validate(harness)

    assert harness.authorize_calls == []


def test_authenticated_session_mismatch_fails_opaque_not_found(
    monkeypatch,
) -> None:
    harness = _harness(
        monkeypatch,
        passed_session=_session(session_id=99),
    )

    with pytest.raises(
        AdvisoryProposalNotFoundError
    ):
        _validate(harness)

    assert harness.authorize_calls == []


def test_current_session_missing_fails_closed(monkeypatch) -> None:
    harness = _harness(monkeypatch)
    harness.db.get.side_effect = (
        lambda model, object_id, **kwargs:
        None
        if model is AuthSession
        else harness.current_user
    )

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_current_session_revoked_fails_closed(monkeypatch) -> None:
    harness = _harness(
        monkeypatch,
        current_session=_session(revoked=True),
    )

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_current_session_expired_fails_closed(monkeypatch) -> None:
    harness = _harness(
        monkeypatch,
        current_session=_session(expired=True),
    )

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_current_session_user_mismatch_fails_closed(monkeypatch) -> None:
    harness = _harness(
        monkeypatch,
        current_session=_session(user_id=99),
    )

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_current_session_token_identity_mismatch_fails_closed(
    monkeypatch,
) -> None:
    harness = _harness(
        monkeypatch,
        current_session=_session(
            token_hash="b" * 64
        ),
    )

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_current_user_missing_fails_closed(monkeypatch) -> None:
    harness = _harness(monkeypatch)

    def get(model, object_id, **kwargs):
        if model is AuthSession:
            return harness.current_session
        if model is User:
            return None
        raise AssertionError(model)

    harness.db.get.side_effect = get

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_current_user_inactive_fails_closed(monkeypatch) -> None:
    harness = _harness(
        monkeypatch,
        current_user=_user(active=False),
    )

    with pytest.raises(
        AdvisoryProposalConsumptionAuthorizationError
    ):
        _validate(harness)


def test_persisted_digest_count_or_protocol_corruption_fails_stale(
    monkeypatch,
) -> None:
    digest_bad = _proposal()
    digest_bad.snapshot_digest = "0" * 64

    count_bad = _proposal()
    count_bad.binding_count += 1

    protocol_bad = _proposal()
    protocol_bad.protocol = "authenticated_advisory_v0"

    for proposal in (
        digest_bad,
        count_bad,
        protocol_bad,
    ):
        harness = _harness(
            monkeypatch,
            proposal=proposal,
        )
        with pytest.raises(
            AdvisoryProposalConsumptionStaleError
        ):
            _validate(harness)


def test_binding_absent_from_snapshot_fails_opaque_not_found(
    monkeypatch,
) -> None:
    proposal = _proposal(
        payload=_payload(binding_id=999)
    )
    harness = _harness(
        monkeypatch,
        proposal=proposal,
    )

    with pytest.raises(
        AdvisoryProposalNotFoundError
    ):
        _validate(harness)


def test_duplicate_binding_identity_in_snapshot_fails_stale(
    monkeypatch,
) -> None:
    proposal = _proposal()
    duplicate = dict(
        proposal.snapshot_payload[
            "agents"
        ][0]["bindings"][0]
    )
    proposal.snapshot_payload[
        "agents"
    ][0]["bindings"].append(
        duplicate
    )
    _rehash(proposal)

    harness = _harness(
        monkeypatch,
        proposal=proposal,
    )

    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(harness)


def test_current_binding_missing_or_disabled_fails_stale(
    monkeypatch,
) -> None:
    missing = _harness(monkeypatch)
    missing.skill_repository.get_binding.return_value = None

    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(missing)

    disabled = _harness(
        monkeypatch,
        binding=_binding(enabled=False),
    )

    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(disabled)


def test_current_binding_agent_version_or_priority_drift_fails_stale(
    monkeypatch,
) -> None:
    drifts = (
        _binding(agent_name="other-agent"),
        _binding(version_id=999),
        _binding(priority=999),
    )

    for binding in drifts:
        harness = _harness(
            monkeypatch,
            binding=binding,
        )
        with pytest.raises(
            AdvisoryProposalConsumptionStaleError
        ):
            _validate(harness)


def test_current_version_or_skill_unavailable_or_inactive_fails_stale(
    monkeypatch,
) -> None:
    missing_version = _harness(monkeypatch)
    missing_version.skill_repository.get_version.return_value = None

    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(missing_version)

    unpublished = _harness(
        monkeypatch,
        version=_version(status="retired"),
    )
    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(unpublished)

    missing_skill = _harness(monkeypatch)
    missing_skill.skill_repository.get_skill.return_value = None
    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(missing_skill)

    inactive_skill = _harness(
        monkeypatch,
        skill=_skill(status="disabled"),
    )
    with pytest.raises(
        AdvisoryProposalConsumptionStaleError
    ):
        _validate(inactive_skill)


def test_snapshot_skill_or_execution_metadata_drift_fails_stale(
    monkeypatch,
) -> None:
    payloads = (
        _payload(skill_id=999),
        _payload(execution_mode="mutating"),
        _payload(runtime_kind="plugin"),
    )

    for payload in payloads:
        harness = _harness(
            monkeypatch,
            proposal=_proposal(
                payload=payload
            ),
        )
        with pytest.raises(
            AdvisoryProposalConsumptionStaleError
        ):
            _validate(harness)


def test_exact_binding_reauthorizes_and_returns_safe_frozen_result(
    monkeypatch,
) -> None:
    harness = _harness(monkeypatch)

    result = _validate(
        harness,
        input_payload={"query": "open"},
    )

    assert isinstance(
        result,
        AuthenticatedAdvisoryProposalConsumptionValidation,
    )
    assert result.proposal_id == PROPOSAL_ID
    assert result.authority_user_id == AUTHORITY_USER_ID
    assert result.auth_session_id == AUTH_SESSION_ID
    assert result.agent_name == AGENT_NAME
    assert result.binding_id == BINDING_ID
    assert result.skill_version_id == VERSION_ID
    assert result.skill_id == SKILL_ID
    assert result.binding_priority == PRIORITY
    assert result.execution_mode == "read_only"
    assert result.runtime_kind == "internal_python"
    assert result.account_id is None
    assert result.subject_user_id is None
    assert len(harness.authorize_calls) == 1

    with pytest.raises(
        AttributeError
    ):
        result.binding_id = 999


def test_account_scope_is_reauthorized_from_current_ephemeral_input(
    monkeypatch,
) -> None:
    skill = _skill()
    version = _version()
    grant = SimpleNamespace(
        skill=skill,
        version=version,
        capabilities=(),
        account_id=42,
        subject_user_id=None,
    )
    harness = _harness(
        monkeypatch,
        skill=skill,
        version=version,
        grant=grant,
    )
    payload = {
        "account_id": 42,
        "query": "open",
    }

    result = _validate(
        harness,
        input_payload=payload,
    )

    call = harness.authorize_calls[0]
    assert call["input_payload"] is payload
    assert call["actor_user_id"] == AUTHORITY_USER_ID
    assert call["role"] == "analyst"
    assert result.account_id == 42


def test_user_scope_uses_current_role_and_active_target(
    monkeypatch,
) -> None:
    current_user = _user(
        role="developer",
    )
    passed_user = _user(
        role="viewer",
    )
    skill = _skill()
    version = _version()
    grant = SimpleNamespace(
        skill=skill,
        version=version,
        capabilities=(),
        account_id=None,
        subject_user_id=77,
    )
    harness = _harness(
        monkeypatch,
        current_user=current_user,
        passed_user=passed_user,
        skill=skill,
        version=version,
        grant=grant,
    )
    payload = {
        "subject_user_id": 77,
    }

    result = _validate(
        harness,
        input_payload=payload,
    )

    call = harness.authorize_calls[0]
    assert call["role"] == "developer"
    assert call["actor_user_id"] == AUTHORITY_USER_ID
    assert call["input_payload"] is payload
    assert result.subject_user_id == 77


def test_mutating_and_external_authority_use_current_role_and_elevation(
    monkeypatch,
) -> None:
    mutating_version = _version(
        execution_mode="mutating"
    )
    mutating_proposal = _proposal(
        payload=_payload(
            execution_mode="mutating"
        )
    )
    mutating_grant = SimpleNamespace(
        skill=_skill(),
        version=mutating_version,
        capabilities=(),
        account_id=None,
        subject_user_id=None,
    )
    mutating = _harness(
        monkeypatch,
        proposal=mutating_proposal,
        passed_user=_user(role="viewer"),
        current_user=_user(role="manager"),
        version=mutating_version,
        grant=mutating_grant,
    )

    _validate(mutating)

    assert mutating.authorize_calls[0]["role"] == "manager"
    assert (
        mutating.authorize_calls[0]["session_elevated"]
        is False
    )

    external_version = _version(
        execution_mode="external"
    )
    external_proposal = _proposal(
        payload=_payload(
            execution_mode="external"
        )
    )
    external_grant = SimpleNamespace(
        skill=_skill(),
        version=external_version,
        capabilities=(),
        account_id=None,
        subject_user_id=None,
    )
    external = _harness(
        monkeypatch,
        proposal=external_proposal,
        passed_user=_user(role="viewer"),
        current_user=_user(role="developer"),
        current_session=_session(elevated=True),
        version=external_version,
        grant=external_grant,
    )

    _validate(external)

    assert external.authorize_calls[0]["role"] == "developer"
    assert (
        external.authorize_calls[0]["session_elevated"]
        is True
    )


def test_boundary_is_select_only_non_executable_non_persistent_and_documented(
    monkeypatch,
) -> None:
    harness = _harness(monkeypatch)
    _validate(harness)

    for method_name in (
        "add",
        "delete",
        "flush",
        "commit",
        "rollback",
    ):
        getattr(
            harness.db,
            method_name,
        ).assert_not_called()

    backend = Path(__file__).resolve().parents[1]
    service_path = (
        backend
        / "app"
        / "services"
        / "authenticated_advisory_proposal_consumption_service.py"
    )
    doc_path = (
        backend
        / "docs"
        / "orchestrator"
        / "AUTHENTICATED_ADVISORY_PROPOSAL_CONSUMPTION.md"
    )

    source = service_path.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    database_write_calls = {
        "add",
        "delete",
        "flush",
        "commit",
        "rollback",
    }
    database_receivers = {
        "self.db",
        "self.proposal_repository.db",
        "self.skill_repository.db",
    }
    execution_calls = {
        "execute",
        "invoke",
        "publish",
    }

    harmless_set_adds = []

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        ):
            continue

        receiver = ast.unparse(
            node.func.value
        )
        attr = node.func.attr

        if (
            attr == "add"
            and receiver in {
                "seen_agents",
                "seen_binding_ids",
            }
        ):
            harmless_set_adds.append(
                f"{receiver}.add"
            )

        if (
            attr in database_write_calls
            and receiver in database_receivers
        ):
            raise AssertionError(
                f"Forbidden 25K database write call: "
                f"{receiver}.{attr}"
            )

        if attr in execution_calls:
            raise AssertionError(
                f"Forbidden 25K execution/event call: "
                f"{receiver}.{attr}"
            )

    assert sorted(harmless_set_adds) == [
        "seen_agents.add",
        "seen_binding_ids.add",
    ]

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {
                "insert",
                "update",
                "delete",
            }
        ):
            raise AssertionError(
                f"Forbidden 25K SQL DML call: "
                f"{node.func.id}"
            )

    for forbidden_symbol in (
        "SkillRuntimeService",
        "GovernedSkillExecutionService",
        "WorkService",
        "ApprovalService",
        "EventBus",
        "MemoryService",
        "APIRouter",
    ):
        assert forbidden_symbol not in source

    document = doc_path.read_text(
        encoding="utf-8"
    )
    normalized = " ".join(
        document.split()
    )

    for phrase in (
        "stored proposal grants no authority",
        "current reloaded User and AuthSession",
        "authorize_skill_execution",
        "one exact persisted binding",
        "SELECT-only",
        "does not survive TOCTOU",
        "no runtime invocation",
        "no Work or Approval mutation",
        "input_payload is ephemeral",
    ):
        assert phrase in normalized
