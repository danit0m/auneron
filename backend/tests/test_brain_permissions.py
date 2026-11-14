"""
B3 -- permissao de escrita do Brain (brain.manage).

PATCH resolve, PATCH reopen e DELETE exigem brain.manage; leitura segue
exigindo apenas brain.view (dependencia do router). PostgreSQL real
(auneron_test), dados sinteticos.
"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import authorization
from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.user import User
from app.services.knowledge_service import KnowledgeService


PASSWORD = "Senha-Brain-Permissoes-123!"


def _seed_knowledge(db_session: Session) -> int:
    knowledge = KnowledgeService.create(
        db=db_session,
        agent_name="FinanceAgent",
        event_name="test_seed",
        knowledge_type="test",
        severity="medium",
        title="Seed brain permissions",
        message="Seeded by test",
    )
    return knowledge.id


def _login_client(db_session: Session, role: str) -> TestClient:
    email = f"{role}.brain.{uuid4().hex[:8]}@example.com"
    db_session.add(
        User(
            name=f"Brain {role}",
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            active=True,
        )
    )
    db_session.commit()
    user_client = TestClient(app)
    user_client.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = user_client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return user_client


def _state(db_session: Session, knowledge_id: int):
    row = db_session.execute(
        text("SELECT resolved FROM knowledge WHERE id = :id"),
        {"id": knowledge_id},
    ).first()
    db_session.rollback()
    return None if row is None else row[0]


def test_brain_manage_is_not_granted_to_viewer_or_system() -> None:
    assert not authorization.has_permission("viewer", "brain.manage")
    assert not authorization.has_permission("system", "brain.manage")
    for role in ("analyst", "manager", "executive", "administrator", "developer"):
        assert authorization.has_permission(role, "brain.manage")
        assert authorization.has_permission(role, "clients.manage")


def test_viewer_cannot_mutate_knowledge_and_nothing_changes(
    client: TestClient, db_session: Session
) -> None:
    knowledge_id = _seed_knowledge(db_session)
    viewer = _login_client(db_session, "viewer")
    try:
        responses = [
            viewer.patch(f"/brain/{knowledge_id}/resolve"),
            viewer.patch(f"/brain/{knowledge_id}/reopen"),
            viewer.delete(f"/brain/{knowledge_id}"),
        ]
    finally:
        viewer.close()

    assert [r.status_code for r in responses] == [403, 403, 403]
    assert _state(db_session, knowledge_id) is False


def test_view_only_profile_reads_but_cannot_mutate(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_id = _seed_knowledge(db_session)
    monkeypatch.setitem(
        authorization.ROLE_PERMISSIONS,
        "analyst",
        authorization.ROLE_PERMISSIONS["analyst"] - {"brain.manage"},
    )
    analyst = _login_client(db_session, "analyst")
    try:
        read = analyst.get(f"/brain/{knowledge_id}")
        responses = [
            analyst.patch(f"/brain/{knowledge_id}/resolve"),
            analyst.patch(f"/brain/{knowledge_id}/reopen"),
            analyst.delete(f"/brain/{knowledge_id}"),
        ]
    finally:
        analyst.close()

    assert read.status_code == 200
    assert [r.status_code for r in responses] == [403, 403, 403]
    assert _state(db_session, knowledge_id) is False


def test_profile_with_brain_manage_can_resolve_reopen_and_delete(
    client: TestClient, db_session: Session
) -> None:
    knowledge_id = _seed_knowledge(db_session)
    analyst = _login_client(db_session, "analyst")
    try:
        assert analyst.get(f"/brain/{knowledge_id}").status_code == 200

        resolved = analyst.patch(f"/brain/{knowledge_id}/resolve")
        assert resolved.status_code == 200
        assert resolved.json()["resolved"] is True

        reopened = analyst.patch(f"/brain/{knowledge_id}/reopen")
        assert reopened.status_code == 200
        assert reopened.json()["resolved"] is False

        assert analyst.delete(f"/brain/{knowledge_id}").status_code == 204
    finally:
        analyst.close()

    assert _state(db_session, knowledge_id) is None
