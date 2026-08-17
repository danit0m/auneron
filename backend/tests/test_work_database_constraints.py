from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


WORK_ITEM_INSERT = text(
    """
    INSERT INTO work_items (
        work_type,
        title,
        description,
        work_key,
        scope_type,
        account_id,
        subject_user_id,
        parent_work_item_id,
        created_by_user_id,
        assignee_user_id,
        status,
        priority,
        blocked_reason,
        status_reason,
        due_at,
        sla_due_at,
        started_at,
        completed_at,
        cancelled_at,
        version,
        origin_type,
        origin_reference
    )
    VALUES (
        :work_type,
        :title,
        :description,
        :work_key,
        :scope_type,
        :account_id,
        :subject_user_id,
        :parent_work_item_id,
        :created_by_user_id,
        :assignee_user_id,
        :status,
        :priority,
        :blocked_reason,
        :status_reason,
        :due_at,
        :sla_due_at,
        :started_at,
        :completed_at,
        :cancelled_at,
        :version,
        :origin_type,
        :origin_reference
    )
    RETURNING id
    """
)


DEPENDENCY_INSERT = text(
    """
    INSERT INTO work_dependencies (
        work_item_id,
        depends_on_work_item_id,
        dependency_type,
        created_by_user_id
    )
    VALUES (
        :work_item_id,
        :depends_on_work_item_id,
        :dependency_type,
        :created_by_user_id
    )
    RETURNING id
    """
)


EVENT_INSERT = text(
    """
    INSERT INTO work_events (
        work_item_id,
        event_type,
        actor_type,
        actor_reference,
        actor_user_id,
        idempotency_key,
        event_data
    )
    VALUES (
        :work_item_id,
        :event_type,
        :actor_type,
        :actor_reference,
        :actor_user_id,
        :idempotency_key,
        CAST(:event_data AS JSONB)
    )
    RETURNING id
    """
)


MEMORY_LINK_INSERT = text(
    """
    INSERT INTO work_memory_links (
        work_item_id,
        memory_id,
        relation,
        created_by_user_id
    )
    VALUES (
        :work_item_id,
        :memory_id,
        :relation,
        :created_by_user_id
    )
    RETURNING id
    """
)


def work_values(
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "work_type": "task",
        "title": "Tarefa de teste",
        "description": None,
        "work_key": None,
        "scope_type": "global",
        "account_id": None,
        "subject_user_id": None,
        "parent_work_item_id": None,
        "created_by_user_id": None,
        "assignee_user_id": None,
        "status": "backlog",
        "priority": "normal",
        "blocked_reason": None,
        "status_reason": None,
        "due_at": None,
        "sla_due_at": None,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "version": 1,
        "origin_type": "system",
        "origin_reference": "test:work",
    }
    values.update(overrides)
    return values


def dependency_values(
    work_item_id: int,
    depends_on_work_item_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "work_item_id": work_item_id,
        "depends_on_work_item_id": (
            depends_on_work_item_id
        ),
        "dependency_type": "finish_to_start",
        "created_by_user_id": None,
    }
    values.update(overrides)
    return values


def event_values(
    work_item_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "work_item_id": work_item_id,
        "event_type": "created",
        "actor_type": "system",
        "actor_reference": "system:test",
        "actor_user_id": None,
        "idempotency_key": None,
        "event_data": "{}",
    }
    values.update(overrides)
    return values


def memory_link_values(
    work_item_id: int,
    memory_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "work_item_id": work_item_id,
        "memory_id": memory_id,
        "relation": "context",
        "created_by_user_id": None,
    }
    values.update(overrides)
    return values


def insert_work(
    db_session: Session,
    **overrides: Any,
) -> int:
    return int(
        db_session.execute(
            WORK_ITEM_INSERT,
            work_values(**overrides),
        ).scalar_one()
    )


def insert_account(
    db_session: Session,
    *,
    name: str,
) -> int:
    return int(
        db_session.execute(
            text(
                """
                INSERT INTO accounts (
                    cliente,
                    valor,
                    vencimento,
                    status
                )
                VALUES (
                    :name,
                    1000.00,
                    '2026-12-31',
                    'aberto'
                )
                RETURNING id
                """
            ),
            {
                "name": name,
            },
        ).scalar_one()
    )


