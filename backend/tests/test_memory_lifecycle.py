from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from threading import Barrier

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryStateError
from app.core.memory_errors import MemoryValidationError
from app.database.database import SessionLocal
from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService


def _create_memory(
    service: MemoryService,
    *,
    memory_key: str | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> MemoryItem:
    result = service.remember(
        memory_type="fact",
        title="Memória de lifecycle",
        content="Versão original.",
        memory_key=memory_key,
        scope_type="global",
        source_type="system",
        source_reference="test:lifecycle",
        confidence="0.900",
        valid_from=valid_from,
        valid_until=valid_until,
    )

    return result.memory


def test_invalidate_requires_reason(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory = _create_memory(service)

    with pytest.raises(
        MemoryValidationError,
        match="reason",
    ):
        service.invalidate(memory.id, reason=" ")

    db_session.refresh(memory)
    assert memory.status == "active"


def test_invalidate_changes_status_and_reason(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory = _create_memory(service)
    previous_changed_at = memory.status_changed_at

    invalidated = service.invalidate(
        memory.id,
        reason=" Fonte incorreta. ",
    )

    assert invalidated.status == "invalidated"
    assert invalidated.status_reason == "Fonte incorreta."
    assert invalidated.status_changed_at >= previous_changed_at


def test_archive_accepts_optional_reason(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory = _create_memory(service)

    archived = service.archive(memory.id)

    assert archived.status == "archived"
    assert archived.status_reason is None


def test_expire_changes_active_memory(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory = _create_memory(service)

    expired = service.expire(
        memory.id,
        reason="Validade encerrada.",
    )

    assert expired.status == "expired"
    assert expired.status_reason == "Validade encerrada."


def test_final_state_cannot_transition_again(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    memory = _create_memory(service)
    service.archive(memory.id)

    with pytest.raises(MemoryStateError):
        service.invalidate(
            memory.id,
            reason="Não permitido.",
        )

    db_session.refresh(memory)
    assert memory.status == "archived"


def test_transition_missing_memory_raises(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(MemoryNotFoundError):
        service.archive(999999999)


def test_supersede_preserves_key_scope_and_history(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    previous = _create_memory(
        service,
        memory_key="company.policy.lifecycle",
    )

    result = service.supersede(
        previous.id,
        reason="Política atualizada.",
        memory_type="fact",
        title="Memória atualizada",
        content="Versão substituta.",
        source_type="system",
        source_reference="test:supersede",
        confidence="0.950",
        importance="0.800",
    )

    assert result.previous.id == previous.id
    assert result.previous.status == "superseded"
    assert result.previous.status_reason == (
        "Política atualizada."
    )
    assert result.replacement.status == "active"
    assert result.replacement.memory_key == previous.memory_key
    assert result.replacement.scope_type == previous.scope_type
    assert result.replacement.account_id == previous.account_id
    assert (
        result.replacement.subject_user_id
        == previous.subject_user_id
    )
    assert (
        result.replacement.supersedes_memory_id
        == previous.id
    )


def test_supersede_adds_evidence_atomically(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    previous = _create_memory(
        service,
        memory_key="company.policy.evidence",
    )
    evidence = EvidenceInput(
        relation="supports",
        source_type="system",
        source_reference="test:supersede-evidence",
        evidence_text="Nova política aprovada.",
    )

    result = service.supersede(
        previous.id,
        reason="Nova versão aprovada.",
        memory_type="fact",
        title="Nova versão",
        content="Conteúdo novo.",
        source_type="system",
        source_reference="test:supersede",
        confidence="0.950",
        evidence=[evidence, evidence],
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].memory_id == (
        result.replacement.id
    )


def test_supersede_rolls_back_on_evidence_failure(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    previous = _create_memory(
        service,
        memory_key="company.policy.rollback",
    )

    with pytest.raises(
        MemoryValidationError,
        match="source_memory_id",
    ):
        service.supersede(
            previous.id,
            reason="Tentativa inválida.",
            memory_type="fact",
            title="Nova versão inválida",
            content="Não deve persistir.",
            source_type="system",
            source_reference="test:rollback",
            confidence="0.950",
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

    restored = service.get(previous.id)
    assert restored.status == "active"
    assert restored.status_reason is None

    memory_count = db_session.execute(
        select(func.count(MemoryItem.id))
    ).scalar_one()
    evidence_count = db_session.execute(
        select(func.count(MemoryEvidence.id))
    ).scalar_one()

    assert memory_count == 1
    assert evidence_count == 0


def test_supersede_rejects_final_memory(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    previous = _create_memory(service)
    service.invalidate(
        previous.id,
        reason="Memória inválida.",
    )

    with pytest.raises(MemoryStateError):
        service.supersede(
            previous.id,
            reason="Não permitido.",
            memory_type="fact",
            title="Tentativa",
            content="Não deve existir.",
            source_type="system",
            source_reference="test:invalid-state",
            confidence="0.900",
        )


def test_concurrent_supersede_creates_one_replacement(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    previous = _create_memory(
        service,
        memory_key="company.policy.concurrent",
    )
    previous_id = previous.id
    barrier = Barrier(2)

    def supersede_worker(
        label: str,
    ) -> tuple[str, int | None]:
        session = SessionLocal()

        try:
            worker_service = MemoryService(session)
            barrier.wait(timeout=10)

            try:
                result = worker_service.supersede(
                    previous_id,
                    reason=f"Atualização concorrente {label}.",
                    memory_type="fact",
                    title=f"Versão {label}",
                    content=f"Conteúdo {label}.",
                    source_type="system",
                    source_reference=(
                        f"test:concurrent:{label}"
                    ),
                    confidence="0.950",
                )
            except MemoryStateError:
                return ("state_error", None)

            return ("success", result.replacement.id)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                supersede_worker,
                ("A", "B"),
            )
        )

    assert sorted(
        outcome for outcome, _ in outcomes
    ) == ["state_error", "success"]

    replacement_ids = [
        replacement_id
        for outcome, replacement_id in outcomes
        if outcome == "success"
    ]

    assert len(replacement_ids) == 1

    db_session.expire_all()
    memories = list(
        db_session.execute(
            select(MemoryItem).order_by(MemoryItem.id)
        ).scalars()
    )

    assert len(memories) == 2
    assert memories[0].id == previous_id
    assert memories[0].status == "superseded"
    assert memories[1].id == replacement_ids[0]
    assert memories[1].status == "active"
    assert memories[1].supersedes_memory_id == previous_id


def test_expire_due_batch_respects_time_and_limit(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)
    start = datetime(
        2026,
        8,
        10,
        tzinfo=timezone.utc,
    )
    first = _create_memory(
        service,
        valid_from=start,
        valid_until=datetime(
            2026,
            8,
            11,
            tzinfo=timezone.utc,
        ),
    )
    second = _create_memory(
        service,
        valid_from=start,
        valid_until=datetime(
            2026,
            8,
            12,
            tzinfo=timezone.utc,
        ),
    )
    future = _create_memory(
        service,
        valid_from=start,
        valid_until=datetime(
            2026,
            8,
            20,
            tzinfo=timezone.utc,
        ),
    )
    as_of = datetime(
        2026,
        8,
        15,
        tzinfo=timezone.utc,
    )

    expired_first = service.expire_due_batch(
        as_of=as_of,
        limit=1,
    )
    expired_second = service.expire_due_batch(
        as_of=as_of,
        limit=100,
    )

    assert [item.id for item in expired_first] == [first.id]
    assert [item.id for item in expired_second] == [second.id]

    db_session.refresh(future)
    assert future.status == "active"


@pytest.mark.parametrize("limit", [0, 101])
def test_expire_due_batch_rejects_invalid_limit(
    db_session: Session,
    limit: int,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(MemoryValidationError):
        service.expire_due_batch(limit=limit)


def test_expire_due_batch_rejects_naive_as_of(
    db_session: Session,
) -> None:
    service = MemoryService(db_session)

    with pytest.raises(
        MemoryValidationError,
        match="as_of",
    ):
        service.expire_due_batch(
            as_of=datetime(2026, 8, 15),
        )
