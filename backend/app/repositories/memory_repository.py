from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy import literal_column
from sqlalchemy import Numeric
from sqlalchemy import select
from sqlalchemy import tuple_
from sqlalchemy.sql import Select
from sqlalchemy.orm import Session

from app.core.memory_query import MemoryQuery
from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem


@dataclass(frozen=True)
class MemorySearchRow:
    memory: MemoryItem
    relevance: Decimal | None


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

    def search(
        self,
        query: MemoryQuery,
        *,
        cursor_position: tuple[Any, ...] | None = None,
    ) -> list[MemorySearchRow]:
        relevance = self._text_rank(query)
        statement = (
            select(
                MemoryItem,
                relevance.label("text_rank"),
            )
            if relevance is not None
            else select(MemoryItem)
        )
        statement = self._apply_query_filters(
            statement,
            query,
            relevance=relevance,
        )
        order_columns, descending = self._sort_columns(
            query.sort,
            relevance=relevance,
        )

        if cursor_position is not None:
            left = tuple_(*order_columns)
            right = tuple_(*cursor_position)
            statement = statement.where(
                left < right if descending else left > right
            )

        ordering = [
            column.desc() if descending else column.asc()
            for column in order_columns
        ]
        statement = statement.order_by(*ordering).limit(
            query.limit + 1
        )

        result = self.db.execute(statement)

        if relevance is None:
            return [
                MemorySearchRow(
                    memory=memory,
                    relevance=None,
                )
                for memory in result.scalars()
            ]

        return [
            MemorySearchRow(
                memory=row[0],
                relevance=row[1],
            )
            for row in result.all()
        ]

    def count(self, query: MemoryQuery) -> int:
        relevance = self._text_rank(query)
        statement = self._apply_query_filters(
            select(func.count(MemoryItem.id)),
            query,
            relevance=relevance,
        )

        return self.db.execute(statement).scalar_one()

    @staticmethod
    def _apply_query_filters(
        statement: Select[Any],
        query: MemoryQuery,
        *,
        relevance: Any | None = None,
    ) -> Select[Any]:
        scope = query.scope
        statement = statement.where(
            MemoryItem.scope_type == scope.scope_type,
            MemoryItem.status.in_(query.statuses),
            MemoryItem.valid_from <= query.valid_at,
            (
                MemoryItem.valid_until.is_(None)
                | (MemoryItem.valid_until > query.valid_at)
            ),
        )

        if scope.scope_type == "global":
            statement = statement.where(
                MemoryItem.account_id.is_(None),
                MemoryItem.subject_user_id.is_(None),
            )
        elif scope.scope_type == "account":
            statement = statement.where(
                MemoryItem.account_id == scope.account_id,
                MemoryItem.subject_user_id.is_(None),
            )
        else:
            statement = statement.where(
                MemoryItem.account_id.is_(None),
                MemoryItem.subject_user_id == scope.subject_user_id,
            )

        if query.memory_types:
            statement = statement.where(
                MemoryItem.memory_type.in_(query.memory_types)
            )

        if query.source_types:
            statement = statement.where(
                MemoryItem.source_type.in_(query.source_types)
            )

        if query.memory_key is not None:
            statement = statement.where(
                MemoryItem.memory_key == query.memory_key
            )

        if query.min_importance is not None:
            statement = statement.where(
                MemoryItem.importance >= query.min_importance
            )

        if query.min_confidence is not None:
            statement = statement.where(
                MemoryItem.confidence >= query.min_confidence
            )

        if query.created_after is not None:
            statement = statement.where(
                MemoryItem.created_at >= query.created_after
            )

        if query.created_before is not None:
            statement = statement.where(
                MemoryItem.created_at <= query.created_before
            )

        if query.text_query is not None:
            statement = statement.where(
                MemoryRepository._text_match(query)
            )

        return statement

    @staticmethod
    def _sort_columns(
        sort: str,
        *,
        relevance: Any | None = None,
    ) -> tuple[tuple[Any, ...], bool]:
        if sort == "relevance":
            if relevance is None:
                raise ValueError(
                    "Ordenação por relevância exige busca textual."
                )

            return (
                relevance,
                MemoryItem.importance,
                MemoryItem.confidence,
                MemoryItem.valid_from,
                MemoryItem.id,
            ), True

        if sort == "newest":
            return (MemoryItem.created_at, MemoryItem.id), True

        if sort == "oldest":
            return (MemoryItem.created_at, MemoryItem.id), False

        if sort == "confidence":
            return (
                MemoryItem.confidence,
                MemoryItem.importance,
                MemoryItem.valid_from,
                MemoryItem.id,
            ), True

        return (
            MemoryItem.importance,
            MemoryItem.confidence,
            MemoryItem.valid_from,
            MemoryItem.id,
        ), True

    @staticmethod
    def _text_rank(query: MemoryQuery) -> Any | None:
        if query.text_query is None:
            return None

        document, text_query = (
            MemoryRepository._text_expressions(query)
        )

        return func.cast(
            func.ts_rank_cd(document, text_query),
            Numeric(
                precision=20,
                scale=12,
                asdecimal=True,
            ),
        )

    @staticmethod
    def _text_match(query: MemoryQuery) -> Any:
        document, text_query = (
            MemoryRepository._text_expressions(query)
        )

        return document.op("@@")(text_query)

    @staticmethod
    def _text_expressions(query: MemoryQuery) -> tuple[Any, Any]:
        if query.text_query is None:
            raise ValueError("Busca textual exige text_query.")

        configuration = literal_column(
            "'portuguese'::regconfig"
        )
        document = func.to_tsvector(
            configuration,
            func.coalesce(
                MemoryItem.title,
                literal_column("''"),
            )
            + literal_column("' '")
            + func.coalesce(
                MemoryItem.content,
                literal_column("''"),
            ),
        )
        text_query = func.websearch_to_tsquery(
            configuration,
            query.text_query,
        )

        return document, text_query

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
