from sqlalchemy import select
from sqlalchemy.orm import Session

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
