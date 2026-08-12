from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.memory_errors import MemoryConflictError
from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryValidationError
from app.models.memory import MemoryItem
from app.services.memory_service import MemoryService


def test_remember_creates_global_memory(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    result = service.remember(
        memory_type="fact",
        title="Moeda padrão",
        content="A moeda operacional é BRL.",
        memory_key="company.policy.currency",
        scope_type="global",
        source_type="system",
        source_reference="test:service",
        confidence="0.950",
    )

    assert result.created is True
    assert result.duplicate is False
    assert result.memory.id is not None
    assert result.memory.status == "active"
    assert result.memory.memory_key == (
        "company.policy.currency"
    )


def test_remember_normalizes_memory_key(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    result = service.remember(
        memory_type="FACT",
        title="Idioma",
        content="Português.",
        memory_key=" Company.Preference.Language ",
        scope_type="GLOBAL",
        source_type="SYSTEM",
        source_reference="test:normalize",
        confidence=0.8,
    )

    assert result.memory.memory_key == (
        "company.preference.language"
    )
    assert result.memory.memory_type == "fact"
    assert result.memory.scope_type == "global"
    assert result.memory.source_type == "system"


def test_remember_same_key_and_content_is_idempotent(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    first = service.remember(
        memory_type="fact",
        title="Timezone",
        content="America/Sao_Paulo",
        memory_key="company.policy.timezone",
        scope_type="global",
        source_type="system",
        source_reference="test:duplicate",
        confidence="0.900",
    )

    second = service.remember(
        memory_type="fact",
        title="Timezone",
        content="America/Sao_Paulo",
        memory_key="company.policy.timezone",
        scope_type="global",
        source_type="system",
        source_reference="test:duplicate",
        confidence="0.900",
    )

    assert first.created is True
    assert second.created is False
    assert second.duplicate is True
    assert second.memory.id == first.memory.id

    count = db_session.execute(
        select(
            func.count(MemoryItem.id)
        )
    ).scalar_one()

    assert count == 1


def test_remember_conflicting_active_key_raises(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    service.remember(
        memory_type="fact",
        title="Política",
        content="Versão A",
        memory_key="company.policy.credit",
        scope_type="global",
        source_type="system",
        source_reference="test:conflict",
        confidence="0.800",
    )

    with pytest.raises(
        MemoryConflictError
    ):
        service.remember(
            memory_type="fact",
            title="Política",
            content="Versão B",
            memory_key="company.policy.credit",
            scope_type="global",
            source_type="system",
            source_reference="test:conflict",
            confidence="0.800",
        )


def test_remember_requires_exact_account_scope(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError
    ):
        service.remember(
            memory_type="fact",
            title="Conta",
            content="Conteúdo",
            scope_type="account",
            source_type="system",
            source_reference="test:scope",
            confidence="0.8",
        )


def test_remember_rejects_naive_valid_from(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError
    ):
        service.remember(
            memory_type="fact",
            title="Tempo",
            content="Conteúdo",
            scope_type="global",
            source_type="system",
            source_reference="test:time",
            confidence="0.8",
            valid_from=datetime(
                2026,
                8,
                11,
                12,
                0,
                0,
            ),
        )


def test_remember_accepts_aware_valid_from(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    result = service.remember(
        memory_type="fact",
        title="Tempo",
        content="Conteúdo",
        scope_type="global",
        source_type="system",
        source_reference="test:aware-time",
        confidence="0.8",
        valid_from=datetime(
            2026,
            8,
            11,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result.created is True


def test_get_returns_existing_memory(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    created = service.remember(
        memory_type="observation",
        title="Teste",
        content="Observação.",
        scope_type="global",
        source_type="system",
        source_reference="test:get",
        confidence="0.700",
    )

    loaded = service.get(
        created.memory.id
    )

    assert loaded.id == created.memory.id


def test_get_missing_memory_raises(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryNotFoundError
    ):
        service.get(999999999)


def test_get_rejects_invalid_id(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError
    ):
        service.get(0)


@pytest.mark.parametrize(
    "confidence",
    [
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_remember_rejects_non_finite_confidence(
    db_session: Session,
    confidence: str,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError
    ):
        service.remember(
            memory_type="fact",
            title="Score",
            content="Conteúdo",
            scope_type="global",
            source_type="system",
            source_reference="test:score",
            confidence=confidence,
        )



def test_remember_rejects_context_over_32_kb(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError,
        match="32 KB",
    ):
        service.remember(
            memory_type="fact",
            title="Contexto grande",
            content="Conteúdo",
            scope_type="global",
            source_type="system",
            source_reference="test:context-size",
            confidence="0.8",
            context_data={
                "payload": "x" * (32 * 1024),
            },
        )


def test_remember_rejects_context_depth_over_five(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError,
        match="profundidade JSON 5",
    ):
        service.remember(
            memory_type="fact",
            title="Contexto profundo",
            content="Conteúdo",
            scope_type="global",
            source_type="system",
            source_reference="test:context-depth",
            confidence="0.8",
            context_data={
                "level1": {
                    "level2": {
                        "level3": {
                            "level4": {
                                "level5": {
                                    "level6": "too-deep",
                                },
                            },
                        },
                    },
                },
            },
        )


def test_remember_accepts_context_at_depth_five(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    result = service.remember(
        memory_type="fact",
        title="Contexto válido",
        content="Conteúdo",
        scope_type="global",
        source_type="system",
        source_reference="test:context-depth-ok",
        confidence="0.8",
        context_data={
            "level1": {
                "level2": {
                    "level3": {
                        "level4": "ok",
                    },
                },
            },
        },
    )

    assert result.created is True
