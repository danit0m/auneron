import ast
import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionAuthorizationError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionStaleError,
)
from app.core.advisory_proposal_errors import AdvisoryProposalDispatchNotAllowedError
from app.core.advisory_proposal_errors import AdvisoryProposalValidationError
from app.core.approval_errors import ApprovalAuthorizationError
from app.core.skill_errors import SkillIdempotencyConflictError
from app.services.authenticated_advisory_proposal_consumption_service import (
    AuthenticatedAdvisoryProposalConsumptionValidation,
)
from app.services.authenticated_advisory_proposal_dispatch_service import (
    AuthenticatedAdvisoryProposalDispatchResult,
)
from app.services.authenticated_advisory_proposal_dispatch_service import (
    AuthenticatedAdvisoryProposalDispatchService,
)


PROPOSAL_ID = 701
BINDING_ID = 702
VERSION_ID = 703
SKILL_ID = 704
AUTHORITY_USER_ID = 705
SESSION_ID = 706
AGENT_NAME = "finance"
INVOCATION_ID = 707


def _validation(
    *,
    execution_mode: str = "read_only",
    runtime_kind: str = "internal_python",
) -> AuthenticatedAdvisoryProposalConsumptionValidation:
    return AuthenticatedAdvisoryProposalConsumptionValidation(
        proposal_id=PROPOSAL_ID,
        snapshot_digest="a" * 64,
        authority_user_id=AUTHORITY_USER_ID,
        auth_session_id=SESSION_ID,
        agent_name=AGENT_NAME,
        binding_id=BINDING_ID,
        skill_version_id=VERSION_ID,
        skill_id=SKILL_ID,
        binding_priority=10,
        execution_mode=execution_mode,
        runtime_kind=runtime_kind,
        account_id=None,
        subject_user_id=None,
    )


def _governed_result(
    *,
    duplicate: bool = False,
    output=None,
):
    if output is None:
        output = {"ok": True}

    return SimpleNamespace(
        policy=SimpleNamespace(
            autonomous_allowed=True,
            disposition="autonomous_allowed",
        ),
        invocation=SimpleNamespace(
            invocation=SimpleNamespace(
                id=INVOCATION_ID,
                status="succeeded",
            ),
            output=output,
            duplicate=duplicate,
        ),
        approval_request_id=None,
        approval_consumption_id=None,
    )


def _harness(
    *,
    validation=None,
    governed_result=None,
):
    db = MagicMock()
    consumption = MagicMock()
    governed = MagicMock()

    consumption.validate.return_value = (
        validation if validation is not None else _validation()
    )
    governed.execute.return_value = (
        governed_result
        if governed_result is not None
        else _governed_result()
    )

    service = AuthenticatedAdvisoryProposalDispatchService(
        db,
        consumption_service=consumption,
        governed_service=governed,
    )

    return SimpleNamespace(
        db=db,
        consumption=consumption,
        governed=governed,
        service=service,
        authenticated=object(),
    )


def _dispatch(harness, *, input_payload=None):
    if input_payload is None:
        input_payload = {"amount": 10}

    return harness.service.dispatch(
        proposal_id=PROPOSAL_ID,
        authenticated=harness.authenticated,
        binding_id=BINDING_ID,
        input_payload=input_payload,
    )


def test_invalid_proposal_id_fails_validation_before_governed_dispatch() -> None:
    harness = _harness()
    harness.consumption.validate.side_effect = (
        AdvisoryProposalValidationError("proposal_id")
    )

    with pytest.raises(AdvisoryProposalValidationError):
        harness.service.dispatch(
            proposal_id=0,
            authenticated=harness.authenticated,
            binding_id=BINDING_ID,
            input_payload={"amount": 10},
        )

    harness.governed.execute.assert_not_called()


def test_invalid_binding_id_fails_validation_before_governed_dispatch() -> None:
    harness = _harness()
    harness.consumption.validate.side_effect = (
        AdvisoryProposalValidationError("binding_id")
    )

    with pytest.raises(AdvisoryProposalValidationError):
        harness.service.dispatch(
            proposal_id=PROPOSAL_ID,
            authenticated=harness.authenticated,
            binding_id=0,
            input_payload={"amount": 10},
        )

    harness.governed.execute.assert_not_called()


def test_invalid_authenticated_input_fails_before_governed_dispatch() -> None:
    harness = _harness()
    harness.consumption.validate.side_effect = (
        AdvisoryProposalValidationError("authenticated")
    )

    with pytest.raises(AdvisoryProposalValidationError):
        _dispatch(harness)

    harness.governed.execute.assert_not_called()


def test_missing_inaccessible_or_stale_proposal_fails_before_governed_dispatch() -> None:
    harness = _harness()
    harness.consumption.validate.side_effect = (
        AdvisoryProposalConsumptionStaleError("stale")
    )

    with pytest.raises(AdvisoryProposalConsumptionStaleError):
        _dispatch(harness)

    harness.governed.execute.assert_not_called()


