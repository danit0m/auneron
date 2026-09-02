import ast
import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.advisory_proposal_errors import (
    AdvisoryProposalApprovalCorrelationError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalApprovalNotAllowedError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionAuthorizationError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionStaleError,
)
from app.core.advisory_proposal_errors import AdvisoryProposalValidationError
from app.core.approval_errors import ApprovalIdempotencyConflictError
from app.core.approval_errors import ApprovalRequiredError
from app.services.approval_service import approval_input_identity
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovalBridgeService,
)
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovalRequestResult,
)
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovedDispatchResult,
)
from app.services.authenticated_advisory_proposal_consumption_service import (
    AuthenticatedAdvisoryProposalConsumptionValidation,
)


PROPOSAL_ID = 801
BINDING_ID = 802
VERSION_ID = 803
SKILL_ID = 804
AUTHORITY_USER_ID = 805
SESSION_ID = 806
APPROVAL_ID = 807
CONSUMPTION_ID = 808
INVOCATION_ID = 809
AGENT_NAME = "finance"
INPUT = {"amount": 10}


def _validation(
    *,
    execution_mode: str = "mutating",
    runtime_kind: str = "internal_python",
    account_id=None,
    subject_user_id=None,
) -> AuthenticatedAdvisoryProposalConsumptionValidation:
    return AuthenticatedAdvisoryProposalConsumptionValidation(
        proposal_id=PROPOSAL_ID,
        snapshot_digest="b" * 64,
        authority_user_id=AUTHORITY_USER_ID,
        auth_session_id=SESSION_ID,
        agent_name=AGENT_NAME,
        binding_id=BINDING_ID,
        skill_version_id=VERSION_ID,
        skill_id=SKILL_ID,
        binding_priority=20,
        execution_mode=execution_mode,
        runtime_kind=runtime_kind,
        account_id=account_id,
        subject_user_id=subject_user_id,
    )


def _request(
    *,
    input_payload=None,
    action_type="skill_execution",
    version_id=VERSION_ID,
    requester_actor_type="agent",
    requester_reference=f"agent:{AGENT_NAME}",
    requester_user_id=None,
    idempotency_key=f"advisory:{PROPOSAL_ID}:{BINDING_ID}",
    risk_level="high",
    required_permission="approval:decide",
    status="pending",
    account_id=None,
    subject_user_id=None,
):
    if input_payload is None:
        input_payload = INPUT
    _, digest = approval_input_identity(input_payload)
    return SimpleNamespace(
        id=APPROVAL_ID,
        action_type=action_type,
        skill_version_id=version_id,
        requester_actor_type=requester_actor_type,
        requester_reference=requester_reference,
        requester_user_id=requester_user_id,
        idempotency_key=idempotency_key,
        request_fingerprint="c" * 64,
        input_digest=digest,
        risk_level=risk_level,
        required_permission=required_permission,
        status=status,
        target_account_id=account_id,
        target_user_id=subject_user_id,
    )


def _materialized_result(
    *,
    duplicate=False,
    output=None,
    work_item_id=810,
    work_skill_execution_id=811,
    approval_consumption_id=CONSUMPTION_ID,
    invocation_id=INVOCATION_ID,
    invocation_status="succeeded",
):
    if output is None:
        output = {"ok": True}
    return SimpleNamespace(
        work_item_id=work_item_id,
        work_skill_execution_id=work_skill_execution_id,
        approval_consumption_id=approval_consumption_id,
        invocation_id=invocation_id,
        invocation_status=invocation_status,
        duplicate=duplicate,
        output=output,
    )


