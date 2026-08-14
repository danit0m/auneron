from datetime import datetime
from decimal import Decimal
from typing import Annotated
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PlainSerializer
from pydantic import model_validator

from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem


MemoryType = Literal[
    "fact",
    "event",
    "observation",
    "decision",
    "summary",
]

MemoryStatus = Literal[
    "active",
    "superseded",
    "expired",
    "invalidated",
    "archived",
]

MemoryScopeType = Literal[
    "global",
    "account",
    "user",
]

MemorySourceType = Literal[
    "database",
    "upload",
    "user",
    "agent",
    "system",
    "api",
    "derived",
]

EvidenceRelation = Literal[
    "supports",
    "contradicts",
    "context",
]

MemorySort = Literal[
    "relevance",
    "newest",
    "oldest",
    "importance",
    "confidence",
]

Score = Annotated[
    Decimal,
    Field(
        ge=Decimal("0"),
        le=Decimal("1"),
        max_digits=4,
        decimal_places=3,
    ),
    PlainSerializer(
        lambda value: float(value),
        return_type=float,
        when_used="json",
    ),
]


class MemorySchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class MemoryScopeRequest(MemorySchema):
    type: MemoryScopeType
    account_id: int | None = Field(
        default=None,
        gt=0,
    )
    subject_user_id: int | None = Field(
        default=None,
        gt=0,
    )

    @model_validator(mode="after")
    def validate_scope(self) -> "MemoryScopeRequest":
        if self.type == "global":
            valid = (
                self.account_id is None
                and self.subject_user_id is None
            )
        elif self.type == "account":
            valid = (
                self.account_id is not None
                and self.subject_user_id is None
            )
        else:
            valid = (
                self.account_id is None
                and self.subject_user_id is not None
            )

        if not valid:
            raise ValueError(
                "Combinacao de scope invalida."
            )

        return self


class MemorySourceRequest(MemorySchema):
    type: MemorySourceType
    reference: str = Field(
        min_length=1,
        max_length=500,
    )


class EvidenceCreateRequest(MemorySchema):
    relation: EvidenceRelation
    source_type: MemorySourceType
    source_reference: str = Field(
        min_length=1,
        max_length=500,
    )
    source_memory_id: int | None = Field(
        default=None,
        gt=0,
    )
    evidence_text: str = Field(
        min_length=1,
        max_length=10000,
    )
    weight: Score = Decimal("1.000")
    observed_at: datetime | None = None
    context_data: dict[str, Any] = Field(
        default_factory=dict,
    )


class MemoryCreateRequest(MemorySchema):
    memory_type: MemoryType
    title: str = Field(
        min_length=1,
        max_length=200,
    )
    content: str = Field(
        min_length=1,
        max_length=10000,
    )
    memory_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    scope: MemoryScopeRequest
    importance: Score = Decimal("0.500")
    confidence: Score
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: MemorySourceRequest
    context_data: dict[str, Any] = Field(
        default_factory=dict,
    )
    evidence: list[EvidenceCreateRequest] = Field(
        default_factory=list,
        max_length=20,
    )


class MemorySupersedeRequest(MemorySchema):
    reason: str = Field(
        min_length=1,
        max_length=2000,
    )
    memory_type: MemoryType
    title: str = Field(
        min_length=1,
        max_length=200,
    )
    content: str = Field(
        min_length=1,
        max_length=10000,
    )
    importance: Score = Decimal("0.500")
    confidence: Score
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: MemorySourceRequest
    context_data: dict[str, Any] = Field(
        default_factory=dict,
    )
    evidence: list[EvidenceCreateRequest] = Field(
        default_factory=list,
        max_length=20,
    )


class MemoryLifecycleRequest(MemorySchema):
    reason: str = Field(
        min_length=1,
        max_length=2000,
    )


class MemoryArchiveRequest(MemorySchema):
    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )


class MemoryScopeResponse(MemorySchema):
    type: MemoryScopeType
    account_id: int | None
    subject_user_id: int | None


class MemorySourceResponse(MemorySchema):
    type: MemorySourceType
    reference: str


class MemoryResponse(MemorySchema):
    id: int
    memory_type: MemoryType
    title: str
    content: str
    memory_key: str | None
    scope: MemoryScopeResponse
    created_by_user_id: int | None
    importance: Score
    confidence: Score
    status: MemoryStatus
    status_reason: str | None
    status_changed_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    source: MemorySourceResponse
    supersedes_memory_id: int | None
    context_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_memory(
        cls,
        memory: MemoryItem,
    ) -> "MemoryResponse":
        return cls(
            id=memory.id,
            memory_type=memory.memory_type,
            title=memory.title,
            content=memory.content,
            memory_key=memory.memory_key,
            scope=MemoryScopeResponse(
                type=memory.scope_type,
                account_id=memory.account_id,
                subject_user_id=(
                    memory.subject_user_id
                ),
            ),
            created_by_user_id=(
                memory.created_by_user_id
            ),
            importance=memory.importance,
            confidence=memory.confidence,
            status=memory.status,
            status_reason=memory.status_reason,
            status_changed_at=(
                memory.status_changed_at
            ),
            valid_from=memory.valid_from,
            valid_until=memory.valid_until,
            source=MemorySourceResponse(
                type=memory.source_type,
                reference=memory.source_reference,
            ),
            supersedes_memory_id=(
                memory.supersedes_memory_id
            ),
            context_data=memory.context_data or {},
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )


class EvidenceResponse(MemorySchema):
    id: int
    memory_id: int
    relation: EvidenceRelation
    source_type: MemorySourceType
    source_reference: str
    source_memory_id: int | None
    evidence_text: str
    evidence_hash: str
    weight: Score
    observed_at: datetime | None
    created_by_user_id: int | None
    context_data: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_evidence(
        cls,
        evidence: MemoryEvidence,
    ) -> "EvidenceResponse":
        return cls(
            id=evidence.id,
            memory_id=evidence.memory_id,
            relation=evidence.relation,
            source_type=evidence.source_type,
            source_reference=(
                evidence.source_reference
            ),
            source_memory_id=(
                evidence.source_memory_id
            ),
            evidence_text=evidence.evidence_text,
            evidence_hash=evidence.evidence_hash,
            weight=evidence.weight,
            observed_at=evidence.observed_at,
            created_by_user_id=(
                evidence.created_by_user_id
            ),
            context_data=evidence.context_data or {},
            created_at=evidence.created_at,
        )


class MemoryMutationResponse(MemorySchema):
    memory: MemoryResponse
    created: bool
    duplicate: bool
    evidence: list[EvidenceResponse]


class MemorySupersedeResponse(MemorySchema):
    previous: MemoryResponse
    replacement: MemoryResponse
    evidence: list[EvidenceResponse]


class EvidenceMutationResponse(MemorySchema):
    evidence: EvidenceResponse
    created: bool
    duplicate: bool


class EvidenceListResponse(MemorySchema):
    items: list[EvidenceResponse]


class MemoryHistoryResponse(MemorySchema):
    items: list[MemoryResponse]


class MemoryPageResponse(MemorySchema):
    limit: int
    has_more: bool
    next_cursor: str | None


class MemoryRecallResponse(MemorySchema):
    items: list[MemoryResponse]
    page: MemoryPageResponse
