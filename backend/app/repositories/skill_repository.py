from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.skill import AgentSkillBinding
from app.models.skill import SkillCapability
from app.models.skill import SkillDefinition
from app.models.skill import SkillInvocation
from app.models.skill import SkillVersion


class SkillRepository:
    """
    Persistência SQLAlchemy do catálogo de Agent Skills.

    Esta camada executa statements e flush, mas nunca commit,
    rollback, begin ou begin_nested. A fronteira transacional
    pertence exclusivamente ao SkillService.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_skill_keys_by_version_ids(
        self,
        version_ids,
    ) -> dict[int, str]:
        """
        Batch-resolves skill_version_id -> SkillDefinition.skill_key
        in a single join, so a caller listing N ApprovalRequests never
        issues N+1 queries. Used to give a client the action's stable
        semantic identity without ever exposing/requiring it to infer
        that identity from a skill_version_id, agent_name or any other
        internal id.

        version_ids with no resolvable Skill (should not happen given
        the ON DELETE RESTRICT chain skills <- skill_versions) are
        simply absent from the returned mapping -- the caller decides
        whether that is fail-closed.
        """
        normalized_ids = {
            version_id
            for version_id in version_ids
            if isinstance(version_id, int)
            and not isinstance(version_id, bool)
            and version_id > 0
        }

        if not normalized_ids:
            return {}

        statement = (
            select(
                SkillVersion.id,
                SkillDefinition.skill_key,
            )
            .join(
                SkillDefinition,
                SkillDefinition.id == SkillVersion.skill_id,
            )
            .where(
                SkillVersion.id.in_(normalized_ids)
            )
        )

        return {
            version_id: skill_key
            for version_id, skill_key in self.db.execute(
                statement
            ).all()
        }

    def add_skill(
        self,
        skill: SkillDefinition,
    ) -> SkillDefinition:
        self.db.add(skill)
        self.db.flush()
        return skill

    def get_skill(
        self,
        skill_id: int,
    ) -> SkillDefinition | None:
        return self.db.get(SkillDefinition, skill_id)

    def lock_skill(
        self,
        skill_id: int,
    ) -> SkillDefinition | None:
        statement = (
            select(SkillDefinition)
            .where(SkillDefinition.id == skill_id)
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def find_skill_by_key(
        self,
        skill_key: str,
        *,
        for_update: bool = False,
    ) -> SkillDefinition | None:
        statement = select(SkillDefinition).where(
            SkillDefinition.skill_key == skill_key
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def add_version(
        self,
        version: SkillVersion,
    ) -> SkillVersion:
        self.db.add(version)
        self.db.flush()
        return version

    def get_version(
        self,
        version_id: int,
    ) -> SkillVersion | None:
        return self.db.get(SkillVersion, version_id)

    def lock_version(
        self,
        version_id: int,
    ) -> SkillVersion | None:
        statement = (
            select(SkillVersion)
            .where(SkillVersion.id == version_id)
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def find_version(
        self,
        *,
        skill_id: int,
        version: str,
        for_update: bool = False,
    ) -> SkillVersion | None:
        statement = select(SkillVersion).where(
            SkillVersion.skill_id == skill_id,
            SkillVersion.version == version,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_versions(
        self,
        skill_id: int,
    ) -> list[SkillVersion]:
        statement = (
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(
                SkillVersion.created_at.asc(),
                SkillVersion.id.asc(),
            )
        )
        return list(self.db.execute(statement).scalars())

    def add_capability(
        self,
        capability: SkillCapability,
    ) -> SkillCapability:
        self.db.add(capability)
        self.db.flush()
        return capability

    def list_capabilities(
        self,
        version_id: int,
    ) -> list[SkillCapability]:
        statement = (
            select(SkillCapability)
            .where(
                SkillCapability.skill_version_id
                == version_id
            )
            .order_by(
                SkillCapability.capability_key.asc(),
                SkillCapability.access_mode.asc(),
                SkillCapability.resource_scope.asc(),
                SkillCapability.id.asc(),
            )
        )
        return list(self.db.execute(statement).scalars())

    def delete_capabilities(
        self,
        capabilities: list[SkillCapability],
    ) -> None:
        for capability in capabilities:
            self.db.delete(capability)
        self.db.flush()

    def add_binding(
        self,
        binding: AgentSkillBinding,
    ) -> AgentSkillBinding:
        self.db.add(binding)
        self.db.flush()
        return binding

    def get_binding(
        self,
        binding_id: int,
    ) -> AgentSkillBinding | None:
        return self.db.get(AgentSkillBinding, binding_id)

    def lock_binding(
        self,
        binding_id: int,
    ) -> AgentSkillBinding | None:
        statement = (
            select(AgentSkillBinding)
            .where(AgentSkillBinding.id == binding_id)
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def find_binding(
        self,
        *,
        agent_name: str,
        version_id: int,
        for_update: bool = False,
    ) -> AgentSkillBinding | None:
        statement = select(AgentSkillBinding).where(
            AgentSkillBinding.agent_name == agent_name,
            AgentSkillBinding.skill_version_id == version_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_bindings_for_agent(
        self,
        agent_name: str,
        *,
        enabled_only: bool = True,
    ) -> list[AgentSkillBinding]:
        statement = select(AgentSkillBinding).where(
            AgentSkillBinding.agent_name == agent_name
        )
        if enabled_only:
            statement = statement.where(
                AgentSkillBinding.enabled.is_(True)
            )
        statement = statement.order_by(
            AgentSkillBinding.priority.asc(),
            AgentSkillBinding.id.asc(),
        )
        return list(self.db.execute(statement).scalars())

    def list_bindings_for_version(
        self,
        version_id: int,
        *,
        enabled_only: bool = False,
        for_update: bool = False,
    ) -> list[AgentSkillBinding]:
        statement = select(AgentSkillBinding).where(
            AgentSkillBinding.skill_version_id == version_id
        )
        if enabled_only:
            statement = statement.where(
                AgentSkillBinding.enabled.is_(True)
            )
        if for_update:
            statement = statement.with_for_update()
        statement = statement.order_by(
            AgentSkillBinding.id.asc()
        )
        return list(self.db.execute(statement).scalars())

    def delete_version(
        self,
        version: SkillVersion,
    ) -> None:
        self.db.delete(version)
        self.db.flush()

    def add_invocation(
        self,
        invocation: SkillInvocation,
    ) -> SkillInvocation:
        self.db.add(invocation)
        self.db.flush()
        return invocation

    def get_invocation(
        self,
        invocation_id: int,
    ) -> SkillInvocation | None:
        return self.db.get(
            SkillInvocation,
            invocation_id,
        )

    def lock_invocation(
        self,
        invocation_id: int,
    ) -> SkillInvocation | None:
        statement = (
            select(SkillInvocation)
            .where(
                SkillInvocation.id
                == invocation_id
            )
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def find_invocation_by_idempotency(
        self,
        *,
        version_id: int,
        actor_type: str,
        actor_reference: str,
        idempotency_key: str,
    ) -> SkillInvocation | None:
        statement = select(SkillInvocation).where(
            SkillInvocation.skill_version_id
            == version_id,
            SkillInvocation.actor_type
            == actor_type,
            SkillInvocation.actor_reference
            == actor_reference,
            SkillInvocation.idempotency_key
            == idempotency_key,
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_invocations_for_version(
        self,
        version_id: int,
        *,
        limit: int = 100,
    ) -> list[SkillInvocation]:
        statement = (
            select(SkillInvocation)
            .where(
                SkillInvocation.skill_version_id
                == version_id
            )
            .order_by(
                SkillInvocation.started_at.desc(),
                SkillInvocation.id.desc(),
            )
            .limit(limit)
        )
        return list(
            self.db.execute(statement).scalars()
        )

    def lock_stale_running_invocations(
        self,
        *,
        cutoff: datetime,
        limit: int = 100,
    ) -> list[SkillInvocation]:
        statement = (
            select(SkillInvocation)
            .where(
                SkillInvocation.status == "running",
                SkillInvocation.started_at <= cutoff,
            )
            .order_by(
                SkillInvocation.started_at.asc(),
                SkillInvocation.id.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(
            self.db.execute(statement).scalars()
        )
