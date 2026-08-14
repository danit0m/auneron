import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.memory_cursor import MemoryCursorCodec
from app.core.memory_errors import InvalidCursorError
from app.core.memory_errors import MemoryConflictError
from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryStateError
from app.core.memory_errors import MemoryValidationError
from app.core.memory_query import MemoryQuery
from app.core.memory_query import MemoryScope
from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem
from app.repositories.memory_repository import MemoryRepository
from app.repositories.memory_repository import MemorySearchRow


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

EVIDENCE_RELATIONS = frozenset({
    "supports",
    "contradicts",
    "context",
})

MEMORY_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,254}$"
)

ACTIVE_KEY_CONSTRAINTS = frozenset({
    "uq_memory_items_active_global_key",
    "uq_memory_items_active_account_key",
    "uq_memory_items_active_user_key",
})

EVIDENCE_HASH_CONSTRAINT = (
    "uq_memory_evidence_memory_hash"
)

MAX_CONTEXT_BYTES = 32 * 1024
MAX_CONTEXT_DEPTH = 5
MAX_EVIDENCE_PER_CREATE = 20
MAX_EXPIRATION_BATCH = 100
MAX_RECALL_LIMIT = 100
MEMORY_STATUSES = frozenset({
    "active",
    "superseded",
    "expired",
    "invalidated",
    "archived",
})
MEMORY_SORTS = frozenset({
    "relevance",
    "newest",
    "oldest",
    "importance",
    "confidence",
})


@dataclass(frozen=True)
class RememberResult:
    memory: MemoryItem
    created: bool
    duplicate: bool
    evidence: tuple[MemoryEvidence, ...] = ()


@dataclass(frozen=True)
class EvidenceResult:
    evidence: MemoryEvidence
    created: bool
    duplicate: bool


@dataclass(frozen=True)
class EvidenceInput:
    relation: str
    source_type: str
    source_reference: str
    evidence_text: str
    weight: Decimal | float | int | str = Decimal(
        "1.000"
    )
    source_memory_id: int | None = None
    observed_at: datetime | None = None
    created_by_user_id: int | None = None
    context_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class SupersedeResult:
    previous: MemoryItem
    replacement: MemoryItem
    evidence: tuple[MemoryEvidence, ...]


@dataclass(frozen=True)
class RecallResult:
    items: tuple[MemoryItem, ...]
    limit: int
    has_more: bool
    next_cursor: str | None


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


