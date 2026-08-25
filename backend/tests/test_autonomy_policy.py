import inspect
from unittest.mock import Mock

import pytest

from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.autonomy_policy import classify_skill_risk
from app.core.autonomy_policy import evaluate_skill_autonomy
from app.models.skill import SkillCapability
from app.models.skill import SkillVersion


def _version(execution_mode: str) -> SkillVersion:
    version = Mock(spec=SkillVersion)
    version.execution_mode = execution_mode
    return version


def _capability(
    *,
    access_mode: str = "read",
    resource_scope: str = "internal",
) -> SkillCapability:
    capability = Mock(spec=SkillCapability)
    capability.access_mode = access_mode
    capability.resource_scope = resource_scope
    return capability


def test_read_only_risk_is_low() -> None:
    assert classify_skill_risk(
        version=_version("read_only"),
        capabilities=(_capability(),),
    ) == ("low", "approval:decide")


def test_mutating_risk_is_high() -> None:
    assert classify_skill_risk(
        version=_version("mutating"),
        capabilities=(_capability(access_mode="write"),),
    ) == ("high", "approval:decide")


def test_external_risk_is_critical() -> None:
    assert classify_skill_risk(
        version=_version("external"),
        capabilities=(
            _capability(
                access_mode="execute",
                resource_scope="external",
            ),
        ),
    ) == ("critical", "approval:decide_sensitive")


def test_read_only_rejects_non_read_capability() -> None:
    with pytest.raises(ApprovalStateError):
        classify_skill_risk(
            version=_version("read_only"),
            capabilities=(_capability(access_mode="write"),),
        )


def test_mutating_rejects_external_capability() -> None:
    with pytest.raises(ApprovalStateError):
        classify_skill_risk(
            version=_version("mutating"),
            capabilities=(
                _capability(
                    access_mode="write",
                    resource_scope="external",
                ),
            ),
        )


def test_invalid_execution_mode_is_blocked_by_state_error() -> None:
    with pytest.raises(ApprovalStateError):
        classify_skill_risk(
            version=_version("unknown"),
            capabilities=(),
        )


def test_agent_read_only_is_autonomously_allowed() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="agent",
        version=_version("read_only"),
        capabilities=(),
    )
    assert decision.disposition == "autonomous_allowed"
    assert decision.reason == "low_risk_read_only"
    assert decision.risk_level == "low"
    assert decision.required_approval_permission is None
    assert decision.autonomous_allowed
    assert not decision.requires_approval


def test_system_read_only_is_autonomously_allowed() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="system",
        version=_version("read_only"),
        capabilities=(),
    )
    assert decision.disposition == "autonomous_allowed"


def test_integration_read_only_is_autonomously_allowed() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="integration",
        version=_version("read_only"),
        capabilities=(),
    )
    assert decision.disposition == "autonomous_allowed"


def test_user_is_blocked_from_autonomous_path() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="user",
        version=_version("read_only"),
        capabilities=(),
    )
    assert decision.disposition == "blocked"
    assert decision.reason == "human_explicit_path"
    assert not decision.autonomous_allowed
    assert not decision.requires_approval


def test_agent_mutating_requires_human_approval() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="agent",
        version=_version("mutating"),
        capabilities=(_capability(access_mode="write"),),
    )
    assert decision.disposition == "approval_required"
    assert decision.reason == "mutating_requires_human"
    assert decision.risk_level == "high"
    assert decision.required_approval_permission == "approval:decide"
    assert decision.requires_approval


def test_system_external_requires_sensitive_human_approval() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="system",
        version=_version("external"),
        capabilities=(
            _capability(
                access_mode="execute",
                resource_scope="external",
            ),
        ),
    )
    assert decision.disposition == "approval_required"
    assert decision.reason == "external_requires_sensitive_human"
    assert decision.risk_level == "critical"
    assert (
        decision.required_approval_permission
        == "approval:decide_sensitive"
    )


def test_integration_mutating_requires_human_approval() -> None:
    decision = evaluate_skill_autonomy(
        actor_type="integration",
        version=_version("mutating"),
        capabilities=(_capability(access_mode="execute"),),
    )
    assert decision.disposition == "approval_required"


def test_unknown_actor_is_rejected() -> None:
    with pytest.raises(ApprovalValidationError):
        evaluate_skill_autonomy(
            actor_type="model",
            version=_version("read_only"),
            capabilities=(),
        )


def test_autonomy_policy_has_no_runtime_execution_dependency() -> None:
    from app.core import autonomy_policy

    source = inspect.getsource(autonomy_policy)
    assert "SkillRuntimeService" not in source
    assert "SkillInvocation(" not in source
    assert ".invoke(" not in source
    assert "ApprovalRequest(" not in source
    assert "ApprovalDecision(" not in source
