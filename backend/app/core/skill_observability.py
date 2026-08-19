import logging
from typing import Any

from app.core.observability import get_request_id


skill_observability_logger = logging.getLogger(
    "auneron.skill.runtime"
)

_ALLOWED_FIELDS = frozenset({
    "actor_type",
    "duplicate",
    "duration_ms",
    "error_code",
    "invocation_id",
    "output_bytes",
    "skill_version_id",
    "status",
})


def log_skill_runtime_event(
    event: str,
    **fields: Any,
) -> None:
    """
    Emit bounded Skill runtime metadata only.

    Payloads, idempotency keys, actor references, credentials, raw
    exceptions and handler output are intentionally outside this API.
    """
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in _ALLOWED_FIELDS
    }
    safe_fields.update({
        "event": event,
        "request_id": get_request_id(),
    })

    skill_observability_logger.info(
        event,
        extra=safe_fields,
    )
