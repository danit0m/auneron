import calendar
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkError
from app.core.work_errors import WorkIdempotencyConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.core.work_errors import WorkVersionConflictError
from app.models.work import WorkDependency
from app.models.work import WorkEvent
from app.models.work import WorkItem
from app.models.work import WorkMemoryLink
from app.models.work import WorkRecurrenceOccurrence
from app.models.work import WorkRecurrenceRule
from app.repositories.work_repository import WorkRepository


WORK_TYPES = frozenset({
    "task",
    "project",
    "milestone",
})

SCOPE_TYPES = frozenset({
    "global",
    "account",
    "user",
})

PRIORITIES = frozenset({
    "low",
    "normal",
    "high",
    "urgent",
})

ORIGIN_TYPES = frozenset({
    "user",
    "agent",
    "system",
    "api",
    "integration",
})

ACTOR_TYPES = frozenset({
    "user",
    "agent",
    "system",
    "integration",
})

WORK_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,254}$"
)

IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,254}$"
)

WORK_KEY_CONSTRAINTS = frozenset({
    "uq_work_items_global_key",
    "uq_work_items_account_key",
    "uq_work_items_user_key",
})

EVENT_IDEMPOTENCY_CONSTRAINT = (
    "uq_work_events_item_idempotency_key"
)

MAX_CONTEXT_BYTES = 32 * 1024
MAX_CONTEXT_DEPTH = 5
MAX_EVENT_TEXT_LENGTH = 7000
MAX_SLA_BATCH = 100
MAX_RECURRENCE_BATCH = 100
MAX_RECURRENCE_OCCURRENCES = 1_000_000
MAX_RECURRING_WORK_KEY_LENGTH = 230
MAX_WORK_LIST = 100
MAX_WORK_PAGE = 100

MEMORY_RELATIONS = frozenset({
    "context",
    "source",
    "decision",
    "outcome",
})

DEPENDENCY_TYPES = frozenset({
    "finish_to_start",
    "start_to_start",
    "finish_to_finish",
    "start_to_finish",
})

RECURRENCE_FREQUENCIES = frozenset({
    "daily",
    "weekly",
    "monthly",
})

TERMINAL_STATUSES = frozenset({
    "completed",
    "cancelled",
})

