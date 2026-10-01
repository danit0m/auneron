"""
VALUE-3.2B -- POST /work-items/{work_item_id}/human-assessment.

Cobre a matriz congelada no VALUE-3.2A: primeira submissao (201),
reconciliacao por Idempotency-Key (200/duplicate), conflito de chave
com payload divergente (409), vocabulario fechado (422), WorkItem
fora de escopo (404), RBAC dedicado (work:assess_escalation, viewer
negado, system negado na fronteira de autenticacao), WorkItem terminal
permitido, multiplas declaracoes append-only, e atribuicao sempre
derivada da sessao autenticada (nunca do corpo da requisicao).
"""

from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.models.account import Account
from app.models.user import User
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


AUTHENTICATED_EMAIL = "developer.test@example.com"


def _current_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _set_role(db_session: Session, role: str) -> User:
    user = _current_user(db_session)
    user.role = role
    db_session.commit()
    db_session.refresh(user)
    return user


def _account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
) -> Account:
    account = Account(
        cliente="Cliente Escalation Write API",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=vencimento,
        status="atrasado",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _actor_user(db_session: Session, *, email: str) -> User:
    user = User(
        name="Escalation Write API Actor",
        email=email,
        password_hash=hash_password("not-used-Aa1!"),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _escalation_work_item(
    db_session: Session,
    *,
    account: Account,
    due_date: date,
    actor_user: User,
):
    result = HumanEscalationMaterializationService(
        db_session
    ).materialize(
        account=account,
        due_date=due_date,
        actor=WorkActor(
            actor_type="user",
            actor_reference=f"user:{actor_user.id}",
            actor_user_id=actor_user.id,
        ),
    )
    return result.work_item


def _setup_escalation(db_session: Session, *, email_prefix: str):
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session,
        email=f"{email_prefix}-account@example.com",
        vencimento=due_date,
    )
    actor_user = _actor_user(
        db_session, email=f"{email_prefix}-actor@example.com"
    )
    work_item = _escalation_work_item(
        db_session,
        account=account,
        due_date=due_date,
        actor_user=actor_user,
    )
    return account, actor_user, work_item


def _submit_assessment(
    client: TestClient,
    work_item_id: int,
    assessment_code: str,
    *,
    idempotency_key: str | None = None,
):
    headers = (
        {"Idempotency-Key": idempotency_key}
        if idempotency_key is not None
        else {}
    )
    return client.post(
        f"/work-items/{work_item_id}/human-assessment",
        json={"assessment_code": assessment_code},
        headers=headers,
    )


# ---------------------------------------------------------------------
# 1 -- primeira submissao: 201, created
# ---------------------------------------------------------------------


def test_first_submission_returns_201_created(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="first-submission"
    )

    response = _submit_assessment(
        client, work_item.id, "contact_made"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["observation_type"] == "human_assessment"
    assert body["assessment_code"] == "contact_made"
    assert body["escalation_work_item_id"] == work_item.id


# ---------------------------------------------------------------------
# 2 -- mesma key + mesmo payload: reconciliacao, sem duplicar
# ---------------------------------------------------------------------


def test_same_key_same_payload_reconciles_without_duplicate(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="reconcile"
    )

    first = _submit_assessment(
        client,
        work_item.id,
        "payment_promised",
        idempotency_key="reconcile:1",
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = _submit_assessment(
        client,
        work_item.id,
        "payment_promised",
        idempotency_key="reconcile:1",
    )

    assert second.status_code == 200
    assert second.json()["id"] == first_id


# ---------------------------------------------------------------------
# 3 -- mesma key + payload divergente: 409
# ---------------------------------------------------------------------


def test_same_key_different_payload_returns_409(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="conflict"
    )

    first = _submit_assessment(
        client,
        work_item.id,
        "contact_made",
        idempotency_key="conflict:1",
    )
    assert first.status_code == 201

    second = _submit_assessment(
        client,
        work_item.id,
        "payment_refused",
        idempotency_key="conflict:1",
    )

    assert second.status_code == 409


# ---------------------------------------------------------------------
# 4 -- vocabulario fechado: 422
# ---------------------------------------------------------------------


def test_invalid_assessment_code_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="invalid-code"
    )

    response = _submit_assessment(
        client, work_item.id, "not_a_real_code"
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# 5 -- WorkItem inexistente: 404
# ---------------------------------------------------------------------


def test_nonexistent_work_item_returns_404(
    client: TestClient,
) -> None:
    response = _submit_assessment(
        client, 999_999_999, "contact_made"
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------
# 6/7 -- RBAC: viewer negado, system negado na fronteira de autenticacao
# ---------------------------------------------------------------------


def test_viewer_role_is_denied_with_403(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="viewer-denied"
    )

    _set_role(db_session, "viewer")
    response = _submit_assessment(
        client, work_item.id, "contact_made"
    )

    assert response.status_code == 403


def test_system_role_is_denied_with_401(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="system-denied"
    )

    _set_role(db_session, "system")
    response = _submit_assessment(
        client, work_item.id, "contact_made"
    )

    # Mesma fronteira ja congelada no VALUE-2.4: "system" nao e uma
    # sessao humana valida, e require_user_session intercepta antes
    # do RBAC (work:assess_escalation) ser avaliado -- 401, nao 403.
    assert response.status_code == 401


def test_role_with_permission_is_allowed(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="role-allowed"
    )

    _set_role(db_session, "analyst")
    response = _submit_assessment(
        client, work_item.id, "contact_made"
    )

    assert response.status_code == 201


# ---------------------------------------------------------------------
# 8 -- WorkItem terminal: permitido
# ---------------------------------------------------------------------


def test_terminal_work_item_still_accepts_assessment(
    client: TestClient,
    db_session: Session,
) -> None:
    _, actor_user, work_item = _setup_escalation(
        db_session, email_prefix="terminal"
    )

    WorkManagerService(db_session).transition_status(
        work_item.id,
        expected_version=work_item.version,
        actor=WorkActor(
            actor_type="user",
            actor_reference=f"user:{actor_user.id}",
            actor_user_id=actor_user.id,
        ),
        status="cancelled",
        reason="teste de WorkItem terminal",
    )

    response = _submit_assessment(
        client, work_item.id, "payment_refused"
    )

    assert response.status_code == 201


# ---------------------------------------------------------------------
# 9 -- multiplas declaracoes distintas: append-only
# ---------------------------------------------------------------------


def test_multiple_distinct_assessments_are_all_preserved(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="append-only"
    )

    first = _submit_assessment(
        client, work_item.id, "contact_made"
    )
    second = _submit_assessment(
        client, work_item.id, "payment_promised"
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    listing = client.get(
        f"/work-items/{work_item.id}/escalation-observations"
    ).json()
    assert len(listing["items"]) == 2
    codes = {
        item["assessment_code"] for item in listing["items"]
    }
    assert codes == {"contact_made", "payment_promised"}


# ---------------------------------------------------------------------
# 10/11 -- atribuicao sempre vem da sessao autenticada, nunca do corpo
# ---------------------------------------------------------------------


def test_declared_by_fields_come_from_authenticated_session(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="attribution"
    )

    _set_role(db_session, "manager")
    response = _submit_assessment(
        client, work_item.id, "unreachable"
    )
    assert response.status_code == 201

    authenticated_user = _current_user(db_session)

    listing = client.get(
        f"/work-items/{work_item.id}/escalation-observations"
    ).json()
    item = listing["items"][0]

    assert item["declared_by_user_id"] == authenticated_user.id
    # declared_by_role nunca e serializado na leitura (VALUE-2.4,
    # test_idempotency_key_and_declared_by_role_never_serialized) --
    # a prova de que o valor persistido e o papel autenticado (e nao
    # um valor arbitrario do corpo, que o schema nem aceita) e feita
    # direto no banco.
    from app.models.escalation_observation import (
        EscalationObservation,
    )

    persisted = (
        db_session.query(EscalationObservation)
        .filter(EscalationObservation.id == item["id"])
        .one()
    )
    assert persisted.declared_by_role == "manager"


def test_request_body_cannot_set_technical_identifiers(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, work_item = _setup_escalation(
        db_session, email_prefix="no-technical-ids"
    )

    response = client.post(
        f"/work-items/{work_item.id}/human-assessment",
        json={
            "assessment_code": "contact_made",
            "declared_by_user_id": 999_999,
            "declared_by_role": "administrator",
        },
    )

    # WorkSchema usa extra="forbid" (app/schemas/work.py:77-79) --
    # qualquer campo tecnico fora do contrato e rejeitado (422), nao
    # apenas descartado silenciosamente. Garantia mais forte do que
    # "ignorado": a tentativa de enviar declared_by_user_id/
    # declared_by_role no corpo nunca e aceita.
    assert response.status_code == 422
