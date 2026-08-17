from datetime import datetime
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from app.models.work import WorkDependency
from app.models.work import WorkEvent
from app.models.work import WorkItem
from app.models.work import WorkMemoryLink
from app.models.work import WorkRecurrenceOccurrence
from app.models.work import WorkRecurrenceRule
from app.services.work_service import WorkSLAStatus


WorkType = Literal[
    "task",
    "project",
    "milestone",
]

WorkScopeType = Literal[
    "global",
    "account",
    "user",
]

WorkStatus = Literal[
    "backlog",
    "ready",
    "in_progress",
    "blocked",
    "completed",
    "cancelled",
]

WorkPriority = Literal[
    "low",
    "normal",
    "high",
    "urgent",
]

DependencyType = Literal[
    "finish_to_start",
    "start_to_start",
    "finish_to_finish",
    "start_to_finish",
]

MemoryRelation = Literal[
    "context",
    "source",
    "decision",
    "outcome",
]

RecurrenceFrequency = Literal[
    "daily",
    "weekly",
    "monthly",
]

SLAStatus = Literal[
    "not_configured",
    "on_track",
    "breached",
    "met",
    "missed",
    "cancelled",
]


class WorkSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class WorkScopeRequest(WorkSchema):
    type: WorkScopeType
    account_id: int | None = Field(
        default=None,
        gt=0,
    )
    subject_user_id: int | None = Field(
        default=None,
        gt=0,
    )

    @model_validator(mode="after")
    def validate_scope(self) -> "WorkScopeRequest":
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
                "Combinação de escopo inválida."
            )

        return self


class WorkCreateRequest(WorkSchema):
    work_type: WorkType
    title: str = Field(
        min_length=1,
        max_length=240,
    )
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=10000,
    )
    work_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,254}$",
    )
    scope: WorkScopeRequest
    parent_work_item_id: int | None = Field(
        default=None,
        gt=0,
    )
    assignee_user_id: int | None = Field(
        default=None,
        gt=0,
    )
    priority: WorkPriority = "normal"
    due_at: datetime | None = None
    sla_due_at: datetime | None = None
    context_data: dict[str, Any] = Field(
        default_factory=dict,
    )


class VersionedWorkRequest(WorkSchema):
    expected_version: int = Field(gt=0)


class WorkDetailsRequest(VersionedWorkRequest):
    title: str = Field(
        min_length=1,
        max_length=240,
    )
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=10000,
    )
    context_data: dict[str, Any] = Field(
        default_factory=dict,
    )


class WorkPriorityRequest(VersionedWorkRequest):
    priority: WorkPriority


class WorkAssigneeRequest(VersionedWorkRequest):
    assignee_user_id: int | None = Field(
        default=None,
        gt=0,
    )


class WorkCommentRequest(VersionedWorkRequest):
    comment: str = Field(
        min_length=1,
        max_length=7000,
    )


class WorkStatusRequest(VersionedWorkRequest):
    status: WorkStatus
    reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )


class WorkScheduleRequest(VersionedWorkRequest):
    due_at: datetime | None = None
    sla_due_at: datetime | None = None


class WorkDependencyRequest(VersionedWorkRequest):
    depends_on_work_item_id: int = Field(gt=0)
    dependency_type: DependencyType = "finish_to_start"


class WorkDependencyRemovalRequest(VersionedWorkRequest):
    pass


class WorkMemoryLinkRequest(VersionedWorkRequest):
    memory_id: int = Field(gt=0)
    relation: MemoryRelation


class WorkMemoryUnlinkRequest(VersionedWorkRequest):
    pass


class WorkRecurrenceRequest(VersionedWorkRequest):
    frequency: RecurrenceFrequency
    interval_value: int = Field(
        default=1,
        ge=1,
        le=365,
    )
    timezone_name: str = Field(
        min_length=1,
        max_length=64,
    )
    starts_at: datetime
    ends_at: datetime | None = None
    max_occurrences: int | None = Field(
        default=None,
        ge=1,
        le=1_000_000,
    )
    sla_lead_minutes: int | None = Field(
        default=None,
        ge=0,
        le=525600,
    )


