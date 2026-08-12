from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.database import engine


MEMORY_INSERT = text(
    """
    INSERT INTO memory_items (
        memory_type,
        title,
        content,
        memory_key,
        scope_type,
        account_id,
        subject_user_id,
        created_by_user_id,
        importance,
        confidence,
        status,
        status_reason,
        valid_from,
        valid_until,
        source_type,
        source_reference,
        supersedes_memory_id
    )
    VALUES (
        :memory_type,
        :title,
        :content,
        :memory_key,
        :scope_type,
        :account_id,
        :subject_user_id,
        :created_by_user_id,
        :importance,
        :confidence,
        :status,
        :status_reason,
        :valid_from,
        :valid_until,
        :source_type,
        :source_reference,
        :supersedes_memory_id
    )
    RETURNING id
    """
)


EVIDENCE_INSERT = text(
    """
    INSERT INTO memory_evidence (
        memory_id,
        relation,
        source_type,
        source_reference,
        source_memory_id,
        evidence_text,
        evidence_hash,
        weight,
        observed_at,
        created_by_user_id
    )
    VALUES (
        :memory_id,
        :relation,
        :source_type,
        :source_reference,
        :source_memory_id,
        :evidence_text,
        :evidence_hash,
        :weight,
        :observed_at,
        :created_by_user_id
    )
    RETURNING id
    """
)


def memory_values(
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "memory_type": "fact",
        "title": "Memória de teste",
        "content": "Conteúdo persistente de teste.",
        "memory_key": None,
        "scope_type": "global",
        "account_id": None,
        "subject_user_id": None,
        "created_by_user_id": None,
        "importance": "0.500",
        "confidence": "0.800",
        "status": "active",
        "status_reason": None,
        "valid_from": "2026-08-11T12:00:00+00:00",
        "valid_until": None,
        "source_type": "system",
        "source_reference": "test:schema",
        "supersedes_memory_id": None,
    }
    values.update(overrides)
    return values


def evidence_values(
    memory_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "memory_id": memory_id,
        "relation": "supports",
        "source_type": "system",
        "source_reference": "test:evidence",
        "source_memory_id": None,
        "evidence_text": "Evidência persistente de teste.",
        "evidence_hash": "a" * 64,
        "weight": "1.000",
        "observed_at": None,
        "created_by_user_id": None,
    }
    values.update(overrides)
    return values


def insert_memory(
    db_session: Session,
    **overrides: Any,
) -> int:
    return int(
        db_session.execute(
            MEMORY_INSERT,
            memory_values(**overrides),
        ).scalar_one()
    )


def insert_evidence(
    db_session: Session,
    memory_id: int,
    **overrides: Any,
) -> int:
    return int(
        db_session.execute(
            EVIDENCE_INSERT,
            evidence_values(
                memory_id,
                **overrides,
            ),
        ).scalar_one()
    )