def test_current_session_or_user_authorization_failure_fails_before_dispatch() -> None:
    harness = _harness()
    harness.consumption.validate.side_effect = (
        AdvisoryProposalConsumptionAuthorizationError("authority")
    )

    with pytest.raises(AdvisoryProposalConsumptionAuthorizationError):
        _dispatch(harness)

    harness.governed.execute.assert_not_called()


def test_consumption_validation_receives_normalized_ephemeral_input() -> None:
    harness = _harness()
    raw = {"z": 1, "nested": {"b": 2, "a": 1}}

    _dispatch(harness, input_payload=raw)

    call = harness.consumption.validate.call_args
    normalized = call.kwargs["input_payload"]

    assert normalized == raw
    assert normalized is not raw
    assert json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
    ) == '{"nested":{"a":1,"b":2},"z":1}'


def test_mutating_candidate_is_rejected_before_governed_dispatch() -> None:
    harness = _harness(
        validation=_validation(
            execution_mode="mutating",
        )
    )

    with pytest.raises(AdvisoryProposalDispatchNotAllowedError):
        _dispatch(harness)

    harness.governed.execute.assert_not_called()


def test_external_candidate_is_rejected_before_governed_dispatch() -> None:
    harness = _harness(
        validation=_validation(
            execution_mode="external",
        )
    )

    with pytest.raises(AdvisoryProposalDispatchNotAllowedError):
        _dispatch(harness)

    harness.governed.execute.assert_not_called()


def test_plugin_runtime_candidate_is_rejected_before_governed_dispatch() -> None:
    harness = _harness(
        validation=_validation(
            runtime_kind="plugin",
        )
    )

    with pytest.raises(AdvisoryProposalDispatchNotAllowedError):
        _dispatch(harness)

    harness.governed.execute.assert_not_called()


def test_agent_actor_is_derived_server_side() -> None:
    harness = _harness()

    result = _dispatch(harness)

    call = harness.governed.execute.call_args
    actor = call.kwargs["actor"]

    assert actor.actor_type == "agent"
    assert actor.actor_reference == "agent:finance"
    assert actor.actor_user_id is None
    assert result.actor_reference == "agent:finance"


def test_runtime_idempotency_key_is_derived_server_side() -> None:
    harness = _harness()

    _dispatch(harness)

    assert (
        harness.governed.execute.call_args.kwargs["idempotency_key"]
        == "advisory:701:702"
    )


def test_caller_cannot_supply_authority_actor_idempotency_approval_or_context() -> None:
    signature = inspect.signature(
        AuthenticatedAdvisoryProposalDispatchService.dispatch
    )

    assert tuple(signature.parameters) == (
        "self",
        "proposal_id",
        "authenticated",
        "binding_id",
        "input_payload",
    )

    forbidden = {
        "actor",
        "actor_type",
        "actor_reference",
        "authority_user_id",
        "auth_session_id",
        "role",
        "permissions",
        "session_elevated",
        "idempotency_key",
        "approval_request_id",
        "runtime_context",
        "work_item_id",
    }

    assert not forbidden.intersection(signature.parameters)


def test_governed_dispatch_receives_exact_revalidated_skill_version() -> None:
    harness = _harness()

    _dispatch(harness)

    call = harness.governed.execute.call_args
    assert call.args == (VERSION_ID,)


def test_governed_authority_user_id_comes_from_revalidated_identity() -> None:
    harness = _harness()

    _dispatch(harness)

    assert (
        harness.governed.execute.call_args.kwargs["authority_user_id"]
        == AUTHORITY_USER_ID
    )


def test_governed_dispatch_receives_exact_normalized_ephemeral_input() -> None:
    harness = _harness()
    raw = {"b": 2, "a": 1}

    _dispatch(harness, input_payload=raw)

    governed_input = (
        harness.governed.execute.call_args.kwargs["input_payload"]
    )
    consumed_input = (
        harness.consumption.validate.call_args.kwargs["input_payload"]
    )

    assert governed_input == {"a": 1, "b": 2}
    assert governed_input is consumed_input


def test_governed_dispatch_supplies_no_approval_request_or_runtime_context() -> None:
    harness = _harness()

    _dispatch(harness)

    kwargs = harness.governed.execute.call_args.kwargs
    assert kwargs["approval_request_id"] is None
    assert kwargs["runtime_context"] is None


def test_same_candidate_and_input_replays_idempotently_without_second_handler() -> None:
    harness = _harness()
    calls = {"handler": 0}
    ledger = {}

    def execute(version_id, **kwargs):
        key = kwargs["idempotency_key"]
        fingerprint = json.dumps(
            kwargs["input_payload"],
            sort_keys=True,
            separators=(",", ":"),
        )

        prior = ledger.get(key)
        if prior is None:
            ledger[key] = fingerprint
            calls["handler"] += 1
            return _governed_result(duplicate=False)

        assert prior == fingerprint
        return _governed_result(duplicate=True)

    harness.governed.execute.side_effect = execute

    first = _dispatch(harness, input_payload={"a": 1})
    second = _dispatch(harness, input_payload={"a": 1})

    assert first.duplicate is False
    assert second.duplicate is True
    assert calls["handler"] == 1