def _evidence_hash(
    normalized: dict[str, Any],
) -> str:
    observed_at = normalized["observed_at"]

    canonical = {
        "context_data": normalized["context_data"],
        "evidence_text": normalized["evidence_text"],
        "observed_at": (
            None
            if observed_at is None
            else observed_at.astimezone(
                timezone.utc
            ).isoformat()
        ),
        "relation": normalized["relation"],
        "source_memory_id": normalized[
            "source_memory_id"
        ],
        "source_reference": normalized[
            "source_reference"
        ],
        "source_type": normalized["source_type"],
        "weight": str(normalized["weight"]),
    }

    serialized = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


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

    21C.2 implementa remember/get. 21C.3 adiciona
    evidence e lifecycle. RBAC e recall avançado entram
    nas fases subsequentes já congeladas na arquitetura.
    """

    def __init__(
        self,
        db: Session,
        repository: MemoryRepository | None = None,
        cursor_secret: str | bytes | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else MemoryRepository(db)
        )
        self._cursor_secret = cursor_secret

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
        evidence: Sequence[EvidenceInput] | None = None,
    ) -> RememberResult:
        evidence_items = self._validate_evidence_items(
            evidence,
            operation="remember",
        )
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
            created_evidence = self._insert_evidence_batch(
                memory.id,
                evidence_items,
            )
            self.db.commit()
            self.db.refresh(memory)

            for item in created_evidence:
                self.db.refresh(item)
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
            evidence=tuple(created_evidence),
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

    def recall(
        self,
        *,
        scope_type: str,
        account_id: int | None = None,
        subject_user_id: int | None = None,
        memory_types: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        memory_key: str | None = None,
        source_types: Sequence[str] | None = None,
        min_importance: Decimal | float | int | str | None = None,
        min_confidence: Decimal | float | int | str | None = None,
        as_of: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        text_query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        sort: str | None = None,
    ) -> RecallResult:
        codec = MemoryCursorCodec(self._get_cursor_secret())
        decoded = None

        if cursor is not None:
            if not isinstance(cursor, str) or not cursor:
                raise InvalidCursorError("Cursor de memória inválido.")

            decoded = codec.decode(cursor)

        normalized_text = self._normalize_text_query(text_query)
        normalized_sort = self._normalize_sort(sort, normalized_text)

        if decoded is not None and decoded.sort != normalized_sort:
            raise InvalidCursorError("Cursor incompatível com a ordenação.")

        normalized_as_of = (
            decoded.valid_at
            if decoded is not None and as_of is None
            else _utc_now() if as_of is None
            else _aware_datetime(as_of, field_name="as_of")
        )
        query = MemoryQuery(
            scope=self._normalize_recall_scope(
                scope_type=scope_type,
                account_id=account_id,
                subject_user_id=subject_user_id,
            ),
            memory_types=self._normalize_filter_values(
                memory_types,
                allowed=MEMORY_TYPES,
                field_name="memory_types",
            ),
            statuses=self._normalize_filter_values(
                ("active",) if statuses is None else statuses,
                allowed=MEMORY_STATUSES,
                field_name="statuses",
                required=True,
            ),
            source_types=self._normalize_filter_values(
                source_types,
                allowed=SOURCE_TYPES,
                field_name="source_types",
            ),
            memory_key=_normalized_memory_key(memory_key),
            min_importance=(
                None
                if min_importance is None
                else _score(min_importance, field_name="min_importance")
            ),
            min_confidence=(
                None
                if min_confidence is None
                else _score(min_confidence, field_name="min_confidence")
            ),
            valid_at=normalized_as_of,
            created_after=(
                None
                if created_after is None
                else _aware_datetime(created_after, field_name="created_after")
            ),
            created_before=(
                None
                if created_before is None
                else _aware_datetime(created_before, field_name="created_before")
            ),
            text_query=normalized_text,
            sort=normalized_sort,
            limit=self._normalize_recall_limit(limit),
        )
        self._validate_created_range(query)
        fingerprint = self._query_fingerprint(query)
        cursor_position = None

        if decoded is not None:
            if not hmac.compare_digest(decoded.fingerprint, fingerprint):
                raise InvalidCursorError("Cursor incompatível com a consulta.")

            cursor_position = self._decode_cursor_position(
                decoded.position,
                query.sort,
            )

        rows = self.repository.search(
            query,
            cursor_position=cursor_position,
        )
        has_more = len(rows) > query.limit
        page_rows = tuple(rows[: query.limit])
        items = tuple(row.memory for row in page_rows)
        next_cursor = None

        if has_more and page_rows:
            next_cursor = codec.encode(
                fingerprint=fingerprint,
                sort=query.sort,
                valid_at=query.valid_at,
                position=self._cursor_position(
                    page_rows[-1],
                    query.sort,
                ),
            )

        return RecallResult(
            items=items,
            limit=query.limit,
            has_more=has_more,
            next_cursor=next_cursor,
        )

    def add_evidence(
        self,
        memory_id: int,
        *,
        relation: str,
        source_type: str,
        source_reference: str,
        evidence_text: str,
        weight: Decimal | float | int | str = (
            Decimal("1.000")
        ),
        source_memory_id: int | None = None,
        observed_at: datetime | None = None,
        created_by_user_id: int | None = None,
        context_data: dict[str, Any] | None = None,
    ) -> EvidenceResult:
        memory = self.get(memory_id)
        normalized = self._normalize_evidence(
            memory_id=memory.id,
            relation=relation,
            source_type=source_type,
            source_reference=source_reference,
            source_memory_id=source_memory_id,
            evidence_text=evidence_text,
            weight=weight,
            observed_at=observed_at,
            created_by_user_id=created_by_user_id,
            context_data=context_data,
        )

        if source_memory_id is not None:
            source_memory = self.repository.get_by_id(
                source_memory_id
            )

            if source_memory is None:
                raise MemoryValidationError(
                    "source_memory_id não encontrada."
                )

        evidence_hash = _evidence_hash(normalized)
        existing = (
            self.repository.find_evidence_by_hash(
                memory_id=memory.id,
                evidence_hash=evidence_hash,
            )
        )

        if existing is not None:
            return EvidenceResult(
                evidence=existing,
                created=False,
                duplicate=True,
            )

        evidence = MemoryEvidence(
            memory_id=memory.id,
            evidence_hash=evidence_hash,
            **normalized,
        )

        try:
            self.repository.insert_evidence(evidence)
            self.db.commit()
            self.db.refresh(evidence)
        except IntegrityError as error:
            self.db.rollback()

            if (
                _constraint_name(error)
                == EVIDENCE_HASH_CONSTRAINT
            ):
                concurrent = (
                    self.repository.find_evidence_by_hash(
                        memory_id=memory.id,
                        evidence_hash=evidence_hash,
                    )
                )

                if concurrent is not None:
                    return EvidenceResult(
                        evidence=concurrent,
                        created=False,
                        duplicate=True,
                    )

            raise MemoryValidationError(
                "A evidência viola uma restrição "
                "de integridade."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return EvidenceResult(
            evidence=evidence,
            created=True,
            duplicate=False,
        )

    def list_evidence(
        self,
        memory_id: int,
    ) -> list[MemoryEvidence]:
        memory = self.get(memory_id)

        return self.repository.list_evidence(
            memory.id
        )

    def history(
        self,
        memory_id: int,
    ) -> tuple[MemoryItem, ...]:
        self._validate_memory_id(memory_id)
        memories = self.repository.list_history_chain(
            memory_id
        )

        if not memories:
            raise MemoryNotFoundError(
                "Memória não encontrada."
            )

        by_id = {
            memory.id: memory
            for memory in memories
        }
        requested = by_id.get(memory_id)

        if requested is None:
            raise MemoryNotFoundError(
                "Memória não encontrada."
            )

        identity = self._history_identity(
            requested
        )

        if any(
            self._history_identity(memory)
            != identity
            for memory in memories
        ):
            raise MemoryStateError(
                "Cadeia de histórico inconsistente."
            )

        oldest = requested
        ancestor_ids: set[int] = set()

        while oldest.supersedes_memory_id is not None:
            if oldest.id in ancestor_ids:
                raise MemoryStateError(
                    "Ciclo na cadeia de histórico."
                )

            ancestor_ids.add(oldest.id)
            parent = by_id.get(
                oldest.supersedes_memory_id
            )

            if parent is None:
                raise MemoryStateError(
                    "Cadeia de histórico incompleta."
                )

            oldest = parent

        children: dict[int, list[MemoryItem]] = {}

        for memory in memories:
            if memory.supersedes_memory_id is None:
                continue

            children.setdefault(
                memory.supersedes_memory_id,
                [],
            ).append(memory)

        ordered: list[MemoryItem] = []
        visited: set[int] = set()
        current = oldest

        while True:
            if current.id in visited:
                raise MemoryStateError(
                    "Ciclo na cadeia de histórico."
                )

            ordered.append(current)
            visited.add(current.id)
            replacements = children.get(
                current.id,
                [],
            )

            if len(replacements) > 1:
                raise MemoryStateError(
                    "Cadeia de histórico ramificada."
                )

            if not replacements:
                break

            current = replacements[0]

        if visited != set(by_id):
            raise MemoryStateError(
                "Cadeia de histórico inconsistente."
            )

        return tuple(ordered)

    def supersede(
        self,
        memory_id: int,
        *,
        reason: str,
        memory_type: str,
        title: str,
        content: str,
        source_type: str,
        source_reference: str,
        confidence: Decimal | float | int | str,
        created_by_user_id: int | None = None,
        importance: Decimal | float | int | str = (
            Decimal("0.500")
        ),
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        context_data: dict[str, Any] | None = None,
        evidence: Sequence[EvidenceInput] | None = None,
    ) -> SupersedeResult:
        self._validate_memory_id(memory_id)
        normalized_reason = _required_text(
            reason,
            field_name="reason",
            max_length=2000,
        )
        evidence_items = self._validate_evidence_items(
            evidence,
            operation="supersede",
        )

        try:
            previous = self.repository.lock_by_id(
                memory_id
            )

            if previous is None:
                raise MemoryNotFoundError(
                    "Memória não encontrada."
                )

            self._require_active(previous)
            normalized = self._normalize_remember(
                memory_type=memory_type,
                title=title,
                content=content,
                scope_type=previous.scope_type,
                source_type=source_type,
                source_reference=source_reference,
                confidence=confidence,
                memory_key=previous.memory_key,
                account_id=previous.account_id,
                subject_user_id=(
                    previous.subject_user_id
                ),
                created_by_user_id=created_by_user_id,
                importance=importance,
                valid_from=valid_from,
                valid_until=valid_until,
                context_data=context_data,
            )
            changed_at = _utc_now()
            self.repository.update_status(
                previous,
                status="superseded",
                reason=normalized_reason,
                changed_at=changed_at,
            )

            normalized["supersedes_memory_id"] = (
                previous.id
            )
            replacement = MemoryItem(
                **normalized,
                status="active",
                status_reason=None,
                status_changed_at=changed_at,
            )
            self.repository.add_memory(replacement)
            created_evidence = (
                self._insert_evidence_batch(
                    replacement.id,
                    evidence_items,
                )
            )
            self.db.commit()
            self.db.refresh(previous)
            self.db.refresh(replacement)

            for item in created_evidence:
                self.db.refresh(item)
        except IntegrityError as error:
            self.db.rollback()

            if (
                _constraint_name(error)
                in ACTIVE_KEY_CONSTRAINTS
            ):
                raise MemoryConflictError(
                    "Conflito concorrente ao substituir memória."
                ) from error

            raise MemoryValidationError(
                "Supersede viola uma restrição "
                "de integridade."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return SupersedeResult(
            previous=previous,
            replacement=replacement,
            evidence=tuple(created_evidence),
        )

    @staticmethod
    def _history_identity(
        memory: MemoryItem,
    ) -> tuple[str, int | None, int | None, str | None]:
        return (
            memory.scope_type,
            memory.account_id,
            memory.subject_user_id,
            memory.memory_key,
        )

    def invalidate(
        self,
        memory_id: int,
        *,
        reason: str,
    ) -> MemoryItem:
        return self._transition(
            memory_id,
            status="invalidated",
            reason=reason,
            reason_required=True,
        )

    def archive(
        self,
        memory_id: int,
        *,
        reason: str | None = None,
    ) -> MemoryItem:
        return self._transition(
            memory_id,
            status="archived",
            reason=reason,
            reason_required=False,
        )

    def expire(
        self,
        memory_id: int,
        *,
        reason: str | None = None,
    ) -> MemoryItem:
        return self._transition(
            memory_id,
            status="expired",
            reason=reason,
            reason_required=False,
        )

    def expire_due_batch(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        if limit <= 0 or limit > MAX_EXPIRATION_BATCH:
            raise MemoryValidationError(
                "limit deve estar entre 1 e 100."
            )

        normalized_as_of = (
            _utc_now()
            if as_of is None
            else _aware_datetime(
                as_of,
                field_name="as_of",
            )
        )
        changed_at = _utc_now()

        try:
            memories = self.repository.expire_due_batch(
                as_of=normalized_as_of,
                limit=limit,
                reason="Validade temporal encerrada.",
                changed_at=changed_at,
            )
            self.db.commit()

            for memory in memories:
                self.db.refresh(memory)
        except Exception:
            self.db.rollback()
            raise

        return memories

    def _get_cursor_secret(self) -> str | bytes:
        if self._cursor_secret is not None:
            return self._cursor_secret

        if settings.api_key is None:
            raise MemoryValidationError(
                "Cursor HMAC indisponível sem API_KEY configurada."
            )

        api_key = settings.api_key.get_secret_value().encode(
            "utf-8"
        )

        return hmac.new(
            api_key,
            b"auneron-memory-cursor-v1",
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _normalize_recall_scope(
        *,
        scope_type: str,
        account_id: int | None,
        subject_user_id: int | None,
    ) -> MemoryScope:
        if not isinstance(scope_type, str):
            raise MemoryValidationError("scope_type inválido.")

        normalized = scope_type.strip().lower()

        if normalized not in SCOPE_TYPES:
            raise MemoryValidationError("scope_type inválido.")

        if account_id is not None and (
            not isinstance(account_id, int)
            or isinstance(account_id, bool)
            or account_id <= 0
        ):
            raise MemoryValidationError("account_id inválido.")

        if subject_user_id is not None and (
            not isinstance(subject_user_id, int)
            or isinstance(subject_user_id, bool)
            or subject_user_id <= 0
        ):
            raise MemoryValidationError("subject_user_id inválido.")

        if normalized == "global":
            if account_id is not None or subject_user_id is not None:
                raise MemoryValidationError(
                    "Escopo global não aceita identificadores."
                )
        elif normalized == "account":
            if account_id is None or subject_user_id is not None:
                raise MemoryValidationError(
                    "Escopo account exige somente account_id."
                )
        elif subject_user_id is None or account_id is not None:
            raise MemoryValidationError(
                "Escopo user exige somente subject_user_id."
            )

        return MemoryScope(
            scope_type=normalized,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )

    @staticmethod
    def _normalize_filter_values(
        values: Sequence[str] | None,
        *,
        allowed: frozenset[str],
        field_name: str,
        required: bool = False,
    ) -> tuple[str, ...]:
        if values is None:
            if required:
                raise MemoryValidationError(f"{field_name} é obrigatório.")

            return ()

        if isinstance(values, (str, bytes)):
            raise MemoryValidationError(
                f"{field_name} deve ser uma sequência."
            )

        normalized: list[str] = []

        for value in values:
            if not isinstance(value, str):
                raise MemoryValidationError(f"{field_name} inválido.")

            item = value.strip().lower()

            if item not in allowed:
                raise MemoryValidationError(f"{field_name} inválido.")

            if item not in normalized:
                normalized.append(item)

        if required and not normalized:
            raise MemoryValidationError(f"{field_name} não pode ser vazio.")

        return tuple(sorted(normalized))

    @staticmethod
    def _normalize_text_query(value: str | None) -> str | None:
        if value is None:
            return None

        if not isinstance(value, str):
            raise MemoryValidationError("text_query inválida.")

        normalized = value.strip()

        if not normalized:
            raise MemoryValidationError("text_query não pode ser vazia.")

        if len(normalized) > 500:
            raise MemoryValidationError("text_query excede 500 caracteres.")

        return normalized

    @staticmethod
    def _normalize_sort(sort: str | None, text_query: str | None) -> str:
        if sort is not None and not isinstance(sort, str):
            raise MemoryValidationError("sort inválido.")

        normalized = (
            "relevance" if text_query is not None else "importance"
        ) if sort is None else sort.strip().lower()

        if normalized not in MEMORY_SORTS:
            raise MemoryValidationError("sort inválido.")

        if normalized == "relevance" and text_query is None:
            raise MemoryValidationError(
                "sort relevance exige text_query."
            )

        return normalized

    @staticmethod
    def _normalize_recall_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise MemoryValidationError("limit inválido.")

        if limit <= 0 or limit > MAX_RECALL_LIMIT:
            raise MemoryValidationError("limit deve estar entre 1 e 100.")

        return limit

    @staticmethod
    def _validate_created_range(query: MemoryQuery) -> None:
        if (
            query.created_after is not None
            and query.created_before is not None
            and query.created_after > query.created_before
        ):
            raise MemoryValidationError(
                "created_after deve ser anterior a created_before."
            )

    @staticmethod
    def _query_fingerprint(query: MemoryQuery) -> str:
        def instant(value: datetime | None) -> str | None:
            if value is None:
                return None

            return value.astimezone(timezone.utc).isoformat()

        payload = {
            "created_after": instant(query.created_after),
            "created_before": instant(query.created_before),
            "memory_key": query.memory_key,
            "memory_types": list(query.memory_types),
            "min_confidence": (
                None
                if query.min_confidence is None
                else str(query.min_confidence)
            ),
            "min_importance": (
                None
                if query.min_importance is None
                else str(query.min_importance)
            ),
            "scope": {
                "account_id": query.scope.account_id,
                "scope_type": query.scope.scope_type,
                "subject_user_id": query.scope.subject_user_id,
            },
            "sort": query.sort,
            "source_types": list(query.source_types),
            "statuses": list(query.statuses),
            "text_query": query.text_query,
            "valid_at": instant(query.valid_at),
        }
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _cursor_position(
        row: MemorySearchRow,
        sort: str,
    ) -> tuple[str, ...]:
        memory = row.memory

        if sort == "relevance":
            if row.relevance is None:
                raise MemoryValidationError(
                    "Resultado textual sem relevância."
                )

            return (
                str(row.relevance),
                str(memory.importance),
                str(memory.confidence),
                memory.valid_from.isoformat(),
                str(memory.id),
            )

        if sort in {"newest", "oldest"}:
            return (
                memory.created_at.isoformat(),
                str(memory.id),
            )

        first = memory.importance
        second = memory.confidence

        if sort == "confidence":
            first, second = second, first

        return (
            str(first),
            str(second),
            memory.valid_from.isoformat(),
            str(memory.id),
        )

    @staticmethod
    def _decode_cursor_position(
        position: tuple[str, ...],
        sort: str,
    ) -> tuple[Any, ...]:
        try:
            if sort == "relevance":
                if len(position) != 5:
                    raise ValueError("invalid position")

                relevance = Decimal(position[0])
                importance = Decimal(position[1])
                confidence = Decimal(position[2])
                valid_from = datetime.fromisoformat(position[3])
                memory_id = int(position[4])

                if (
                    not relevance.is_finite()
                    or not importance.is_finite()
                    or not confidence.is_finite()
                    or valid_from.tzinfo is None
                    or memory_id <= 0
                ):
                    raise ValueError("invalid position")

                return (
                    relevance,
                    importance,
                    confidence,
                    valid_from,
                    memory_id,
                )

            if sort in {"newest", "oldest"}:
                if len(position) != 2:
                    raise ValueError("invalid position")

                created_at = datetime.fromisoformat(position[0])
                memory_id = int(position[1])

                if created_at.tzinfo is None or memory_id <= 0:
                    raise ValueError("invalid position")

                return created_at, memory_id

            if len(position) != 4:
                raise ValueError("invalid position")

            first = Decimal(position[0])
            second = Decimal(position[1])
            valid_from = datetime.fromisoformat(position[2])
            memory_id = int(position[3])

            if (
                not first.is_finite()
                or not second.is_finite()
                or valid_from.tzinfo is None
                or memory_id <= 0
            ):
                raise ValueError("invalid position")

            return first, second, valid_from, memory_id
        except (ValueError, InvalidOperation) as error:
            raise InvalidCursorError("Cursor de memória inválido.") from error

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

    def _transition(
        self,
        memory_id: int,
        *,
        status: str,
        reason: str | None,
        reason_required: bool,
    ) -> MemoryItem:
        self._validate_memory_id(memory_id)
        normalized_reason = self._normalize_reason(
            reason,
            required=reason_required,
        )

        try:
            memory = self.repository.lock_by_id(
                memory_id
            )

            if memory is None:
                raise MemoryNotFoundError(
                    "Memória não encontrada."
                )

            self._require_active(memory)
            self.repository.update_status(
                memory,
                status=status,
                reason=normalized_reason,
                changed_at=_utc_now(),
            )
            self.db.commit()
            self.db.refresh(memory)
        except Exception:
            self.db.rollback()
            raise

        return memory

    def _insert_evidence_batch(
        self,
        memory_id: int,
        evidence: Sequence[EvidenceInput],
    ) -> list[MemoryEvidence]:
        created: list[MemoryEvidence] = []
        seen_hashes: set[str] = set()

        for item in evidence:
            normalized = self._normalize_evidence(
                memory_id=memory_id,
                relation=item.relation,
                source_type=item.source_type,
                source_reference=item.source_reference,
                source_memory_id=item.source_memory_id,
                evidence_text=item.evidence_text,
                weight=item.weight,
                observed_at=item.observed_at,
                created_by_user_id=(
                    item.created_by_user_id
                ),
                context_data=item.context_data,
            )
            self._validate_source_memory(
                memory_id,
                item.source_memory_id,
            )
            evidence_hash = _evidence_hash(normalized)

            if evidence_hash in seen_hashes:
                continue

            seen_hashes.add(evidence_hash)
            evidence_item = MemoryEvidence(
                memory_id=memory_id,
                evidence_hash=evidence_hash,
                **normalized,
            )
            self.repository.insert_evidence(
                evidence_item
            )
            created.append(evidence_item)

        return created

    @staticmethod
    def _validate_evidence_items(
        evidence: Sequence[EvidenceInput] | None,
        *,
        operation: str,
    ) -> tuple[EvidenceInput, ...]:
        items = tuple(evidence or ())

        if len(items) > MAX_EVIDENCE_PER_CREATE:
            raise MemoryValidationError(
                f"{operation} aceita no máximo 20 evidências."
            )

        if not all(
            isinstance(item, EvidenceInput)
            for item in items
        ):
            raise MemoryValidationError(
                "evidence deve conter EvidenceInput."
            )

        return items

    def _validate_source_memory(
        self,
        memory_id: int,
        source_memory_id: int | None,
    ) -> None:
        if source_memory_id is None:
            return

        if source_memory_id == memory_id:
            raise MemoryValidationError(
                "Evidência não pode referenciar "
                "a própria memória."
            )

        source_memory = self.repository.get_by_id(
            source_memory_id
        )

        if source_memory is None:
            raise MemoryValidationError(
                "source_memory_id não encontrada."
            )

    @staticmethod
    def _validate_memory_id(memory_id: int) -> None:
        if memory_id <= 0:
            raise MemoryValidationError(
                "memory_id inválido."
            )

    @staticmethod
    def _require_active(memory: MemoryItem) -> None:
        if memory.status != "active":
            raise MemoryStateError(
                "Somente memória active pode "
                "mudar de lifecycle."
            )

    @staticmethod
    def _normalize_reason(
        reason: str | None,
        *,
        required: bool,
    ) -> str | None:
        if reason is None:
            if required:
                raise MemoryValidationError(
                    "reason é obrigatório."
                )

            return None

        normalized = reason.strip()

        if not normalized:
            if required:
                raise MemoryValidationError(
                    "reason não pode ser vazio."
                )

            return None

        if len(normalized) > 2000:
            raise MemoryValidationError(
                "reason excede 2000 caracteres."
            )

        return normalized

    @staticmethod
    def _normalize_evidence(
        *,
        memory_id: int,
        relation: str,
        source_type: str,
        source_reference: str,
        source_memory_id: int | None,
        evidence_text: str,
        weight: Decimal | float | int | str,
        observed_at: datetime | None,
        created_by_user_id: int | None,
        context_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_relation = relation.strip().lower()

        if normalized_relation not in EVIDENCE_RELATIONS:
            raise MemoryValidationError(
                "relation de evidência inválida."
            )

        normalized_source_type = (
            source_type.strip().lower()
        )

        if normalized_source_type not in SOURCE_TYPES:
            raise MemoryValidationError(
                "source_type de evidência inválido."
            )

        if source_memory_id is not None:
            if source_memory_id <= 0:
                raise MemoryValidationError(
                    "source_memory_id inválida."
                )

            if source_memory_id == memory_id:
                raise MemoryValidationError(
                    "Evidência não pode referenciar "
                    "a própria memória."
                )

        return {
            "relation": normalized_relation,
            "source_type": normalized_source_type,
            "source_reference": _required_text(
                source_reference,
                field_name=(
                    "source_reference da evidência"
                ),
                max_length=500,
            ),
            "source_memory_id": source_memory_id,
            "evidence_text": _required_text(
                evidence_text,
                field_name="evidence_text",
                max_length=10000,
            ),
            "weight": _score(
                weight,
                field_name="weight",
            ),
            "observed_at": (
                None
                if observed_at is None
                else _aware_datetime(
                    observed_at,
                    field_name="observed_at",
                )
            ),
            "created_by_user_id": created_by_user_id,
            "context_data": _normalized_context(
                context_data
            ),
        }

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
