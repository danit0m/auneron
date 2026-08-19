from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


SKILL_INSERT = text(
    """
    INSERT INTO skills (
        skill_key,
        provider,
        display_name,
        description,
        status,
        created_by_user_id
    )
    VALUES (
        :skill_key,
        :provider,
        :display_name,
        :description,
        :status,
        :created_by_user_id
    )
    RETURNING id
    """
)


VERSION_INSERT = text(
    """
    INSERT INTO skill_versions (
        skill_id,
        version,
        runtime_kind,
        handler_reference,
        execution_mode,
        manifest_digest,
        timeout_seconds,
        max_output_bytes,
        status,
        published_at,
        retired_at,
        created_by_user_id
    )
    VALUES (
        :skill_id,
        :version,
        :runtime_kind,
        :handler_reference,
        :execution_mode,
        :manifest_digest,
        :timeout_seconds,
        :max_output_bytes,
        :status,
        :published_at,
        :retired_at,
        :created_by_user_id
    )
    RETURNING id
    """
)


CAPABILITY_INSERT = text(
    """
    INSERT INTO skill_capabilities (
        skill_version_id,
        capability_key,
        access_mode,
        resource_scope,
        required
    )
    VALUES (
        :skill_version_id,
        :capability_key,
        :access_mode,
        :resource_scope,
        :required
    )
    RETURNING id
    """
)


BINDING_INSERT = text(
    """
    INSERT INTO agent_skill_bindings (
        agent_name,
        skill_version_id,
        priority,
        enabled,
        created_by_user_id
    )
    VALUES (
        :agent_name,
        :skill_version_id,
        :priority,
        :enabled,
        :created_by_user_id
    )
    RETURNING id
    """
)


def skill_values(
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "skill_key": "finance.account-summary",
        "provider": "auneron.core",
        "display_name": "Resumo financeiro",
        "description": (
            "Produz um resumo financeiro controlado."
        ),
        "status": "active",
        "created_by_user_id": None,
    }
    values.update(overrides)
    return values


def version_values(
    skill_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "skill_id": skill_id,
        "version": "1.0.0",
        "runtime_kind": "internal_python",
        "handler_reference": (
            "app.skills.finance:account_summary"
        ),
        "execution_mode": "read_only",
        "manifest_digest": "a" * 64,
        "timeout_seconds": 30,
        "max_output_bytes": 65536,
        "status": "draft",
        "published_at": None,
        "retired_at": None,
        "created_by_user_id": None,
    }
    values.update(overrides)
    return values


def capability_values(
    skill_version_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "skill_version_id": skill_version_id,
        "capability_key": "accounts.summary",
        "access_mode": "read",
        "resource_scope": "account",
        "required": True,
    }
    values.update(overrides)
    return values


def binding_values(
    skill_version_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "agent_name": "FinanceAgent",
        "skill_version_id": skill_version_id,
        "priority": 100,
        "enabled": True,
        "created_by_user_id": None,
    }
    values.update(overrides)
    return values


def insert_skill(
    db_session: Session,
    **overrides: Any,
) -> int:
    return int(
        db_session.execute(
            SKILL_INSERT,
            skill_values(**overrides),
        ).scalar_one()
    )


def insert_version(
    db_session: Session,
    skill_id: int,
    **overrides: Any,
) -> int:
    return int(
        db_session.execute(
            VERSION_INSERT,
            version_values(
                skill_id,
                **overrides,
            ),
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
                    'Autor de skill',
                    :email,
                    'hash-de-teste',
                    'developer',
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


def assert_database_rejection(
    db_session: Session,
    statement,
    parameters: Mapping[str, Any],
) -> None:
    with pytest.raises((DataError, IntegrityError)):
        db_session.execute(
            statement,
            parameters,
        )
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    "field",
    [
        "skill_key",
        "provider",
        "display_name",
        "description",
    ],
)
def test_database_rejects_blank_skill_identity(
    db_session: Session,
    field: str,
) -> None:
    assert_integrity_error(
        db_session,
        SKILL_INSERT,
        skill_values(
            **{
                field: " ",
            }
        ),
    )


@pytest.mark.parametrize(
    "skill_key",
    [
        "Finance.Account-Summary",
        " finance.account-summary",
        "finance.account-summary ",
    ],
)
def test_database_rejects_noncanonical_skill_key(
    db_session: Session,
    skill_key: str,
) -> None:
    assert_integrity_error(
        db_session,
        SKILL_INSERT,
        skill_values(skill_key=skill_key),
    )


def test_database_rejects_invalid_skill_status(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        SKILL_INSERT,
        skill_values(status="unknown"),
    )


def test_database_rejects_duplicate_skill_key(
    db_session: Session,
) -> None:
    values = skill_values()
    db_session.execute(
        SKILL_INSERT,
        values,
    ).scalar_one()

    assert_integrity_error(
        db_session,
        SKILL_INSERT,
        values,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_kind", "unknown"),
        ("execution_mode", "unknown"),
        ("status", "unknown"),
    ],
)
def test_database_rejects_invalid_version_enums(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    skill_id = insert_skill(db_session)

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        version_values(
            skill_id,
            **{
                field: value,
            },
        ),
    )


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "handler_reference",
    ],
)
def test_database_rejects_blank_version_identity(
    db_session: Session,
    field: str,
) -> None:
    skill_id = insert_skill(db_session)

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        version_values(
            skill_id,
            **{
                field: " ",
            },
        ),
    )


