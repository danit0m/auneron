from datetime import datetime
from datetime import timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.memory_authorization import authorize_memory_scope
from app.core.skill_errors import SkillValidationError
from app.core.work_authorization import authorize_work_scope
from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.models.user import User
from app.models.work import WorkItem
from app.models.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshot,
)
from app.repositories.work_learning_runtime_context_snapshot_repository import (
    WorkLearningRuntimeContextSnapshotRepository,
)
from app.services.skill_runtime_context import (
    WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
)
from app.services.skill_runtime_context import WorkLearningRuntimeContext
from app.services.skill_runtime_context import (
    normalize_work_learning_runtime_context,
)
from app.services.work_learning_context import WorkLearningContextService


PRODUCTION_WORK_LEARNING_CONTEXT_LIMIT = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


class WorkLearningRuntimeContextSnapshotService:
    """
    Durable server-only Work learning-context snapshot boundary.

    Current Work and Memory read authority is revalidated on every call.
    The first authorized context is persisted once per WorkSkillExecution and
    retries reuse that exact immutable payload instead of re-resolving outcomes.
    """

    def __init__(
        self,
        db: Session,
        *,
        repository: (
            WorkLearningRuntimeContextSnapshotRepository | None
        ) = None,
        learning_context_service: WorkLearningContextService | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else WorkLearningRuntimeContextSnapshotRepository(db)
        )
        self.learning_context_service = (
            learning_context_service
            if learning_context_service is not None
            else WorkLearningContextService(db)
        )

    def get_or_create(
        self,
        *,
        work_skill_execution_id: int,
        work_item_id: int,
        skill_version_id: int,
        authority_user_id: int,
    ) -> WorkLearningRuntimeContext:
        execution_id = _positive_id(
            work_skill_execution_id,
            field_name="work_skill_execution_id",
        )
        normalized_work_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_version_id = _positive_id(
            skill_version_id,
            field_name="skill_version_id",
        )
        normalized_authority_id = _positive_id(
            authority_user_id,
            field_name="authority_user_id",
        )

        try:
            self._load_and_authorize(
                work_item_id=normalized_work_id,
                authority_user_id=normalized_authority_id,
            )

            existing = self.repository.get_by_execution_id(
                execution_id
            )
            if existing is not None:
                return self._validate_snapshot(
                    existing,
                    work_skill_execution_id=execution_id,
                    work_item_id=normalized_work_id,
                    skill_version_id=normalized_version_id,
                )

            resolved_as_of = _utc_now()
            items = self.learning_context_service.resolve(
                normalized_work_id,
                skill_version_id=normalized_version_id,
                authority_user_id=normalized_authority_id,
                limit=PRODUCTION_WORK_LEARNING_CONTEXT_LIMIT,
                as_of=resolved_as_of,
            )
            payload = {
                "protocol": WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
                "items": [
                    {
                        "memory_id": item.memory_id,
                        "source_work_item_id": item.source_work_item_id,
                        "work_skill_execution_id": (
                            item.work_skill_execution_id
                        ),
                        "skill_version_id": item.skill_version_id,
                        "terminal_status": item.terminal_status,
                        "evaluation_code": item.evaluation_code,
                        "learning_signal": item.learning_signal,
                        "observed_at": item.observed_at,
                    }
                    for item in items
                ],
            }
            try:
                normalized = normalize_work_learning_runtime_context(
                    payload,
                    expected_skill_version_id=normalized_version_id,
                )
            except SkillValidationError as error:
                raise WorkStateError(
                    "Learning context autorizado não satisfaz "
                    "o protocolo runtime."
                ) from error
        except Exception:
            self.db.rollback()
            raise

        snapshot = WorkLearningRuntimeContextSnapshot(
            work_skill_execution_id=execution_id,
            work_item_id=normalized_work_id,
            skill_version_id=normalized_version_id,
            protocol=normalized.protocol,
            context_payload=normalized.payload,
            context_digest=normalized.digest,
            item_count=len(normalized.payload["items"]),
            context_bytes=len(normalized.canonical_bytes),
            resolved_as_of=resolved_as_of,
        )

        try:
            self.repository.add(
                snapshot
            )
            self.db.commit()
            self.db.refresh(
                snapshot
            )
        except IntegrityError as error:
            self.db.rollback()
            self._load_and_authorize(
                work_item_id=normalized_work_id,
                authority_user_id=normalized_authority_id,
            )
            concurrent = self.repository.get_by_execution_id(
                execution_id
            )
            if concurrent is None:
                raise WorkConflictError(
                    "Conflito ao persistir snapshot de Work Learning Context."
                ) from error
            return self._validate_snapshot(
                concurrent,
                work_skill_execution_id=execution_id,
                work_item_id=normalized_work_id,
                skill_version_id=normalized_version_id,
            )
        except Exception:
            self.db.rollback()
            raise

        return self._validate_snapshot(
            snapshot,
            work_skill_execution_id=execution_id,
            work_item_id=normalized_work_id,
            skill_version_id=normalized_version_id,
        )

    def _load_and_authorize(
        self,
        *,
        work_item_id: int,
        authority_user_id: int,
    ) -> tuple[WorkItem, User]:
        work_item = self.db.get(
            WorkItem,
            work_item_id,
            populate_existing=True,
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Trabalho inexistente ou não acessível."
            )

        authority = self.db.get(
            User,
            authority_user_id,
            populate_existing=True,
        )
        if authority is None or not authority.active:
            raise WorkStateError(
                "Usuário-principal inexistente ou inativo."
            )

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
        return work_item, authority

    @staticmethod
    def _validate_snapshot(
        snapshot: WorkLearningRuntimeContextSnapshot,
        *,
        work_skill_execution_id: int,
        work_item_id: int,
        skill_version_id: int,
    ) -> WorkLearningRuntimeContext:
        if (
            snapshot.work_skill_execution_id
            != work_skill_execution_id
            or snapshot.work_item_id != work_item_id
            or snapshot.skill_version_id != skill_version_id
            or snapshot.protocol
            != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
        ):
            raise WorkConflictError(
                "Snapshot de Work Learning Context diverge da execução."
            )

        if (
            snapshot.resolved_as_of is None
            or snapshot.resolved_as_of.tzinfo is None
            or snapshot.resolved_as_of.utcoffset() is None
        ):
            raise WorkConflictError(
                "Snapshot de Work Learning Context possui timestamp inválido."
            )

        try:
            normalized = normalize_work_learning_runtime_context(
                snapshot.context_payload,
                expected_skill_version_id=skill_version_id,
            )
        except SkillValidationError as error:
            raise WorkConflictError(
                "Snapshot de Work Learning Context possui payload inválido."
            ) from error

        if (
            snapshot.context_digest != normalized.digest
            or snapshot.item_count
            != len(normalized.payload["items"])
            or snapshot.context_bytes
            != len(normalized.canonical_bytes)
        ):
            raise WorkConflictError(
                "Snapshot de Work Learning Context falhou na validação imutável."
            )

        return normalized
