import logging
from typing import Any

from app.core.observability import get_request_id


approval_observability_logger = logging.getLogger(
    "auneron.approval"
)

_ALLOWED_FIELDS = frozenset({
    "operation",
    "user_id",
    "approval_request_id",
    "skill_version_id",
    "risk_level",
    "status",
    "decision",
    "duplicate",
    "sensitive",
    "error_code",
    "count",
})


def log_approval_event(
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in _ALLOWED_FIELDS
    }
    safe_fields[
        "event"
    ] = event
    safe_fields[
        "request_id"
    ] = get_request_id()

    approval_observability_logger.log(
        level,
        event,
        extra=safe_fields,
    )
