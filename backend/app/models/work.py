from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


WorkJSON = JSON().with_variant(
    JSONB(),
    "postgresql",
)


class WorkItem(Base):
    __tablename__ = "work_items"

    __table_args__ = (
        CheckConstraint(
            "work_type IN ('task', 'project', 'milestone')",
            name="ck_work_items_work_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(title)) >= 1",
            name="ck_work_items_title_not_blank",
        ),
        CheckConstraint(
            "description IS NULL "
            "OR char_length(btrim(description)) >= 1",
            name="ck_work_items_description_not_blank",
        ),
        CheckConstraint(
            "work_key IS NULL "
            "OR char_length(btrim(work_key)) >= 1",
            name="ck_work_items_work_key_not_blank",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'account', 'user')",
            name="ck_work_items_scope_type_valid",
        ),
        CheckConstraint(
            "("
            "scope_type = 'global' "
            "AND account_id IS NULL "
            "AND subject_user_id IS NULL"
            ") OR ("
            "scope_type = 'account' "
            "AND account_id IS NOT NULL "
            "AND subject_user_id IS NULL"
            ") OR ("
            "scope_type = 'user' "
            "AND account_id IS NULL "
            "AND subject_user_id IS NOT NULL"
            ")",
            name="ck_work_items_scope_integrity",
        ),
        CheckConstraint(
            "status IN ("
            "'backlog', "
            "'ready', "
            "'in_progress', "
            "'blocked', "
            "'completed', "
            "'cancelled'"
            ")",
            name="ck_work_items_status_valid",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_work_items_priority_valid",
        ),
        CheckConstraint(
            "("
            "status = 'blocked' "
            "AND blocked_reason IS NOT NULL "
            "AND char_length(btrim(blocked_reason)) >= 1"
            ") OR ("
            "status <> 'blocked' "
            "AND blocked_reason IS NULL"
            ")",
            name="ck_work_items_blocked_reason_integrity",
        ),
        CheckConstraint(
            "status <> 'cancelled' "
            "OR ("
            "status_reason IS NOT NULL "
            "AND char_length(btrim(status_reason)) >= 1"
            ")",
            name="ck_work_items_cancel_reason_required",
        ),
        CheckConstraint(
            "("
            "status = 'completed' "
            "AND completed_at IS NOT NULL "
            "AND cancelled_at IS NULL"
            ") OR ("
            "status = 'cancelled' "
            "AND completed_at IS NULL "
            "AND cancelled_at IS NOT NULL"
            ") OR ("
            "status NOT IN ('completed', 'cancelled') "
            "AND completed_at IS NULL "
            "AND cancelled_at IS NULL"
            ")",
            name="ck_work_items_terminal_timestamp_integrity",
        ),
        CheckConstraint(
            "status NOT IN ('in_progress', 'blocked', 'completed') "
            "OR started_at IS NOT NULL",
            name="ck_work_items_started_at_required",
        ),
        CheckConstraint(
            "completed_at IS NULL "
            "OR started_at IS NULL "
            "OR completed_at >= started_at",
            name="ck_work_items_completed_after_started",
        ),
        CheckConstraint(
            "cancelled_at IS NULL "
            "OR started_at IS NULL "
            "OR cancelled_at >= started_at",
            name="ck_work_items_cancelled_after_started",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_work_items_version_positive",
        ),
        CheckConstraint(
            "parent_work_item_id IS NULL "
            "OR parent_work_item_id <> id",
            name="ck_work_items_not_self_parent",
        ),
        CheckConstraint(
            "origin_type IN ("
            "'user', "
            "'agent', "
            "'system', "
            "'api', "
            "'integration'"
            ")",
            name="ck_work_items_origin_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(origin_reference)) >= 1",
            name="ck_work_items_origin_reference_not_blank",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    work_type = Column(
        String(24),
        nullable=False,
    )

    title = Column(
        String(240),
        nullable=False,
    )

    description = Column(
        Text,
        nullable=True,
    )

    work_key = Column(
        String(255),
        nullable=True,
    )

    scope_type = Column(
        String(20),
        nullable=False,
    )

    account_id = Column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="fk_work_items_account_id_accounts",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    subject_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_work_items_subject_user_id_users",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    parent_work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name=(
                "fk_work_items_parent_work_item_id_"
                "work_items"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_work_items_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assignee_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_work_items_assignee_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    status = Column(
        String(24),
        nullable=False,
        default="backlog",
        server_default="backlog",
    )

    priority = Column(
        String(16),
        nullable=False,
        default="normal",
        server_default="normal",
    )

    blocked_reason = Column(
        Text,
        nullable=True,
    )

    status_reason = Column(
        Text,
        nullable=True,
    )

    status_changed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    due_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    sla_due_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    cancelled_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    origin_type = Column(
        String(24),
        nullable=False,
    )

    origin_reference = Column(
        String(500),
        nullable=False,
    )

    context_data = Column(
        WorkJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "ix_work_items_status_priority_due_at",
    WorkItem.status,
    WorkItem.priority,
    WorkItem.due_at,
    WorkItem.id,
)

Index(
    "ix_work_items_account_status_due_at",
    WorkItem.account_id,
    WorkItem.status,
    WorkItem.due_at,
)

Index(
    "ix_work_items_user_status_due_at",
    WorkItem.subject_user_id,
    WorkItem.status,
    WorkItem.due_at,
)

Index(
    "ix_work_items_assignee_status_priority",
    WorkItem.assignee_user_id,
    WorkItem.status,
    WorkItem.priority,
    WorkItem.due_at,
)

Index(
    "ix_work_items_parent_status",
    WorkItem.parent_work_item_id,
    WorkItem.status,
)

Index(
    "ix_work_items_origin_type_reference",
    WorkItem.origin_type,
    WorkItem.origin_reference,
)

_open_due_where = text(
    "status NOT IN ('completed', 'cancelled') "
    "AND due_at IS NOT NULL"
)

Index(
    "ix_work_items_open_due_at",
    WorkItem.due_at,
    WorkItem.priority,
    postgresql_where=_open_due_where,
    sqlite_where=_open_due_where,
)

_global_key_where = text(
    "scope_type = 'global' "
    "AND work_key IS NOT NULL"
)

Index(
    "uq_work_items_global_key",
    WorkItem.work_key,
    unique=True,
    postgresql_where=_global_key_where,
    sqlite_where=_global_key_where,
)

_account_key_where = text(
    "scope_type = 'account' "
    "AND work_key IS NOT NULL"
)

Index(
    "uq_work_items_account_key",
    WorkItem.account_id,
    WorkItem.work_key,
    unique=True,
    postgresql_where=_account_key_where,
    sqlite_where=_account_key_where,
)

_user_key_where = text(
    "scope_type = 'user' "
    "AND work_key IS NOT NULL"
)

Index(
    "uq_work_items_user_key",
    WorkItem.subject_user_id,
    WorkItem.work_key,
    unique=True,
    postgresql_where=_user_key_where,
    sqlite_where=_user_key_where,
)


class WorkDependency(Base):
    __tablename__ = "work_dependencies"

    __table_args__ = (
        CheckConstraint(
            "work_item_id <> depends_on_work_item_id",
            name="ck_work_dependencies_not_self",
        ),
        CheckConstraint(
            "dependency_type IN ("
            "'finish_to_start', "
            "'start_to_start', "
            "'finish_to_finish', "
            "'start_to_finish'"
            ")",
            name="ck_work_dependencies_type_valid",
        ),
        UniqueConstraint(
            "work_item_id",
            "depends_on_work_item_id",
            name="uq_work_dependencies_pair",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name=(
                "fk_work_dependencies_work_item_id_"
                "work_items"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    depends_on_work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name=(
                "fk_work_dependencies_depends_on_work_item_id_"
                "work_items"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    dependency_type = Column(
        String(32),
        nullable=False,
        default="finish_to_start",
        server_default="finish_to_start",
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_work_dependencies_created_by_user_id_"
                "users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_work_dependencies_depends_on_type",
    WorkDependency.depends_on_work_item_id,
    WorkDependency.dependency_type,
)


class WorkEvent(Base):
    __tablename__ = "work_events"

    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'created', "
            "'details_changed', "
            "'status_changed', "
            "'priority_changed', "
            "'assignee_changed', "
            "'schedule_changed', "
            "'dependency_added', "
            "'dependency_removed', "
            "'memory_linked', "
            "'memory_unlinked', "
            "'comment_added', "
            "'system_note', "
            "'recurrence_configured', "
            "'recurrence_disabled', "
            "'recurrence_generated'"
            ")",
            name="ck_work_events_event_type_valid",
        ),
        CheckConstraint(
            "actor_type IN ("
            "'user', "
            "'agent', "
            "'system', "
            "'integration'"
            ")",
            name="ck_work_events_actor_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_work_events_actor_reference_not_blank",
        ),
        CheckConstraint(
            "idempotency_key IS NULL "
            "OR char_length(btrim(idempotency_key)) >= 1",
            name="ck_work_events_idempotency_key_not_blank",
        ),
        UniqueConstraint(
            "work_item_id",
            "idempotency_key",
            name="uq_work_events_item_idempotency_key",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name="fk_work_events_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    event_type = Column(
        String(32),
        nullable=False,
    )

    actor_type = Column(
        String(24),
        nullable=False,
    )

    actor_reference = Column(
        String(255),
        nullable=False,
    )

    actor_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_work_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    idempotency_key = Column(
        String(255),
        nullable=True,
    )

    event_data = Column(
        WorkJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_work_events_item_created_at",
    WorkEvent.work_item_id,
    WorkEvent.created_at,
    WorkEvent.id,
)

Index(
    "ix_work_events_type_created_at",
    WorkEvent.event_type,
    WorkEvent.created_at,
)

Index(
    "ix_work_events_actor_reference",
    WorkEvent.actor_type,
    WorkEvent.actor_reference,
)


class WorkMemoryLink(Base):
    __tablename__ = "work_memory_links"

    __table_args__ = (
        CheckConstraint(
            "relation IN ('context', 'source', 'decision', 'outcome')",
            name="ck_work_memory_links_relation_valid",
        ),
        UniqueConstraint(
            "work_item_id",
            "memory_id",
            "relation",
            name="uq_work_memory_links_item_memory_relation",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name=(
                "fk_work_memory_links_work_item_id_"
                "work_items"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    memory_id = Column(
        BigInteger,
        ForeignKey(
            "memory_items.id",
            name=(
                "fk_work_memory_links_memory_id_"
                "memory_items"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    relation = Column(
        String(20),
        nullable=False,
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_work_memory_links_created_by_user_id_"
                "users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_work_memory_links_memory_relation",
    WorkMemoryLink.memory_id,
    WorkMemoryLink.relation,
)


class WorkRecurrenceRule(Base):
    __tablename__ = "work_recurrence_rules"

    __table_args__ = (
        CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_work_recurrence_rules_frequency_valid",
        ),
        CheckConstraint(
            "interval_value >= 1 AND interval_value <= 365",
            name="ck_work_recurrence_rules_interval_range",
        ),
        CheckConstraint(
            "char_length(btrim(timezone_name)) >= 1",
            name="ck_work_recurrence_rules_timezone_not_blank",
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_work_recurrence_rules_time_range",
        ),
        CheckConstraint(
            "max_occurrences IS NULL OR max_occurrences >= 1",
            name=(
                "ck_work_recurrence_rules_"
                "max_occurrences_positive"
            ),
        ),
        CheckConstraint(
            "generated_occurrences >= 0",
            name=(
                "ck_work_recurrence_rules_"
                "generated_nonnegative"
            ),
        ),
        CheckConstraint(
            "max_occurrences IS NULL "
            "OR generated_occurrences <= max_occurrences",
            name=(
                "ck_work_recurrence_rules_"
                "generated_within_max"
            ),
        ),
        CheckConstraint(
            "sla_lead_minutes IS NULL "
            "OR (sla_lead_minutes >= 0 "
            "AND sla_lead_minutes <= 525600)",
            name="ck_work_recurrence_rules_sla_lead_range",
        ),
        CheckConstraint(
            "next_occurrence_at IS NULL "
            "OR next_occurrence_at >= starts_at",
            name="ck_work_recurrence_rules_next_after_start",
        ),
        CheckConstraint(
            "ends_at IS NULL "
            "OR next_occurrence_at IS NULL "
            "OR next_occurrence_at <= ends_at",
            name="ck_work_recurrence_rules_next_before_end",
        ),
        CheckConstraint(
            "last_occurrence_at IS NULL "
            "OR next_occurrence_at IS NULL "
            "OR next_occurrence_at > last_occurrence_at",
            name="ck_work_recurrence_rules_next_after_last",
        ),
        CheckConstraint(
            "(active AND next_occurrence_at IS NOT NULL) "
            "OR (NOT active AND next_occurrence_at IS NULL)",
            name="ck_work_recurrence_rules_active_next_integrity",
        ),
        UniqueConstraint(
            "work_item_id",
            name="uq_work_recurrence_rules_work_item_id",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name=(
                "fk_work_recurrence_rules_"
                "work_item_id_work_items"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    frequency = Column(
        String(20),
        nullable=False,
    )

    interval_value = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    timezone_name = Column(
        String(64),
        nullable=False,
    )

    starts_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    ends_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    max_occurrences = Column(
        Integer,
        nullable=True,
    )

    generated_occurrences = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    next_occurrence_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_occurrence_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    sla_lead_minutes = Column(
        Integer,
        nullable=True,
    )

    active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_work_recurrence_rules_"
                "created_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "ix_work_recurrence_rules_due",
    WorkRecurrenceRule.active,
    WorkRecurrenceRule.next_occurrence_at,
    WorkRecurrenceRule.id,
)


class WorkRecurrenceOccurrence(Base):
    __tablename__ = "work_recurrence_occurrences"

    __table_args__ = (
        CheckConstraint(
            "occurrence_number >= 1",
            name=(
                "ck_work_recurrence_occurrences_"
                "number_positive"
            ),
        ),
        UniqueConstraint(
            "recurrence_rule_id",
            "occurrence_number",
            name=(
                "uq_work_recurrence_occurrences_"
                "rule_number"
            ),
        ),
        UniqueConstraint(
            "recurrence_rule_id",
            "scheduled_for",
            name=(
                "uq_work_recurrence_occurrences_"
                "rule_scheduled_for"
            ),
        ),
        UniqueConstraint(
            "work_item_id",
            name=(
                "uq_work_recurrence_occurrences_"
                "work_item_id"
            ),
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    recurrence_rule_id = Column(
        BigInteger,
        ForeignKey(
            "work_recurrence_rules.id",
            name=(
                "fk_work_recurrence_occurrences_"
                "rule_id_rules"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    work_item_id = Column(
        BigInteger,
        ForeignKey(
            "work_items.id",
            name=(
                "fk_work_recurrence_occurrences_"
                "work_item_id_work_items"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    occurrence_number = Column(
        Integer,
        nullable=False,
    )

    scheduled_for = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_work_recurrence_occurrences_scheduled",
    WorkRecurrenceOccurrence.scheduled_for,
    WorkRecurrenceOccurrence.id,
)
