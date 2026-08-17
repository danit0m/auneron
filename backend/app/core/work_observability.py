import logging

from app.core.observability import get_request_id
from app.models.work import WorkEvent
from app.models.work import WorkItem


work_observability_logger = logging.getLogger(
    "auneron.work"
)


def log_work_change(
    *,
    work_item: WorkItem,
    event: WorkEvent,
    applied: bool,
    duplicate: bool,
) -> None:
    if duplicate:
        outcome = "replayed"
    elif applied:
        outcome = "applied"
    else:
        outcome = "unchanged"

    work_observability_logger.info(
        "work_change_completed",
        extra={
            "event": "work.change",
            "request_id": get_request_id(),
            "outcome": outcome,
            "work_item_id": work_item.id,
            "scope_type": work_item.scope_type,
            "work_event_type": event.event_type,
            "actor_type": event.actor_type,
            "actor_user_id": event.actor_user_id,
            "version": work_item.version,
        },
    )