@pytest.mark.parametrize(
    "manifest_digest",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
    ],
)
def test_database_rejects_invalid_manifest_digest(
    db_session: Session,
    manifest_digest: str,
) -> None:
    skill_id = insert_skill(db_session)

    assert_database_rejection(
        db_session,
        VERSION_INSERT,
        version_values(
            skill_id,
            manifest_digest=manifest_digest,
        ),
    )


@pytest.mark.parametrize(
    "timeout_seconds",
    [
        0,
        301,
    ],
)
def test_database_rejects_timeout_outside_limit(
    db_session: Session,
    timeout_seconds: int,
) -> None:
    skill_id = insert_skill(db_session)

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        version_values(
            skill_id,
            timeout_seconds=timeout_seconds,
        ),
    )


@pytest.mark.parametrize(
    "max_output_bytes",
    [
        1023,
        1048577,
    ],
)
def test_database_rejects_output_limit_outside_range(
    db_session: Session,
    max_output_bytes: int,
) -> None:
    skill_id = insert_skill(db_session)

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        version_values(
            skill_id,
            max_output_bytes=max_output_bytes,
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "status": "draft",
            "published_at": "2026-08-17T12:00:00+00:00",
        },
        {
            "status": "draft",
            "retired_at": "2026-08-17T13:00:00+00:00",
        },
        {
            "status": "published",
            "published_at": None,
        },
        {
            "status": "published",
            "published_at": "2026-08-17T12:00:00+00:00",
            "retired_at": "2026-08-17T13:00:00+00:00",
        },
        {
            "status": "retired",
            "published_at": None,
            "retired_at": "2026-08-17T13:00:00+00:00",
        },
        {
            "status": "retired",
            "published_at": "2026-08-17T12:00:00+00:00",
            "retired_at": None,
        },
        {
            "status": "retired",
            "published_at": "2026-08-17T13:00:00+00:00",
            "retired_at": "2026-08-17T12:00:00+00:00",
        },
    ],
)
def test_database_enforces_version_status_timestamps(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    skill_id = insert_skill(db_session)

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        version_values(
            skill_id,
            **overrides,
        ),
    )


def test_database_rejects_duplicate_skill_version(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    first = version_values(skill_id)
    db_session.execute(
        VERSION_INSERT,
        first,
    ).scalar_one()

    duplicate = version_values(
        skill_id,
        manifest_digest="b" * 64,
    )

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        duplicate,
    )


def test_database_rejects_duplicate_manifest_for_skill(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    first = version_values(skill_id)
    db_session.execute(
        VERSION_INSERT,
        first,
    ).scalar_one()

    duplicate = version_values(
        skill_id,
        version="1.1.0",
    )

    assert_integrity_error(
        db_session,
        VERSION_INSERT,
        duplicate,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("access_mode", "unknown"),
        ("resource_scope", "unknown"),
    ],
)
def test_database_rejects_invalid_capability_enums(
    db_session: Session,
    field: str,
    value: str,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )

    assert_integrity_error(
        db_session,
        CAPABILITY_INSERT,
        capability_values(
            version_id,
            **{
                field: value,
            },
        ),
    )


@pytest.mark.parametrize(
    "capability_key",
    [
        " ",
        "Accounts.Summary",
        " accounts.summary",
        "accounts.summary ",
    ],
)
def test_database_rejects_invalid_capability_key(
    db_session: Session,
    capability_key: str,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )

    assert_integrity_error(
        db_session,
        CAPABILITY_INSERT,
        capability_values(
            version_id,
            capability_key=capability_key,
        ),
    )


