import hashlib
import json
import logging
from typing import Any
from typing import Final


intelligence_logger = logging.getLogger("auneron.intelligence")

# Allowlist: only these keys may reach a log record. Everything else
# (prompt, instructions, input, output, provider messages, credentials)
# is dropped by construction -- not filtered by pattern.
ALLOWED_LOG_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "event",
        "call_id",
        "call_key_digest",
        "state",
        "from_state",
        "to_state",
        "provider",
        "model_profile",
        "purpose",
        "attempt",
        "error_code",
        "error_class",
        "reserved_micro",
        "actual_micro",
        "reserved_tokens",
        "actual_tokens",
        "budget_id",
        "mode",
        "duration_ms",
    }
)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_digest(
    *,
    model_id: str,
    instructions: str,
    input_text: str,
    output_schema: Any,
    max_output_tokens: int,
) -> str:
    """Stable digest of a request; the content itself is never stored."""
    canonical = json.dumps(
        {
            "model_id": model_id,
            "instructions": instructions,
            "input_text": input_text,
            "output_schema": output_schema,
            "max_output_tokens": max_output_tokens,
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return sha256_hex(canonical)


def safe_log_fields(**fields: Any) -> dict[str, Any]:
    """Keeps only allowlisted keys with scalar values."""
    safe: dict[str, Any] = {}

    for key, value in fields.items():
        if key not in ALLOWED_LOG_FIELDS:
            continue

        if isinstance(value, (str, int, float, bool, type(None))):
            safe[key] = value
        else:
            safe[key] = type(value).__name__

    return safe


def safe_error_class(error: BaseException) -> str:
    """Exception class name only -- never str(error), which may echo input."""
    return type(error).__name__[:64]


def log_event(
    level: int,
    event: str,
    **fields: Any,
) -> None:
    intelligence_logger.log(
        level,
        event,
        extra=safe_log_fields(event=event, **fields),
    )