def insert_account(
    db_session: Session,
    *,
    name: str = "Conta de Teste",
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
                    :cliente,
                    1000.00,
                    '2026-12-31',
                    'aberto'
                )
                RETURNING id
                """
            ),
            {
                "cliente": name,
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
                    'Usuário de Teste',
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


def test_memory_tables_are_registered_in_postgresql() -> None:
    inspector = inspect(engine)

    table_names = set(
        inspector.get_table_names()
    )

    assert "memory_items" in table_names
    assert "memory_evidence" in table_names


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("memory_type", "unknown"),
        ("scope_type", "unknown"),
        ("source_type", "unknown"),
        ("status", "unknown"),
    ],
)
def test_database_rejects_invalid_memory_enums(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    values = memory_values(
        **{
            field: value,
        }
    )

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        values,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", " "),
        ("content", " "),
        ("source_reference", " "),
    ],
)
def test_database_rejects_blank_required_memory_text(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    values = memory_values(
        **{
            field: value,
        }
    )

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        values,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("importance", "-0.001"),
        ("importance", "1.001"),
        ("confidence", "-0.001"),
        ("confidence", "1.001"),
    ],
)
def test_database_rejects_memory_score_out_of_range(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    values = memory_values(
        **{
            field: value,
        }
    )

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        values,
    )


def test_database_rejects_global_scope_with_account(
    db_session: Session,
) -> None:
    account_id = insert_account(db_session)

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="global",
            account_id=account_id,
        ),
    )


def test_database_rejects_account_scope_without_account(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="account",
            account_id=None,
        ),
    )


def test_database_rejects_user_scope_without_subject(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="user",
            subject_user_id=None,
        ),
    )


def test_database_rejects_account_scope_with_user_subject(
    db_session: Session,
) -> None:
    account_id = insert_account(db_session)
    user_id = insert_user(
        db_session,
        email="invalid.account.scope@example.com",
    )

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="account",
            account_id=account_id,
            subject_user_id=user_id,
        ),
    )


def test_database_rejects_user_scope_with_account(
    db_session: Session,
) -> None:
    account_id = insert_account(db_session)
    user_id = insert_user(
        db_session,
        email="invalid.user.scope@example.com",
    )

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="user",
            account_id=account_id,
            subject_user_id=user_id,
        ),
    )


def test_database_rejects_invalid_memory_validity_range(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            valid_until=(
                "2026-08-11T12:00:00+00:00"
            ),
        ),
    )


def test_database_requires_reason_for_superseded_memory(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            status="superseded",
            status_reason=None,
        ),
    )


def test_database_requires_reason_for_invalidated_memory(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            status="invalidated",
            status_reason=" ",
        ),
    )


def test_database_rejects_self_supersession(
    db_session: Session,
) -> None:
    statement = text(
        """
        INSERT INTO memory_items (
            id,
            memory_type,
            title,
            content,
            scope_type,
            confidence,
            status,
            valid_from,
            source_type,
            source_reference,
            supersedes_memory_id
        )
        VALUES (
            900001,
            'fact',
            'Self supersession',
            'Não pode apontar para si.',
            'global',
            0.8,
            'active',
            '2026-08-11T12:00:00+00:00',
            'system',
            'test:self',
            900001
        )
        """
    )

    assert_integrity_error(
        db_session,
        statement,
        {},
    )


def test_active_global_memory_key_is_unique(
    db_session: Session,
) -> None:
    insert_memory(
        db_session,
        memory_key="company.policy.currency",
    )
    db_session.commit()

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            memory_key="company.policy.currency",
        ),
    )


def test_active_account_memory_key_is_unique(
    db_session: Session,
) -> None:
    account_id = insert_account(db_session)

    insert_memory(
        db_session,
        scope_type="account",
        account_id=account_id,
        memory_key="account.credit.status",
    )
    db_session.commit()

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="account",
            account_id=account_id,
            memory_key="account.credit.status",
        ),
    )


def test_active_user_memory_key_is_unique(
    db_session: Session,
) -> None:
    user_id = insert_user(
        db_session,
        email="scope.user@example.com",
    )

    insert_memory(
        db_session,
        scope_type="user",
        subject_user_id=user_id,
        memory_key="user.preference.language",
    )
    db_session.commit()

    assert_integrity_error(
        db_session,
        MEMORY_INSERT,
        memory_values(
            scope_type="user",
            subject_user_id=user_id,
            memory_key="user.preference.language",
        ),
    )


def test_same_key_is_allowed_after_supersession(
    db_session: Session,
) -> None:
    old_memory_id = insert_memory(
        db_session,
        memory_key="company.policy.timezone",
    )
    db_session.commit()

    db_session.execute(
        text(
            """
            UPDATE memory_items
            SET
                status = 'superseded',
                status_reason = 'Nova versão',
                status_changed_at = now()
            WHERE id = :memory_id
            """
        ),
        {
            "memory_id": old_memory_id,
        },
    )
    db_session.commit()

    replacement_id = insert_memory(
        db_session,
        memory_key="company.policy.timezone",
        content="Nova versão.",
        supersedes_memory_id=old_memory_id,
    )
    db_session.commit()

    assert replacement_id != old_memory_id


def test_evidence_hash_is_unique_per_memory(
    db_session: Session,
) -> None:
    memory_id = insert_memory(db_session)
    insert_evidence(
        db_session,
        memory_id,
        evidence_hash="b" * 64,
    )
    db_session.commit()

    assert_integrity_error(
        db_session,
        EVIDENCE_INSERT,
        evidence_values(
            memory_id,
            evidence_hash="b" * 64,
        ),
    )




@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("relation", "invalid"),
        ("source_type", "invalid"),
        ("source_reference", " "),
        ("evidence_text", " "),
        ("evidence_hash", "short"),
    ],
)
def test_database_rejects_invalid_evidence_fields(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    memory_id = insert_memory(db_session)

    values = evidence_values(memory_id)
    values[field] = value

    assert_integrity_error(
        db_session,
        EVIDENCE_INSERT,
        values,
    )


@pytest.mark.parametrize(
    "weight",
    [
        "-0.001",
        "1.001",
    ],
)
def test_database_rejects_evidence_weight_out_of_range(
    db_session: Session,
    weight: str,
) -> None:
    memory_id = insert_memory(db_session)

    assert_integrity_error(
        db_session,
        EVIDENCE_INSERT,
        evidence_values(
            memory_id,
            weight=weight,
        ),
    )


def test_database_rejects_self_evidence_reference(
    db_session: Session,
) -> None:
    memory_id = insert_memory(db_session)

    assert_integrity_error(
        db_session,
        EVIDENCE_INSERT,
        evidence_values(
            memory_id,
            source_memory_id=memory_id,
        ),
    )


def test_account_delete_is_restricted_by_memory(
    db_session: Session,
) -> None:
    account_id = insert_account(db_session)

    insert_memory(
        db_session,
        scope_type="account",
        account_id=account_id,
    )
    db_session.commit()

    assert_integrity_error(
        db_session,
        text(
            """
            DELETE FROM accounts
            WHERE id = :account_id
            """
        ),
        {
            "account_id": account_id,
        },
    )


def test_subject_user_delete_is_restricted_by_memory(
    db_session: Session,
) -> None:
    user_id = insert_user(
        db_session,
        email="subject.user@example.com",
    )

    insert_memory(
        db_session,
        scope_type="user",
        subject_user_id=user_id,
    )
    db_session.commit()

    assert_integrity_error(
        db_session,
        text(
            """
            DELETE FROM users
            WHERE id = :user_id
            """
        ),
        {
            "user_id": user_id,
        },
    )


def test_created_by_user_is_set_null_on_user_delete(
    db_session: Session,
) -> None:
    user_id = insert_user(
        db_session,
        email="creator.user@example.com",
    )

    memory_id = insert_memory(
        db_session,
        created_by_user_id=user_id,
    )
    db_session.commit()

    db_session.execute(
        text(
            """
            DELETE FROM users
            WHERE id = :user_id
            """
        ),
        {
            "user_id": user_id,
        },
    )
    db_session.commit()

    created_by_user_id = db_session.execute(
        text(
            """
            SELECT created_by_user_id
            FROM memory_items
            WHERE id = :memory_id
            """
        ),
        {
            "memory_id": memory_id,
        },
    ).scalar_one()

    assert created_by_user_id is None


def test_evidence_is_deleted_with_parent_memory(
    db_session: Session,
) -> None:
    memory_id = insert_memory(db_session)
    evidence_id = insert_evidence(
        db_session,
        memory_id,
    )
    db_session.commit()

    db_session.execute(
        text(
            """
            DELETE FROM memory_items
            WHERE id = :memory_id
            """
        ),
        {
            "memory_id": memory_id,
        },
    )
    db_session.commit()

    evidence_count = db_session.execute(
        text(
            """
            SELECT count(*)
            FROM memory_evidence
            WHERE id = :evidence_id
            """
        ),
        {
            "evidence_id": evidence_id,
        },
    ).scalar_one()

    assert evidence_count == 0


def test_evidence_source_memory_is_set_null_on_delete(
    db_session: Session,
) -> None:
    target_memory_id = insert_memory(
        db_session,
        memory_key="target.memory",
    )
    source_memory_id = insert_memory(
        db_session,
        memory_key="source.memory",
    )

    evidence_id = insert_evidence(
        db_session,
        target_memory_id,
        source_memory_id=source_memory_id,
    )
    db_session.commit()

    db_session.execute(
        text(
            """
            DELETE FROM memory_items
            WHERE id = :memory_id
            """
        ),
        {
            "memory_id": source_memory_id,
        },
    )
    db_session.commit()

    stored_source_memory_id = db_session.execute(
        text(
            """
            SELECT source_memory_id
            FROM memory_evidence
            WHERE id = :evidence_id
            """
        ),
        {
            "evidence_id": evidence_id,
        },
    ).scalar_one()

    assert stored_source_memory_id is None
