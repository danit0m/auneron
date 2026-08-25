from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work import WorkItem
from app.models.work_skill_execution import WorkSkillExecution


class WorkSkillExecutionRepository:
    """
    Persistência do vínculo durável Work -> Skill.

    O repositório é deliberadamente transaction-free. O serviço que compõe
    Work/Approval/Skill é o único dono de commit/rollback.
    """

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add(
        self,
        execution: WorkSkillExecution,
    ) -> WorkSkillExecution:
        self.db.add(execution)
        self.db.flush()
        return execution

    def get_by_work_item(
        self,
        work_item_id: int,
    ) -> WorkSkillExecution | None:
        statement = select(
            WorkSkillExecution
        ).where(
            WorkSkillExecution.work_item_id
            == work_item_id
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def lock_by_work_item(
        self,
        work_item_id: int,
    ) -> WorkSkillExecution | None:
        statement = (
            select(WorkSkillExecution)
            .where(
                WorkSkillExecution.work_item_id
                == work_item_id
            )
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_reconcilable(
        self,
        *,
        limit: int = 100,
    ) -> list[WorkSkillExecution]:
        statement = (
            select(WorkSkillExecution)
            .where(
                WorkSkillExecution.status.in_({
                    "configured",
                    "approval_pending",
                    "ready",
                    "succeeded",
                    "failed",
                    "timed_out",
                    "cancelled",
                })
            )
            .order_by(
                WorkSkillExecution.updated_at,
                WorkSkillExecution.id,
            )
            .limit(limit)
        )
        return list(
            self.db.execute(
                statement
            ).scalars().all()
        )

    def list_recovery_candidate_work_ids(
        self,
        *,
        limit: int = 100,
    ) -> list[int]:
        """Return bounded Work IDs that require non-executing reconciliation."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 1000
        ):
            raise ValueError(
                "limit inválido para Work Skill recovery."
            )

        statement = (
            select(WorkSkillExecution.work_item_id)
            .join(
                WorkItem,
                WorkItem.id == WorkSkillExecution.work_item_id,
            )
            .where(
                or_(
                    WorkSkillExecution.status.in_({
                        "configured",
                        "approval_pending",
                    }),
                    and_(
                        WorkSkillExecution.status == "ready",
                        WorkItem.status == "in_progress",
                    ),
                    and_(
                        WorkSkillExecution.status == "succeeded",
                        WorkItem.status != "completed",
                    ),
                    and_(
                        WorkSkillExecution.status.in_({
                            "failed",
                            "timed_out",
                        }),
                        WorkItem.status != "blocked",
                    ),
                    and_(
                        WorkSkillExecution.status == "cancelled",
                        WorkItem.status != "cancelled",
                    ),
                )
            )
            .order_by(
                WorkSkillExecution.updated_at,
                WorkSkillExecution.id,
            )
            .limit(limit)
        )
        return [
            int(work_item_id)
            for work_item_id in self.db.execute(
                statement
            ).scalars().all()
        ]
