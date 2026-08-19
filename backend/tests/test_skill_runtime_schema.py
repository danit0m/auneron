from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import DataError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.database import Base
from app.database.database import engine
from app.models import SkillInvocation
from app.models.skill import SkillInvocation as DirectSkillInvocation
from app.services.skill_service import SkillService


def _published_version(
    db_session: Session,
):
    service = SkillService(db_session)
    skill = service.register_skill(
        skill_key="runtime.schema-test",
        provider="auneron.core",
        display_name="Runtime schema",
        description="Skill para validar o ledger 23C.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.runtime:schema_test"
        ),
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(
        draft.id
    ).version


def _running_invocation(
    version_id: int,
    *,
    idempotency_key: str | None = None,
) -> DirectSkillInvocation:
    return DirectSkillInvocation(
        skill_version_id=version_id,
        actor_type="system",
        actor_reference="runtime-schema-test",
        actor_user_id=None,
        idempotency_key=idempotency_key,
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        status="running",
        output_payload=None,
        output_digest=None,
        output_bytes=None,
        error_code=None,
        duration_ms=None,
        started_at=datetime.now(timezone.utc),
        finished_at=None,
    )


def test_skill_invocation_model_is_exported() -> None:
    assert SkillInvocation is DirectSkillInvocation
    assert "skill_invocations" in Base.metadata.tables


def test_skill_invocation_table_exists_in_postgresql() -> None:
    inspector = inspect(engine)

    assert "skill_invocations" in inspector.get_table_names()


def test_skill_invocation_columns_use_expected_types() -> None:
    inspector = inspect(engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns(
            "skill_invocations"
        )
    }

    expected = {
        "id",
        "skill_version_id",
        "actor_type",
        "actor_reference",
        "actor_user_id",
        "idempotency_key",
        "request_fingerprint",
        "input_digest",
        "status",
        "output_payload",
        "output_digest",
        "output_bytes",
        "error_code",
        "duration_ms",
        "started_at",
        "finished_at",
    }

    assert set(columns) == expected
    assert columns["started_at"]["type"].timezone is True
    assert columns["finished_at"]["type"].timezone is True


def test_skill_invocation_foreign_key_delete_rules() -> None:
    inspector = inspect(engine)
    foreign_keys = {
        tuple(item["constrained_columns"]): item
        for item in inspector.get_foreign_keys(
            "skill_invocations"
        )
    }

    version_fk = foreign_keys[
        ("skill_version_id",)
    ]
    actor_fk = foreign_keys[
        ("actor_user_id",)
    ]

    assert version_fk["referred_table"] == "skill_versions"
    assert version_fk["options"]["ondelete"] == "RESTRICT"
    assert actor_fk["referred_table"] == "users"
    assert actor_fk["options"]["ondelete"] == "SET NULL"


def test_skill_invocation_indexes_and_unique_scope() -> None:
    inspector = inspect(engine)
    indexes = {
        item["name"]
        for item in inspector.get_indexes(
            "skill_invocations"
        )
    }
    uniques = {
        item["name"]
        for item in inspector.get_unique_constraints(
            "skill_invocations"
        )
    }

    assert {
        "ix_skill_invocations_version_started",
        "ix_skill_invocations_actor_started",
        "ix_skill_invocations_status_started",
    }.issubset(indexes)
    assert (
        "uq_skill_invocations_idempotency_scope"
        in uniques
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("actor_type", "unknown"),
        ("actor_reference", "   "),
        ("idempotency_key", "NOT-CANONICAL"),
        ("request_fingerprint", "a" * 63),
        ("input_digest", "b" * 65),
        ("status", "unknown"),
    ],
)
def test_database_rejects_invalid_invocation_identity(
    db_session: Session,
    field_name: str,
    value: str,
) -> None:
    version = _published_version(db_session)
    invocation = _running_invocation(
        version.id,
        idempotency_key="schema-db-1",
    )
    setattr(
        invocation,
        field_name,
        value,
    )
    db_session.add(invocation)

    accepted_error = (
        (DataError, IntegrityError)
        if field_name == "input_digest"
        else IntegrityError
    )

    with pytest.raises(accepted_error):
        db_session.commit()

    db_session.rollback()


def test_database_rejects_inconsistent_terminal_state(
    db_session: Session,
) -> None:
    version = _published_version(db_session)
    invocation = _running_invocation(
        version.id,
        idempotency_key="schema-db-2",
    )
    invocation.status = "succeeded"
    invocation.finished_at = datetime.now(
        timezone.utc
    )
    invocation.duration_ms = 1
    db_session.add(invocation)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_database_rejects_duplicate_idempotency_scope(
    db_session: Session,
) -> None:
    version = _published_version(db_session)

    first = _running_invocation(
        version.id,
        idempotency_key="same-key",
    )
    second = _running_invocation(
        version.id,
        idempotency_key="same-key",
    )

    db_session.add(first)
    db_session.commit()

    db_session.add(second)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
