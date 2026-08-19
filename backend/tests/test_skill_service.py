import hashlib
import json
from datetime import datetime
from datetime import timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.skill_errors import SkillConflictError
from app.core.skill_errors import SkillImmutableError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService


def register_skill(
    service: SkillService,
    *,
    skill_key: str = "finance.account-summary",
) -> Any:
    return service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Resumo financeiro",
        description="Produz um resumo financeiro controlado.",
    )


def create_draft(
    service: SkillService,
    skill_id: int,
    *,
    version: str = "1.0.0",
    manifest: dict[str, Any] | None = None,
) -> Any:
    return service.create_draft_version(
        skill_id=skill_id,
        version=version,
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.finance:account_summary"
        ),
        execution_mode="read_only",
        manifest=manifest or {"title": "Resumo"},
        input_schema={
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
            },
        },
        output_schema={"type": "object"},
        timeout_seconds=20,
        max_output_bytes=32768,
    )


def publish(
    service: SkillService,
    version_id: int,
) -> Any:
    return service.publish_version(
        version_id,
        capabilities=(
            CapabilityInput(
                capability_key="accounts.summary",
                access_mode="read",
                resource_scope="account",
            ),
        ),
    )


def test_register_skill_normalizes_key(
    db_session: Session,
) -> None:
    service = SkillService(db_session)

    skill = register_skill(
        service,
        skill_key="Finance.Account-Summary",
    )

    assert skill.skill_key == "finance.account-summary"
    assert skill.status == "active"


@pytest.mark.parametrize(
    "skill_key",
    [
        "",
        "finance account",
        "-finance.account",
        "finance/account",
        "a" * 129,
    ],
)
def test_register_skill_rejects_invalid_key(
    db_session: Session,
    skill_key: str,
) -> None:
    service = SkillService(db_session)

    with pytest.raises(SkillValidationError):
        register_skill(service, skill_key=skill_key)


