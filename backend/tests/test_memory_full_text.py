from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.memory_errors import InvalidCursorError
from app.core.memory_errors import MemoryValidationError
from app.database.database import Base
from app.models.account import Account
from app.services.memory_service import MemoryService


CURSOR_SECRET = "full-text-test-secret-with-at-least-32-bytes"
NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def _service(db_session: Session) -> MemoryService:
    return MemoryService(
        db_session,
        cursor_secret=CURSOR_SECRET,
    )


def _remember(
    service: MemoryService,
    *,
    title: str,
    content: str,
    importance: str = "0.500",
    scope_type: str = "global",
    account_id: int | None = None,
) -> int:
    result = service.remember(
        memory_type="fact",
        title=title,
        content=content,
        scope_type=scope_type,
        account_id=account_id,
        source_type="system",
        source_reference=f"test:full-text:{title}",
        importance=importance,
        confidence="0.900",
        valid_from=NOW - timedelta(days=1),
    )

    return result.memory.id


def test_full_text_uses_portuguese_stemming(
    db_session: Session,
) -> None:
    service = _service(db_session)
    expected_id = _remember(
        service,
        title="Pagamento vencido",
        content="A obrigação financeira está pendente.",
    )

    result = service.recall(
        scope_type="global",
        text_query="pagamentos vencidos",
        as_of=NOW,
    )

    assert [item.id for item in result.items] == [expected_id]


def test_full_text_searches_title_and_content(
    db_session: Session,
) -> None:
    service = _service(db_session)
    title_id = _remember(
        service,
        title="Renegociação aprovada",
        content="Condição registrada.",
    )
    content_id = _remember(
        service,
        title="Acordo financeiro",
        content="A renegociação foi confirmada.",
    )

    result = service.recall(
        scope_type="global",
        text_query="renegociação",
        as_of=NOW,
    )

    assert {item.id for item in result.items} == {
        title_id,
        content_id,
    }


def test_relevance_ranks_stronger_match_first(
    db_session: Session,
) -> None:
    service = _service(db_session)
    weak_id = _remember(
        service,
        title="Pagamento",
        content="Registro financeiro comum.",
    )
    strong_id = _remember(
        service,
        title="Pagamento pagamento pagamento",
        content="Pagamento confirmado.",
    )

    result = service.recall(
        scope_type="global",
        text_query="pagamento",
        as_of=NOW,
    )

    assert [item.id for item in result.items] == [
        strong_id,
        weak_id,
    ]


def test_full_text_preserves_scope_isolation(
    db_session: Session,
) -> None:
    service = _service(db_session)
    account = Account(
        cliente="Conta Full Text",
        valor=Decimal("100.00"),
        vencimento=date(2026, 8, 30),
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    global_id = _remember(
        service,
        title="Crédito global",
        content="Política global de crédito.",
    )
    account_id = _remember(
        service,
        title="Crédito da conta",
        content="Política específica de crédito.",
        scope_type="account",
        account_id=account.id,
    )

    global_result = service.recall(
        scope_type="global",
        text_query="crédito",
        as_of=NOW,
    )
    account_result = service.recall(
        scope_type="account",
        account_id=account.id,
        text_query="crédito",
        as_of=NOW,
    )

    assert [item.id for item in global_result.items] == [global_id]
    assert [item.id for item in account_result.items] == [account_id]


def test_relevance_cursor_has_no_skip_or_duplicate(
    db_session: Session,
) -> None:
    service = _service(db_session)
    expected_ids = []

    for index in range(7):
        expected_ids.append(
            _remember(
                service,
                title=f"Cobrança {index}",
                content="Cobrança pendente.",
            )
        )

    expected_ids.reverse()
    observed: list[int] = []
    cursor = None

    while True:
        result = service.recall(
            scope_type="global",
            text_query="cobrança",
            as_of=NOW if cursor is None else None,
            limit=3,
            cursor=cursor,
        )
        observed.extend(item.id for item in result.items)

        if not result.has_more:
            break

        assert result.next_cursor is not None
        cursor = result.next_cursor

    assert observed == expected_ids
    assert len(observed) == len(set(observed))


def test_relevance_cursor_is_bound_to_text_query(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _remember(
        service,
        title="Pagamento um",
        content="Pagamento pendente.",
    )
    _remember(
        service,
        title="Pagamento dois",
        content="Pagamento confirmado.",
    )
    first = service.recall(
        scope_type="global",
        text_query="pagamento",
        as_of=NOW,
        limit=1,
    )

    with pytest.raises(InvalidCursorError, match="incompatível"):
        service.recall(
            scope_type="global",
            text_query="saldo",
            cursor=first.next_cursor,
        )


def test_text_filter_allows_explicit_importance_sort(
    db_session: Session,
) -> None:
    service = _service(db_session)
    low_id = _remember(
        service,
        title="Risco risco risco",
        content="Risco elevado.",
        importance="0.100",
    )
    high_id = _remember(
        service,
        title="Risco",
        content="Registro de risco.",
        importance="0.900",
    )

    result = service.recall(
        scope_type="global",
        text_query="risco",
        sort="importance",
        as_of=NOW,
    )

    assert [item.id for item in result.items] == [high_id, low_id]


def test_full_text_gin_index_is_present(
    db_session: Session,
) -> None:
    definition = db_session.execute(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'memory_items'
              AND indexname = (
                  'ix_memory_items_search_portuguese_gin'
              )
            """
        )
    ).scalar_one()
    normalized = definition.lower()

    assert " using gin " in normalized
    assert "to_tsvector" in normalized
    assert "portuguese" in normalized
    assert "title" in normalized
    assert "content" in normalized


def test_full_text_index_matches_alembic_metadata(
    db_session: Session,
) -> None:
    context = MigrationContext.configure(
        db_session.connection()
    )
    differences = compare_metadata(
        context,
        Base.metadata,
    )
    relevant_differences = [
        difference
        for difference in differences
        if "ix_memory_items_search_portuguese_gin"
        in repr(difference)
    ]

    assert relevant_differences == []


def test_full_text_query_can_use_gin_index(
    db_session: Session,
) -> None:
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = db_session.execute(
        text(
            """
            EXPLAIN (COSTS OFF)
            SELECT id
            FROM memory_items
            WHERE to_tsvector(
                'portuguese'::regconfig,
                coalesce(title, '') || ' ' ||
                coalesce(content, '')
            ) @@ websearch_to_tsquery(
                'portuguese'::regconfig,
                'pagamento'
            )
            """
        )
    ).scalars().all()
    plan_text = "\n".join(plan)

    assert "ix_memory_items_search_portuguese_gin" in plan_text


@pytest.mark.parametrize(
    "text_query",
    [
        " ",
        "x" * 501,
    ],
)
def test_full_text_rejects_invalid_query(
    db_session: Session,
    text_query: str,
) -> None:
    service = _service(db_session)

    with pytest.raises(MemoryValidationError):
        service.recall(
            scope_type="global",
            text_query=text_query,
        )


def test_full_text_does_not_search_evidence_body(
    db_session: Session,
) -> None:
    service = _service(db_session)
    memory_id = _remember(
        service,
        title="Documento comum",
        content="Conteúdo sem a expressão procurada.",
    )
    service.add_evidence(
        memory_id,
        relation="supports",
        source_type="system",
        source_reference="test:full-text:evidence",
        evidence_text="ultrassecreto",
    )

    result = service.recall(
        scope_type="global",
        text_query="ultrassecreto",
        as_of=NOW,
    )

    assert result.items == ()