class WorkRecurrenceDisableRequest(VersionedWorkRequest):
    reason: str = Field(
        min_length=1,
        max_length=2000,
    )


class WorkRecurrenceGenerateRequest(VersionedWorkRequest):
    as_of: datetime | None = None


class WorkScopeResponse(WorkSchema):
    type: WorkScopeType
    account_id: int | None
    subject_user_id: int | None


class WorkOriginResponse(WorkSchema):
    type: Literal[
        "user",
        "agent",
        "system",
        "api",
        "integration",
    ]
    reference: str


class WorkResponse(WorkSchema):
    id: int
    work_type: WorkType
    title: str
    description: str | None
    work_key: str | None
    scope: WorkScopeResponse
    parent_work_item_id: int | None
    created_by_user_id: int | None
    assignee_user_id: int | None
    status: WorkStatus
    priority: WorkPriority
    blocked_reason: str | None
    status_reason: str | None
    status_changed_at: datetime
    due_at: datetime | None
    sla_due_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    version: int
    origin: WorkOriginResponse
    context_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_work_item(
        cls,
        item: WorkItem,
    ) -> "WorkResponse":
        return cls(
            id=item.id,
            work_type=item.work_type,
            title=item.title,
            description=item.description,
            work_key=item.work_key,
            scope=WorkScopeResponse(
                type=item.scope_type,
                account_id=item.account_id,
                subject_user_id=(
                    item.subject_user_id
                ),
            ),
            parent_work_item_id=(
                item.parent_work_item_id
            ),
            created_by_user_id=(
                item.created_by_user_id
            ),
            assignee_user_id=item.assignee_user_id,
            status=item.status,
            priority=item.priority,
            blocked_reason=item.blocked_reason,
            status_reason=item.status_reason,
            status_changed_at=item.status_changed_at,
            due_at=item.due_at,
            sla_due_at=item.sla_due_at,
            started_at=item.started_at,
            completed_at=item.completed_at,
            cancelled_at=item.cancelled_at,
            version=item.version,
            origin=WorkOriginResponse(
                type=item.origin_type,
                reference=item.origin_reference,
            ),
            context_data=item.context_data or {},
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class WorkEventResponse(WorkSchema):
    id: int
    work_item_id: int
    event_type: str
    actor_type: Literal[
        "user",
        "agent",
        "system",
        "integration",
    ]
    actor_reference: str
    actor_user_id: int | None
    idempotency_key: str | None
    event_data: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_event(
        cls,
        event: WorkEvent,
    ) -> "WorkEventResponse":
        return cls(
            id=event.id,
            work_item_id=event.work_item_id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_reference=event.actor_reference,
            actor_user_id=event.actor_user_id,
            idempotency_key=event.idempotency_key,
            event_data=event.event_data or {},
            created_at=event.created_at,
        )


class WorkCreationResponse(WorkSchema):
    work_item: WorkResponse
    event: WorkEventResponse
    created: bool
    duplicate: bool


class WorkMutationResponse(WorkSchema):
    work_item: WorkResponse
    event: WorkEventResponse
    applied: bool
    duplicate: bool


class WorkListResponse(WorkSchema):
    items: list[WorkResponse]


class WorkEventListResponse(WorkSchema):
    items: list[WorkEventResponse]
    next_cursor: int | None = None


class WorkDependencyResponse(WorkSchema):
    id: int
    work_item_id: int
    depends_on_work_item_id: int
    dependency_type: DependencyType
    created_by_user_id: int | None
    created_at: datetime

    @classmethod
    def from_dependency(
        cls,
        dependency: WorkDependency,
    ) -> "WorkDependencyResponse":
        return cls(
            id=dependency.id,
            work_item_id=dependency.work_item_id,
            depends_on_work_item_id=(
                dependency.depends_on_work_item_id
            ),
            dependency_type=dependency.dependency_type,
            created_by_user_id=(
                dependency.created_by_user_id
            ),
            created_at=dependency.created_at,
        )


class WorkDependencyListResponse(WorkSchema):
    items: list[WorkDependencyResponse]
    next_cursor: int | None = None


class WorkMemoryLinkResponse(WorkSchema):
    id: int
    work_item_id: int
    memory_id: int
    relation: MemoryRelation
    created_by_user_id: int | None
    created_at: datetime

    @classmethod
    def from_link(
        cls,
        link: WorkMemoryLink,
    ) -> "WorkMemoryLinkResponse":
        return cls(
            id=link.id,
            work_item_id=link.work_item_id,
            memory_id=link.memory_id,
            relation=link.relation,
            created_by_user_id=(
                link.created_by_user_id
            ),
            created_at=link.created_at,
        )


class WorkMemoryLinkListResponse(WorkSchema):
    items: list[WorkMemoryLinkResponse]
    next_cursor: int | None = None


class WorkSLAResponse(WorkSchema):
    work_item_id: int
    status: SLAStatus
    sla_due_at: datetime | None
    evaluated_at: datetime
    remaining_seconds: float | None

    @classmethod
    def from_status(
        cls,
        item: WorkSLAStatus,
    ) -> "WorkSLAResponse":
        return cls(
            work_item_id=item.work_item_id,
            status=item.status,
            sla_due_at=item.sla_due_at,
            evaluated_at=item.evaluated_at,
            remaining_seconds=item.remaining_seconds,
        )


class WorkSLAListResponse(WorkSchema):
    items: list[WorkResponse]


class WorkRecurrenceResponse(WorkSchema):
    id: int
    work_item_id: int
    frequency: RecurrenceFrequency
    interval_value: int
    timezone_name: str
    starts_at: datetime
    ends_at: datetime | None
    max_occurrences: int | None
    generated_occurrences: int
    next_occurrence_at: datetime | None
    last_occurrence_at: datetime | None
    sla_lead_minutes: int | None
    active: bool
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_rule(
        cls,
        rule: WorkRecurrenceRule,
    ) -> "WorkRecurrenceResponse":
        return cls(
            id=rule.id,
            work_item_id=rule.work_item_id,
            frequency=rule.frequency,
            interval_value=rule.interval_value,
            timezone_name=rule.timezone_name,
            starts_at=rule.starts_at,
            ends_at=rule.ends_at,
            max_occurrences=rule.max_occurrences,
            generated_occurrences=(
                rule.generated_occurrences
            ),
            next_occurrence_at=(
                rule.next_occurrence_at
            ),
            last_occurrence_at=(
                rule.last_occurrence_at
            ),
            sla_lead_minutes=rule.sla_lead_minutes,
            active=rule.active,
            created_by_user_id=(
                rule.created_by_user_id
            ),
            created_at=rule.created_at,
            updated_at=rule.updated_at,
        )


class WorkRecurrenceMutationResponse(WorkSchema):
    recurrence: WorkRecurrenceResponse
    mutation: WorkMutationResponse


class WorkRecurrenceOccurrenceResponse(WorkSchema):
    id: int
    recurrence_rule_id: int
    work_item_id: int
    occurrence_number: int
    scheduled_for: datetime
    created_at: datetime

    @classmethod
    def from_occurrence(
        cls,
        occurrence: WorkRecurrenceOccurrence,
    ) -> "WorkRecurrenceOccurrenceResponse":
        return cls(
            id=occurrence.id,
            recurrence_rule_id=(
                occurrence.recurrence_rule_id
            ),
            work_item_id=occurrence.work_item_id,
            occurrence_number=(
                occurrence.occurrence_number
            ),
            scheduled_for=occurrence.scheduled_for,
            created_at=occurrence.created_at,
        )


class WorkRecurrenceOccurrenceListResponse(WorkSchema):
    items: list[WorkRecurrenceOccurrenceResponse]
    next_cursor: int | None = None


class WorkRecurrenceGenerationResponse(WorkSchema):
    template: WorkResponse
    occurrence_work_item: WorkResponse
    occurrence: WorkRecurrenceOccurrenceResponse
    recurrence: WorkRecurrenceResponse
    event: WorkEventResponse
    applied: bool
    duplicate: bool