def test_same_candidate_with_different_input_conflicts_without_second_handler() -> None:
    harness = _harness()
    calls = {"handler": 0}
    ledger = {}

    def execute(version_id, **kwargs):
        key = kwargs["idempotency_key"]
        fingerprint = json.dumps(
            kwargs["input_payload"],
            sort_keys=True,
            separators=(",", ":"),
        )

        prior = ledger.get(key)
        if prior is None:
            ledger[key] = fingerprint
            calls["handler"] += 1
            return _governed_result()

        if prior != fingerprint:
            raise SkillIdempotencyConflictError(
                "different fingerprint"
            )

        return _governed_result(duplicate=True)

    harness.governed.execute.side_effect = execute

    _dispatch(harness, input_payload={"a": 1})

    with pytest.raises(SkillIdempotencyConflictError):
        _dispatch(harness, input_payload={"a": 2})

    assert calls["handler"] == 1


def test_trusted_handler_and_low_risk_policy_are_not_bypassed() -> None:
    harness = _harness()
    harness.governed.execute.side_effect = (
        ApprovalAuthorizationError("autonomy blocked")
    )

    with pytest.raises(ApprovalAuthorizationError):
        _dispatch(harness)

    source = Path(
        inspect.getsourcefile(
            AuthenticatedAdvisoryProposalDispatchService
        )
    ).read_text(encoding="utf-8")

    assert "GovernedSkillExecutionService" in source
    assert "evaluate_skill_autonomy" not in source
    assert "handler_registry" not in source


def test_dispatch_result_is_frozen_and_uses_exact_safe_allowlist() -> None:
    harness = _harness()

    result = _dispatch(harness)

    assert tuple(result.__dataclass_fields__) == (
        "proposal_id",
        "binding_id",
        "skill_version_id",
        "skill_id",
        "agent_name",
        "actor_reference",
        "invocation_id",
        "invocation_status",
        "duplicate",
        "output",
    )

    with pytest.raises(FrozenInstanceError):
        result.proposal_id = 1


def test_dispatch_service_never_imports_or_calls_skill_runtime_service_directly() -> None:
    path = Path(
        inspect.getsourcefile(
            AuthenticatedAdvisoryProposalDispatchService
        )
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "SkillRuntimeService" not in source

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "invoke"
        ):
            raise AssertionError("25L must not invoke runtime directly")


def test_dispatch_service_does_not_create_work_or_approval_state() -> None:
    path = Path(
        inspect.getsourcefile(
            AuthenticatedAdvisoryProposalDispatchService
        )
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_modules = {
        "app.models.work",
        "app.repositories.work_repository",
        "app.services.work_service",
        "app.models.approval",
        "app.repositories.approval_repository",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules

    for token in (
        "ApprovalRequest(",
        "ApprovalConsumption(",
        "WorkItem(",
        "create_work",
        "link_memory",
    ):
        assert token not in source


def test_dispatch_has_no_eventbus_route_schema_alembic_or_production_wiring() -> None:
    service_path = Path(
        inspect.getsourcefile(
            AuthenticatedAdvisoryProposalDispatchService
        )
    ).resolve()
    backend = service_path.parents[2]
    app_root = backend / "app"

    offenders = []

    for path in app_root.rglob("*.py"):
        if path.resolve() == service_path:
            continue

        text = path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
        if (
            "AuthenticatedAdvisoryProposalDispatchService" in text
            or "authenticated_advisory_proposal_dispatch_service" in text
        ):
            offenders.append(
                path.relative_to(backend).as_posix()
            )

    assert offenders == []
    assert "EventBus" not in service_path.read_text(encoding="utf-8")
    assert not list(
        (backend / "migrations" / "versions").glob("*25l*")
    )


def test_documentation_freezes_final_reauthorization_idempotency_and_deferred_boundaries() -> None:
    backend = Path(__file__).resolve().parents[1]
    doc = (
        backend
        / "docs"
        / "orchestrator"
        / "AUTHENTICATED_ADVISORY_PROPOSAL_DISPATCH.md"
    ).read_text(encoding="utf-8")

    normalized = " ".join(doc.split())

    for phrase in (
        "read_only` + `internal_python",
        "AuthenticatedAdvisoryProposalConsumptionService.validate",
        "GovernedSkillExecutionService.execute",
        "advisory:<proposal_id>:<binding_id>",
        "same canonical input replays",
        "different canonical input",
        "mutating",
        "external",
        "creates no Work",
        "no public route",
    ):
        assert phrase in normalized

    assert (
        "has no public route, production wiring, schema migration, "
        "Alembic change, or OpenAPI change"
        in normalized
    )
