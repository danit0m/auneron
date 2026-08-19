from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.skill import AgentSkillBinding
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.repositories.skill_repository import SkillRepository


def add_skill(
    repository: SkillRepository,
    *,
    skill_key: str = "finance.account-summary",
) -> SkillDefinition:
    return repository.add_skill(
        SkillDefinition(
            skill_key=skill_key,
            provider="auneron.core",
            display_name="Resumo financeiro",
            description="Resumo controlado.",
            status="active",
        )
    )


def add_version(
    repository: SkillRepository,
    skill_id: int,
    *,
    version: str = "1.0.0",
    digest_character: str = "a",
    status: str = "draft",
) -> SkillVersion:
    return repository.add_version(
        SkillVersion(
            skill_id=skill_id,
            version=version,
            runtime_kind="internal_python",
            handler_reference=(
                "app.skills.finance:account_summary"
            ),
            execution_mode="read_only",
            manifest_digest=digest_character * 64,
            manifest={},
            input_schema={},
            output_schema={},
            timeout_seconds=30,
            max_output_bytes=65536,
            status=status,
            published_at=(
                datetime.now(timezone.utc)
                if status in {"published", "retired"}
                else None
            ),
            retired_at=(
                datetime.now(timezone.utc)
                if status == "retired"
                else None
            ),
        )
    )


def test_repository_adds_and_finds_skill(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)

    assert repository.get_skill(skill.id) is skill
    assert repository.find_skill_by_key(
        skill.skill_key
    ) is skill


def test_repository_locks_skill_and_version(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)
    version = add_version(repository, skill.id)

    assert repository.lock_skill(skill.id) is skill
    assert repository.lock_version(version.id) is version


def test_repository_finds_exact_version(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)
    first = add_version(repository, skill.id)
    add_version(
        repository,
        skill.id,
        version="2.0.0",
        digest_character="b",
    )

    found = repository.find_version(
        skill_id=skill.id,
        version="1.0.0",
    )

    assert found is first


def test_repository_lists_capabilities_deterministically(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)
    version = add_version(repository, skill.id)
    for key in ("z.resource", "a.resource"):
        repository.add_capability(
            SkillCapability(
                skill_version_id=version.id,
                capability_key=key,
                access_mode="read",
                resource_scope="internal",
                required=True,
            )
        )

    capabilities = repository.list_capabilities(version.id)

    assert [item.capability_key for item in capabilities] == [
        "a.resource",
        "z.resource",
    ]


def test_repository_lists_enabled_bindings_by_priority(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)
    versions = [
        add_version(
            repository,
            skill.id,
            version="1.0.0",
            digest_character="a",
            status="published",
        ),
        add_version(
            repository,
            skill.id,
            version="2.0.0",
            digest_character="b",
            status="published",
        ),
        add_version(
            repository,
            skill.id,
            version="3.0.0",
            digest_character="c",
            status="published",
        ),
    ]
    priorities = (200, 10, 1)
    enabled = (True, True, False)
    bindings = []
    for version, priority, is_enabled in zip(
        versions,
        priorities,
        enabled,
        strict=True,
    ):
        bindings.append(
            repository.add_binding(
                AgentSkillBinding(
                    agent_name="FinanceAgent",
                    skill_version_id=version.id,
                    priority=priority,
                    enabled=is_enabled,
                    configuration={},
                )
            )
        )

    found = repository.list_bindings_for_agent(
        "FinanceAgent"
    )

    assert [item.id for item in found] == [
        bindings[1].id,
        bindings[0].id,
    ]


def test_repository_deletes_only_requested_capabilities(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)
    version = add_version(repository, skill.id)
    first = repository.add_capability(
        SkillCapability(
            skill_version_id=version.id,
            capability_key="accounts.summary",
            access_mode="read",
            resource_scope="account",
            required=True,
        )
    )
    second = repository.add_capability(
        SkillCapability(
            skill_version_id=version.id,
            capability_key="audit.events",
            access_mode="write",
            resource_scope="internal",
            required=False,
        )
    )

    repository.delete_capabilities([first])

    assert repository.list_capabilities(version.id) == [second]


def test_repository_deletes_draft_version(
    db_session: Session,
) -> None:
    repository = SkillRepository(db_session)
    skill = add_skill(repository)
    version = add_version(repository, skill.id)

    repository.delete_version(version)

    assert repository.get_version(version.id) is None