def test_register_skill_maps_duplicate_to_domain_conflict(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    register_skill(service)

    with pytest.raises(SkillConflictError):
        register_skill(service)

    assert service.repository.find_skill_by_key(
        "finance.account-summary"
    ) is not None


def test_create_draft_calculates_canonical_digest(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(
        service,
        skill.id,
        manifest={"z": 1, "a": {"b": True}},
    )
    envelope = {
        "execution_mode": "read_only",
        "handler_reference": (
            "app.skills.finance:account_summary"
        ),
        "input_schema": {
            "properties": {
                "account_id": {"type": "integer"},
            },
            "type": "object",
        },
        "manifest": {"a": {"b": True}, "z": 1},
        "max_output_bytes": 32768,
        "output_schema": {"type": "object"},
        "runtime_kind": "internal_python",
        "timeout_seconds": 20,
        "version": "1.0.0",
    }
    expected = hashlib.sha256(
        json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert draft.manifest_digest == expected
    assert len(draft.manifest_digest) == 64
    assert draft.status == "draft"


def test_equivalent_json_order_keeps_digest(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(
        service,
        skill.id,
        manifest={"z": 1, "a": 2},
    )
    first_digest = draft.manifest_digest

    updated = service.replace_draft_contract(
        draft.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.finance:account_summary"
        ),
        execution_mode="read_only",
        manifest={"a": 2, "z": 1},
        input_schema={
            "properties": {
                "account_id": {"type": "integer"},
            },
            "type": "object",
        },
        output_schema={"type": "object"},
        timeout_seconds=20,
        max_output_bytes=32768,
    )

    assert updated.manifest_digest == first_digest


def test_contract_change_changes_digest(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    first_digest = draft.manifest_digest

    updated = service.replace_draft_contract(
        draft.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.finance:account_summary"
        ),
        execution_mode="read_only",
        manifest={"title": "Resumo alterado"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        timeout_seconds=20,
        max_output_bytes=32768,
    )

    assert updated.manifest_digest != first_digest


@pytest.mark.parametrize(
    ("runtime_kind", "handler_reference"),
    [
        ("internal_python", "os:system"),
        ("internal_python", "app.skills.finance"),
        ("plugin", "invalid plugin"),
        ("unknown", "a:b"),
    ],
)
def test_create_draft_rejects_unsafe_handler_contract(
    db_session: Session,
    runtime_kind: str,
    handler_reference: str,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)

    with pytest.raises(SkillValidationError):
        service.create_draft_version(
            skill_id=skill.id,
            version="1.0.0",
            runtime_kind=runtime_kind,
            handler_reference=handler_reference,
            execution_mode="read_only",
        )


@pytest.mark.parametrize(
    "manifest",
    [
        {"api_key": "value"},
        {"nested": {"password": "value"}},
        {"token": "value"},
        {"number": float("nan")},
    ],
)
def test_create_draft_rejects_secret_or_invalid_manifest(
    db_session: Session,
    manifest: dict[str, Any],
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)

    with pytest.raises(SkillValidationError):
        create_draft(
            service,
            skill.id,
            manifest=manifest,
        )


def test_create_draft_requires_active_skill(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    service.set_skill_status(skill.id, status="disabled")

    with pytest.raises(SkillStateError):
        create_draft(service, skill.id)


def test_publish_persists_normalized_capabilities(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    published_at = datetime(
        2026,
        8,
        17,
        12,
        0,
        tzinfo=timezone.utc,
    )

    result = service.publish_version(
        draft.id,
        capabilities=(
            CapabilityInput(
                capability_key="Accounts.Summary",
                access_mode="READ",
                resource_scope="ACCOUNT",
            ),
            CapabilityInput(
                capability_key="audit.events",
                access_mode="write",
                resource_scope="internal",
                required=False,
            ),
        ),
        published_at=published_at,
    )

    assert result.version.status == "published"
    assert result.version.published_at == published_at
    assert result.version.retired_at is None
    assert [
        capability.capability_key
        for capability in result.capabilities
    ] == ["accounts.summary", "audit.events"]


def test_publish_rejects_duplicate_capability_before_write(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    duplicate = CapabilityInput(
        capability_key="accounts.summary",
        access_mode="read",
        resource_scope="account",
    )

    with pytest.raises(SkillValidationError):
        service.publish_version(
            draft.id,
            capabilities=(duplicate, duplicate),
        )

    persisted = service.repository.get_version(draft.id)
    assert persisted is not None
    assert persisted.status == "draft"
    assert service.repository.list_capabilities(draft.id) == []


def test_published_version_contract_is_immutable(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)

    with pytest.raises(SkillImmutableError):
        service.replace_draft_contract(
            draft.id,
            version="1.0.1",
            runtime_kind="internal_python",
            handler_reference=(
                "app.skills.finance:account_summary"
            ),
            execution_mode="read_only",
        )

    with pytest.raises(SkillImmutableError):
        service.delete_draft_version(draft.id)


def test_publish_is_not_repeatable_mutation(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)

    with pytest.raises(SkillImmutableError):
        publish(service, draft.id)


def test_binding_requires_published_version(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)

    with pytest.raises(SkillStateError):
        service.bind_agent(
            agent_name="FinanceAgent",
            version_id=draft.id,
        )


def test_binding_rejects_secret_configuration(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)

    with pytest.raises(SkillValidationError):
        service.bind_agent(
            agent_name="FinanceAgent",
            version_id=draft.id,
            configuration={
                "nested": {"access_token": "secret"},
            },
        )


def test_binding_duplicate_maps_to_conflict(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)
    service.bind_agent(
        agent_name="FinanceAgent",
        version_id=draft.id,
    )

    with pytest.raises(SkillConflictError):
        service.bind_agent(
            agent_name="FinanceAgent",
            version_id=draft.id,
        )


def test_resolution_is_pinned_and_deterministic(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    first = create_draft(service, skill.id, version="1.0.0")
    second = create_draft(service, skill.id, version="2.0.0")
    publish(service, first.id)
    publish(service, second.id)
    second_binding = service.bind_agent(
        agent_name="FinanceAgent",
        version_id=second.id,
        priority=200,
    )
    first_binding = service.bind_agent(
        agent_name="FinanceAgent",
        version_id=first.id,
        priority=10,
    )

    resolved = service.resolve_agent_bindings("FinanceAgent")

    assert [item.binding.id for item in resolved] == [
        first_binding.id,
        second_binding.id,
    ]
    assert [item.version.id for item in resolved] == [
        first.id,
        second.id,
    ]
    assert all(item.skill.id == skill.id for item in resolved)


def test_disabled_binding_is_not_resolved(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)
    binding = service.bind_agent(
        agent_name="FinanceAgent",
        version_id=draft.id,
    )
    service.set_binding_enabled(binding.id, enabled=False)

    assert service.resolve_agent_bindings("FinanceAgent") == ()


def test_retirement_disables_bindings_and_preserves_contract(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    digest = draft.manifest_digest
    publish(service, draft.id)
    binding = service.bind_agent(
        agent_name="FinanceAgent",
        version_id=draft.id,
    )

    retired = service.retire_version(draft.id)

    persisted_binding = service.repository.get_binding(binding.id)
    assert retired.status == "retired"
    assert retired.retired_at is not None
    assert retired.manifest_digest == digest
    assert persisted_binding is not None
    assert persisted_binding.enabled is False
    assert service.resolve_agent_bindings("FinanceAgent") == ()


def test_retired_binding_cannot_be_reenabled(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)
    binding = service.bind_agent(
        agent_name="FinanceAgent",
        version_id=draft.id,
    )
    service.retire_version(draft.id)

    with pytest.raises(SkillStateError):
        service.set_binding_enabled(binding.id, enabled=True)


def test_disabling_skill_disables_all_bindings(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    publish(service, draft.id)
    binding = service.bind_agent(
        agent_name="FinanceAgent",
        version_id=draft.id,
    )

    disabled = service.set_skill_status(
        skill.id,
        status="disabled",
    )

    persisted_binding = service.repository.get_binding(binding.id)
    assert disabled.status == "disabled"
    assert persisted_binding is not None
    assert persisted_binding.enabled is False


def test_retired_skill_is_terminal(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    service.set_skill_status(skill.id, status="retired")

    with pytest.raises(SkillStateError):
        service.set_skill_status(skill.id, status="active")


def test_draft_without_binding_can_be_deleted(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)

    service.delete_draft_version(draft.id)

    assert service.repository.get_version(draft.id) is None
