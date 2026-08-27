import json

import pytest
from sqlalchemy import delete
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)


@pytest.fixture(autouse=True)
def clear_advisory_proposals(db_session):
    db_session.execute(
        delete(AuthenticatedAdvisoryProposal)
    )
    db_session.commit()
    yield
    db_session.rollback()
    db_session.execute(
        delete(AuthenticatedAdvisoryProposal)
    )
    db_session.commit()


def _insert(
    db_session,
    *,
    user_id: int = 1,
    session_id: int = 1,
    source: str = "authenticated_http_session",
    request_id: str | None = "request-1",
    key: str = "db-key",
    protocol: str = "authenticated_advisory_v1",
    digest: str = "a" * 64,
    agent_count: int = 0,
    binding_count: int = 0,
    snapshot_bytes: int = 100,
):
    payload = {
        "decision_name": "db-contract",
        "selected_agents": [],
        "agents": [],
    }

    return db_session.execute(
        text(
            """
            INSERT INTO authenticated_advisory_proposals (
                authority_user_id,
                auth_session_id,
                authority_source,
                request_id,
                idempotency_key,
                protocol,
                snapshot_payload,
                snapshot_digest,
                agent_count,
                binding_count,
                snapshot_bytes
            )
            VALUES (
                :user_id,
                :session_id,
                :source,
                :request_id,
                :key,
                :protocol,
                CAST(:payload AS jsonb),
                :digest,
                :agent_count,
                :binding_count,
                :snapshot_bytes
            )
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "session_id": session_id,
            "source": source,
            "request_id": request_id,
            "key": key,
            "protocol": protocol,
            "payload": json.dumps(payload),
            "digest": digest,
            "agent_count": agent_count,
            "binding_count": binding_count,
            "snapshot_bytes": snapshot_bytes,
        },
    ).scalar_one()


def test_model_and_migration_expose_exact_immutable_fields_and_constraints(
    db_session,
):
    inspector = inspect(
        db_session.get_bind()
    )

    columns = [
        column["name"]
        for column in inspector.get_columns(
            "authenticated_advisory_proposals"
        )
    ]

    assert columns == [
        "id",
        "authority_user_id",
        "auth_session_id",
        "authority_source",
        "request_id",
        "idempotency_key",
        "protocol",
        "snapshot_payload",
        "snapshot_digest",
        "agent_count",
        "binding_count",
        "snapshot_bytes",
        "created_at",
    ]

    foreign_keys = inspector.get_foreign_keys(
        "authenticated_advisory_proposals"
    )
    assert foreign_keys == []

    unique_constraints = inspector.get_unique_constraints(
        "authenticated_advisory_proposals"
    )
    names = {
        item["name"]
        for item in unique_constraints
    }
    assert (
        "uq_authenticated_advisory_proposals_authority_session_key"
        in names
    )


def test_database_rejects_nonpositive_authority_and_session_ids(
    db_session,
):
    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            user_id=0,
            key="bad-user",
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            session_id=0,
            key="bad-session",
        )
    db_session.rollback()


def test_database_rejects_invalid_source_and_protocol(
    db_session,
):
    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            source="caller_supplied",
            key="bad-source",
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            protocol="other_protocol",
            key="bad-protocol",
        )
    db_session.rollback()


def test_database_rejects_noncanonical_key_and_digest(
    db_session,
):
    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            key="UPPER",
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            key="good-key",
            digest="A" * 64,
        )
    db_session.rollback()


def test_database_rejects_agent_binding_and_snapshot_bounds(
    db_session,
):
    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            key="bad-agents",
            agent_count=33,
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            key="bad-bindings",
            binding_count=513,
        )
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            key="bad-bytes",
            snapshot_bytes=65537,
        )
    db_session.rollback()


def test_database_unique_identity_allows_same_key_in_different_sessions(
    db_session,
):
    first = _insert(
        db_session,
        user_id=9,
        session_id=10,
        key="identity-key",
    )
    db_session.commit()

    second = _insert(
        db_session,
        user_id=9,
        session_id=11,
        key="identity-key",
    )
    db_session.commit()

    assert first != second

    with pytest.raises(IntegrityError):
        _insert(
            db_session,
            user_id=9,
            session_id=10,
            key="identity-key",
        )
    db_session.rollback()
