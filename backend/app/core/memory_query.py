from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class MemoryScope:
    scope_type: str
    account_id: int | None = None
    subject_user_id: int | None = None


@dataclass(frozen=True)
class MemoryQuery:
    scope: MemoryScope
    memory_types: tuple[str, ...]
    statuses: tuple[str, ...]
    source_types: tuple[str, ...]
    memory_key: str | None
    min_importance: Decimal | None
    min_confidence: Decimal | None
    valid_at: datetime
    created_after: datetime | None
    created_before: datetime | None
    text_query: str | None
    sort: str
    limit: int
