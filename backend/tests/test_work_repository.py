import ast
import inspect

from sqlalchemy.orm import Session

from app.models.work import WorkDependency
from app.models.work import WorkEvent
from app.models.work import WorkMemoryLink
from app.repositories.work_repository import WorkRepository
from app.services.memory_service import MemoryService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-repository",
)


def _create_work(
    db_session: Session,
    *,
    title: str,
    work_key: str,
) -> int:
    result = WorkManagerService(db_session).create(
        work_type="task",
        title=title,
        work_key=work_key,
        scope_type="global",
        origin_type="system",
        origin_reference="test:repository",
        actor=SYSTEM_ACTOR,
    )
    return result.work_item.id


def test_repository_contract_has_no_transaction_control() -> None:
    tree = ast.parse(inspect.getsource(WorkRepository))
    forbidden = {
        "commit",
        "rollback",
        "begin",
        "begin_nested",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }

    assert calls.isdisjoint(forbidden)


def test_repository_exposes_no_event_update_or_delete() -> None:
    method_names = {
        name
        for name, value in vars(WorkRepository).items()
        if callable(value)
    }

    assert "update_event" not in method_names
    assert "delete_event" not in method_names
    assert "remove_event" not in method_names


def test_repository_get_and_lock_return_work_item(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        title="Item bloqueável",
        work_key="test.repository.lock",
    )
    repository = WorkRepository(db_session)

    loaded = repository.get_by_id(work_item_id)
    locked = repository.lock_by_id(work_item_id)

    assert loaded is not None
    assert locked is not None
    assert locked.id == work_item_id


def test_repository_finds_scope_unique_key(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        title="Item por chave",
        work_key="test.repository.key",
    )
    repository = WorkRepository(db_session)

    found = repository.find_by_key(
        scope_type="global",
        work_key="test.repository.key",
    )

    assert found is not None
    assert found.id == work_item_id


def test_repository_lists_events_in_stable_order(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        title="Timeline",
        work_key="test.repository.timeline",
    )
    service = WorkManagerService(db_session)
    service.add_comment(
        work_item_id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        comment="Primeiro comentário.",
    )
    service.add_system_note(
        work_item_id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        note="Segunda anotação.",
    )

    events = WorkRepository(db_session).list_events(
        work_item_id
    )

    assert [event.event_type for event in events] == [
        "created",
        "comment_added",
        "system_note",
    ]
    assert [event.id for event in events] == sorted(
        event.id for event in events
    )


def test_repository_finds_idempotent_event(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        title="Evento idempotente",
        work_key="test.repository.event",
    )
    service = WorkManagerService(db_session)
    result = service.change_priority(
        work_item_id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        priority="high",
        idempotency_key="test.repository.priority",
    )

    found = WorkRepository(
        db_session
    ).find_event_by_idempotency_key(
        work_item_id=work_item_id,
        idempotency_key="test.repository.priority",
    )

    assert found is not None
    assert found.id == result.event.id


def test_repository_dependency_primitives(
    db_session: Session,
) -> None:
    dependent_id = _create_work(
        db_session,
        title="Dependente",
        work_key="test.repository.dependent",
    )
    predecessor_id = _create_work(
        db_session,
        title="Predecessor",
        work_key="test.repository.predecessor",
    )
    repository = WorkRepository(db_session)
    dependency = WorkDependency(
        work_item_id=dependent_id,
        depends_on_work_item_id=predecessor_id,
        dependency_type="finish_to_start",
    )

    repository.add_dependency(dependency)
    found = repository.find_dependency(
        work_item_id=dependent_id,
        depends_on_work_item_id=predecessor_id,
    )
    listed = repository.list_dependencies(dependent_id)

    assert found is dependency
    assert listed == [dependency]

    repository.remove_dependency(dependency)
    assert repository.list_dependencies(dependent_id) == []


def test_repository_memory_link_primitives(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        title="Item com memória",
        work_key="test.repository.memory-link",
    )
    memory = MemoryService(db_session).remember(
        memory_type="fact",
        title="Memória vinculável",
        content="Contexto do trabalho.",
        scope_type="global",
        source_type="system",
        source_reference="test:repository-memory",
        confidence="0.900",
    ).memory
    repository = WorkRepository(db_session)
    link = WorkMemoryLink(
        work_item_id=work_item_id,
        memory_id=memory.id,
        relation="context",
    )

    assert repository.memory_exists(memory.id) is True
    assert repository.memory_exists(999999999) is False

    repository.add_memory_link(link)
    found = repository.find_memory_link(
        work_item_id=work_item_id,
        memory_id=memory.id,
        relation="context",
    )
    listed = repository.list_memory_links(work_item_id)

    assert found is link
    assert listed == [link]

    repository.remove_memory_link(link)
    assert repository.list_memory_links(work_item_id) == []


def test_repository_add_event_flushes_without_committing(
    db_session: Session,
) -> None:
    work_item_id = _create_work(
        db_session,
        title="Flush de evento",
        work_key="test.repository.flush",
    )
    repository = WorkRepository(db_session)
    event = WorkEvent(
        work_item_id=work_item_id,
        event_type="system_note",
        actor_type="system",
        actor_reference="test:manual-event",
        event_data={"note": "Ainda não confirmado."},
    )

    repository.add_event(event)

    assert event.id is not None
    assert event in db_session.new or event in db_session
