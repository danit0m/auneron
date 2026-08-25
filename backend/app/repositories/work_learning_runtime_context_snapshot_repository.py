from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshot,
)


class WorkLearningRuntimeContextSnapshotRepository:
    """Transaction-free persistence for immutable Work runtime-context snapshots."""

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add(
        self,
        snapshot: WorkLearningRuntimeContextSnapshot,
    ) -> WorkLearningRuntimeContextSnapshot:
        self.db.add(snapshot)
        self.db.flush()
        return snapshot

    def get_by_execution_id(
        self,
        work_skill_execution_id: int,
    ) -> WorkLearningRuntimeContextSnapshot | None:
        statement = select(
            WorkLearningRuntimeContextSnapshot
        ).where(
            WorkLearningRuntimeContextSnapshot.work_skill_execution_id
            == work_skill_execution_id
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()
