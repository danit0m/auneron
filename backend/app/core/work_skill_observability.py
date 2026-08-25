import logging
from typing import Any

from app.core.observability import get_request_id


work_skill_observability_logger = logging.getLogger(
    "auneron.work.skill_execution"
)

_ALLOWED_FIELDS = frozenset({
    "approval_request_id",
    "attention_required_count",
    "authority_user_id",
    "candidate_count",
    "dispatch_attempts",
    "duplicate",
    "error_code",
    "failure_count",
    "outcome",
    "reconciled_count",
    "retry_after_seconds",
    "skill_invocation_id",
    "skill_version_id",
    "status",
    "work_item_id",
    "work_skill_execution_id",
})


def log_work_skill_execution_event(
    event: str,
    **fields: Any,
) -> None:
    """Emit bounded Work -> Skill metadata only."""
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in _ALLOWED_FIELDS
    }
    safe_fields.update({
        "event": event,
        "request_id": get_request_id(),
    })
    work_skill_observability_logger.info(
        event,
        extra=safe_fields,
    )
