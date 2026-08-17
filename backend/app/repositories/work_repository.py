from datetime import datetime

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem
from app.models.work import WorkDependency
from app.models.work import WorkEvent
from app.models.work import WorkItem
from app.models.work import WorkMemoryLink
from app.models.work import WorkRecurrenceOccurrence
from app.models.work import WorkRecurrenceRule


class WorkRepository:
    """
    Persistência SQLAlchemy do Work Manager.

    O repositório nunca executa commit ou rollback. A fronteira
    transacional pertence exclusivamente ao WorkManagerService.
    Eventos são append-only: não existem métodos para alterá-los
    ou removê-los.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def add_work_item(self, item: WorkItem) -> WorkItem:
        self.db.add(item)
        self.db.flush()
        return item

    def get_by_id(self, work_item_id: int) -> WorkItem | None:
        return self.db.get(WorkItem, work_item_id)

    def lock_by_id(self, work_item_id: int) -> WorkItem | None:
        statement = (
            select(WorkItem)
            .where(WorkItem.id == work_item_id)
            .with_for_update()
        )
        return self.db.execute(statement).scalar_one_or_none()

    def find_by_key(
        self,
        *,
        scope_type: str,
        work_key: str,
        account_id: int | None = None,
        subject_user_id: int | None = None,
        for_update: bool = False,
    ) -> WorkItem | None:
        statement = select(WorkItem).where(
            WorkItem.scope_type == scope_type,
            WorkItem.work_key == work_key,
        )

        if scope_type == "global":
            statement = statement.where(
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id.is_(None),
            )
        elif scope_type == "account":
            statement = statement.where(
                WorkItem.account_id == account_id,
                WorkItem.subject_user_id.is_(None),
            )
        elif scope_type == "user":
            statement = statement.where(
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id == subject_user_id,
            )

        if for_update:
            statement = statement.with_for_update()

        return self.db.execute(statement).scalar_one_or_none()

    def list_by_scope(
        self,
        *,
        scope_type: str,
        account_id: int | None,
        subject_user_id: int | None,
        statuses: tuple[str, ...] | None,
        priorities: tuple[str, ...] | None,
        assignee_user_id: int | None,
        limit: int,
    ) -> list[WorkItem]:
        statement = select(WorkItem).where(
            WorkItem.scope_type == scope_type
        )

        if scope_type == "global":
            statement = statement.where(
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id.is_(None),
            )
        elif scope_type == "account":
            statement = statement.where(
                WorkItem.account_id == account_id,
                WorkItem.subject_user_id.is_(None),
            )
        elif scope_type == "user":
            statement = statement.where(
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id == subject_user_id,
            )

        if statuses:
            statement = statement.where(
                WorkItem.status.in_(statuses)
            )

        if priorities:
            statement = statement.where(
                WorkItem.priority.in_(priorities)
            )

        if assignee_user_id is not None:
            statement = statement.where(
                WorkItem.assignee_user_id
                == assignee_user_id
            )

        statement = statement.order_by(
            WorkItem.updated_at.desc(),
            WorkItem.id.desc(),
        ).limit(limit)

        return list(self.db.execute(statement).scalars())

    def add_event(self, event: WorkEvent) -> WorkEvent:
        self.db.add(event)
        self.db.flush()
        return event

    def find_event_by_idempotency_key(
        self,
        *,
        work_item_id: int,
        idempotency_key: str,
    ) -> WorkEvent | None:
        statement = select(WorkEvent).where(
            WorkEvent.work_item_id == work_item_id,
            WorkEvent.idempotency_key == idempotency_key,
        )
        return self.db.execute(statement).scalar_one_or_none()

    def get_created_event(
        self,
        work_item_id: int,
    ) -> WorkEvent | None:
        statement = (
            select(WorkEvent)
            .where(
                WorkEvent.work_item_id == work_item_id,
                WorkEvent.event_type == "created",
            )
            .order_by(WorkEvent.id.asc())
            .limit(1)
        )
        return self.db.execute(statement).scalar_one_or_none()

    def list_events(
        self,
        work_item_id: int,
        *,
        after_id: int | None = None,
        limit: int | None = None,
    ) -> list[WorkEvent]:
        statement = (
            select(WorkEvent)
            .where(WorkEvent.work_item_id == work_item_id)
            .order_by(WorkEvent.id.asc())
        )

        if after_id is not None:
            statement = statement.where(
                WorkEvent.id > after_id
            )

        if limit is not None:
            statement = statement.limit(limit)

        return list(self.db.execute(statement).scalars())

    def add_dependency(
        self,
        dependency: WorkDependency,
    ) -> WorkDependency:
        self.db.add(dependency)
        self.db.flush()
        return dependency

    def find_dependency(
        self,
        *,
        work_item_id: int,
        depends_on_work_item_id: int,
    ) -> WorkDependency | None:
        statement = select(WorkDependency).where(
            WorkDependency.work_item_id == work_item_id,
            WorkDependency.depends_on_work_item_id
            == depends_on_work_item_id,
        )
        return self.db.execute(statement).scalar_one_or_none()

    def list_dependencies(
        self,
        work_item_id: int,
    ) -> list[WorkDependency]:
        statement = (
            select(WorkDependency)
            .where(WorkDependency.work_item_id == work_item_id)
            .order_by(WorkDependency.id.asc())
        )
        return list(self.db.execute(statement).scalars())

    def list_dependency_predecessors(
        self,
        work_item_id: int,
        *,
        after_id: int | None = None,
        limit: int | None = None,
    ) -> list[tuple[WorkDependency, WorkItem]]:
        statement = (
            select(WorkDependency, WorkItem)
            .join(
                WorkItem,
                WorkItem.id
                == WorkDependency.depends_on_work_item_id,
            )
            .where(
                WorkDependency.work_item_id == work_item_id
            )
            .order_by(WorkDependency.id.asc())
        )

        if after_id is not None:
            statement = statement.where(
                WorkDependency.id > after_id
            )

        if limit is not None:
            statement = statement.limit(limit)

        return [
            (row[0], row[1])
            for row in self.db.execute(statement).all()
        ]

    def lock_dependency_graph(self) -> None:
        self.db.execute(
            select(func.pg_advisory_xact_lock(220022))
        ).scalar_one()

    def would_create_dependency_cycle(
        self,
        *,
        work_item_id: int,
        depends_on_work_item_id: int,
    ) -> bool:
        statement = text(
            """
            WITH RECURSIVE reachable(id) AS (
                SELECT depends_on_work_item_id
                FROM work_dependencies
                WHERE work_item_id = :start_id

                UNION

                SELECT dependency.depends_on_work_item_id
                FROM work_dependencies AS dependency
                JOIN reachable
                  ON dependency.work_item_id = reachable.id
            )
            SELECT EXISTS (
                SELECT 1
                FROM reachable
                WHERE id = :target_id
            )
            """
        )
        return bool(
            self.db.execute(
                statement,
                {
                    "start_id": depends_on_work_item_id,
                    "target_id": work_item_id,
                },
            ).scalar_one()
        )

    def remove_dependency(
        self,
        dependency: WorkDependency,
    ) -> None:
        self.db.delete(dependency)
        self.db.flush()

    def add_memory_link(
        self,
        link: WorkMemoryLink,
    ) -> WorkMemoryLink:
        self.db.add(link)
        self.db.flush()
        return link

    def find_memory_link(
        self,
        *,
        work_item_id: int,
        memory_id: int,
        relation: str,
    ) -> WorkMemoryLink | None:
        statement = select(WorkMemoryLink).where(
            WorkMemoryLink.work_item_id == work_item_id,
            WorkMemoryLink.memory_id == memory_id,
            WorkMemoryLink.relation == relation,
        )
        return self.db.execute(statement).scalar_one_or_none()

    def list_memory_links(
        self,
        work_item_id: int,
        *,
        after_id: int | None = None,
        limit: int | None = None,
    ) -> list[WorkMemoryLink]:
        statement = (
            select(WorkMemoryLink)
            .where(WorkMemoryLink.work_item_id == work_item_id)
            .order_by(WorkMemoryLink.id.asc())
        )

        if after_id is not None:
            statement = statement.where(
                WorkMemoryLink.id > after_id
            )

        if limit is not None:
            statement = statement.limit(limit)

        return list(self.db.execute(statement).scalars())

    def remove_memory_link(self, link: WorkMemoryLink) -> None:
        self.db.delete(link)
        self.db.flush()

    def memory_exists(self, memory_id: int) -> bool:
        return self.db.get(MemoryItem, memory_id) is not None

    def list_sla_breaches(
        self,
        *,
        as_of: datetime,
        limit: int,
        scope_type: str | None = None,
        account_id: int | None = None,
        subject_user_id: int | None = None,
    ) -> list[WorkItem]:
        statement = (
            select(WorkItem)
            .where(
                WorkItem.status.notin_(("completed", "cancelled")),
                WorkItem.sla_due_at.is_not(None),
                WorkItem.sla_due_at < as_of,
            )
        )

        if scope_type == "global":
            statement = statement.where(
                WorkItem.scope_type == "global",
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id.is_(None),
            )
        elif scope_type == "account":
            statement = statement.where(
                WorkItem.scope_type == "account",
                WorkItem.account_id == account_id,
                WorkItem.subject_user_id.is_(None),
            )
        elif scope_type == "user":
            statement = statement.where(
                WorkItem.scope_type == "user",
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id == subject_user_id,
            )

        statement = statement.order_by(
            WorkItem.sla_due_at.asc(),
            WorkItem.id.asc(),
        ).limit(limit)

        return list(self.db.execute(statement).scalars())

    def add_recurrence_rule(
        self,
        rule: WorkRecurrenceRule,
    ) -> WorkRecurrenceRule:
        self.db.add(rule)
        self.db.flush()
        return rule

    def get_recurrence_rule(
        self,
        work_item_id: int,
    ) -> WorkRecurrenceRule | None:
        statement = select(WorkRecurrenceRule).where(
            WorkRecurrenceRule.work_item_id == work_item_id
        )
        return self.db.execute(statement).scalar_one_or_none()

    def lock_recurrence_rule(
        self,
        work_item_id: int,
    ) -> WorkRecurrenceRule | None:
        statement = (
            select(WorkRecurrenceRule)
            .where(
                WorkRecurrenceRule.work_item_id == work_item_id
            )
            .with_for_update()
        )
        return self.db.execute(statement).scalar_one_or_none()

    def list_due_recurrence_rules(
        self,
        *,
        as_of: datetime,
        limit: int,
    ) -> list[WorkRecurrenceRule]:
        statement = (
            select(WorkRecurrenceRule)
            .where(
                WorkRecurrenceRule.active.is_(True),
                WorkRecurrenceRule.next_occurrence_at <= as_of,
            )
            .order_by(
                WorkRecurrenceRule.next_occurrence_at.asc(),
                WorkRecurrenceRule.id.asc(),
            )
            .limit(limit)
        )
        return list(self.db.execute(statement).scalars())

    def add_recurrence_occurrence(
        self,
        occurrence: WorkRecurrenceOccurrence,
    ) -> WorkRecurrenceOccurrence:
        self.db.add(occurrence)
        self.db.flush()
        return occurrence

    def get_recurrence_occurrence(
        self,
        *,
        recurrence_rule_id: int,
        occurrence_number: int,
    ) -> WorkRecurrenceOccurrence | None:
        statement = select(WorkRecurrenceOccurrence).where(
            WorkRecurrenceOccurrence.recurrence_rule_id
            == recurrence_rule_id,
            WorkRecurrenceOccurrence.occurrence_number
            == occurrence_number,
        )
        return self.db.execute(statement).scalar_one_or_none()

    def list_recurrence_occurrences(
        self,
        recurrence_rule_id: int,
        *,
        after_id: int | None = None,
        limit: int | None = None,
    ) -> list[WorkRecurrenceOccurrence]:
        statement = (
            select(WorkRecurrenceOccurrence)
            .where(
                WorkRecurrenceOccurrence.recurrence_rule_id
                == recurrence_rule_id
            )
            .order_by(WorkRecurrenceOccurrence.id.asc())
        )

        if after_id is not None:
            statement = statement.where(
                WorkRecurrenceOccurrence.id > after_id
            )

        if limit is not None:
            statement = statement.limit(limit)

        return list(self.db.execute(statement).scalars())
