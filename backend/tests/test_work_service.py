from datetime import datetime
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkIdempotencyConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkValidationError
from app.core.work_errors import WorkVersionConflictError
from app.models.account import Account
from app.models.user import User
from app.models.work import WorkEvent
from app.services.memory_service import MemoryService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="test:work-service",
)


def _create_user(
    db_session: Session,
    *,
    email: str = "work-user@example.com",
) -> User:
    user = User(
        name="Usuário Work",
        email=email,
        password_hash="test-password-hash",
        role="viewer",
        active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _create_account(db_session: Session) -> Account:
    account = Account(
        cliente="Conta Work",
        valor=Decimal("1000.00"),
        vencimento=datetime(2026, 12, 31).date(),
        status="aberto",
    )
    db_session.add(account)
    db_session.flush()
    return account


def _create_work(
    service: WorkManagerService,
    **overrides: object,
):
    payload: dict[str, object] = {
        "work_type": "task",
        "title": "Item de trabalho",
        "work_key": "test.work.default",
        "scope_type": "global",
        "origin_type": "system",
        "origin_reference": "test:create",
        "actor": SYSTEM_ACTOR,
    }
    payload.update(overrides)
    return service.create(**payload)


def _event_count(
    db_session: Session,
    work_item_id: int,
) -> int:
    return db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.work_item_id == work_item_id
        )
    ).scalar_one()


def test_create_persists_item_and_created_event(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)

    result = _create_work(
        service,
        title="  Preparar relatório  ",
        work_key=" TEST.WORK.CREATE ",
        priority=" HIGH ",
        context_data={"source": "test"},
    )

    assert result.created is True
    assert result.duplicate is False
    assert result.work_item.title == "Preparar relatório"
    assert result.work_item.work_key == "test.work.create"
    assert result.work_item.priority == "high"
    assert result.work_item.status == "backlog"
    assert result.work_item.version == 1
    assert result.event.event_type == "created"
    assert result.event.work_item_id == result.work_item.id
    assert result.event.event_data["result_version"] == 1


@pytest.mark.parametrize(
    ("scope_type", "account", "subject"),
    [
        ("global", False, False),
        ("account", True, False),
        ("user", False, True),
    ],
)
def test_create_accepts_each_valid_scope(
    db_session: Session,
    scope_type: str,
    account: bool,
    subject: bool,
) -> None:
    account_id = (
        _create_account(db_session).id if account else None
    )
    subject_user_id = (
        _create_user(db_session).id if subject else None
    )

    result = _create_work(
        WorkManagerService(db_session),
        work_key=f"test.scope.{scope_type}",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
    )

    assert result.work_item.scope_type == scope_type
    assert result.work_item.account_id == account_id
    assert result.work_item.subject_user_id == subject_user_id


