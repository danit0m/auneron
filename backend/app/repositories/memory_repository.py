from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem


class MemoryRepository:
    """
    Persistência SQLAlchemy do Memory System.

    Esta camada nunca executa commit/rollback. A fronteira
    transacional pertence ao MemoryService.
    """

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add_memory(
        self,
        memory: MemoryItem,
    ) -> MemoryItem:
        self.db.add(memory)
        self.db.flush()

        return memory

    def get_by_id(
        self,
        memory_id: int,
    ) -> MemoryItem | None:
        return self.db.get(
            MemoryItem,
            memory_id,
        )

    def find_active_by_key(
        self,
        *,
        scope_type: str,
        memory_key: str,
        account_id: int | None = None,
        subject_user_id: int | None = None,
        for_update: bool = False,
    ) -> MemoryItem | None:
        statement = (
            select(MemoryItem)
            .where(
                MemoryItem.status == "active",
                MemoryItem.scope_type == scope_type,
                MemoryItem.memory_key == memory_key,
            )
        )

        if scope_type == "global":
            statement = statement.where(
                MemoryItem.account_id.is_(None),
                MemoryItem.subject_user_id.is_(None),
            )
        elif scope_type == "account":
            statement = statement.where(
                MemoryItem.account_id == account_id,
                MemoryItem.subject_user_id.is_(None),
            )
        elif scope_type == "user":
            statement = statement.where(
                MemoryItem.account_id.is_(None),
                MemoryItem.subject_user_id
                == subject_user_id,
            )

        if for_update:
            statement = statement.with_for_update()

        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def lock_by_id(
        self,
        memory_id: int,
    ) -> MemoryItem | None:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.id == memory_id)
            .with_for_update()
        )

        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def update_status(
        self,
        memory: MemoryItem,
        *,
        status: str,
        reason: str | None,
        changed_at: datetime,
    ) -> MemoryItem:
        memory.status = status
        memory.status_reason = reason
        memory.status_changed_at = changed_at
        self.db.flush()

        return memory

    def insert_evidence(
        self,
        evidence: MemoryEvidence,
    ) -> MemoryEvidence:
        self.db.add(evidence)
        self.db.flush()

        return evidence

    def find_evidence_by_hash(
        self,
        *,
        memory_id: int,
        evidence_hash: str,
    ) -> MemoryEvidence | None:
        statement = select(MemoryEvidence).where(
            MemoryEvidence.memory_id == memory_id,
            MemoryEvidence.evidence_hash == evidence_hash,
        )

        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_evidence(
        self,
        memory_id: int,
    ) -> list[MemoryEvidence]:
        statement = (
            select(MemoryEvidence)
            .where(
                MemoryEvidence.memory_id == memory_id,
            )
            .order_by(
                MemoryEvidence.created_at.asc(),
                MemoryEvidence.id.asc(),
            )
        )

        return list(
            self.db.execute(statement).scalars()
        )

    def expire_due_batch(
        self,
        *,
        as_of: datetime,
        limit: int,
        reason: str,
        changed_at: datetime,
    ) -> list[MemoryItem]:
        statement = (
            select(MemoryItem)
            .where(
                MemoryItem.status == "active",
                MemoryItem.valid_until.is_not(None),
                MemoryItem.valid_until <= as_of,
            )
            .order_by(
                MemoryItem.valid_until.asc(),
                MemoryItem.id.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        memories = list(
            self.db.execute(statement).scalars()
        )

        for memory in memories:
            memory.status = "expired"
            memory.status_reason = reason
            memory.status_changed_at = changed_at

        self.db.flush()

        return memories