def insert_user(
    db_session: Session,
    *,
    email: str,
) -> int:
    return int(
        db_session.execute(
            text(
                """
                INSERT INTO users (
                    name,
                    email,
                    password_hash,
                    role,
                    active
                )
                VALUES (
                    'Usuário Work Manager',
                    :email,
                    'hash-de-teste',
                    'viewer',
                    true
                )
                RETURNING id
                """
            ),
            {
                "email": email,
            },
        ).scalar_one()
    )


def insert_memory(
    db_session: Session,
) -> int:
    return int(
        db_session.execute(
            text(
                """
                INSERT INTO memory_items (
                    memory_type,
                    title,
                    content,
                    scope_type,
                    importance,
                    confidence,
                    valid_from,
                    source_type,
                    source_reference
                )
                VALUES (
                    'fact',
                    'Memória para Work Manager',
                    'Contexto persistente para a tarefa.',
                    'global',
                    0.500,
                    0.900,
                    '2026-08-14T12:00:00+00:00',
                    'system',
                    'test:work-memory'
                )
                RETURNING id
                """
            )
        ).scalar_one()
    )


def assert_integrity_error(
    db_session: Session,
    statement,
    parameters: Mapping[str, Any],
) -> None:
    with pytest.raises(IntegrityError):
        db_session.execute(
            statement,
            parameters,
        )
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_type", "unknown"),
        ("scope_type", "unknown"),
        ("status", "unknown"),
        ("priority", "unknown"),
        ("origin_type", "unknown"),
    ],
)
def test_database_rejects_invalid_work_enums(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(
            **{
                field: value,
            }
        ),
    )


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "description",
        "work_key",
        "origin_reference",
    ],
)
def test_database_rejects_blank_work_text(
    db_session: Session,
    field: str,
) -> None:
    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(
            **{
                field: " ",
            }
        ),
    )


@pytest.mark.parametrize(
    "case",
    [
        "global_account",
        "global_user",
        "account_missing",
        "account_with_user",
        "user_missing",
        "user_with_account",
    ],
)
def test_database_rejects_invalid_work_scope(
    db_session: Session,
    case: str,
) -> None:
    account_id = insert_account(
        db_session,
        name=f"Conta {case}",
    )
    user_id = insert_user(
        db_session,
        email=f"{case}@example.com",
    )

    cases = {
        "global_account": {
            "scope_type": "global",
            "account_id": account_id,
        },
        "global_user": {
            "scope_type": "global",
            "subject_user_id": user_id,
        },
        "account_missing": {
            "scope_type": "account",
        },
        "account_with_user": {
            "scope_type": "account",
            "account_id": account_id,
            "subject_user_id": user_id,
        },
        "user_missing": {
            "scope_type": "user",
        },
        "user_with_account": {
            "scope_type": "user",
            "account_id": account_id,
            "subject_user_id": user_id,
        },
    }

    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(**cases[case]),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "status": "blocked",
            "started_at": "2026-08-14T12:00:00+00:00",
            "blocked_reason": None,
        },
        {
            "status": "blocked",
            "started_at": "2026-08-14T12:00:00+00:00",
            "blocked_reason": " ",
        },
        {
            "status": "backlog",
            "blocked_reason": "Dependência pendente",
        },
    ],
)
def test_database_enforces_blocked_reason_integrity(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(**overrides),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "status": "backlog",
            "completed_at": "2026-08-14T13:00:00+00:00",
        },
        {
            "status": "backlog",
            "cancelled_at": "2026-08-14T13:00:00+00:00",
        },
        {
            "status": "completed",
            "started_at": "2026-08-14T12:00:00+00:00",
        },
        {
            "status": "completed",
            "started_at": "2026-08-14T12:00:00+00:00",
            "completed_at": "2026-08-14T13:00:00+00:00",
            "cancelled_at": "2026-08-14T13:00:00+00:00",
        },
        {
            "status": "cancelled",
            "status_reason": "Cancelada",
        },
        {
            "status": "cancelled",
            "cancelled_at": "2026-08-14T13:00:00+00:00",
            "status_reason": None,
        },
    ],
)
def test_database_enforces_terminal_timestamp_integrity(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(**overrides),
    )


