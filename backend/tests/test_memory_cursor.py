from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.orm import Session

from app.core.memory_errors import InvalidCursorError
from app.services.memory_service import MemoryService


CURSOR_SECRET = "cursor-test-secret-with-at-least-32-bytes"
NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def _service(db_session: Session) -> MemoryService:
    return MemoryService(db_session, cursor_secret=CURSOR_SECRET)


def _seed(service: MemoryService, count: int = 7) -> list[int]:
    ids = []

    for index in range(count):
        result = service.remember(
            memory_type="fact",
            title=f"Memória {index}",
            content=f"Conteúdo {index}",
            scope_type="global",
            source_type="system",
            source_reference=f"test:cursor:{index}",
            importance="0.800",
            confidence="0.900",
            valid_from=NOW - timedelta(days=1),
        )
        ids.append(result.memory.id)

    return ids


def test_cursor_paginates_without_skip_or_duplicate(
    db_session: Session,
) -> None:
    service = _service(db_session)
    expected = list(reversed(_seed(service)))
    observed: list[int] = []
    cursor = None

    while True:
        result = service.recall(
            scope_type="global",
            as_of=NOW if cursor is None else None,
            limit=3,
            cursor=cursor,
        )
        observed.extend(item.id for item in result.items)

        if not result.has_more:
            assert result.next_cursor is None
            break

        assert result.next_cursor is not None
        cursor = result.next_cursor

    assert observed == expected
    assert len(observed) == len(set(observed))


def test_cursor_allows_limit_change_without_changing_query(
    db_session: Session,
) -> None:
    service = _service(db_session)
    expected = list(reversed(_seed(service, 5)))
    first = service.recall(
        scope_type="global",
        as_of=NOW,
        limit=2,
    )
    second = service.recall(
        scope_type="global",
        limit=3,
        cursor=first.next_cursor,
    )

    assert [item.id for item in first.items + second.items] == expected


def test_cursor_rejects_tampering(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _seed(service, 2)
    first = service.recall(scope_type="global", as_of=NOW, limit=1)
    assert first.next_cursor is not None
    payload, signature = first.next_cursor.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    tampered = f"{payload}.{replacement}{signature[1:]}"

    with pytest.raises(InvalidCursorError):
        service.recall(scope_type="global", cursor=tampered)


def test_cursor_rejects_incompatible_filters(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _seed(service, 2)
    first = service.recall(scope_type="global", as_of=NOW, limit=1)

    with pytest.raises(InvalidCursorError, match="incompatível"):
        service.recall(
            scope_type="global",
            memory_types=["decision"],
            cursor=first.next_cursor,
        )


def test_cursor_rejects_incompatible_sort(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _seed(service, 2)
    first = service.recall(scope_type="global", as_of=NOW, limit=1)

    with pytest.raises(InvalidCursorError, match="ordenação"):
        service.recall(
            scope_type="global",
            sort="newest",
            cursor=first.next_cursor,
        )


@pytest.mark.parametrize("cursor", ["", "invalid", "a.b.c", "%%%%.%%%%"])
def test_cursor_rejects_malformed_value(
    db_session: Session,
    cursor: str,
) -> None:
    service = _service(db_session)

    with pytest.raises(InvalidCursorError):
        service.recall(scope_type="global", cursor=cursor)


def test_cursor_preserves_original_as_of_between_pages(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _seed(service, 3)
    service.remember(
        memory_type="fact",
        title="Futura",
        content="Ainda não válida",
        scope_type="global",
        source_type="system",
        source_reference="test:cursor:future",
        confidence="0.900",
        valid_from=NOW + timedelta(seconds=1),
    )
    first = service.recall(scope_type="global", as_of=NOW, limit=2)
    second = service.recall(
        scope_type="global",
        cursor=first.next_cursor,
        limit=2,
    )

    assert len(first.items + second.items) == 3
