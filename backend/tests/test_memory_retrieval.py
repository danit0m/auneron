from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.memory_errors import MemoryValidationError
from app.core.memory_query import MemoryQuery
from app.core.memory_query import MemoryScope
from app.models.account import Account
from app.models.user import User
from app.repositories.memory_repository import MemoryRepository
from app.services.memory_service import MemoryService


CURSOR_SECRET = "retrieval-test-secret-with-at-least-32-bytes"
NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def _service(db_session: Session) -> MemoryService:
    return MemoryService(db_session, cursor_secret=CURSOR_SECRET)


def _remember(
    service: MemoryService,
    *,
    title: str,
    scope_type: str = "global",
    account_id: int | None = None,
    subject_user_id: int | None = None,
    memory_type: str = "fact",
    source_type: str = "system",
    importance: str = "0.500",
    confidence: str = "0.800",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> int:
    result = service.remember(
        memory_type=memory_type,
        title=title,
        content=f"Conteúdo de {title}",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
        source_type=source_type,
        source_reference=f"test:retrieval:{title}",
        importance=importance,
        confidence=confidence,
        valid_from=valid_from or NOW - timedelta(days=1),
        valid_until=valid_until,
    )

    return result.memory.id


def _account(db_session: Session, name: str) -> Account:
    account = Account(
        cliente=name,
        valor=Decimal("100.00"),
        vencimento=date(2026, 8, 30),
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()

    return account


def _user(db_session: Session, email: str) -> User:
    user = User(
        name="Usuário Retrieval",
        email=email,
        password_hash="not-used-in-domain-test",
        role="viewer",
        active=True,
    )
    db_session.add(user)
    db_session.commit()

    return user


def test_recall_defaults_to_active_and_temporally_valid(
    db_session: Session,
) -> None:
    service = _service(db_session)
    active_id = _remember(service, title="Ativa")
    future_id = _remember(
        service,
        title="Futura",
        valid_from=NOW + timedelta(days=1),
    )
    expired_id = _remember(
        service,
        title="Encerrada",
        valid_from=NOW - timedelta(days=2),
        valid_until=NOW - timedelta(seconds=1),
    )
    archived_id = _remember(service, title="Arquivada")
    service.archive(archived_id)

    result = service.recall(scope_type="global", as_of=NOW)

    assert [item.id for item in result.items] == [active_id]
    assert future_id not in [item.id for item in result.items]
    assert expired_id not in [item.id for item in result.items]


def test_recall_isolates_global_account_and_user_scopes(
    db_session: Session,
) -> None:
    service = _service(db_session)
    account_a = _account(db_session, "Conta A")
    account_b = _account(db_session, "Conta B")
    user = _user(db_session, "retrieval@example.com")
    global_id = _remember(service, title="Global")
    account_a_id = _remember(
        service,
        title="Conta A",
        scope_type="account",
        account_id=account_a.id,
    )
    _remember(
        service,
        title="Conta B",
        scope_type="account",
        account_id=account_b.id,
    )
    user_id = _remember(
        service,
        title="Usuário",
        scope_type="user",
        subject_user_id=user.id,
    )

    global_result = service.recall(scope_type="global", as_of=NOW)
    account_result = service.recall(
        scope_type="account",
        account_id=account_a.id,
        as_of=NOW,
    )
    user_result = service.recall(
        scope_type="user",
        subject_user_id=user.id,
        as_of=NOW,
    )

    assert [item.id for item in global_result.items] == [global_id]
    assert [item.id for item in account_result.items] == [account_a_id]
    assert [item.id for item in user_result.items] == [user_id]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"scope_type": "global", "account_id": 1},
        {"scope_type": "account"},
        {"scope_type": "account", "account_id": 0},
        {"scope_type": "user"},
        {"scope_type": "user", "subject_user_id": -1},
    ],
)
def test_recall_rejects_missing_or_inconsistent_scope(
    db_session: Session,
    arguments: dict[str, object],
) -> None:
    service = _service(db_session)

    with pytest.raises((TypeError, MemoryValidationError)):
        service.recall(**arguments)


def test_recall_applies_domain_filters(
    db_session: Session,
) -> None:
    service = _service(db_session)
    expected_id = _remember(
        service,
        title="Decisão relevante",
        memory_type="decision",
        source_type="derived",
        importance="0.900",
        confidence="0.950",
    )
    _remember(
        service,
        title="Fato fraco",
        memory_type="fact",
        source_type="system",
        importance="0.200",
        confidence="0.300",
    )

    result = service.recall(
        scope_type="global",
        memory_types=["DECISION"],
        source_types=["DERIVED"],
        min_importance="0.800",
        min_confidence="0.900",
        as_of=NOW,
    )

    assert [item.id for item in result.items] == [expected_id]


def test_recall_default_order_is_deterministic(
    db_session: Session,
) -> None:
    service = _service(db_session)
    low_id = _remember(
        service,
        title="Baixa",
        importance="0.100",
        confidence="1.000",
    )
    first_high_id = _remember(
        service,
        title="Alta antiga",
        importance="0.900",
        confidence="0.800",
        valid_from=NOW - timedelta(days=2),
    )
    second_high_id = _remember(
        service,
        title="Alta nova",
        importance="0.900",
        confidence="0.800",
        valid_from=NOW - timedelta(days=1),
    )

    result = service.recall(scope_type="global", as_of=NOW)

    assert [item.id for item in result.items] == [
        second_high_id,
        first_high_id,
        low_id,
    ]


def test_recall_supports_explicit_historical_status(
    db_session: Session,
) -> None:
    service = _service(db_session)
    archived_id = _remember(service, title="Histórica")
    service.archive(archived_id, reason="Concluída")

    result = service.recall(
        scope_type="global",
        statuses=["archived"],
        as_of=NOW,
    )

    assert [item.id for item in result.items] == [archived_id]


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_recall_rejects_invalid_limit(
    db_session: Session,
    limit: object,
) -> None:
    service = _service(db_session)

    with pytest.raises(MemoryValidationError):
        service.recall(scope_type="global", limit=limit)  # type: ignore[arg-type]


def test_repository_count_uses_same_retrieval_filters(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _remember(service, title="Primeira", importance="0.800")
    _remember(service, title="Segunda", importance="0.900")
    _remember(service, title="Ignorada", importance="0.200")
    query = MemoryQuery(
        scope=MemoryScope("global"),
        memory_types=(),
        statuses=("active",),
        source_types=(),
        memory_key=None,
        min_importance=Decimal("0.800"),
        min_confidence=None,
        valid_at=NOW,
        created_after=None,
        created_before=None,
        text_query=None,
        sort="importance",
        limit=20,
    )

    assert MemoryRepository(db_session).count(query) == 2