@pytest.mark.parametrize(
    "status",
    [
        "in_progress",
        "blocked",
        "completed",
    ],
)
def test_database_requires_started_at_for_execution_states(
    db_session: Session,
    status: str,
) -> None:
    overrides: dict[str, Any] = {
        "status": status,
    }

    if status == "blocked":
        overrides["blocked_reason"] = "Bloqueada"

    if status == "completed":
        overrides["completed_at"] = (
            "2026-08-14T13:00:00+00:00"
        )

    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(**overrides),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "status": "completed",
            "started_at": "2026-08-14T13:00:00+00:00",
            "completed_at": "2026-08-14T12:00:00+00:00",
        },
        {
            "status": "cancelled",
            "status_reason": "Cancelada",
            "started_at": "2026-08-14T13:00:00+00:00",
            "cancelled_at": "2026-08-14T12:00:00+00:00",
        },
    ],
)
def test_database_rejects_terminal_timestamp_before_start(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(**overrides),
    )


@pytest.mark.parametrize(
    "version",
    [
        0,
        -1,
    ],
)
def test_database_rejects_non_positive_work_version(
    db_session: Session,
    version: int,
) -> None:
    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(version=version),
    )


def test_database_rejects_direct_self_parent(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)

    assert_integrity_error(
        db_session,
        text(
            """
            UPDATE work_items
            SET parent_work_item_id = :work_item_id
            WHERE id = :work_item_id
            """
        ),
        {
            "work_item_id": work_item_id,
        },
    )


@pytest.mark.parametrize(
    "scope_type",
    [
        "global",
        "account",
        "user",
    ],
)
def test_database_enforces_scope_unique_work_key(
    db_session: Session,
    scope_type: str,
) -> None:
    overrides: dict[str, Any] = {
        "scope_type": scope_type,
        "work_key": "work.retry.identity",
    }

    if scope_type == "account":
        overrides["account_id"] = insert_account(
            db_session,
            name="Conta de unicidade",
        )

    if scope_type == "user":
        overrides["subject_user_id"] = insert_user(
            db_session,
            email="work-key@example.com",
        )

    insert_work(
        db_session,
        **overrides,
    )

    assert_integrity_error(
        db_session,
        WORK_ITEM_INSERT,
        work_values(**overrides),
    )


def test_same_work_key_is_allowed_in_different_accounts(
    db_session: Session,
) -> None:
    first_account_id = insert_account(
        db_session,
        name="Conta A",
    )
    second_account_id = insert_account(
        db_session,
        name="Conta B",
    )

    insert_work(
        db_session,
        scope_type="account",
        account_id=first_account_id,
        work_key="account.shared.key",
    )
    insert_work(
        db_session,
        scope_type="account",
        account_id=second_account_id,
        work_key="account.shared.key",
    )

    db_session.commit()

    count = db_session.execute(
        text(
            """
            SELECT count(*)
            FROM work_items
            WHERE work_key = 'account.shared.key'
            """
        )
    ).scalar_one()

    assert count == 2


def test_database_rejects_self_dependency(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)

    assert_integrity_error(
        db_session,
        DEPENDENCY_INSERT,
        dependency_values(
            work_item_id,
            work_item_id,
        ),
    )


def test_database_rejects_invalid_dependency_type(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)
    predecessor_id = insert_work(
        db_session,
        title="Predecessora",
    )

    assert_integrity_error(
        db_session,
        DEPENDENCY_INSERT,
        dependency_values(
            work_item_id,
            predecessor_id,
            dependency_type="unknown",
        ),
    )


