from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.core.memory_authorization import authorize_memory_scope
from app.core.work_authorization import authorize_work_scope
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.models.user import User
from app.models.work import WorkItem
from app.repositories.work_learning_context_repository import (
    WorkLearningContextRepository,
)


DEFAULT_LEARNING_CONTEXT_LIMIT = 5
MAX_LEARNING_CONTEXT_LIMIT = 10


@dataclass(frozen=True)
class WorkLearningContextItem:
    memory_id: int
    source_work_item_id: int
    work_skill_execution_id: int
    skill_version_id: int
    terminal_status: str
    evaluation_code: str
    learning_signal: str
    observed_at: datetime


class WorkLearningContextService:
    """
    Resolve prior deterministic outcome learning as read-only metadata.

    This service never grants Skill authority, mutates Work/Memory, injects
    handler input, dispatches execution, retries, replans, or calls the legacy
    Orchestrator. Current persisted Work and User state are authoritative.
    """

    def __init__(
        self,
        db: Session,
        *,
        repository: WorkLearningContextRepository | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else WorkLearningContextRepository(db)
        )

    def resolve(
        self,
        work_item_id: int,
        *,
        skill_version_id: int,
        authority_user_id: int,
        limit: int = DEFAULT_LEARNING_CONTEXT_LIMIT,
        as_of: datetime | None = None,
    ) -> tuple[WorkLearningContextItem, ...]:
        normalized_work_id = self._positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_skill_version_id = self._positive_id(
            skill_version_id,
            field_name="skill_version_id",
        )
        normalized_authority_user_id = self._positive_id(
            authority_user_id,
            field_name="authority_user_id",
        )
        normalized_limit = self._bounded_limit(limit)
        normalized_as_of = self._as_of(as_of)

        work_item = self.db.get(
            WorkItem,
            normalized_work_id,
            populate_existing=True,
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Trabalho inexistente ou não acessível."
            )

        authority = self.db.get(
            User,
            normalized_authority_user_id,
            populate_existing=True,
        )
        if authority is None or not authority.active:
            raise WorkStateError(
                "Usuário-principal inexistente ou inativo."
            )

        self._authorize_reads(
            work_item=work_item,
            authority=authority,
        )

        candidates = self.repository.list_outcome_candidates(
            target_work_item_id=work_item.id,
            skill_version_id=normalized_skill_version_id,
            scope_type=work_item.scope_type,
            account_id=work_item.account_id,
            subject_user_id=work_item.subject_user_id,
            as_of=normalized_as_of,
            limit=normalized_limit,
        )

        return tuple(
            WorkLearningContextItem(
                memory_id=candidate.memory_id,
                source_work_item_id=candidate.source_work_item_id,
                work_skill_execution_id=(
                    candidate.work_skill_execution_id
                ),
                skill_version_id=candidate.skill_version_id,
                terminal_status=candidate.terminal_status,
                evaluation_code=candidate.evaluation_code,
                learning_signal=candidate.learning_signal,
                observed_at=candidate.observed_at,
            )
            for candidate in candidates[:normalized_limit]
        )

    def _authorize_reads(
        self,
        *,
        work_item: WorkItem,
        authority: User,
    ) -> None:
        scope = {
            "scope_type": work_item.scope_type,
            "account_id": work_item.account_id,
            "subject_user_id": work_item.subject_user_id,
        }
        authorize_work_scope(
            db=self.db,
            role=authority.role,
            actor_user_id=authority.id,
            operation="read",
            **scope,
        )
        authorize_memory_scope(
            db=self.db,
            role=authority.role,
            actor_user_id=authority.id,
            operation="read",
            **scope,
        )

    @staticmethod
    def _positive_id(
        value: int,
        *,
        field_name: str,
    ) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise WorkValidationError(
                f"{field_name} inválido."
            )
        return value

    @staticmethod
    def _bounded_limit(value: int) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            or value > MAX_LEARNING_CONTEXT_LIMIT
        ):
            raise WorkValidationError(
                "limit deve estar entre 1 e 10."
            )
        return value

    @staticmethod
    def _as_of(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise WorkValidationError(
                "as_of deve possuir timezone."
            )
        return value.astimezone(timezone.utc)
