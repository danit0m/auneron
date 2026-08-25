import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any

from app.core.skill_errors import SkillValidationError


WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL = "work_learning_v1"
MAX_WORK_LEARNING_CONTEXT_ITEMS = 10
MAX_WORK_LEARNING_CONTEXT_BYTES = 16 * 1024
MAX_DATABASE_ID = (1 << 63) - 1

WORK_LEARNING_CONTEXT_ITEM_FIELDS = (
    "memory_id",
    "source_work_item_id",
    "work_skill_execution_id",
    "skill_version_id",
    "terminal_status",
    "evaluation_code",
    "learning_signal",
    "observed_at",
)

_DETERMINISTIC_MAPPING = {
    "succeeded": (
        "execution_succeeded",
        "positive",
    ),
    "failed": (
        "execution_failed",
        "negative",
    ),
    "timed_out": (
        "execution_timed_out",
        "negative",
    ),
    "cancelled": (
        "execution_cancelled",
        "neutral",
    ),
}


@dataclass(frozen=True)
class WorkLearningRuntimeContext:
    protocol: str
    payload: dict[str, Any]
    canonical_bytes: bytes
    digest: str


def _positive_database_id(
    value: Any,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_DATABASE_ID
    ):
        raise SkillValidationError(
            f"{field_name} inválido no runtime context."
        )
    return value


def _observed_at_text(value: Any) -> str:
    if isinstance(value, datetime):
        observed_at = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise SkillValidationError(
                "observed_at inválido no runtime context."
            )
        try:
            observed_at = datetime.fromisoformat(
                normalized.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise SkillValidationError(
                "observed_at inválido no runtime context."
            ) from error
    else:
        raise SkillValidationError(
            "observed_at inválido no runtime context."
        )

    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise SkillValidationError(
            "observed_at deve possuir timezone no runtime context."
        )

    return (
        observed_at.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _normalize_item(
    value: Any,
    *,
    expected_skill_version_id: int | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillValidationError(
            "Item de runtime context deve ser objeto."
        )

    keys = set(value.keys())
    expected_keys = set(WORK_LEARNING_CONTEXT_ITEM_FIELDS)
    if keys != expected_keys:
        raise SkillValidationError(
            "Item de runtime context possui campos inválidos."
        )

    memory_id = _positive_database_id(
        value["memory_id"],
        field_name="memory_id",
    )
    source_work_item_id = _positive_database_id(
        value["source_work_item_id"],
        field_name="source_work_item_id",
    )
    work_skill_execution_id = _positive_database_id(
        value["work_skill_execution_id"],
        field_name="work_skill_execution_id",
    )
    skill_version_id = _positive_database_id(
        value["skill_version_id"],
        field_name="skill_version_id",
    )

    if (
        expected_skill_version_id is not None
        and skill_version_id != expected_skill_version_id
    ):
        raise SkillValidationError(
            "Runtime context pertence a outra versão de skill."
        )

    terminal_status = value["terminal_status"]
    evaluation_code = value["evaluation_code"]
    learning_signal = value["learning_signal"]

    if not all(
        isinstance(item, str)
        for item in (
            terminal_status,
            evaluation_code,
            learning_signal,
        )
    ):
        raise SkillValidationError(
            "Mapeamento determinístico inválido no runtime context."
        )

    expected_mapping = _DETERMINISTIC_MAPPING.get(
        terminal_status
    )
    if expected_mapping != (
        evaluation_code,
        learning_signal,
    ):
        raise SkillValidationError(
            "Mapeamento determinístico inválido no runtime context."
        )

    return {
        "memory_id": memory_id,
        "source_work_item_id": source_work_item_id,
        "work_skill_execution_id": work_skill_execution_id,
        "skill_version_id": skill_version_id,
        "terminal_status": terminal_status,
        "evaluation_code": evaluation_code,
        "learning_signal": learning_signal,
        "observed_at": _observed_at_text(
            value["observed_at"]
        ),
    }


def normalize_work_learning_runtime_context(
    value: Any,
    *,
    expected_skill_version_id: int | None = None,
) -> WorkLearningRuntimeContext:
    if expected_skill_version_id is not None:
        expected_skill_version_id = _positive_database_id(
            expected_skill_version_id,
            field_name="expected_skill_version_id",
        )

    if not isinstance(value, Mapping):
        raise SkillValidationError(
            "runtime_context deve ser objeto."
        )

    if set(value.keys()) != {"protocol", "items"}:
        raise SkillValidationError(
            "runtime_context possui campos inválidos."
        )

    protocol = value["protocol"]
    if protocol != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL:
        raise SkillValidationError(
            "runtime_context protocol inválido."
        )

    items = value["items"]
    if not isinstance(items, list):
        raise SkillValidationError(
            "runtime_context items deve ser lista."
        )
    if len(items) > MAX_WORK_LEARNING_CONTEXT_ITEMS:
        raise SkillValidationError(
            "runtime_context excede 10 itens."
        )

    normalized_items = [
        _normalize_item(
            item,
            expected_skill_version_id=(
                expected_skill_version_id
            ),
        )
        for item in items
    ]

    payload = {
        "protocol": WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
        "items": normalized_items,
    }

    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    if len(serialized) > MAX_WORK_LEARNING_CONTEXT_BYTES:
        raise SkillValidationError(
            "runtime_context excede 16384 bytes."
        )

    return WorkLearningRuntimeContext(
        protocol=WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
        payload=payload,
        canonical_bytes=serialized,
        digest=hashlib.sha256(serialized).hexdigest(),
    )
