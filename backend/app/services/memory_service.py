import json
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.memory_errors import MemoryConflictError
from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryValidationError
from app.models.memory import MemoryItem
from app.repositories.memory_repository import MemoryRepository


MEMORY_TYPES = frozenset({
    "fact",
    "event",
    "observation",
    "decision",
    "summary",
})

SCOPE_TYPES = frozenset({
    "global",
    "account",
    "user",
})

SOURCE_TYPES = frozenset({
    "database",
    "upload",
    "user",
    "agent",
    "system",
    "api",
    "derived",
})

MEMORY_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,254}$"
)

ACTIVE_KEY_CONSTRAINTS = frozenset({
    "uq_memory_items_active_global_key",
    "uq_memory_items_active_account_key",
    "uq_memory_items_active_user_key",
})

MAX_CONTEXT_BYTES = 32 * 1024
MAX_CONTEXT_DEPTH = 5


@dataclass(frozen=True)
class RememberResult:
    memory: MemoryItem
    created: bool
    duplicate: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(
    value: str,
    *,
    field_name: str,
    max_length: int | None = None,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise MemoryValidationError(
            f"{field_name} não pode ser vazio."
        )

    if (
        max_length is not None
        and len(normalized) > max_length
    ):
        raise MemoryValidationError(
            f"{field_name} excede {max_length} caracteres."
        )

    return normalized


def _score(
    value: Decimal | float | int | str,
    *,
    field_name: str,
) -> Decimal:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MemoryValidationError(
            f"{field_name} inválido."
        ) from error

    if not normalized.is_finite():
        raise MemoryValidationError(
            f"{field_name} deve ser finito."
        )

    if (
        normalized < Decimal("0")
        or normalized > Decimal("1")
    ):
        raise MemoryValidationError(
            f"{field_name} deve estar entre 0 e 1."
        )

    return normalized.quantize(
        Decimal("0.001")
    )


def _aware_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MemoryValidationError(
            f"{field_name} deve possuir timezone."
        )

    return value


def _normalized_memory_key(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip().lower()

    if not MEMORY_KEY_PATTERN.fullmatch(
        normalized
    ):
        raise MemoryValidationError(
            "memory_key inválida."
        )

    return normalized


def _json_depth(
    value: Any,
    *,
    current_depth: int = 1,
) -> int:
    if isinstance(value, dict):
        if not value:
            return current_depth

        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
            )
            for child in value.values()
        )

    if isinstance(value, list):
        if not value:
            return current_depth

        return max(
            _json_depth(
                child,
                current_depth=current_depth + 1,
            )
            for child in value
        )

    return current_depth