def test_database_rejects_duplicate_dependency_pair(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)
    predecessor_id = insert_work(
        db_session,
        title="Predecessora",
    )
    values = dependency_values(
        work_item_id,
        predecessor_id,
    )

    db_session.execute(
        DEPENDENCY_INSERT,
        values,
    ).scalar_one()

    assert_integrity_error(
        db_session,
        DEPENDENCY_INSERT,
        values,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "event_type": "unknown",
        },
        {
            "actor_type": "unknown",
        },
    ],
)
def test_database_rejects_invalid_event_enums(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    work_item_id = insert_work(db_session)

    assert_integrity_error(
        db_session,
        EVENT_INSERT,
        event_values(
            work_item_id,
            **overrides,
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "actor_reference": " ",
        },
        {
            "idempotency_key": " ",
        },
    ],
)
def test_database_rejects_blank_event_identity(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    work_item_id = insert_work(db_session)

    assert_integrity_error(
        db_session,
        EVENT_INSERT,
        event_values(
            work_item_id,
            **overrides,
        ),
    )


def test_database_rejects_duplicate_event_idempotency_key(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)
    values = event_values(
        work_item_id,
        idempotency_key="event:created:1",
    )

    db_session.execute(
        EVENT_INSERT,
        values,
    ).scalar_one()

    assert_integrity_error(
        db_session,
        EVENT_INSERT,
        values,
    )


def test_database_allows_multiple_events_without_key(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)

    db_session.execute(
        EVENT_INSERT,
        event_values(work_item_id),
    ).scalar_one()
    db_session.execute(
        EVENT_INSERT,
        event_values(
            work_item_id,
            event_type="system_note",
        ),
    ).scalar_one()
    db_session.commit()

    count = db_session.execute(
        text(
            """
            SELECT count(*)
            FROM work_events
            WHERE work_item_id = :work_item_id
            """
        ),
        {
            "work_item_id": work_item_id,
        },
    ).scalar_one()

    assert count == 2


def test_database_rejects_invalid_memory_link_relation(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)
    memory_id = insert_memory(db_session)

    assert_integrity_error(
        db_session,
        MEMORY_LINK_INSERT,
        memory_link_values(
            work_item_id,
            memory_id,
            relation="unknown",
        ),
    )


def test_database_rejects_duplicate_memory_link(
    db_session: Session,
) -> None:
    work_item_id = insert_work(db_session)
    memory_id = insert_memory(db_session)
    values = memory_link_values(
        work_item_id,
        memory_id,
    )

    db_session.execute(
        MEMORY_LINK_INSERT,
        values,
    ).scalar_one()

    assert_integrity_error(
        db_session,
        MEMORY_LINK_INSERT,
        values,
    )


def test_complete_work_aggregate_is_persisted(
    db_session: Session,
) -> None:
    actor_user_id = insert_user(
        db_session,
        email="work-actor@example.com",
    )
    project_id = insert_work(
        db_session,
        work_type="project",
        title="Projeto principal",
        work_key="project.main",
        created_by_user_id=actor_user_id,
        assignee_user_id=actor_user_id,
        origin_type="user",
        origin_reference=f"user:{actor_user_id}",
    )
    task_id = insert_work(
        db_session,
        title="Tarefa do projeto",
        parent_work_item_id=project_id,
        work_key="project.main.task.1",
        created_by_user_id=actor_user_id,
        assignee_user_id=actor_user_id,
        origin_type="user",
        origin_reference=f"user:{actor_user_id}",
    )
    memory_id = insert_memory(db_session)

    db_session.execute(
        DEPENDENCY_INSERT,
        dependency_values(
            task_id,
            project_id,
            created_by_user_id=actor_user_id,
        ),
    ).scalar_one()
    db_session.execute(
        EVENT_INSERT,
        event_values(
            task_id,
            actor_type="user",
            actor_reference=f"user:{actor_user_id}",
            actor_user_id=actor_user_id,
            idempotency_key="task:created:1",
        ),
    ).scalar_one()
    db_session.execute(
        MEMORY_LINK_INSERT,
        memory_link_values(
            task_id,
            memory_id,
            created_by_user_id=actor_user_id,
        ),
    ).scalar_one()
    db_session.commit()

    counts = db_session.execute(
        text(
            """
            SELECT
                (SELECT count(*) FROM work_items) AS items,
                (SELECT count(*) FROM work_dependencies) AS dependencies,
                (SELECT count(*) FROM work_events) AS events,
                (SELECT count(*) FROM work_memory_links) AS links
            """
        )
    ).one()

    assert counts._mapping["items"] == 2
    assert counts._mapping["dependencies"] == 1
    assert counts._mapping["events"] == 1
    assert counts._mapping["links"] == 1