def _harness(
    *,
    validation=None,
    request=None,
    materialized_result=None,
    account_id=None,
    subject_user_id=None,
):
    db = MagicMock()
    consumption = MagicMock()
    approval = MagicMock()
    work_materialization = MagicMock()

    candidate = (
        validation
        if validation is not None
        else _validation(
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
    )
    approval_request = (
        request
        if request is not None
        else _request(
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
    )

    consumption.validate.return_value = candidate
    approval.create_skill_execution_request.return_value = (
        SimpleNamespace(
            request=approval_request,
            duplicate=False,
        )
    )
    approval.get_request.return_value = approval_request
    work_materialization.materialize_and_execute.return_value = (
        materialized_result
        if materialized_result is not None
        else _materialized_result()
    )

    service = AuthenticatedAdvisoryProposalApprovalBridgeService(
        db,
        consumption_service=consumption,
        approval_service=approval,
        work_materialization_service=work_materialization,
    )

    return SimpleNamespace(
        db=db,
        consumption=consumption,
        approval=approval,
        work_materialization=work_materialization,
        service=service,
        authenticated=object(),
        candidate=candidate,
        request=approval_request,
    )


def _request_approval(harness, *, input_payload=None):
    return harness.service.request_approval(
        proposal_id=PROPOSAL_ID,
        authenticated=harness.authenticated,
        binding_id=BINDING_ID,
        input_payload=INPUT if input_payload is None else input_payload,
    )


def _dispatch_approved(
    harness,
    *,
    input_payload=None,
    approval_request_id=APPROVAL_ID,
):
    return harness.service.dispatch_approved(
        proposal_id=PROPOSAL_ID,
        authenticated=harness.authenticated,
        binding_id=BINDING_ID,
        input_payload=INPUT if input_payload is None else input_payload,
        approval_request_id=approval_request_id,
    )


def test_request_invalid_proposal_id_fails_before_approval_creation() -> None:
    h = _harness()
    h.consumption.validate.side_effect = AdvisoryProposalValidationError("proposal")

    with pytest.raises(AdvisoryProposalValidationError):
        h.service.request_approval(
            proposal_id=0,
            authenticated=h.authenticated,
            binding_id=BINDING_ID,
            input_payload=INPUT,
        )

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_invalid_binding_id_fails_before_approval_creation() -> None:
    h = _harness()
    h.consumption.validate.side_effect = AdvisoryProposalValidationError("binding")

    with pytest.raises(AdvisoryProposalValidationError):
        h.service.request_approval(
            proposal_id=PROPOSAL_ID,
            authenticated=h.authenticated,
            binding_id=0,
            input_payload=INPUT,
        )

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_invalid_authenticated_input_fails_before_approval_creation() -> None:
    h = _harness()
    h.consumption.validate.side_effect = AdvisoryProposalValidationError("auth")

    with pytest.raises(AdvisoryProposalValidationError):
        _request_approval(h)

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_missing_inaccessible_or_stale_proposal_fails_before_approval_creation() -> None:
    h = _harness()
    h.consumption.validate.side_effect = AdvisoryProposalConsumptionStaleError("stale")

    with pytest.raises(AdvisoryProposalConsumptionStaleError):
        _request_approval(h)

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_current_session_or_user_authorization_failure_fails_before_approval_creation() -> None:
    h = _harness()
    h.consumption.validate.side_effect = (
        AdvisoryProposalConsumptionAuthorizationError("authority")
    )

    with pytest.raises(AdvisoryProposalConsumptionAuthorizationError):
        _request_approval(h)

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_25k_validation_receives_normalized_ephemeral_input() -> None:
    h = _harness()
    raw = {"z": [1, 2], "a": 10.0}
    normalized, _ = approval_input_identity(raw)
    req = _request(input_payload=normalized)
    h.approval.create_skill_execution_request.return_value = (
        SimpleNamespace(request=req, duplicate=False)
    )

    _request_approval(h, input_payload=raw)

    h.consumption.validate.assert_called_once_with(
        proposal_id=PROPOSAL_ID,
        authenticated=h.authenticated,
        binding_id=BINDING_ID,
        input_payload=normalized,
    )


def test_request_read_only_candidate_is_rejected_before_approval_creation() -> None:
    h = _harness(validation=_validation(execution_mode="read_only"))

    with pytest.raises(AdvisoryProposalApprovalNotAllowedError):
        _request_approval(h)

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_external_candidate_is_rejected_before_approval_creation() -> None:
    h = _harness(validation=_validation(execution_mode="external"))

    with pytest.raises(AdvisoryProposalApprovalNotAllowedError):
        _request_approval(h)

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_plugin_runtime_candidate_is_rejected_before_approval_creation() -> None:
    h = _harness(validation=_validation(runtime_kind="plugin"))

    with pytest.raises(AdvisoryProposalApprovalNotAllowedError):
        _request_approval(h)

    h.approval.create_skill_execution_request.assert_not_called()


def test_request_requester_actor_is_server_derived_from_validated_agent_name() -> None:
    h = _harness()

    _request_approval(h)

    requester = h.approval.create_skill_execution_request.call_args.kwargs["requester"]
    assert requester.actor_type == "agent"
    assert requester.actor_reference == f"agent:{AGENT_NAME}"
    assert requester.actor_user_id is None


def test_request_approval_idempotency_key_is_server_derived_from_candidate_identity() -> None:
    h = _harness()

    _request_approval(h)

    assert (
        h.approval.create_skill_execution_request.call_args.kwargs["idempotency_key"]
        == f"advisory:{PROPOSAL_ID}:{BINDING_ID}"
    )


def test_request_signature_does_not_accept_caller_authority_or_runtime_identity() -> None:
    signature = inspect.signature(
        AuthenticatedAdvisoryProposalApprovalBridgeService.request_approval
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
        "requester",
        "authority_user_id",
        "auth_session_id",
        "role",
        "permissions",
        "session_elevated",
        "idempotency_key",
        "approval_request_id",
        "decision",
        "runtime_context",
    }
    assert forbidden.isdisjoint(signature.parameters)


def test_request_approval_service_receives_exact_version_and_normalized_input() -> None:
    h = _harness()
    raw = {"b": 2.0, "a": 1}
    normalized, _ = approval_input_identity(raw)
    req = _request(input_payload=normalized)
    h.approval.create_skill_execution_request.return_value = (
        SimpleNamespace(request=req, duplicate=False)
    )

    _request_approval(h, input_payload=raw)

    kwargs = h.approval.create_skill_execution_request.call_args.kwargs
    assert kwargs["version_id"] == VERSION_ID
    assert kwargs["input_payload"] == normalized


def test_request_persisted_approval_correlation_checks_actor_key_version_input_risk_permission_and_scope() -> None:
    h = _harness(account_id=22, subject_user_id=33)

    result = _request_approval(h)

    assert result.approval_request_id == APPROVAL_ID
    assert result.risk_level == "high"
    assert h.request.requester_reference == f"agent:{AGENT_NAME}"
    assert h.request.idempotency_key == f"advisory:{PROPOSAL_ID}:{BINDING_ID}"
    assert h.request.skill_version_id == VERSION_ID
    assert h.request.required_permission == "approval:decide"
    assert h.request.target_account_id == 22
    assert h.request.target_user_id == 33


def test_request_result_is_frozen_with_exact_safe_allowlist() -> None:
    h = _harness()
    result = _request_approval(h)

    assert tuple(result.__dataclass_fields__) == (
        "proposal_id",
        "binding_id",
        "skill_version_id",
        "skill_id",
        "agent_name",
        "actor_reference",
        "approval_request_id",
        "approval_status",
        "risk_level",
        "duplicate",
    )
    with pytest.raises(FrozenInstanceError):
        result.proposal_id = 1


def test_same_candidate_and_input_replays_same_approval_request_idempotently() -> None:
    h = _harness()
    h.approval.create_skill_execution_request.side_effect = [
        SimpleNamespace(request=h.request, duplicate=False),
        SimpleNamespace(request=h.request, duplicate=True),
    ]

    first = _request_approval(h)
    second = _request_approval(h)

    assert first.approval_request_id == second.approval_request_id == APPROVAL_ID
    assert first.duplicate is False
    assert second.duplicate is True


def test_same_candidate_with_different_input_conflicts_without_second_approval_request() -> None:
    h = _harness()
    h.approval.create_skill_execution_request.side_effect = [
        SimpleNamespace(request=h.request, duplicate=False),
        ApprovalIdempotencyConflictError("conflict"),
    ]

    _request_approval(h)

    with pytest.raises(ApprovalIdempotencyConflictError):
        _request_approval(h, input_payload={"amount": 11})

    h.work_materialization.materialize_and_execute.assert_not_called()


def test_request_phase_never_executes_governed_runtime_decides_approval_or_wires_other_domains() -> None:
    h = _harness()

    _request_approval(h)

    h.work_materialization.materialize_and_execute.assert_not_called()
    source = inspect.getsource(AuthenticatedAdvisoryProposalApprovalBridgeService.request_approval)
    assert ".decide(" not in source
    assert "Work" not in source
    assert "Memory" not in source
    assert "EventBus" not in source


def test_dispatch_invalid_approval_request_id_fails_before_current_candidate_or_governed_execution() -> None:
    h = _harness()

    with pytest.raises(AdvisoryProposalValidationError):
        _dispatch_approved(h, approval_request_id=0)

    h.consumption.validate.assert_not_called()
    h.approval.get_request.assert_not_called()
    h.work_materialization.materialize_and_execute.assert_not_called()


def test_dispatch_revalidates_candidate_with_normalized_input_and_does_not_accept_request_phase_validation() -> None:
    h = _harness()
    raw = {"z": 2.0, "a": 1}
    normalized, _ = approval_input_identity(raw)
    h.approval.get_request.return_value = _request(input_payload=normalized)

    _dispatch_approved(h, input_payload=raw)

    h.consumption.validate.assert_called_once_with(
        proposal_id=PROPOSAL_ID,
        authenticated=h.authenticated,
        binding_id=BINDING_ID,
        input_payload=normalized,
    )


def test_dispatch_stale_candidate_or_current_authority_failure_stops_before_governed_execution() -> None:
    for error in (
        AdvisoryProposalConsumptionStaleError("stale"),
        AdvisoryProposalConsumptionAuthorizationError("authority"),
    ):
        h = _harness()
        h.consumption.validate.side_effect = error

        with pytest.raises(type(error)):
            _dispatch_approved(h)

        h.work_materialization.materialize_and_execute.assert_not_called()


def test_dispatch_read_only_external_or_plugin_candidate_is_rejected_before_governed_execution() -> None:
    candidates = (
        _validation(execution_mode="read_only"),
        _validation(execution_mode="external"),
        _validation(runtime_kind="plugin"),
    )
    for candidate in candidates:
        h = _harness(validation=candidate)
        with pytest.raises(AdvisoryProposalApprovalNotAllowedError):
            _dispatch_approved(h)
        h.work_materialization.materialize_and_execute.assert_not_called()


def test_dispatch_signature_does_not_accept_caller_actor_authority_idempotency_decision_context_or_consumption() -> None:
    signature = inspect.signature(
        AuthenticatedAdvisoryProposalApprovalBridgeService.dispatch_approved
    )
    assert tuple(signature.parameters) == (
        "self",
        "proposal_id",
        "authenticated",
        "binding_id",
        "input_payload",
        "approval_request_id",
    )
    forbidden = {
        "actor",
        "authority_user_id",
        "idempotency_key",
        "decision",
        "decision_user_id",
        "runtime_context",
        "approval_consumption_id",
    }
    assert forbidden.isdisjoint(signature.parameters)


def test_dispatch_exact_matching_approval_request_reaches_governed_boundary() -> None:
    h = _harness()

    result = _dispatch_approved(h)

    assert result.approval_request_id == APPROVAL_ID
    h.work_materialization.materialize_and_execute.assert_called_once()


def test_dispatch_wrong_proposal_binding_idempotency_identity_is_rejected_before_governed_execution() -> None:
    h = _harness(
        request=_request(idempotency_key="advisory:999:999")
    )

    with pytest.raises(AdvisoryProposalApprovalCorrelationError):
        _dispatch_approved(h)

    h.work_materialization.materialize_and_execute.assert_not_called()


def test_dispatch_wrong_requester_identity_or_requester_user_id_is_rejected_before_governed_execution() -> None:
    for req in (
        _request(requester_reference="agent:other"),
        _request(requester_user_id=AUTHORITY_USER_ID),
    ):
        h = _harness(request=req)
        with pytest.raises(AdvisoryProposalApprovalCorrelationError):
            _dispatch_approved(h)
        h.work_materialization.materialize_and_execute.assert_not_called()


def test_dispatch_wrong_version_input_digest_or_scope_is_rejected_before_governed_execution() -> None:
    bad_requests = (
        _request(version_id=VERSION_ID + 1),
        _request(input_payload={"amount": 11}),
        _request(account_id=999),
    )
    for req in bad_requests:
        h = _harness(request=req)
        with pytest.raises(AdvisoryProposalApprovalCorrelationError):
            _dispatch_approved(h)
        h.work_materialization.materialize_and_execute.assert_not_called()


def test_pending_rejected_expired_or_otherwise_invalid_approval_cannot_reach_handler_through_governed_execution() -> None:
    for status in ("pending", "rejected", "expired"):
        h = _harness(request=_request(status=status))
        h.work_materialization.materialize_and_execute.side_effect = ApprovalRequiredError(status)

        with pytest.raises(ApprovalRequiredError):
            _dispatch_approved(h)

        h.work_materialization.materialize_and_execute.assert_called_once()


def test_work_materialization_dispatch_receives_exact_proposal_binding_authenticated_input_and_approval_identity() -> None:
    h = _harness()

    _dispatch_approved(h)

    kwargs = h.work_materialization.materialize_and_execute.call_args.kwargs
    assert kwargs["proposal_id"] == PROPOSAL_ID
    assert kwargs["authenticated"] is h.authenticated
    assert kwargs["binding_id"] == BINDING_ID
    assert kwargs["input_payload"] == INPUT
    assert kwargs["approval_request_id"] == APPROVAL_ID


def test_final_governed_boundary_retains_current_skill_scope_approval_human_authority_and_consumption_checks() -> None:
    backend = Path(__file__).resolve().parents[1]
    source = (
        backend / "app" / "services" / "governed_skill_execution.py"
    ).read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    for token in (
        "authorize_skill_execution(",
        "_validate_approved_action(",
        "_validate_consumption(",
        "request.status != \"approved\"",
        "request.expires_at <= now",
        "decider is None",
        "get_consumption_by_request(",
        "runtime_key = ( f\"approval:{request.id}\" )",
    ):
        assert token in normalized


def test_same_approved_dispatch_retry_returns_same_invocation_without_second_handler_action() -> None:
    class FakeMaterializer:
        def __init__(self):
            self.handler_runs = 0
            self.seen = {}

        def materialize_and_execute(self, **kwargs):
            key = kwargs["approval_request_id"]
            duplicate = key in self.seen
            if not duplicate:
                self.handler_runs += 1
                self.seen[key] = _materialized_result()
            if not duplicate:
                return self.seen[key]
            return _materialized_result(duplicate=True)

    h = _harness()
    fake = FakeMaterializer()
    h.service.work_materialization_service = fake

    first = _dispatch_approved(h)
    second = _dispatch_approved(h)

    assert first.invocation_id == second.invocation_id == INVOCATION_ID
    assert first.duplicate is False
    assert second.duplicate is True
    assert fake.handler_runs == 1


def test_different_input_under_same_candidate_and_approval_is_rejected_before_second_governed_action() -> None:
    h = _harness()

    _dispatch_approved(h)

    with pytest.raises(AdvisoryProposalApprovalCorrelationError):
        _dispatch_approved(h, input_payload={"amount": 11})

    assert h.work_materialization.materialize_and_execute.call_count == 1


def test_approved_dispatch_result_is_frozen_with_exact_safe_allowlist() -> None:
    h = _harness()
    result = _dispatch_approved(h)

    assert tuple(result.__dataclass_fields__) == (
        "proposal_id",
        "binding_id",
        "skill_version_id",
        "skill_id",
        "agent_name",
        "actor_reference",
        "approval_request_id",
        "approval_consumption_id",
        "invocation_id",
        "invocation_status",
        "duplicate",
        "output",
    )
    with pytest.raises(FrozenInstanceError):
        result.binding_id = 1


def test_static_and_documentation_contract_forbids_bypass_wiring_schema_api_and_external_execution() -> None:
    backend = Path(__file__).resolve().parents[1]
    service_path = (
        backend
        / "app"
        / "services"
        / "authenticated_advisory_proposal_approval_bridge_service.py"
    )
    source = service_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "SkillRuntimeService" not in source
    assert ".decide(" not in source
    assert "WorkService" not in source
    assert "Memory" not in source
    assert "EventBus" not in source

    calls = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "self.work_materialization_service.materialize_and_execute" in calls
    assert "self.approval_service.create_skill_execution_request" in calls
    assert "self.consumption_service.validate" in calls

    doc = (
        backend
        / "docs"
        / "orchestrator"
        / "AUTHENTICATED_ADVISORY_PROPOSAL_APPROVAL_BRIDGE.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(doc.split())

    for phrase in (
        "mutating + internal_python ONLY",
        "AuthenticatedAdvisoryProposalConsumptionService.validate",
        "ApprovalService.create_skill_execution_request",
        "advisory:<proposal_id>:<binding_id>",
        "existing authenticated /approvals API",
        "AuthenticatedAdvisoryProposalWorkMaterializationService.materialize_and_execute",
        "approval:<approval_request_id>",
        "no direct SkillRuntimeService",
        "no external execution",
        "no generic/ad-hoc Work materialization",
        "no Memory integration",
        "no EventBus integration",
        "no public route",
        "no schema change",
        "no Alembic change",
        "no OpenAPI change",
    ):
        assert phrase in normalized


def test_25o_bridge_routes_approved_dispatch_through_work_materializer() -> None:
    from pathlib import Path
    source = Path(
        "app/services/authenticated_advisory_proposal_approval_bridge_service.py"
    ).read_text(encoding="utf-8")
    assert "AuthenticatedAdvisoryProposalWorkMaterializationService" in source
    assert ".materialize_and_execute(" in source