def _normalized_context(
    context_data: dict[str, Any] | None,
) -> dict[str, Any]:
    if context_data is None:
        return {}

    if not isinstance(context_data, dict):
        raise MemoryValidationError(
            "context_data deve ser um objeto JSON."
        )

    try:
        serialized = json.dumps(
            context_data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as error:
        raise MemoryValidationError(
            "context_data não é serializável em JSON."
        ) from error

    if len(serialized.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise MemoryValidationError(
            "context_data excede 32 KB."
        )

    if _json_depth(context_data) > MAX_CONTEXT_DEPTH:
        raise MemoryValidationError(
            "context_data excede profundidade JSON 5."
        )

    return dict(context_data)


def _constraint_name(
    error: IntegrityError,
) -> str | None:
    original = getattr(
        error,
        "orig",
        None,
    )
    diagnostic = getattr(
        original,
        "diag",
        None,
    )

    return getattr(
        diagnostic,
        "constraint_name",
        None,
    )


class MemoryService:
    """
    Fronteira transacional do Memory System.

    21C.2 implementa a fundação de remember/get. RBAC,
    recall avançado, evidence e lifecycle completo entram
    nas fases subsequentes já congeladas na arquitetura.
    """

    def __init__(
        self,
        db: Session,
        repository: MemoryRepository | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else MemoryRepository(db)
        )

    def remember(
        self,
        *,
        memory_type: str,
        title: str,
        content: str,
        scope_type: str,
        source_type: str,
        source_reference: str,
        confidence: Decimal | float | int | str,
        memory_key: str | None = None,
        account_id: int | None = None,
        subject_user_id: int | None = None,
        created_by_user_id: int | None = None,
        importance: Decimal | float | int | str = (
            Decimal("0.500")
        ),
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        context_data: dict[str, Any] | None = None,
    ) -> RememberResult:
        normalized = self._normalize_remember(
            memory_type=memory_type,
            title=title,
            content=content,
            scope_type=scope_type,
            source_type=source_type,
            source_reference=source_reference,
            confidence=confidence,
            memory_key=memory_key,
            account_id=account_id,
            subject_user_id=subject_user_id,
            created_by_user_id=created_by_user_id,
            importance=importance,
            valid_from=valid_from,
            valid_until=valid_until,
            context_data=context_data,
        )

        existing = self._find_existing(
            normalized
        )

        if existing is not None:
            return self._existing_result(
                existing,
                normalized,
            )

        memory = MemoryItem(
            **normalized,
            status="active",
            status_reason=None,
            status_changed_at=_utc_now(),
        )

        try:
            self.repository.add_memory(
                memory
            )
            self.db.commit()
            self.db.refresh(memory)
        except IntegrityError as error:
            self.db.rollback()

            if (
                normalized["memory_key"] is not None
                and _constraint_name(error)
                in ACTIVE_KEY_CONSTRAINTS
            ):
                concurrent = self._find_existing(
                    normalized
                )

                if concurrent is not None:
                    return self._existing_result(
                        concurrent,
                        normalized,
                    )

                raise MemoryConflictError(
                    "Conflito concorrente de memory_key."
                ) from error

            raise MemoryValidationError(
                "A memória viola uma restrição "
                "de integridade."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return RememberResult(
            memory=memory,
            created=True,
            duplicate=False,
        )

    def get(
        self,
        memory_id: int,
    ) -> MemoryItem:
        if memory_id <= 0:
            raise MemoryValidationError(
                "memory_id inválido."
            )

        memory = self.repository.get_by_id(
            memory_id
        )

        if memory is None:
            raise MemoryNotFoundError(
                "Memória não encontrada."
            )

        return memory

    def _find_existing(
        self,
        normalized: dict[str, Any],
    ) -> MemoryItem | None:
        memory_key = normalized[
            "memory_key"
        ]

        if memory_key is None:
            return None

        return self.repository.find_active_by_key(
            scope_type=normalized["scope_type"],
            memory_key=memory_key,
            account_id=normalized["account_id"],
            subject_user_id=(
                normalized["subject_user_id"]
            ),
        )

    def _existing_result(
        self,
        existing: MemoryItem,
        normalized: dict[str, Any],
    ) -> RememberResult:
        if self._is_equivalent(
            existing,
            normalized,
        ):
            return RememberResult(
                memory=existing,
                created=False,
                duplicate=True,
            )

        raise MemoryConflictError(
            "Já existe memória ativa diferente "
            "para esta memory_key e escopo."
        )

    @staticmethod
    def _is_equivalent(
        existing: MemoryItem,
        normalized: dict[str, Any],
    ) -> bool:
        existing_context = (
            existing.context_data or {}
        )

        return (
            existing.memory_type
            == normalized["memory_type"]
            and existing.title
            == normalized["title"]
            and existing.content
            == normalized["content"]
            and existing.memory_key
            == normalized["memory_key"]
            and existing.scope_type
            == normalized["scope_type"]
            and existing.account_id
            == normalized["account_id"]
            and existing.subject_user_id
            == normalized["subject_user_id"]
            and existing.importance
            == normalized["importance"]
            and existing.confidence
            == normalized["confidence"]
            and existing.valid_until
            == normalized["valid_until"]
            and existing.source_type
            == normalized["source_type"]
            and existing.source_reference
            == normalized["source_reference"]
            and existing_context
            == normalized["context_data"]
        )

    @staticmethod
    def _normalize_remember(
        *,
        memory_type: str,
        title: str,
        content: str,
        scope_type: str,
        source_type: str,
        source_reference: str,
        confidence: Decimal | float | int | str,
        memory_key: str | None,
        account_id: int | None,
        subject_user_id: int | None,
        created_by_user_id: int | None,
        importance: Decimal | float | int | str,
        valid_from: datetime | None,
        valid_until: datetime | None,
        context_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_memory_type = (
            memory_type.strip().lower()
        )

        if normalized_memory_type not in MEMORY_TYPES:
            raise MemoryValidationError(
                "memory_type inválido."
            )

        normalized_scope = (
            scope_type.strip().lower()
        )

        if normalized_scope not in SCOPE_TYPES:
            raise MemoryValidationError(
                "scope_type inválido."
            )

        if normalized_scope == "global":
            if (
                account_id is not None
                or subject_user_id is not None
            ):
                raise MemoryValidationError(
                    "Escopo global não aceita "
                    "account_id/subject_user_id."
                )
        elif normalized_scope == "account":
            if (
                account_id is None
                or subject_user_id is not None
            ):
                raise MemoryValidationError(
                    "Escopo account exige somente "
                    "account_id."
                )
        elif (
            subject_user_id is None
            or account_id is not None
        ):
            raise MemoryValidationError(
                "Escopo user exige somente "
                "subject_user_id."
            )

        normalized_source_type = (
            source_type.strip().lower()
        )

        if normalized_source_type not in SOURCE_TYPES:
            raise MemoryValidationError(
                "source_type inválido."
            )

        normalized_valid_from = (
            _utc_now()
            if valid_from is None
            else _aware_datetime(
                valid_from,
                field_name="valid_from",
            )
        )

        normalized_valid_until = (
            None
            if valid_until is None
            else _aware_datetime(
                valid_until,
                field_name="valid_until",
            )
        )

        if (
            normalized_valid_until is not None
            and normalized_valid_until
            <= normalized_valid_from
        ):
            raise MemoryValidationError(
                "valid_until deve ser posterior "
                "a valid_from."
            )

        return {
            "memory_type": normalized_memory_type,
            "title": _required_text(
                title,
                field_name="title",
                max_length=200,
            ),
            "content": _required_text(
                content,
                field_name="content",
                max_length=10000,
            ),
            "memory_key": _normalized_memory_key(
                memory_key
            ),
            "scope_type": normalized_scope,
            "account_id": account_id,
            "subject_user_id": subject_user_id,
            "created_by_user_id": (
                created_by_user_id
            ),
            "importance": _score(
                importance,
                field_name="importance",
            ),
            "confidence": _score(
                confidence,
                field_name="confidence",
            ),
            "valid_from": normalized_valid_from,
            "valid_until": normalized_valid_until,
            "source_type": normalized_source_type,
            "source_reference": _required_text(
                source_reference,
                field_name="source_reference",
                max_length=500,
            ),
            "supersedes_memory_id": None,
            "context_data": _normalized_context(
                context_data
            ),
        }