STATUS_TRANSITIONS = {
    "backlog": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"backlog", "in_progress", "cancelled"}),
    "in_progress": frozenset({"blocked", "completed", "cancelled"}),
    "blocked": frozenset({"in_progress", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


@dataclass(frozen=True)
class WorkActor:
    actor_type: str
    actor_reference: str
    actor_user_id: int | None = None


@dataclass(frozen=True)
class WorkCreationResult:
    work_item: WorkItem
    event: WorkEvent
    created: bool
    duplicate: bool


@dataclass(frozen=True)
class WorkMutationResult:
    work_item: WorkItem
    event: WorkEvent
    applied: bool
    duplicate: bool


@dataclass(frozen=True)
class WorkSLAStatus:
    work_item_id: int
    status: str
    sla_due_at: datetime | None
    evaluated_at: datetime
    remaining_seconds: float | None


@dataclass(frozen=True)
class RecurrenceConfigurationResult:
    rule: WorkRecurrenceRule
    mutation: WorkMutationResult


@dataclass(frozen=True)
class RecurrenceGenerationResult:
    template: WorkItem
    occurrence_work_item: WorkItem
    occurrence: WorkRecurrenceOccurrence
    rule: WorkRecurrenceRule
    event: WorkEvent
    applied: bool
    duplicate: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(
    value: str,
    *,
    field_name: str,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise WorkValidationError(
            f"{field_name} deve ser texto."
        )

    normalized = value.strip()

    if not normalized:
        raise WorkValidationError(
            f"{field_name} não pode ser vazio."
        )

    if (
        max_length is not None
        and len(normalized) > max_length
    ):
        raise WorkValidationError(
            f"{field_name} excede {max_length} caracteres."
        )

    return normalized


def _optional_text(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None

    return _required_text(
        value,
        field_name=field_name,
    )


def _positive_id(
    value: int,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise WorkValidationError(
            f"{field_name} inválido."
        )

    return value


def _optional_positive_id(
    value: int | None,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None

    return _positive_id(
        value,
        field_name=field_name,
    )


def _aware_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise WorkValidationError(
            f"{field_name} deve possuir timezone."
        )

    return value.astimezone(timezone.utc)


def _normalized_choice(
    value: str,
    *,
    field_name: str,
    allowed: frozenset[str],
) -> str:
    normalized = _required_text(
        value,
        field_name=field_name,
    ).lower()

    if normalized not in allowed:
        raise WorkValidationError(
            f"{field_name} inválido."
        )

    return normalized


def _normalized_work_key(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().lower()

    if not WORK_KEY_PATTERN.fullmatch(normalized):
        raise WorkValidationError("work_key inválida.")

    return normalized


def _normalized_idempotency_key(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise WorkValidationError(
            "idempotency_key deve ser texto."
        )

    normalized = value.strip().lower()

    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(normalized):
        raise WorkValidationError(
            "idempotency_key inválida."
        )

    return normalized


def _json_depth(
    value: Any,
    *,
    current_depth: int = 1,
    ancestors: frozenset[int] = frozenset(),
) -> int:
    if current_depth > MAX_CONTEXT_DEPTH:
        return current_depth

    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON keys must be strings")

        identity = id(value)

        if identity in ancestors:
            raise ValueError("cyclic JSON value")

        if not value:
            return current_depth

        child_ancestors = ancestors | {identity}
        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
                ancestors=child_ancestors,
            )
            for child in value.values()
        )

    if isinstance(value, list):
        identity = id(value)

        if identity in ancestors:
            raise ValueError("cyclic JSON value")

        if not value:
            return current_depth

        child_ancestors = ancestors | {identity}
        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
                ancestors=child_ancestors,
            )
            for child in value
        )

    return current_depth


def _normalized_json_object(
    value: dict[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {} if value is None else value

    if not isinstance(normalized, dict):
        raise WorkValidationError(
            f"{field_name} deve ser um objeto JSON."
        )

    try:
        depth = _json_depth(normalized)

        if depth > MAX_CONTEXT_DEPTH:
            raise WorkValidationError(
                f"{field_name} excede profundidade JSON 5."
            )

        serialized = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except WorkValidationError:
        raise
    except (TypeError, ValueError) as error:
        raise WorkValidationError(
            f"{field_name} não é JSON válido."
        ) from error

    if len(serialized.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise WorkValidationError(
            f"{field_name} excede 32 KB."
        )

    return json.loads(serialized)


def _normalized_actor(actor: WorkActor) -> WorkActor:
    if not isinstance(actor, WorkActor):
        raise WorkValidationError("actor inválido.")

    actor_type = _normalized_choice(
        actor.actor_type,
        field_name="actor_type",
        allowed=ACTOR_TYPES,
    )
    actor_reference = _required_text(
        actor.actor_reference,
        field_name="actor_reference",
        max_length=255,
    )
    actor_user_id = _optional_positive_id(
        actor.actor_user_id,
        field_name="actor_user_id",
    )

    if actor_type == "user" and actor_user_id is None:
        raise WorkValidationError(
            "actor_user_id é obrigatório para ator user."
        )

    if actor_type != "user" and actor_user_id is not None:
        raise WorkValidationError(
            "actor_user_id somente é permitido para ator user."
        )

    return WorkActor(
        actor_type=actor_type,
        actor_reference=actor_reference,
        actor_user_id=actor_user_id,
    )


def _fingerprint(value: dict[str, Any]) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=lambda item: item.isoformat()
        if isinstance(item, datetime)
        else str(item),
    )
    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def _value_hash(value: Any) -> str:
    return _fingerprint({"value": value})


def _constraint_name(error: IntegrityError) -> str | None:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _bounded_integer(
    value: int,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise WorkValidationError(
            f"{field_name} deve estar entre {minimum} e {maximum}."
        )

    return value


def _optional_bounded_integer(
    value: int | None,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None

    return _bounded_integer(
        value,
        field_name=field_name,
        minimum=minimum,
        maximum=maximum,
    )


def _normalized_schedule(
    *,
    due_at: datetime | None,
    sla_due_at: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    normalized_due_at = _aware_datetime(
        due_at,
        field_name="due_at",
    )
    normalized_sla_due_at = _aware_datetime(
        sla_due_at,
        field_name="sla_due_at",
    )

    if (
        normalized_due_at is not None
        and normalized_sla_due_at is not None
        and normalized_sla_due_at > normalized_due_at
    ):
        raise WorkValidationError(
            "sla_due_at não pode ser posterior a due_at."
        )

    return normalized_due_at, normalized_sla_due_at


def _normalized_timezone(value: str) -> ZoneInfo:
    timezone_name = _required_text(
        value,
        field_name="timezone_name",
        max_length=64,
    )

    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise WorkValidationError(
            "timezone_name não corresponde a uma zona IANA."
        ) from error


def _next_recurrence_at(
    current: datetime,
    *,
    frequency: str,
    interval_value: int,
    recurrence_timezone: ZoneInfo,
) -> datetime:
    local = current.astimezone(recurrence_timezone)

    if frequency == "daily":
        next_local = local + timedelta(days=interval_value)
    elif frequency == "weekly":
        next_local = local + timedelta(weeks=interval_value)
    else:
        month_index = (
            local.year * 12
            + local.month
            - 1
            + interval_value
        )
        next_year, zero_based_month = divmod(month_index, 12)
        next_month = zero_based_month + 1
        next_day = min(
            local.day,
            calendar.monthrange(next_year, next_month)[1],
        )
        next_local = local.replace(
            year=next_year,
            month=next_month,
            day=next_day,
        )

    return next_local.astimezone(timezone.utc)


def _mutation_fingerprint(
    *,
    work_item_id: int,
    expected_version: int,
    event_type: str,
    actor: WorkActor,
    request_data: dict[str, Any],
) -> str:
    return _fingerprint({
        "work_item_id": work_item_id,
        "expected_version": expected_version,
        "event_type": event_type,
        "actor": {
            "actor_type": actor.actor_type,
            "actor_reference": actor.actor_reference,
            "actor_user_id": actor.actor_user_id,
        },
        "request": request_data,
    })


class WorkManagerService:
    """
    Fronteira transacional do Work Manager.

    Toda mutação bloqueia o agregado, verifica expected_version,
    incrementa version exatamente uma vez e anexa o evento na
    mesma transação. Nenhuma chamada externa ocorre aqui.
    """

    def __init__(
        self,
        db: Session,
        repository: WorkRepository | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else WorkRepository(db)
        )

    def create(
        self,
        *,
        work_type: str,
        title: str,
        scope_type: str,
        origin_type: str,
        origin_reference: str,
        actor: WorkActor,
        description: str | None = None,
        work_key: str | None = None,
        account_id: int | None = None,
        subject_user_id: int | None = None,
        parent_work_item_id: int | None = None,
        assignee_user_id: int | None = None,
        priority: str = "normal",
        due_at: datetime | None = None,
        sla_due_at: datetime | None = None,
        context_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> WorkCreationResult:
        normalized_actor = _normalized_actor(actor)
        normalized = self._normalize_create(
            work_type=work_type,
            title=title,
            scope_type=scope_type,
            origin_type=origin_type,
            origin_reference=origin_reference,
            description=description,
            work_key=work_key,
            account_id=account_id,
            subject_user_id=subject_user_id,
            parent_work_item_id=parent_work_item_id,
            assignee_user_id=assignee_user_id,
            priority=priority,
            due_at=due_at,
            sla_due_at=sla_due_at,
            context_data=context_data,
        )
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )

        if (
            normalized_idempotency_key is not None
            and normalized["work_key"] is None
        ):
            raise WorkValidationError(
                "create idempotente exige work_key."
            )

        request_fingerprint = self._creation_fingerprint(
            normalized,
            normalized_actor,
        )

        try:
            self._validate_parent(normalized)
            existing = self._find_existing(normalized)

            if existing is not None:
                result = self._existing_creation_result(
                    existing,
                    request_fingerprint=request_fingerprint,
                )
                self.db.commit()
                return result

            now = _utc_now()
            item = WorkItem(
                **normalized,
                created_by_user_id=(
                    normalized_actor.actor_user_id
                ),
                status="backlog",
                blocked_reason=None,
                status_reason=None,
                status_changed_at=now,
                started_at=None,
                completed_at=None,
                cancelled_at=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self.repository.add_work_item(item)
            event = WorkEvent(
                work_item_id=item.id,
                event_type="created",
                actor_type=normalized_actor.actor_type,
                actor_reference=(
                    normalized_actor.actor_reference
                ),
                actor_user_id=(
                    normalized_actor.actor_user_id
                ),
                idempotency_key=normalized_idempotency_key,
                event_data={
                    "request_fingerprint": request_fingerprint,
                    "result_version": 1,
                    "scope_type": item.scope_type,
                    "work_type": item.work_type,
                    "work_key": item.work_key,
                },
                created_at=now,
            )
            self.repository.add_event(event)
            self.db.commit()
            self.db.refresh(item)
            self.db.refresh(event)
        except IntegrityError as error:
            self.db.rollback()

            if (
                normalized["work_key"] is not None
                and _constraint_name(error)
                in WORK_KEY_CONSTRAINTS
            ):
                concurrent = self._find_existing(normalized)

                if concurrent is not None:
                    try:
                        result = self._existing_creation_result(
                            concurrent,
                            request_fingerprint=(
                                request_fingerprint
                            ),
                        )
                        self.db.commit()
                        return result
                    except Exception:
                        self.db.rollback()
                        raise

                raise WorkConflictError(
                    "Conflito concorrente de work_key."
                ) from error

            raise WorkValidationError(
                "O item de trabalho viola uma restrição "
                "de integridade."
            ) from error
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return WorkCreationResult(
            work_item=item,
            event=event,
            created=True,
            duplicate=False,
        )

    def get(self, work_item_id: int) -> WorkItem:
        _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        item = self.repository.get_by_id(work_item_id)

        if item is None:
            raise WorkNotFoundError(
                "Item de trabalho não encontrado."
            )

        return item

    def list_items(
        self,
        *,
        scope_type: str,
        account_id: int | None = None,
        subject_user_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
        priorities: tuple[str, ...] | None = None,
        assignee_user_id: int | None = None,
        limit: int = 50,
    ) -> tuple[WorkItem, ...]:
        normalized_scope = _normalized_choice(
            scope_type,
            field_name="scope_type",
            allowed=SCOPE_TYPES,
        )
        normalized_account_id = _optional_positive_id(
            account_id,
            field_name="account_id",
        )
        normalized_subject_user_id = _optional_positive_id(
            subject_user_id,
            field_name="subject_user_id",
        )

        if normalized_scope == "global":
            valid_scope = (
                normalized_account_id is None
                and normalized_subject_user_id is None
            )
        elif normalized_scope == "account":
            valid_scope = (
                normalized_account_id is not None
                and normalized_subject_user_id is None
            )
        else:
            valid_scope = (
                normalized_account_id is None
                and normalized_subject_user_id is not None
            )

        if not valid_scope:
            raise WorkValidationError(
                "Combinação de escopo inválida."
            )

        normalized_statuses = (
            tuple(
                _normalized_choice(
                    status,
                    field_name="status",
                    allowed=frozenset(STATUS_TRANSITIONS),
                )
                for status in statuses
            )
            if statuses is not None
            else None
        )
        normalized_priorities = (
            tuple(
                _normalized_choice(
                    priority,
                    field_name="priority",
                    allowed=PRIORITIES,
                )
                for priority in priorities
            )
            if priorities is not None
            else None
        )
        normalized_assignee = _optional_positive_id(
            assignee_user_id,
            field_name="assignee_user_id",
        )
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_WORK_LIST,
        )

        return tuple(
            self.repository.list_by_scope(
                scope_type=normalized_scope,
                account_id=normalized_account_id,
                subject_user_id=normalized_subject_user_id,
                statuses=normalized_statuses,
                priorities=normalized_priorities,
                assignee_user_id=normalized_assignee,
                limit=normalized_limit,
            )
        )

    def list_events(
        self,
        work_item_id: int,
        *,
        limit: int = 50,
        after_id: int | None = None,
    ) -> tuple[WorkEvent, ...]:
        self.get(work_item_id)
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_WORK_PAGE,
        )
        normalized_after_id = _optional_positive_id(
            after_id,
            field_name="after_id",
        )
        return tuple(
            self.repository.list_events(
                work_item_id,
                after_id=normalized_after_id,
                limit=normalized_limit + 1,
            )
        )

    def update_details(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        title: str,
        description: str | None,
        context_data: dict[str, Any] | None,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_title = _required_text(
            title,
            field_name="title",
            max_length=240,
        )
        normalized_description = _optional_text(
            description,
            field_name="description",
        )
        normalized_context = _normalized_json_object(
            context_data,
            field_name="context_data",
        )
        request_data = {
            "title": normalized_title,
            "description_hash": _value_hash(
                normalized_description
            ),
            "context_hash": _value_hash(
                normalized_context
            ),
        }

        def apply(item: WorkItem) -> dict[str, Any]:
            self._require_nonterminal(item)
            changes: dict[str, Any] = {}

            if item.title != normalized_title:
                changes["title"] = {
                    "from": item.title,
                    "to": normalized_title,
                }
                item.title = normalized_title

            if item.description != normalized_description:
                changes["description"] = {
                    "from_hash": _value_hash(item.description),
                    "to_hash": _value_hash(
                        normalized_description
                    ),
                }
                item.description = normalized_description

            if item.context_data != normalized_context:
                changes["context_data"] = {
                    "from_hash": _value_hash(item.context_data),
                    "to_hash": _value_hash(normalized_context),
                }
                item.context_data = normalized_context

            return changes

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="details_changed",
            idempotency_key=idempotency_key,
            request_data=request_data,
            apply=apply,
        )

    def change_priority(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        priority: str,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_priority = _normalized_choice(
            priority,
            field_name="priority",
            allowed=PRIORITIES,
        )

        def apply(item: WorkItem) -> dict[str, Any]:
            self._require_nonterminal(item)
            if item.priority == normalized_priority:
                return {}

            previous = item.priority
            item.priority = normalized_priority
            return {
                "priority": {
                    "from": previous,
                    "to": normalized_priority,
                },
            }

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="priority_changed",
            idempotency_key=idempotency_key,
            request_data={"priority": normalized_priority},
            apply=apply,
        )

    def change_assignee(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        assignee_user_id: int | None,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_assignee = _optional_positive_id(
            assignee_user_id,
            field_name="assignee_user_id",
        )

        def apply(item: WorkItem) -> dict[str, Any]:
            self._require_nonterminal(item)
            if item.assignee_user_id == normalized_assignee:
                return {}

            previous = item.assignee_user_id
            item.assignee_user_id = normalized_assignee
            return {
                "assignee_user_id": {
                    "from": previous,
                    "to": normalized_assignee,
                },
            }

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="assignee_changed",
            idempotency_key=idempotency_key,
            request_data={
                "assignee_user_id": normalized_assignee,
            },
            apply=apply,
        )

    def add_comment(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        comment: str,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_comment = _required_text(
            comment,
            field_name="comment",
            max_length=MAX_EVENT_TEXT_LENGTH,
        )

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="comment_added",
            idempotency_key=idempotency_key,
            request_data={"comment": normalized_comment},
            apply=lambda item: {
                "comment": normalized_comment,
            },
        )

    def add_system_note(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        note: str,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_actor = _normalized_actor(actor)

        if normalized_actor.actor_type != "system":
            raise WorkValidationError(
                "system_note exige ator system."
            )

        normalized_note = _required_text(
            note,
            field_name="note",
            max_length=MAX_EVENT_TEXT_LENGTH,
        )

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=normalized_actor,
            event_type="system_note",
            idempotency_key=idempotency_key,
            request_data={"note": normalized_note},
            apply=lambda item: {"note": normalized_note},
        )

    def transition_status(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        status: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_status = _normalized_choice(
            status,
            field_name="status",
            allowed=frozenset(STATUS_TRANSITIONS),
        )

        if normalized_status in {"blocked", "cancelled"}:
            normalized_reason = _required_text(
                reason,
                field_name="reason",
            )
        else:
            normalized_reason = _optional_text(
                reason,
                field_name="reason",
            )

        def apply(item: WorkItem) -> dict[str, Any]:
            allowed = STATUS_TRANSITIONS.get(
                item.status,
                frozenset(),
            )

            if normalized_status not in allowed:
                raise WorkStateError(
                    "Transição de status inválida: "
                    f"{item.status} -> {normalized_status}."
                )

            if normalized_status in TERMINAL_STATUSES:
                rule = self.repository.get_recurrence_rule(item.id)

                if rule is not None and rule.active:
                    raise WorkStateError(
                        "Recorrência ativa deve ser desabilitada "
                        "antes do estado terminal."
                    )

            self._validate_transition_dependencies(
                item,
                target_status=normalized_status,
            )
            previous_status = item.status
            now = _utc_now()
            item.status = normalized_status
            item.status_changed_at = now
            item.blocked_reason = None
            item.status_reason = normalized_reason

            if normalized_status == "in_progress":
                if item.started_at is None:
                    item.started_at = now
            elif normalized_status == "blocked":
                item.blocked_reason = normalized_reason
                item.status_reason = None
            elif normalized_status == "completed":
                item.completed_at = now
                item.cancelled_at = None
            elif normalized_status == "cancelled":
                item.cancelled_at = now
                item.completed_at = None

            return {
                "status": {
                    "from": previous_status,
                    "to": normalized_status,
                },
                "reason": normalized_reason,
                "changed_at": now.isoformat(),
            }

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="status_changed",
            idempotency_key=idempotency_key,
            request_data={
                "status": normalized_status,
                "reason": normalized_reason,
            },
            apply=apply,
        )

    def change_schedule(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        due_at: datetime | None,
        sla_due_at: datetime | None,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_due_at, normalized_sla_due_at = (
            _normalized_schedule(
                due_at=due_at,
                sla_due_at=sla_due_at,
            )
        )

        def apply(item: WorkItem) -> dict[str, Any]:
            self._require_nonterminal(item)
            changes: dict[str, Any] = {}

            if item.due_at != normalized_due_at:
                changes["due_at"] = {
                    "from": item.due_at.isoformat()
                    if item.due_at is not None
                    else None,
                    "to": normalized_due_at.isoformat()
                    if normalized_due_at is not None
                    else None,
                }
                item.due_at = normalized_due_at

            if item.sla_due_at != normalized_sla_due_at:
                changes["sla_due_at"] = {
                    "from": item.sla_due_at.isoformat()
                    if item.sla_due_at is not None
                    else None,
                    "to": normalized_sla_due_at.isoformat()
                    if normalized_sla_due_at is not None
                    else None,
                }
                item.sla_due_at = normalized_sla_due_at

            return changes

        return self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="schedule_changed",
            idempotency_key=idempotency_key,
            request_data={
                "due_at": normalized_due_at.isoformat()
                if normalized_due_at is not None
                else None,
                "sla_due_at": normalized_sla_due_at.isoformat()
                if normalized_sla_due_at is not None
                else None,
            },
            apply=apply,
        )

    def add_dependency(
        self,
        work_item_id: int,
        *,
        depends_on_work_item_id: int,
        dependency_type: str,
        expected_version: int,
        actor: WorkActor,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_work_item_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_predecessor_id = _positive_id(
            depends_on_work_item_id,
            field_name="depends_on_work_item_id",
        )
        normalized_expected_version = _positive_id(
            expected_version,
            field_name="expected_version",
        )
        normalized_dependency_type = _normalized_choice(
            dependency_type,
            field_name="dependency_type",
            allowed=DEPENDENCY_TYPES,
        )
        normalized_actor = _normalized_actor(actor)
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )
        request_fingerprint = _mutation_fingerprint(
            work_item_id=normalized_work_item_id,
            expected_version=normalized_expected_version,
            event_type="dependency_added",
            actor=normalized_actor,
            request_data={
                "depends_on_work_item_id": (
                    normalized_predecessor_id
                ),
                "dependency_type": normalized_dependency_type,
            },
        )

        try:
            self.repository.lock_dependency_graph()
            item = self.repository.lock_by_id(
                normalized_work_item_id
            )

            if item is None:
                raise WorkNotFoundError(
                    "Item de trabalho não encontrado."
                )

            replay = self._replayed_mutation(
                item,
                idempotency_key=normalized_idempotency_key,
                event_type="dependency_added",
                request_fingerprint=request_fingerprint,
            )

            if replay is not None:
                self.db.commit()
                return replay

            self._validate_expected_version(
                item,
                normalized_expected_version,
            )
            self._require_dependency_editable(item)
            predecessor = self.repository.get_by_id(
                normalized_predecessor_id
            )

            if predecessor is None:
                raise WorkNotFoundError(
                    "Item predecessor não encontrado."
                )

            self._validate_same_scope(item, predecessor)

            if item.id == predecessor.id:
                raise WorkValidationError(
                    "Item não pode depender de si mesmo."
                )

            existing = self.repository.find_dependency(
                work_item_id=item.id,
                depends_on_work_item_id=predecessor.id,
            )

            if existing is not None:
                raise WorkConflictError(
                    "Dependência já existe."
                )

            if self.repository.would_create_dependency_cycle(
                work_item_id=item.id,
                depends_on_work_item_id=predecessor.id,
            ):
                raise WorkStateError(
                    "Dependência criaria ciclo no grafo."
                )

            dependency = WorkDependency(
                work_item_id=item.id,
                depends_on_work_item_id=predecessor.id,
                dependency_type=normalized_dependency_type,
                created_by_user_id=normalized_actor.actor_user_id,
            )
            self.repository.add_dependency(dependency)
            event = self._append_locked_event(
                item,
                actor=normalized_actor,
                event_type="dependency_added",
                idempotency_key=normalized_idempotency_key,
                request_fingerprint=request_fingerprint,
                changes={
                    "dependency_id": dependency.id,
                    "depends_on_work_item_id": predecessor.id,
                    "dependency_type": normalized_dependency_type,
                },
            )
            self.db.commit()
            self.db.refresh(item)
            self.db.refresh(event)
        except IntegrityError as error:
            self.db.rollback()
            raise WorkConflictError(
                "Dependência concorrente ou inválida."
            ) from error
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return WorkMutationResult(
            work_item=item,
            event=event,
            applied=True,
            duplicate=False,
        )

    def remove_dependency(
        self,
        work_item_id: int,
        *,
        depends_on_work_item_id: int,
        expected_version: int,
        actor: WorkActor,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_work_item_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_predecessor_id = _positive_id(
            depends_on_work_item_id,
            field_name="depends_on_work_item_id",
        )
        normalized_expected_version = _positive_id(
            expected_version,
            field_name="expected_version",
        )
        normalized_actor = _normalized_actor(actor)
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )
        request_fingerprint = _mutation_fingerprint(
            work_item_id=normalized_work_item_id,
            expected_version=normalized_expected_version,
            event_type="dependency_removed",
            actor=normalized_actor,
            request_data={
                "depends_on_work_item_id": (
                    normalized_predecessor_id
                ),
            },
        )

        try:
            self.repository.lock_dependency_graph()
            item = self.repository.lock_by_id(
                normalized_work_item_id
            )

            if item is None:
                raise WorkNotFoundError(
                    "Item de trabalho não encontrado."
                )

            replay = self._replayed_mutation(
                item,
                idempotency_key=normalized_idempotency_key,
                event_type="dependency_removed",
                request_fingerprint=request_fingerprint,
            )

            if replay is not None:
                self.db.commit()
                return replay

            self._validate_expected_version(
                item,
                normalized_expected_version,
            )
            self._require_dependency_editable(item)
            dependency = self.repository.find_dependency(
                work_item_id=item.id,
                depends_on_work_item_id=(
                    normalized_predecessor_id
                ),
            )

            if dependency is None:
                raise WorkNotFoundError(
                    "Dependência não encontrada."
                )

            dependency_id = dependency.id
            dependency_type = dependency.dependency_type
            self.repository.remove_dependency(dependency)
            event = self._append_locked_event(
                item,
                actor=normalized_actor,
                event_type="dependency_removed",
                idempotency_key=normalized_idempotency_key,
                request_fingerprint=request_fingerprint,
                changes={
                    "dependency_id": dependency_id,
                    "depends_on_work_item_id": (
                        normalized_predecessor_id
                    ),
                    "dependency_type": dependency_type,
                },
            )
            self.db.commit()
            self.db.refresh(item)
            self.db.refresh(event)
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return WorkMutationResult(
            work_item=item,
            event=event,
            applied=True,
            duplicate=False,
        )

    def list_dependencies(
        self,
        work_item_id: int,
        *,
        limit: int = 50,
        after_id: int | None = None,
    ) -> tuple[tuple[WorkDependency, WorkItem], ...]:
        self.get(work_item_id)
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_WORK_PAGE,
        )
        normalized_after_id = _optional_positive_id(
            after_id,
            field_name="after_id",
        )
        return tuple(
            self.repository.list_dependency_predecessors(
                work_item_id,
                after_id=normalized_after_id,
                limit=normalized_limit + 1,
            )
        )

    def link_memory(
        self,
        work_item_id: int,
        *,
        memory_id: int,
        relation: str,
        expected_version: int,
        actor: WorkActor,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_work_item_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_memory_id = _positive_id(
            memory_id,
            field_name="memory_id",
        )
        normalized_expected_version = _positive_id(
            expected_version,
            field_name="expected_version",
        )
        normalized_relation = _normalized_choice(
            relation,
            field_name="relation",
            allowed=MEMORY_RELATIONS,
        )
        normalized_actor = _normalized_actor(actor)
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )
        request_fingerprint = _mutation_fingerprint(
            work_item_id=normalized_work_item_id,
            expected_version=normalized_expected_version,
            event_type="memory_linked",
            actor=normalized_actor,
            request_data={
                "memory_id": normalized_memory_id,
                "relation": normalized_relation,
            },
        )

        try:
            item = self.repository.lock_by_id(
                normalized_work_item_id
            )

            if item is None:
                raise WorkNotFoundError(
                    "Item de trabalho não encontrado."
                )

            replay = self._replayed_mutation(
                item,
                idempotency_key=normalized_idempotency_key,
                event_type="memory_linked",
                request_fingerprint=request_fingerprint,
            )

            if replay is not None:
                self.db.commit()
                return replay

            self._validate_expected_version(
                item,
                normalized_expected_version,
            )

            if not self.repository.memory_exists(
                normalized_memory_id
            ):
                raise WorkNotFoundError(
                    "Memória não encontrada."
                )

            existing = self.repository.find_memory_link(
                work_item_id=item.id,
                memory_id=normalized_memory_id,
                relation=normalized_relation,
            )

            if existing is not None:
                raise WorkConflictError(
                    "Vínculo de memória já existe."
                )

            link = WorkMemoryLink(
                work_item_id=item.id,
                memory_id=normalized_memory_id,
                relation=normalized_relation,
                created_by_user_id=(
                    normalized_actor.actor_user_id
                ),
            )
            self.repository.add_memory_link(link)
            event = self._append_locked_event(
                item,
                actor=normalized_actor,
                event_type="memory_linked",
                idempotency_key=normalized_idempotency_key,
                request_fingerprint=request_fingerprint,
                changes={
                    "memory_link_id": link.id,
                    "memory_id": normalized_memory_id,
                    "relation": normalized_relation,
                },
            )
            self.db.commit()
            self.db.refresh(item)
            self.db.refresh(event)
        except IntegrityError as error:
            self.db.rollback()
            raise WorkConflictError(
                "Vínculo de memória concorrente ou inválido."
            ) from error
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return WorkMutationResult(
            work_item=item,
            event=event,
            applied=True,
            duplicate=False,
        )

    def unlink_memory(
        self,
        work_item_id: int,
        *,
        memory_id: int,
        relation: str,
        expected_version: int,
        actor: WorkActor,
        idempotency_key: str | None = None,
    ) -> WorkMutationResult:
        normalized_work_item_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_memory_id = _positive_id(
            memory_id,
            field_name="memory_id",
        )
        normalized_expected_version = _positive_id(
            expected_version,
            field_name="expected_version",
        )
        normalized_relation = _normalized_choice(
            relation,
            field_name="relation",
            allowed=MEMORY_RELATIONS,
        )
        normalized_actor = _normalized_actor(actor)
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )
        request_fingerprint = _mutation_fingerprint(
            work_item_id=normalized_work_item_id,
            expected_version=normalized_expected_version,
            event_type="memory_unlinked",
            actor=normalized_actor,
            request_data={
                "memory_id": normalized_memory_id,
                "relation": normalized_relation,
            },
        )

        try:
            item = self.repository.lock_by_id(
                normalized_work_item_id
            )

            if item is None:
                raise WorkNotFoundError(
                    "Item de trabalho não encontrado."
                )

            replay = self._replayed_mutation(
                item,
                idempotency_key=normalized_idempotency_key,
                event_type="memory_unlinked",
                request_fingerprint=request_fingerprint,
            )

            if replay is not None:
                self.db.commit()
                return replay

            self._validate_expected_version(
                item,
                normalized_expected_version,
            )
            link = self.repository.find_memory_link(
                work_item_id=item.id,
                memory_id=normalized_memory_id,
                relation=normalized_relation,
            )

            if link is None:
                raise WorkNotFoundError(
                    "Vínculo de memória não encontrado."
                )

            link_id = link.id
            self.repository.remove_memory_link(link)
            event = self._append_locked_event(
                item,
                actor=normalized_actor,
                event_type="memory_unlinked",
                idempotency_key=normalized_idempotency_key,
                request_fingerprint=request_fingerprint,
                changes={
                    "memory_link_id": link_id,
                    "memory_id": normalized_memory_id,
                    "relation": normalized_relation,
                },
            )
            self.db.commit()
            self.db.refresh(item)
            self.db.refresh(event)
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return WorkMutationResult(
            work_item=item,
            event=event,
            applied=True,
            duplicate=False,
        )

    def list_memory_links(
        self,
        work_item_id: int,
        *,
        limit: int = 50,
        after_id: int | None = None,
    ) -> tuple[WorkMemoryLink, ...]:
        self.get(work_item_id)
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_WORK_PAGE,
        )
        normalized_after_id = _optional_positive_id(
            after_id,
            field_name="after_id",
        )
        return tuple(
            self.repository.list_memory_links(
                work_item_id,
                after_id=normalized_after_id,
                limit=normalized_limit + 1,
            )
        )

    def evaluate_sla(
        self,
        work_item_id: int,
        *,
        as_of: datetime | None = None,
    ) -> WorkSLAStatus:
        item = self.get(work_item_id)
        evaluated_at = (
            _utc_now()
            if as_of is None
            else _aware_datetime(
                as_of,
                field_name="as_of",
            )
        )
        assert evaluated_at is not None

        if item.sla_due_at is None:
            return WorkSLAStatus(
                work_item_id=item.id,
                status="not_configured",
                sla_due_at=None,
                evaluated_at=evaluated_at,
                remaining_seconds=None,
            )

        remaining = (
            item.sla_due_at - evaluated_at
        ).total_seconds()

        if item.status == "cancelled":
            status = "cancelled"
        elif item.status == "completed":
            status = (
                "met"
                if item.completed_at is not None
                and item.completed_at <= item.sla_due_at
                else "missed"
            )
        elif evaluated_at > item.sla_due_at:
            status = "breached"
        else:
            status = "on_track"

        return WorkSLAStatus(
            work_item_id=item.id,
            status=status,
            sla_due_at=item.sla_due_at,
            evaluated_at=evaluated_at,
            remaining_seconds=remaining,
        )

    def list_sla_breaches(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
        scope_type: str | None = None,
        account_id: int | None = None,
        subject_user_id: int | None = None,
    ) -> tuple[WorkItem, ...]:
        evaluated_at = (
            _utc_now()
            if as_of is None
            else _aware_datetime(
                as_of,
                field_name="as_of",
            )
        )
        assert evaluated_at is not None
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_SLA_BATCH,
        )
        return tuple(
            self.repository.list_sla_breaches(
                as_of=evaluated_at,
                limit=normalized_limit,
                scope_type=scope_type,
                account_id=account_id,
                subject_user_id=subject_user_id,
            )
        )

    def configure_recurrence(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        frequency: str,
        starts_at: datetime,
        timezone_name: str,
        interval_value: int = 1,
        ends_at: datetime | None = None,
        max_occurrences: int | None = None,
        sla_lead_minutes: int | None = None,
        idempotency_key: str | None = None,
    ) -> RecurrenceConfigurationResult:
        normalized_actor = _normalized_actor(actor)
        normalized_frequency = _normalized_choice(
            frequency,
            field_name="frequency",
            allowed=RECURRENCE_FREQUENCIES,
        )
        normalized_interval = _bounded_integer(
            interval_value,
            field_name="interval_value",
            minimum=1,
            maximum=365,
        )
        recurrence_timezone = _normalized_timezone(timezone_name)
        normalized_starts_at = _aware_datetime(
            starts_at,
            field_name="starts_at",
        )
        normalized_ends_at = _aware_datetime(
            ends_at,
            field_name="ends_at",
        )
        assert normalized_starts_at is not None

        if (
            normalized_ends_at is not None
            and normalized_ends_at <= normalized_starts_at
        ):
            raise WorkValidationError(
                "ends_at deve ser posterior a starts_at."
            )

        normalized_max_occurrences = _optional_bounded_integer(
            max_occurrences,
            field_name="max_occurrences",
            minimum=1,
            maximum=MAX_RECURRENCE_OCCURRENCES,
        )
        normalized_sla_lead = _optional_bounded_integer(
            sla_lead_minutes,
            field_name="sla_lead_minutes",
            minimum=0,
            maximum=525600,
        )

        def apply(item: WorkItem) -> dict[str, Any]:
            self._require_nonterminal(item)

            if item.work_key is None:
                raise WorkValidationError(
                    "Recorrência exige work_key no item modelo."
                )

            if len(item.work_key) > MAX_RECURRING_WORK_KEY_LENGTH:
                raise WorkValidationError(
                    "work_key é longa demais para ocorrências."
                )

            existing = self.repository.get_recurrence_rule(item.id)

            if existing is not None:
                raise WorkConflictError(
                    "Item já possui regra de recorrência."
                )

            rule = WorkRecurrenceRule(
                work_item_id=item.id,
                frequency=normalized_frequency,
                interval_value=normalized_interval,
                timezone_name=recurrence_timezone.key,
                starts_at=normalized_starts_at,
                ends_at=normalized_ends_at,
                max_occurrences=normalized_max_occurrences,
                generated_occurrences=0,
                next_occurrence_at=normalized_starts_at,
                last_occurrence_at=None,
                sla_lead_minutes=normalized_sla_lead,
                active=True,
                created_by_user_id=normalized_actor.actor_user_id,
            )
            self.repository.add_recurrence_rule(rule)
            return {
                "recurrence_rule_id": rule.id,
                "frequency": normalized_frequency,
                "interval_value": normalized_interval,
                "timezone_name": recurrence_timezone.key,
                "starts_at": normalized_starts_at.isoformat(),
                "ends_at": normalized_ends_at.isoformat()
                if normalized_ends_at is not None
                else None,
                "max_occurrences": normalized_max_occurrences,
                "sla_lead_minutes": normalized_sla_lead,
            }

        mutation = self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=normalized_actor,
            event_type="recurrence_configured",
            idempotency_key=idempotency_key,
            request_data={
                "frequency": normalized_frequency,
                "interval_value": normalized_interval,
                "timezone_name": recurrence_timezone.key,
                "starts_at": normalized_starts_at.isoformat(),
                "ends_at": normalized_ends_at.isoformat()
                if normalized_ends_at is not None
                else None,
                "max_occurrences": normalized_max_occurrences,
                "sla_lead_minutes": normalized_sla_lead,
            },
            apply=apply,
        )
        rule = self.repository.get_recurrence_rule(work_item_id)

        if rule is None:
            raise WorkStateError(
                "Regra de recorrência não foi persistida."
            )

        return RecurrenceConfigurationResult(
            rule=rule,
            mutation=mutation,
        )

    def disable_recurrence(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        reason: str,
        idempotency_key: str | None = None,
    ) -> RecurrenceConfigurationResult:
        normalized_reason = _required_text(
            reason,
            field_name="reason",
        )

        def apply(item: WorkItem) -> dict[str, Any]:
            rule = self.repository.get_recurrence_rule(item.id)

            if rule is None:
                raise WorkNotFoundError(
                    "Regra de recorrência não encontrada."
                )

            if not rule.active:
                raise WorkStateError(
                    "Regra de recorrência já está desabilitada."
                )

            previous_next = rule.next_occurrence_at
            rule.active = False
            rule.next_occurrence_at = None
            rule.updated_at = _utc_now()
            return {
                "recurrence_rule_id": rule.id,
                "reason": normalized_reason,
                "previous_next_occurrence_at": (
                    previous_next.isoformat()
                    if previous_next is not None
                    else None
                ),
            }

        mutation = self._mutate(
            work_item_id,
            expected_version=expected_version,
            actor=actor,
            event_type="recurrence_disabled",
            idempotency_key=idempotency_key,
            request_data={"reason": normalized_reason},
            apply=apply,
        )
        rule = self.repository.get_recurrence_rule(work_item_id)

        if rule is None:
            raise WorkStateError(
                "Regra de recorrência não foi preservada."
            )

        return RecurrenceConfigurationResult(
            rule=rule,
            mutation=mutation,
        )

    def get_recurrence(
        self,
        work_item_id: int,
    ) -> WorkRecurrenceRule:
        self.get(work_item_id)
        rule = self.repository.get_recurrence_rule(work_item_id)

        if rule is None:
            raise WorkNotFoundError(
                "Regra de recorrência não encontrada."
            )

        return rule

    def list_recurrence_occurrences(
        self,
        work_item_id: int,
        *,
        limit: int = 50,
        after_id: int | None = None,
    ) -> tuple[WorkRecurrenceOccurrence, ...]:
        rule = self.get_recurrence(work_item_id)
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_WORK_PAGE,
        )
        normalized_after_id = _optional_positive_id(
            after_id,
            field_name="after_id",
        )
        return tuple(
            self.repository.list_recurrence_occurrences(
                rule.id,
                after_id=normalized_after_id,
                limit=normalized_limit + 1,
            )
        )

    def list_due_recurrences(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> tuple[WorkRecurrenceRule, ...]:
        evaluated_at = (
            _utc_now()
            if as_of is None
            else _aware_datetime(
                as_of,
                field_name="as_of",
            )
        )
        assert evaluated_at is not None
        normalized_limit = _bounded_integer(
            limit,
            field_name="limit",
            minimum=1,
            maximum=MAX_RECURRENCE_BATCH,
        )
        return tuple(
            self.repository.list_due_recurrence_rules(
                as_of=evaluated_at,
                limit=normalized_limit,
            )
        )

    def generate_due_occurrence(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        as_of: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> RecurrenceGenerationResult:
        normalized_work_item_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_expected_version = _positive_id(
            expected_version,
            field_name="expected_version",
        )
        normalized_actor = _normalized_actor(actor)
        normalized_as_of = (
            _utc_now()
            if as_of is None
            else _aware_datetime(
                as_of,
                field_name="as_of",
            )
        )
        assert normalized_as_of is not None
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )
        request_fingerprint = _mutation_fingerprint(
            work_item_id=normalized_work_item_id,
            expected_version=normalized_expected_version,
            event_type="recurrence_generated",
            actor=normalized_actor,
            request_data={"generate_next": True},
        )

        try:
            template = self.repository.lock_by_id(
                normalized_work_item_id
            )

            if template is None:
                raise WorkNotFoundError(
                    "Item modelo não encontrado."
                )

            if normalized_idempotency_key is not None:
                existing_event = (
                    self.repository.find_event_by_idempotency_key(
                        work_item_id=template.id,
                        idempotency_key=(
                            normalized_idempotency_key
                        ),
                    )
                )

                if existing_event is not None:
                    self._validate_event_replay(
                        existing_event,
                        event_type="recurrence_generated",
                        request_fingerprint=request_fingerprint,
                    )
                    result = self._recurrence_replay_result(
                        template,
                        existing_event,
                    )
                    self.db.commit()
                    return result

            self._validate_expected_version(
                template,
                normalized_expected_version,
            )
            self._require_nonterminal(template)
            rule = self.repository.lock_recurrence_rule(
                template.id
            )

            if rule is None:
                raise WorkNotFoundError(
                    "Regra de recorrência não encontrada."
                )

            if not rule.active or rule.next_occurrence_at is None:
                raise WorkStateError(
                    "Regra de recorrência não está ativa."
                )

            if rule.next_occurrence_at > normalized_as_of:
                raise WorkStateError(
                    "Próxima ocorrência ainda não está vencida."
                )

            scheduled_for = rule.next_occurrence_at
            occurrence_number = rule.generated_occurrences + 1
            occurrence_key = (
                f"{template.work_key}:occ:{occurrence_number}"
            )

            if len(occurrence_key) > 255:
                raise WorkValidationError(
                    "work_key da ocorrência excede 255 caracteres."
                )

            now = _utc_now()
            occurrence_work_item = WorkItem(
                work_type=template.work_type,
                title=template.title,
                description=template.description,
                work_key=occurrence_key,
                scope_type=template.scope_type,
                account_id=template.account_id,
                subject_user_id=template.subject_user_id,
                parent_work_item_id=template.parent_work_item_id,
                created_by_user_id=normalized_actor.actor_user_id,
                assignee_user_id=template.assignee_user_id,
                status="backlog",
                priority=template.priority,
                blocked_reason=None,
                status_reason=None,
                status_changed_at=now,
                due_at=scheduled_for,
                sla_due_at=(
                    scheduled_for
                    - timedelta(minutes=rule.sla_lead_minutes)
                    if rule.sla_lead_minutes is not None
                    else None
                ),
                started_at=None,
                completed_at=None,
                cancelled_at=None,
                version=1,
                origin_type="system",
                origin_reference=(
                    f"recurrence:{rule.id}:{occurrence_number}"
                ),
                context_data=_normalized_json_object(
                    template.context_data,
                    field_name="context_data",
                ),
                created_at=now,
                updated_at=now,
            )
            self.repository.add_work_item(occurrence_work_item)
            created_event = WorkEvent(
                work_item_id=occurrence_work_item.id,
                event_type="created",
                actor_type=normalized_actor.actor_type,
                actor_reference=normalized_actor.actor_reference,
                actor_user_id=normalized_actor.actor_user_id,
                idempotency_key=(
                    f"recurrence.{rule.id}.{occurrence_number}.created"
                ),
                event_data={
                    "request_fingerprint": _fingerprint({
                        "recurrence_rule_id": rule.id,
                        "occurrence_number": occurrence_number,
                        "scheduled_for": scheduled_for,
                    }),
                    "result_version": 1,
                    "recurrence_rule_id": rule.id,
                    "occurrence_number": occurrence_number,
                },
                created_at=now,
            )
            self.repository.add_event(created_event)
            occurrence = WorkRecurrenceOccurrence(
                recurrence_rule_id=rule.id,
                work_item_id=occurrence_work_item.id,
                occurrence_number=occurrence_number,
                scheduled_for=scheduled_for,
                created_at=now,
            )
            self.repository.add_recurrence_occurrence(occurrence)

            next_occurrence = _next_recurrence_at(
                scheduled_for,
                frequency=rule.frequency,
                interval_value=rule.interval_value,
                recurrence_timezone=ZoneInfo(rule.timezone_name),
            )
            reached_max = (
                rule.max_occurrences is not None
                and occurrence_number >= rule.max_occurrences
            )
            reached_end = (
                rule.ends_at is not None
                and next_occurrence > rule.ends_at
            )
            rule.generated_occurrences = occurrence_number
            rule.last_occurrence_at = scheduled_for
            rule.active = not (reached_max or reached_end)
            rule.next_occurrence_at = (
                next_occurrence if rule.active else None
            )
            rule.updated_at = now
            event = self._append_locked_event(
                template,
                actor=normalized_actor,
                event_type="recurrence_generated",
                idempotency_key=normalized_idempotency_key,
                request_fingerprint=request_fingerprint,
                changes={
                    "recurrence_rule_id": rule.id,
                    "occurrence_id": occurrence.id,
                    "occurrence_work_item_id": occurrence_work_item.id,
                    "occurrence_number": occurrence_number,
                    "scheduled_for": scheduled_for.isoformat(),
                    "next_occurrence_at": (
                        rule.next_occurrence_at.isoformat()
                        if rule.next_occurrence_at is not None
                        else None
                    ),
                    "recurrence_active": rule.active,
                },
            )
            self.db.commit()
            self.db.refresh(template)
            self.db.refresh(occurrence_work_item)
            self.db.refresh(occurrence)
            self.db.refresh(rule)
            self.db.refresh(event)
        except IntegrityError as error:
            self.db.rollback()
            raise WorkConflictError(
                "Ocorrência recorrente concorrente ou inválida."
            ) from error
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return RecurrenceGenerationResult(
            template=template,
            occurrence_work_item=occurrence_work_item,
            occurrence=occurrence,
            rule=rule,
            event=event,
            applied=True,
            duplicate=False,
        )

    def _mutate(
        self,
        work_item_id: int,
        *,
        expected_version: int,
        actor: WorkActor,
        event_type: str,
        idempotency_key: str | None,
        request_data: dict[str, Any],
        apply: Callable[[WorkItem], dict[str, Any]],
    ) -> WorkMutationResult:
        normalized_work_item_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_expected_version = _positive_id(
            expected_version,
            field_name="expected_version",
        )
        normalized_actor = _normalized_actor(actor)
        normalized_idempotency_key = (
            _normalized_idempotency_key(idempotency_key)
        )
        request_fingerprint = _mutation_fingerprint(
            work_item_id=normalized_work_item_id,
            expected_version=normalized_expected_version,
            event_type=event_type,
            actor=normalized_actor,
            request_data=request_data,
        )

        try:
            item = self.repository.lock_by_id(
                normalized_work_item_id
            )

            if item is None:
                raise WorkNotFoundError(
                    "Item de trabalho não encontrado."
                )

            replay = self._replayed_mutation(
                item,
                idempotency_key=normalized_idempotency_key,
                event_type=event_type,
                request_fingerprint=request_fingerprint,
            )

            if replay is not None:
                self.db.commit()
                return replay

            self._validate_expected_version(
                item,
                normalized_expected_version,
            )

            changes = apply(item)

            if not changes:
                raise WorkValidationError(
                    "A mutação não produz alteração."
                )

            event = self._append_locked_event(
                item,
                actor=normalized_actor,
                event_type=event_type,
                idempotency_key=normalized_idempotency_key,
                request_fingerprint=request_fingerprint,
                changes=changes,
            )
            self.db.commit()
            self.db.refresh(item)
            self.db.refresh(event)
        except IntegrityError as error:
            self.db.rollback()

            if (
                _constraint_name(error)
                == EVENT_IDEMPOTENCY_CONSTRAINT
            ):
                raise WorkIdempotencyConflictError(
                    "Conflito concorrente de idempotency_key."
                ) from error

            raise WorkValidationError(
                "A mutação viola uma restrição de integridade."
            ) from error
        except WorkError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        return WorkMutationResult(
            work_item=item,
            event=event,
            applied=True,
            duplicate=False,
        )

    def _normalize_create(
        self,
        *,
        work_type: str,
        title: str,
        scope_type: str,
        origin_type: str,
        origin_reference: str,
        description: str | None,
        work_key: str | None,
        account_id: int | None,
        subject_user_id: int | None,
        parent_work_item_id: int | None,
        assignee_user_id: int | None,
        priority: str,
        due_at: datetime | None,
        sla_due_at: datetime | None,
        context_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_scope = _normalized_choice(
            scope_type,
            field_name="scope_type",
            allowed=SCOPE_TYPES,
        )
        normalized_account_id = _optional_positive_id(
            account_id,
            field_name="account_id",
        )
        normalized_subject_user_id = _optional_positive_id(
            subject_user_id,
            field_name="subject_user_id",
        )

        if (
            normalized_scope == "global"
            and (
                normalized_account_id is not None
                or normalized_subject_user_id is not None
            )
        ):
            raise WorkValidationError(
                "Escopo global não aceita account_id ou "
                "subject_user_id."
            )

        if (
            normalized_scope == "account"
            and (
                normalized_account_id is None
                or normalized_subject_user_id is not None
            )
        ):
            raise WorkValidationError(
                "Escopo account exige somente account_id."
            )

        if (
            normalized_scope == "user"
            and (
                normalized_account_id is not None
                or normalized_subject_user_id is None
            )
        ):
            raise WorkValidationError(
                "Escopo user exige somente subject_user_id."
            )

        normalized_due_at, normalized_sla_due_at = (
            _normalized_schedule(
                due_at=due_at,
                sla_due_at=sla_due_at,
            )
        )

        return {
            "work_type": _normalized_choice(
                work_type,
                field_name="work_type",
                allowed=WORK_TYPES,
            ),
            "title": _required_text(
                title,
                field_name="title",
                max_length=240,
            ),
            "description": _optional_text(
                description,
                field_name="description",
            ),
            "work_key": _normalized_work_key(work_key),
            "scope_type": normalized_scope,
            "account_id": normalized_account_id,
            "subject_user_id": normalized_subject_user_id,
            "parent_work_item_id": _optional_positive_id(
                parent_work_item_id,
                field_name="parent_work_item_id",
            ),
            "assignee_user_id": _optional_positive_id(
                assignee_user_id,
                field_name="assignee_user_id",
            ),
            "priority": _normalized_choice(
                priority,
                field_name="priority",
                allowed=PRIORITIES,
            ),
            "due_at": normalized_due_at,
            "sla_due_at": normalized_sla_due_at,
            "origin_type": _normalized_choice(
                origin_type,
                field_name="origin_type",
                allowed=ORIGIN_TYPES,
            ),
            "origin_reference": _required_text(
                origin_reference,
                field_name="origin_reference",
                max_length=500,
            ),
            "context_data": _normalized_json_object(
                context_data,
                field_name="context_data",
            ),
        }

    def _validate_parent(
        self,
        normalized: dict[str, Any],
    ) -> None:
        parent_id = normalized["parent_work_item_id"]

        if parent_id is None:
            return

        parent = self.repository.get_by_id(parent_id)

        if parent is None:
            raise WorkValidationError(
                "parent_work_item_id inexistente."
            )

        child_scope = (
            normalized["scope_type"],
            normalized["account_id"],
            normalized["subject_user_id"],
        )
        parent_scope = (
            parent.scope_type,
            parent.account_id,
            parent.subject_user_id,
        )

        if child_scope != parent_scope:
            raise WorkValidationError(
                "Pai e filho devem possuir o mesmo escopo."
            )

    def _find_existing(
        self,
        normalized: dict[str, Any],
    ) -> WorkItem | None:
        work_key = normalized["work_key"]

        if work_key is None:
            return None

        return self.repository.find_by_key(
            scope_type=normalized["scope_type"],
            work_key=work_key,
            account_id=normalized["account_id"],
            subject_user_id=(
                normalized["subject_user_id"]
            ),
        )

    @staticmethod
    def _creation_fingerprint(
        normalized: dict[str, Any],
        actor: WorkActor,
    ) -> str:
        return _fingerprint({
            "work_item": normalized,
            "actor": {
                "actor_type": actor.actor_type,
                "actor_reference": actor.actor_reference,
                "actor_user_id": actor.actor_user_id,
            },
        })

    def _existing_creation_result(
        self,
        item: WorkItem,
        *,
        request_fingerprint: str,
    ) -> WorkCreationResult:
        event = self.repository.get_created_event(item.id)

        if (
            event is None
            or event.event_data.get("request_fingerprint")
            != request_fingerprint
        ):
            raise WorkConflictError(
                "work_key já existe com conteúdo diferente."
            )

        return WorkCreationResult(
            work_item=item,
            event=event,
            created=False,
            duplicate=True,
        )

    def _append_locked_event(
        self,
        item: WorkItem,
        *,
        actor: WorkActor,
        event_type: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        changes: dict[str, Any],
    ) -> WorkEvent:
        previous_version = item.version
        item.version = previous_version + 1
        item.updated_at = _utc_now()
        event_data = _normalized_json_object(
            {
                "request_fingerprint": request_fingerprint,
                "from_version": previous_version,
                "to_version": item.version,
                "changes": changes,
            },
            field_name="event_data",
        )
        event = WorkEvent(
            work_item_id=item.id,
            event_type=event_type,
            actor_type=actor.actor_type,
            actor_reference=actor.actor_reference,
            actor_user_id=actor.actor_user_id,
            idempotency_key=idempotency_key,
            event_data=event_data,
            created_at=_utc_now(),
        )
        self.repository.add_event(event)
        return event

    def _replayed_mutation(
        self,
        item: WorkItem,
        *,
        idempotency_key: str | None,
        event_type: str,
        request_fingerprint: str,
    ) -> WorkMutationResult | None:
        if idempotency_key is None:
            return None

        event = self.repository.find_event_by_idempotency_key(
            work_item_id=item.id,
            idempotency_key=idempotency_key,
        )

        if event is None:
            return None

        self._validate_event_replay(
            event,
            event_type=event_type,
            request_fingerprint=request_fingerprint,
        )
        return WorkMutationResult(
            work_item=item,
            event=event,
            applied=False,
            duplicate=True,
        )

    @staticmethod
    def _validate_expected_version(
        item: WorkItem,
        expected_version: int,
    ) -> None:
        if item.version != expected_version:
            raise WorkVersionConflictError(
                expected_version=expected_version,
                current_version=item.version,
            )

    @staticmethod
    def _require_nonterminal(item: WorkItem) -> None:
        if item.status in TERMINAL_STATUSES:
            raise WorkStateError(
                "Item em estado terminal não pode ser alterado."
            )

    @staticmethod
    def _require_dependency_editable(item: WorkItem) -> None:
        if item.status not in {"backlog", "ready"}:
            raise WorkStateError(
                "Dependências somente podem mudar em backlog ou ready."
            )

    @staticmethod
    def _validate_same_scope(
        first: WorkItem,
        second: WorkItem,
    ) -> None:
        first_scope = (
            first.scope_type,
            first.account_id,
            first.subject_user_id,
        )
        second_scope = (
            second.scope_type,
            second.account_id,
            second.subject_user_id,
        )

        if first_scope != second_scope:
            raise WorkValidationError(
                "Itens relacionados devem possuir o mesmo escopo."
            )

    def _validate_transition_dependencies(
        self,
        item: WorkItem,
        *,
        target_status: str,
    ) -> None:
        if target_status not in {"in_progress", "completed"}:
            return

        blockers: list[str] = []

        for dependency, predecessor in (
            self.repository.list_dependency_predecessors(item.id)
        ):
            satisfied = True

            if (
                target_status == "in_progress"
                and dependency.dependency_type
                == "finish_to_start"
            ):
                satisfied = predecessor.status == "completed"
            elif (
                target_status == "in_progress"
                and dependency.dependency_type
                == "start_to_start"
            ):
                satisfied = predecessor.started_at is not None
            elif (
                target_status == "completed"
                and dependency.dependency_type
                == "finish_to_finish"
            ):
                satisfied = predecessor.status == "completed"
            elif (
                target_status == "completed"
                and dependency.dependency_type
                == "start_to_finish"
            ):
                satisfied = predecessor.started_at is not None

            if not satisfied:
                blockers.append(
                    f"{predecessor.id}:{dependency.dependency_type}"
                )

        if blockers:
            raise WorkStateError(
                "Dependências não satisfeitas: "
                + ", ".join(blockers)
                + "."
            )

    def _recurrence_replay_result(
        self,
        template: WorkItem,
        event: WorkEvent,
    ) -> RecurrenceGenerationResult:
        changes = event.event_data.get("changes", {})
        occurrence_number = changes.get("occurrence_number")
        rule_id = changes.get("recurrence_rule_id")

        if (
            isinstance(occurrence_number, bool)
            or not isinstance(occurrence_number, int)
            or isinstance(rule_id, bool)
            or not isinstance(rule_id, int)
        ):
            raise WorkStateError(
                "Evento recorrente idempotente está inconsistente."
            )

        rule = self.repository.get_recurrence_rule(template.id)

        if rule is None or rule.id != rule_id:
            raise WorkStateError(
                "Regra recorrente idempotente não foi encontrada."
            )

        occurrence = self.repository.get_recurrence_occurrence(
            recurrence_rule_id=rule.id,
            occurrence_number=occurrence_number,
        )

        if occurrence is None:
            raise WorkStateError(
                "Ocorrência idempotente não foi encontrada."
            )

        occurrence_work_item = self.repository.get_by_id(
            occurrence.work_item_id
        )

        if occurrence_work_item is None:
            raise WorkStateError(
                "Item da ocorrência idempotente não foi encontrado."
            )

        return RecurrenceGenerationResult(
            template=template,
            occurrence_work_item=occurrence_work_item,
            occurrence=occurrence,
            rule=rule,
            event=event,
            applied=False,
            duplicate=True,
        )

    @staticmethod
    def _validate_event_replay(
        event: WorkEvent,
        *,
        event_type: str,
        request_fingerprint: str,
    ) -> None:
        if (
            event.event_type != event_type
            or event.event_data.get("request_fingerprint")
            != request_fingerprint
        ):
            raise WorkIdempotencyConflictError(
                "idempotency_key já utilizada com outro pedido."
            )
