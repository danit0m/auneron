from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryValidationError
from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService


def _create_memory(
    service: MemoryService,
    *,
    memory_key: str | None = None,
) -> int:
    result = service.remember(
        memory_type="fact",
        title="Memória para evidência",
        content="Conteúdo confirmado.",
        memory_key=memory_key,
        scope_type="global",
        source_type="system",
        source_reference="test:evidence-memory",
        confidence="0.900",
    )

    return result.memory.id


def test_add_evidence_creates_normalized_record(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    result = service.add_evidence(
        memory_id,
        relation=" SUPPORTS ",
        source_type=" DATABASE ",
        source_reference=" payments:991 ",
        evidence_text=" Pagamento confirmado. ",
        weight="0.8754",
        observed_at=datetime(
            2026,
            8,
            12,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        context_data={"currency": "BRL"},
    )

    assert result.created is True
    assert result.duplicate is False
    assert result.evidence.memory_id == memory_id
    assert result.evidence.relation == "supports"
    assert result.evidence.source_type == "database"
    assert result.evidence.source_reference == "payments:991"
    assert result.evidence.evidence_text == "Pagamento confirmado."
    assert str(result.evidence.weight) == "0.875"
    assert len(result.evidence.evidence_hash) == 64


def test_duplicate_evidence_is_idempotent(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)
    payload = {
        "relation": "supports",
        "source_type": "database",
        "source_reference": "payments:991",
        "evidence_text": "Pagamento confirmado.",
        "weight": "1.000",
        "context_data": {"currency": "BRL"},
    }

    first = service.add_evidence(memory_id, **payload)
    second = service.add_evidence(memory_id, **payload)

    assert first.created is True
    assert second.created is False
    assert second.duplicate is True
    assert second.evidence.id == first.evidence.id

    count = db_session.execute(
        select(func.count(MemoryEvidence.id)).where(
            MemoryEvidence.memory_id == memory_id,
        )
    ).scalar_one()

    assert count == 1


def test_remember_adds_initial_evidence_atomically(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    evidence = EvidenceInput(
        relation="supports",
        source_type="database",
        source_reference="payments:initial",
        evidence_text="Pagamento localizado.",
    )

    result = service.remember(
        memory_type="fact",
        title="Memória com evidência",
        content="Pagamento confirmado.",
        memory_key="company.payment.initial",
        scope_type="global",
        source_type="database",
        source_reference="payments:initial",
        confidence="0.950",
        evidence=[evidence],
    )

    assert result.created is True
    assert len(result.evidence) == 1
    assert result.evidence[0].memory_id == result.memory.id


def test_remember_deduplicates_initial_evidence(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    evidence = EvidenceInput(
        relation="supports",
        source_type="system",
        source_reference="test:initial-duplicate",
        evidence_text="Evidência repetida.",
    )

    result = service.remember(
        memory_type="fact",
        title="Deduplicação inicial",
        content="Conteúdo.",
        scope_type="global",
        source_type="system",
        source_reference="test:initial-duplicate",
        confidence="0.900",
        evidence=[evidence, evidence],
    )

    assert len(result.evidence) == 1


def test_remember_rolls_back_on_initial_evidence_failure(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError,
        match="source_memory_id",
    ):
        service.remember(
            memory_type="fact",
            title="Rollback inicial",
            content="Não deve persistir.",
            scope_type="global",
            source_type="system",
            source_reference="test:initial-rollback",
            confidence="0.900",
            evidence=[
                EvidenceInput(
                    relation="supports",
                    source_type="system",
                    source_reference="test:missing-source",
                    source_memory_id=999999999,
                    evidence_text="Fonte inexistente.",
                ),
            ],
        )

    memory_count = db_session.execute(
        select(func.count(MemoryItem.id))
    ).scalar_one()
    evidence_count = db_session.execute(
        select(func.count(MemoryEvidence.id))
    ).scalar_one()

    assert memory_count == 0
    assert evidence_count == 0


def test_remember_rejects_more_than_twenty_evidence(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    evidence = [
        EvidenceInput(
            relation="context",
            source_type="system",
            source_reference=f"test:evidence:{index}",
            evidence_text=f"Evidência {index}.",
        )
        for index in range(21)
    ]

    with pytest.raises(
        MemoryValidationError,
        match="20 evidências",
    ):
        service.remember(
            memory_type="fact",
            title="Limite de evidências",
            content="Não deve persistir.",
            scope_type="global",
            source_type="system",
            source_reference="test:evidence-limit",
            confidence="0.900",
            evidence=evidence,
        )


def test_evidence_hash_includes_relation(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)
    payload = {
        "source_type": "database",
        "source_reference": "payments:991",
        "evidence_text": "Pagamento localizado.",
    }

    supports = service.add_evidence(
        memory_id,
        relation="supports",
        **payload,
    )
    contradicts = service.add_evidence(
        memory_id,
        relation="contradicts",
        **payload,
    )

    assert supports.evidence.id != contradicts.evidence.id
    assert (
        supports.evidence.evidence_hash
        != contradicts.evidence.evidence_hash
    )


def test_list_evidence_is_deterministic(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    first = service.add_evidence(
        memory_id,
        relation="supports",
        source_type="system",
        source_reference="test:first",
        evidence_text="Primeira evidência.",
    )
    second = service.add_evidence(
        memory_id,
        relation="context",
        source_type="system",
        source_reference="test:second",
        evidence_text="Segunda evidência.",
    )

    evidence = service.list_evidence(memory_id)

    assert [item.id for item in evidence] == [
        first.evidence.id,
        second.evidence.id,
    ]


@pytest.mark.parametrize(
    "relation",
    ["", "agrees", "unknown"],
)
def test_add_evidence_rejects_invalid_relation(
    db_session: Session,
    relation: str,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    with pytest.raises(MemoryValidationError):
        service.add_evidence(
            memory_id,
            relation=relation,
            source_type="system",
            source_reference="test:invalid-relation",
            evidence_text="Evidência.",
        )


@pytest.mark.parametrize(
    "weight",
    ["-0.001", "1.001", "NaN", "Infinity"],
)
def test_add_evidence_rejects_invalid_weight(
    db_session: Session,
    weight: str,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    with pytest.raises(MemoryValidationError):
        service.add_evidence(
            memory_id,
            relation="supports",
            source_type="system",
            source_reference="test:invalid-weight",
            evidence_text="Evidência.",
            weight=weight,
        )


def test_add_evidence_rejects_naive_observed_at(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    with pytest.raises(
        MemoryValidationError,
        match="observed_at",
    ):
        service.add_evidence(
            memory_id,
            relation="supports",
            source_type="system",
            source_reference="test:naive-time",
            evidence_text="Evidência.",
            observed_at=datetime(2026, 8, 12, 9, 0),
        )


def test_add_evidence_rejects_self_reference(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    with pytest.raises(
        MemoryValidationError,
        match="própria memória",
    ):
        service.add_evidence(
            memory_id,
            relation="context",
            source_type="system",
            source_reference="test:self-reference",
            source_memory_id=memory_id,
            evidence_text="Evidência.",
        )


def test_add_evidence_rejects_missing_source_memory(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory_id = _create_memory(service)

    with pytest.raises(
        MemoryValidationError,
        match="source_memory_id",
    ):
        service.add_evidence(
            memory_id,
            relation="context",
            source_type="system",
            source_reference="test:missing-source",
            source_memory_id=999999999,
            evidence_text="Evidência.",
        )


def test_add_evidence_to_missing_memory_raises(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(MemoryNotFoundError):
        service.add_evidence(
            999999999,
            relation="supports",
            source_type="system",
            source_reference="test:missing-parent",
            evidence_text="Evidência.",
        )


def test_list_evidence_validates_parent_memory(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(MemoryNotFoundError):
        service.list_evidence(999999999)
