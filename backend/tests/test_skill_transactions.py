from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.skill_errors import SkillConflictError
from app.models.skill import SkillCapability
from app.repositories.skill_repository import SkillRepository
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
        description="Resumo controlado.",
    )


def create_draft(
    service: SkillService,
    skill_id: int,
) -> Any:
    return service.create_draft_version(
        skill_id=skill_id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.finance:account_summary"
        ),
        execution_mode="read_only",
        manifest={"title": "Resumo"},
    )


def test_failed_publication_rolls_back_capabilities_and_state(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SkillRepository(db_session)
    service = SkillService(db_session, repository=repository)
    skill = register_skill(service)
    draft = create_draft(service, skill.id)
    original_add = repository.add_capability
    calls = 0

    def fail_second(
        capability: SkillCapability,
    ) -> SkillCapability:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("falha induzida")
        return original_add(capability)

    monkeypatch.setattr(
        repository,
        "add_capability",
        fail_second,
    )

    with pytest.raises(RuntimeError, match="falha induzida"):
        service.publish_version(
            draft.id,
            capabilities=(
                CapabilityInput(
                    capability_key="accounts.summary",
                    access_mode="read",
                    resource_scope="account",
                ),
                CapabilityInput(
                    capability_key="audit.events",
                    access_mode="write",
                    resource_scope="internal",
                ),
            ),
        )

    db_session.expire_all()
    persisted = repository.get_version(draft.id)
    assert persisted is not None
    assert persisted.status == "draft"
    assert persisted.published_at is None
    assert repository.list_capabilities(draft.id) == []


def test_session_remains_usable_after_conflict(
    db_session: Session,
) -> None:
    service = SkillService(db_session)
    register_skill(service)

    with pytest.raises(SkillConflictError):
        register_skill(service)

    second = register_skill(
        service,
        skill_key="finance.cash-flow",
    )
    assert second.id is not None


def test_repository_has_no_transaction_ownership_calls() -> None:
    forbidden = {
        "begin",
        "begin_nested",
        "commit",
        "rollback",
    }
    names: set[str] = set()

    for value in vars(SkillRepository).values():
        code = getattr(value, "__code__", None)
        if code is not None:
            names.update(code.co_names)

    assert names.isdisjoint(forbidden)


def test_service_owns_commit_and_rollback_contract() -> None:
    names: set[str] = set()

    for value in vars(SkillService).values():
        code = getattr(value, "__code__", None)
        if code is not None:
            names.update(code.co_names)

    assert "commit" in names
    assert "rollback" in names
    assert "begin_nested" not in names
