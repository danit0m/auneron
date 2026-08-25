import logging
from typing import Any

from app.core.observability import get_request_id


work_outcome_evaluation_logger = logging.getLogger(
    "auneron.work.outcome_evaluation"
)

_ALLOWED_FIELDS = frozenset({
    "attempts",
    "attention_required_count",
    "candidate_count",
    "completed_count",
    "duplicate",
    "error_code",
    "evaluation_code",
    "failure_count",
    "learning_signal",
    "memory_item_id",
    "outcome",
    "status",
    "terminal_status",
    "work_item_id",
    "work_skill_execution_id",
})


def log_work_outcome_evaluation_event(
    event: str,
    **fields: Any,
) -> None:
    """Emit bounded outcome-learning metadata only."""
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in _ALLOWED_FIELDS
    }
    safe_fields.update({
        "event": event,
        "request_id": get_request_id(),
    })
    work_outcome_evaluation_logger.info(
        event,
        extra=safe_fields,
    )