def test_database_rejects_duplicate_capability_declaration(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )
    values = capability_values(version_id)
    db_session.execute(
        CAPABILITY_INSERT,
        values,
    ).scalar_one()

    assert_integrity_error(
        db_session,
        CAPABILITY_INSERT,
        values,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "agent_name": " ",
        },
        {
            "priority": 0,
        },
        {
            "priority": 1001,
        },
    ],
)
def test_database_rejects_invalid_agent_binding(
    db_session: Session,
    overrides: dict[str, Any],
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )

    assert_integrity_error(
        db_session,
        BINDING_INSERT,
        binding_values(
            version_id,
            **overrides,
        ),
    )


def test_database_rejects_duplicate_agent_binding(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )
    values = binding_values(version_id)
    db_session.execute(
        BINDING_INSERT,
        values,
    ).scalar_one()

    assert_integrity_error(
        db_session,
        BINDING_INSERT,
        values,
    )


def test_database_restricts_skill_delete_with_version(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    insert_version(
        db_session,
        skill_id,
    )

    assert_integrity_error(
        db_session,
        text(
            "DELETE FROM skills WHERE id = :skill_id"
        ),
        {
            "skill_id": skill_id,
        },
    )


def test_database_cascades_capabilities_with_version(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )
    db_session.execute(
        CAPABILITY_INSERT,
        capability_values(version_id),
    ).scalar_one()

    db_session.execute(
        text(
            """
            DELETE FROM skill_versions
            WHERE id = :version_id
            """
        ),
        {
            "version_id": version_id,
        },
    )
    db_session.commit()

    capability_count = db_session.execute(
        text(
            """
            SELECT count(*)
            FROM skill_capabilities
            WHERE skill_version_id = :version_id
            """
        ),
        {
            "version_id": version_id,
        },
    ).scalar_one()

    assert capability_count == 0


def test_database_restricts_version_delete_with_binding(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
    )
    db_session.execute(
        BINDING_INSERT,
        binding_values(version_id),
    ).scalar_one()

    assert_integrity_error(
        db_session,
        text(
            """
            DELETE FROM skill_versions
            WHERE id = :version_id
            """
        ),
        {
            "version_id": version_id,
        },
    )


def test_database_nulls_creator_without_losing_catalog(
    db_session: Session,
) -> None:
    user_id = insert_user(
        db_session,
        email="skill-author@example.com",
    )
    skill_id = insert_skill(
        db_session,
        created_by_user_id=user_id,
    )
    version_id = insert_version(
        db_session,
        skill_id,
        created_by_user_id=user_id,
    )
    binding_id = db_session.execute(
        BINDING_INSERT,
        binding_values(
            version_id,
            created_by_user_id=user_id,
        ),
    ).scalar_one()

    db_session.execute(
        text("DELETE FROM users WHERE id = :user_id"),
        {
            "user_id": user_id,
        },
    )
    db_session.commit()

    creator_ids = db_session.execute(
        text(
            """
            SELECT
                (SELECT created_by_user_id
                 FROM skills
                 WHERE id = :skill_id) AS skill_creator,
                (SELECT created_by_user_id
                 FROM skill_versions
                 WHERE id = :version_id) AS version_creator,
                (SELECT created_by_user_id
                 FROM agent_skill_bindings
                 WHERE id = :binding_id) AS binding_creator
            """
        ),
        {
            "skill_id": skill_id,
            "version_id": version_id,
            "binding_id": binding_id,
        },
    ).one()

    assert creator_ids.skill_creator is None
    assert creator_ids.version_creator is None
    assert creator_ids.binding_creator is None


def test_complete_agent_skill_aggregate_is_persisted(
    db_session: Session,
) -> None:
    skill_id = insert_skill(db_session)
    version_id = insert_version(
        db_session,
        skill_id,
        status="published",
        published_at="2026-08-17T12:00:00+00:00",
    )
    db_session.execute(
        CAPABILITY_INSERT,
        capability_values(version_id),
    ).scalar_one()
    db_session.execute(
        CAPABILITY_INSERT,
        capability_values(
            version_id,
            capability_key="memory.retrieve",
            resource_scope="internal",
        ),
    ).scalar_one()
    db_session.execute(
        BINDING_INSERT,
        binding_values(version_id),
    ).scalar_one()
    db_session.commit()

    counts = db_session.execute(
        text(
            """
            SELECT
                (SELECT count(*) FROM skills) AS skills,
                (SELECT count(*) FROM skill_versions) AS versions,
                (SELECT count(*) FROM skill_capabilities) AS capabilities,
                (SELECT count(*) FROM agent_skill_bindings) AS bindings
            """
        )
    ).one()

    assert counts.skills == 1
    assert counts.versions == 1
    assert counts.capabilities == 2
    assert counts.bindings == 1