@pytest.mark.parametrize(
    ("scope_type", "account_id", "subject_user_id"),
    [
        ("global", 1, None),
        ("global", None, 1),
        ("account", None, None),
        ("account", 1, 1),
        ("user", None, None),
        ("user", 1, 1),
    ],
)
def test_create_rejects_invalid_scope_shape(
    db_session: Session,
    scope_type: str,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    service = WorkManagerService(db_session)

    with pytest.raises(WorkValidationError):
        _create_work(
            service,
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_type", "job"),
        ("priority", "critical"),
        ("scope_type", "team"),
        ("origin_type", "unknown"),
    ],
)
def test_create_rejects_invalid_vocabulary(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    service = WorkManagerService(db_session)

    with pytest.raises(WorkValidationError):
        _create_work(service, **{field: value})


@pytest.mark.parametrize(
    "field",
    ["due_at", "sla_due_at"],
)
def test_create_rejects_naive_schedule_datetime(
    db_session: Session,
    field: str,
) -> None:
    service = WorkManagerService(db_session)

    with pytest.raises(
        WorkValidationError,
        match=field,
    ):
        _create_work(
            service,
            **{field: datetime(2026, 8, 20, 9, 0)},
        )


def test_create_normalizes_aware_schedule_to_utc(
    db_session: Session,
) -> None:
    due_at = datetime(
        2026,
        8,
        20,
        9,
        0,
        tzinfo=timezone.utc,
    )

    result = _create_work(
        WorkManagerService(db_session),
        due_at=due_at,
        sla_due_at=due_at,
    )

    assert result.work_item.due_at == due_at
    assert result.work_item.sla_due_at == due_at


def test_create_rejects_context_over_32_kb(
    db_session: Session,
) -> None:
    with pytest.raises(
        WorkValidationError,
        match="32 KB",
    ):
        _create_work(
            WorkManagerService(db_session),
            context_data={"payload": "x" * (32 * 1024)},
        )


def test_create_rejects_context_depth_over_five(
    db_session: Session,
) -> None:
    with pytest.raises(
        WorkValidationError,
        match="profundidade JSON 5",
    ):
        _create_work(
            WorkManagerService(db_session),
            context_data={
                "one": {
                    "two": {
                        "three": {
                            "four": {
                                "five": {
                                    "six": "too-deep",
                                },
                            },
                        },
                    },
                },
            },
        )


def test_create_rejects_non_json_context(
    db_session: Session,
) -> None:
    with pytest.raises(
        WorkValidationError,
        match="JSON válido",
    ):
        _create_work(
            WorkManagerService(db_session),
            context_data={"invalid": object()},
        )


def test_create_rejects_cyclic_context(
    db_session: Session,
) -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(
        WorkValidationError,
        match="JSON válido",
    ):
        _create_work(
            WorkManagerService(db_session),
            context_data=cyclic,
        )


@pytest.mark.parametrize(
    "actor",
    [
        WorkActor("user", "user:missing"),
        WorkActor("system", "system:test", 1),
        WorkActor("unknown", "unknown:test"),
        WorkActor("system", " "),
    ],
)
def test_create_rejects_invalid_actor(
    db_session: Session,
    actor: WorkActor,
) -> None:
    with pytest.raises(WorkValidationError):
        _create_work(
            WorkManagerService(db_session),
            actor=actor,
        )


def test_user_actor_is_recorded_on_item_and_event(
    db_session: Session,
) -> None:
    user = _create_user(db_session)
    actor = WorkActor(
        actor_type="user",
        actor_reference=f"user:{user.id}",
        actor_user_id=user.id,
    )

    result = _create_work(
        WorkManagerService(db_session),
        actor=actor,
    )

    assert result.work_item.created_by_user_id == user.id
    assert result.event.actor_user_id == user.id


def test_create_is_idempotent_by_scope_and_work_key(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    payload = {
        "title": "Importação idempotente",
        "work_key": "test.create.idempotent",
        "idempotency_key": "test.create.event",
    }

    first = _create_work(service, **payload)
    second = _create_work(service, **payload)

    assert first.created is True
    assert second.created is False
    assert second.duplicate is True
    assert second.work_item.id == first.work_item.id
    assert second.event.id == first.event.id
    assert _event_count(db_session, first.work_item.id) == 1


def test_create_key_conflicts_with_different_payload(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    _create_work(
        service,
        title="Payload original",
        work_key="test.create.conflict",
    )

    with pytest.raises(WorkConflictError):
        _create_work(
            service,
            title="Payload diferente",
            work_key="test.create.conflict",
        )


def test_create_idempotency_key_requires_work_key(
    db_session: Session,
) -> None:
    with pytest.raises(
        WorkValidationError,
        match="exige work_key",
    ):
        _create_work(
            WorkManagerService(db_session),
            work_key=None,
            idempotency_key="test.create.no-work-key",
        )


def test_create_accepts_parent_in_same_scope(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    parent = _create_work(
        service,
        work_type="project",
        title="Projeto pai",
        work_key="test.parent",
    ).work_item

    child = _create_work(
        service,
        title="Tarefa filha",
        work_key="test.child",
        parent_work_item_id=parent.id,
    ).work_item

    assert child.parent_work_item_id == parent.id


def test_create_rejects_parent_from_another_scope(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    parent = _create_work(
        service,
        work_type="project",
        title="Projeto global",
        work_key="test.parent.global",
    ).work_item
    account = _create_account(db_session)

    with pytest.raises(
        WorkValidationError,
        match="mesmo escopo",
    ):
        _create_work(
            service,
            title="Tarefa de conta",
            work_key="test.child.account",
            scope_type="account",
            account_id=account.id,
            parent_work_item_id=parent.id,
        )


def test_get_returns_item_and_validates_id(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item

    assert service.get(item.id).id == item.id

    with pytest.raises(WorkValidationError):
        service.get(0)

    with pytest.raises(WorkNotFoundError):
        service.get(999999999)


def test_update_details_is_atomic_and_increments_once(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(
        service,
        description="Descrição inicial.",
        context_data={"stage": 1},
    ).work_item

    result = service.update_details(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        title="Título atualizado",
        description="Descrição atualizada.",
        context_data={"stage": 2},
        idempotency_key="test.details.update",
    )

    assert result.applied is True
    assert result.work_item.version == 2
    assert result.work_item.title == "Título atualizado"
    assert result.event.event_type == "details_changed"
    assert result.event.event_data["from_version"] == 1
    assert result.event.event_data["to_version"] == 2
    assert _event_count(db_session, item.id) == 2


def test_update_details_rejects_noop_without_version_bump(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item

    with pytest.raises(
        WorkValidationError,
        match="não produz alteração",
    ):
        service.update_details(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            title=item.title,
            description=item.description,
            context_data=item.context_data,
        )

    db_session.refresh(item)
    assert item.version == 1
    assert _event_count(db_session, item.id) == 1


def test_change_priority_records_event_and_version(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item

    result = service.change_priority(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        priority="urgent",
    )

    assert result.work_item.priority == "urgent"
    assert result.work_item.version == 2
    assert result.event.event_type == "priority_changed"


def test_change_assignee_and_unassign(
    db_session: Session,
) -> None:
    user = _create_user(db_session)
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item

    assigned = service.change_assignee(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        assignee_user_id=user.id,
    )
    unassigned = service.change_assignee(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        assignee_user_id=None,
    )

    assert assigned.event.event_type == "assignee_changed"
    assert unassigned.work_item.assignee_user_id is None
    assert unassigned.work_item.version == 3


def test_comment_and_system_note_are_append_only_mutations(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item

    comment = service.add_comment(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        comment="  Informação operacional.  ",
    )
    note = service.add_system_note(
        item.id,
        expected_version=2,
        actor=SYSTEM_ACTOR,
        note="  Verificação automática concluída.  ",
    )

    assert comment.event.event_data["changes"]["comment"] == (
        "Informação operacional."
    )
    assert note.event.event_type == "system_note"
    assert note.work_item.version == 3


def test_system_note_rejects_non_system_actor(
    db_session: Session,
) -> None:
    user = _create_user(db_session)
    item = _create_work(
        WorkManagerService(db_session)
    ).work_item

    with pytest.raises(
        WorkValidationError,
        match="ator system",
    ):
        WorkManagerService(db_session).add_system_note(
            item.id,
            expected_version=1,
            actor=WorkActor(
                "user",
                f"user:{user.id}",
                user.id,
            ),
            note="Não permitido.",
        )


def test_expected_version_conflict_preserves_state(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item
    service.change_priority(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        priority="high",
    )

    with pytest.raises(
        WorkVersionConflictError
    ) as captured:
        service.change_priority(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            priority="urgent",
        )

    assert captured.value.expected_version == 1
    assert captured.value.current_version == 2
    db_session.refresh(item)
    assert item.priority == "high"
    assert item.version == 2
    assert _event_count(db_session, item.id) == 2


def test_idempotent_mutation_replays_without_increment(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item
    payload = {
        "expected_version": 1,
        "actor": SYSTEM_ACTOR,
        "priority": "high",
        "idempotency_key": "test.priority.replay",
    }

    first = service.change_priority(item.id, **payload)
    second = service.change_priority(item.id, **payload)

    assert first.applied is True
    assert second.applied is False
    assert second.duplicate is True
    assert second.event.id == first.event.id
    assert second.work_item.version == 2
    assert _event_count(db_session, item.id) == 2


def test_idempotency_key_reuse_with_other_request_conflicts(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item
    service.change_priority(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        priority="high",
        idempotency_key="test.priority.conflict",
    )

    with pytest.raises(WorkIdempotencyConflictError):
        service.change_priority(
            item.id,
            expected_version=1,
            actor=SYSTEM_ACTOR,
            priority="urgent",
            idempotency_key="test.priority.conflict",
        )

    db_session.refresh(item)
    assert item.priority == "high"
    assert item.version == 2


def test_list_events_returns_complete_timeline(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(service).work_item
    service.change_priority(
        item.id,
        expected_version=1,
        actor=SYSTEM_ACTOR,
        priority="high",
    )

    events = service.list_events(item.id)

    assert [event.event_type for event in events] == [
        "created",
        "priority_changed",
    ]


@pytest.mark.parametrize(
    ("expected_version", "assignee_user_id"),
    [
        (0, None),
        (True, None),
        (1, 0),
        (1, True),
    ],
)
def test_mutation_rejects_invalid_identifiers(
    db_session: Session,
    expected_version: int,
    assignee_user_id: int | None,
) -> None:
    item = _create_work(
        WorkManagerService(db_session)
    ).work_item

    with pytest.raises(WorkValidationError):
        WorkManagerService(db_session).change_assignee(
            item.id,
            expected_version=expected_version,
            actor=SYSTEM_ACTOR,
            assignee_user_id=assignee_user_id,
        )


def test_list_items_is_explicitly_scoped_and_filtered(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    _create_work(
        service,
        work_key="test.list.normal",
        priority="normal",
    )
    urgent = _create_work(
        service,
        work_key="test.list.urgent",
        priority="urgent",
    ).work_item

    items = service.list_items(
        scope_type="global",
        priorities=("urgent",),
    )

    assert [item.id for item in items] == [urgent.id]


def test_memory_link_mutation_is_atomic_and_audited(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(
        service,
        work_key="test.memory.link.service",
    ).work_item
    memory = MemoryService(db_session).remember(
        memory_type="fact",
        title="Memória de serviço",
        content="Contexto de serviço.",
        scope_type="global",
        source_type="system",
        source_reference="test:work-service",
        confidence="1.000",
    ).memory

    linked = service.link_memory(
        item.id,
        memory_id=memory.id,
        relation="context",
        expected_version=1,
        actor=SYSTEM_ACTOR,
    )
    links = service.list_memory_links(item.id)
    unlinked = service.unlink_memory(
        item.id,
        memory_id=memory.id,
        relation="context",
        expected_version=2,
        actor=SYSTEM_ACTOR,
    )

    assert linked.event.event_type == "memory_linked"
    assert len(links) == 1
    assert links[0].memory_id == memory.id
    assert unlinked.event.event_type == "memory_unlinked"
    assert unlinked.work_item.version == 3
    assert service.list_memory_links(item.id) == ()


def test_memory_link_idempotency_replays_without_duplicate(
    db_session: Session,
) -> None:
    service = WorkManagerService(db_session)
    item = _create_work(
        service,
        work_key="test.memory.link.replay",
    ).work_item
    memory = MemoryService(db_session).remember(
        memory_type="fact",
        title="Memória idempotente",
        content="Contexto idempotente.",
        scope_type="global",
        source_type="system",
        source_reference="test:work-service",
        confidence="1.000",
    ).memory
    payload = {
        "memory_id": memory.id,
        "relation": "source",
        "expected_version": 1,
        "actor": SYSTEM_ACTOR,
        "idempotency_key": "test.memory.link.replay",
    }

    first = service.link_memory(item.id, **payload)
    second = service.link_memory(item.id, **payload)

    assert first.applied is True
    assert second.applied is False
    assert second.duplicate is True
    assert second.event.id == first.event.id
    assert len(service.list_memory_links(item.id)) == 1
