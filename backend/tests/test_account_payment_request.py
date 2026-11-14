"""
Fatias A2/A3: registrar pagamento pela tela, com data efetiva.

Cobre POST /accounts/{id}/request-mark-paid (pedido + resumo na mesma
transacao), a data efetiva nas respostas de aprovacao e de conta, e a
execucao governada existente (execute-mark-paid) com a data no digest.
PostgreSQL real (auneron_test), dados sinteticos.

Fluxo: A (gerente) pede, B (gerente) aprova, A executa. O decisor nao
pode executar (regra existente).
"""

import os
from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.receivable_lifecycle import business_today
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.account_payment import AccountPayment
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalRequest
from app.models.approval_request_input import ApprovalRequestInput
from app.models.skill import SkillInvocation
from app.models.user import User
from app.repositories.skill_repository import SkillRepository
from scripts.register_account_mark_paid_skill import (
    main as register_account_mark_paid_skill,
)


PASSWORD = "Senha-Pagamento-Tela-123!"


def _login_as(db_session: Session, email: str, role: str) -> TestClient:
    db_session.add(
        User(
            name=f"Usuario {email}",
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            active=True,
        )
    )
    db_session.commit()
    test_client = TestClient(app)
    test_client.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = test_client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return test_client


def _requester(db_session: Session) -> TestClient:
    return _login_as(db_session, "gerente.pede@example.com", "manager")


def _approver(db_session: Session) -> TestClient:
    return _login_as(db_session, "gerente.aprova@example.com", "manager")


def _make_account(
    db_session: Session, *, email: str, status: str = "aberto"
) -> Account:
    account = Account(
        cliente="Cliente Pagamento Tela Teste",
        email=email,
        whatsapp=None,
        valor=420,
        vencimento=date(2026, 6, 15),
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _register_skill(db_session: Session) -> int:
    register_account_mark_paid_skill()
    repository = SkillRepository(db_session)
    skill = repository.find_skill_by_key("account.mark_paid")
    versions = [
        version
        for version in repository.list_versions(skill.id)
        if version.status == "published"
    ]
    assert len(versions) == 1
    return versions[0].id


def _request(
    client: TestClient,
    account_id: int,
    paid_on,
    *,
    key: str = "pay-req-1",
):
    return client.post(
        f"/accounts/{account_id}/request-mark-paid",
        json={"paid_on": paid_on if isinstance(paid_on, str) else paid_on.isoformat()},
        headers={"Idempotency-Key": key},
    )


def _decide(client: TestClient, request_id: int, decision: str) -> None:
    response = client.post(
        f"/approvals/{request_id}/decision", json={"decision": decision}
    )
    assert response.status_code == 200, response.text


def _execute(client: TestClient, account_id: int, request_id: int, status="aberto"):
    return client.post(
        f"/accounts/{account_id}/execute-mark-paid",
        json={"approval_request_id": request_id, "expected_status": status},
    )


def _count(db_session: Session, model) -> int:
    db_session.expire_all()
    return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def _snapshot(db_session: Session) -> dict:
    return {
        model.__name__: _count(db_session, model)
        for model in (
            ApprovalRequest,
            ApprovalRequestInput,
            ApprovalConsumption,
            SkillInvocation,
            AccountEvent,
            AccountPayment,
        )
    }


def _status(db_session: Session, account_id: int) -> str:
    db_session.expire_all()
    return db_session.get(Account, account_id).status


def _approved_request(
    db_session: Session, account: Account, paid_on: date, key: str = "pay-flow-1"
):
    """A pede, B aprova. Devolve (A, B, request_id)."""
    _register_skill(db_session)
    requester = _requester(db_session)
    approver = _approver(db_session)
    created = _request(requester, account.id, paid_on, key=key)
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]
    _decide(approver, request_id, "approved")
    return requester, approver, request_id


# ----------------------------------------------------------------- pedido


def test_manager_request_creates_pending_approval_and_summary(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="pedido.ok@example.com")
    version_id = _register_skill(db_session)
    requester = _requester(db_session)
    paid_on = business_today() - timedelta(days=3)

    response = _request(requester, account.id, paid_on)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert body["duplicate"] is False
    request = body["request"]
    assert request["status"] == "pending"
    assert request["skill_key"] == "account.mark_paid"
    assert request["skill_version_id"] == version_id
    assert request["target_account_id"] == account.id
    assert request["effective_date"] == paid_on.isoformat()

    assert _snapshot(db_session) == {
        "ApprovalRequest": 1,
        "ApprovalRequestInput": 1,
        "ApprovalConsumption": 0,
        "SkillInvocation": 0,
        "AccountEvent": 0,
        "AccountPayment": 0,
    }
    summary = db_session.execute(select(ApprovalRequestInput)).scalar_one()
    assert summary.approval_request_id == request["request_id"]
    assert summary.effective_date == paid_on
    assert _status(db_session, account.id) == "aberto"


def test_request_on_overdue_account_is_accepted(db_session: Session) -> None:
    account = _make_account(
        db_session, email="pedido.atrasado@example.com", status="atrasado"
    )
    _register_skill(db_session)
    response = _request(_requester(db_session), account.id, business_today())

    assert response.status_code == 201, response.text
    assert response.json()["request"]["effective_date"] == business_today().isoformat()


def test_payment_date_before_account_creation_is_accepted(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="pedido.antigo@example.com")
    _register_skill(db_session)

    response = _request(_requester(db_session), account.id, date(2020, 1, 1))

    assert response.status_code == 201, response.text
    assert response.json()["request"]["effective_date"] == "2020-01-01"


def test_analyst_and_viewer_cannot_request(db_session: Session) -> None:
    account = _make_account(db_session, email="pedido.rbac@example.com")
    _register_skill(db_session)
    analyst = _login_as(db_session, "analista.pede@example.com", "analyst")
    viewer = _login_as(db_session, "viewer.pede@example.com", "viewer")
    before = _snapshot(db_session)

    for client, key in ((analyst, "rbac-analyst"), (viewer, "rbac-viewer")):
        response = _request(client, account.id, business_today(), key=key)
        assert response.status_code == 403, response.text

    assert _snapshot(db_session) == before
    assert before["ApprovalRequest"] == 0


def test_future_date_is_rejected(db_session: Session) -> None:
    account = _make_account(db_session, email="pedido.futuro@example.com")
    _register_skill(db_session)
    before = _snapshot(db_session)

    response = _request(
        _requester(db_session), account.id, business_today() + timedelta(days=1)
    )

    assert response.status_code == 422, response.text
    assert _snapshot(db_session) == before


def test_invalid_date_format_and_missing_key_are_rejected(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="pedido.formato@example.com")
    _register_skill(db_session)
    requester = _requester(db_session)

    bad_format = _request(requester, account.id, "31/12/2026")
    assert bad_format.status_code == 422, bad_format.text

    no_key = requester.post(
        f"/accounts/{account.id}/request-mark-paid",
        json={"paid_on": business_today().isoformat()},
    )
    assert no_key.status_code == 422, no_key.text
    assert _snapshot(db_session)["ApprovalRequest"] == 0


def test_already_paid_account_is_rejected(db_session: Session) -> None:
    account = _make_account(
        db_session, email="pedido.pago@example.com", status="pago"
    )
    _register_skill(db_session)
    before = _snapshot(db_session)

    response = _request(_requester(db_session), account.id, business_today())

    assert response.status_code == 409, response.text
    assert _snapshot(db_session) == before


def test_missing_account_is_404(db_session: Session) -> None:
    _register_skill(db_session)
    response = _request(_requester(db_session), 999999, business_today())
    assert response.status_code == 404, response.text


def test_unregistered_skill_returns_clear_409(db_session: Session) -> None:
    account = _make_account(db_session, email="pedido.semskill@example.com")
    requester = _requester(db_session)

    response = _request(requester, account.id, business_today())

    assert response.status_code == 409, response.text
    assert "account.mark_paid" in response.json()["detail"]
    assert _snapshot(db_session)["ApprovalRequest"] == 0


def test_duplicate_pending_request_is_blocked(db_session: Session) -> None:
    account = _make_account(db_session, email="pedido.duplicado@example.com")
    _register_skill(db_session)
    requester = _requester(db_session)
    first = _request(requester, account.id, business_today(), key="dup-1")
    assert first.status_code == 201, first.text
    before = _snapshot(db_session)

    second = _request(requester, account.id, business_today(), key="dup-2")

    assert second.status_code == 409, second.text
    assert _snapshot(db_session) == before
    assert before["ApprovalRequest"] == 1
    assert before["ApprovalRequestInput"] == 1


def test_same_key_retry_is_idempotent_and_keeps_one_summary(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="pedido.retry@example.com")
    _register_skill(db_session)
    requester = _requester(db_session)
    first = _request(requester, account.id, business_today(), key="retry-1")
    assert first.status_code == 201, first.text

    again = _request(requester, account.id, business_today(), key="retry-1")

    assert again.status_code == 200, again.text
    assert again.json()["duplicate"] is True
    assert again.json()["request"]["request_id"] == first.json()["request"]["request_id"]
    assert _snapshot(db_session)["ApprovalRequest"] == 1
    assert _snapshot(db_session)["ApprovalRequestInput"] == 1


# ------------------------------------------------------ leitura (aprovacao)


def test_approver_sees_effective_date_in_list_and_detail(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="pedido.leitura@example.com")
    _register_skill(db_session)
    requester = _requester(db_session)
    approver = _approver(db_session)
    paid_on = business_today() - timedelta(days=1)
    created = _request(requester, account.id, paid_on)
    request_id = created.json()["request"]["request_id"]

    listed = approver.get("/approvals", params={"status": "pending"})
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["request_id"] for item in items] == [request_id]
    assert items[0]["effective_date"] == paid_on.isoformat()

    detail = approver.get(f"/approvals/{request_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["request"]["effective_date"] == paid_on.isoformat()


def test_request_created_by_generic_route_has_no_effective_date(
    client: TestClient, db_session: Session
) -> None:
    account = _make_account(db_session, email="pedido.generico@example.com")
    version_id = _register_skill(db_session)
    created = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account.id,
                "expected_status": "aberto",
            }
        },
        headers={"Idempotency-Key": "generic-1"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["request"]["effective_date"] is None
    assert _snapshot(db_session)["ApprovalRequestInput"] == 0


# --------------------------------------------------------------- execucao


def test_nothing_changes_without_approval_or_when_rejected(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="exec.semaprov@example.com")
    _register_skill(db_session)
    requester = _requester(db_session)
    approver = _approver(db_session)
    created = _request(requester, account.id, business_today())
    request_id = created.json()["request"]["request_id"]
    before = _snapshot(db_session)

    pending = _execute(requester, account.id, request_id)
    assert pending.status_code == 409, pending.text
    assert _snapshot(db_session) == before

    _decide(approver, request_id, "rejected")
    before = _snapshot(db_session)
    rejected = _execute(requester, account.id, request_id)
    assert rejected.status_code == 409, rejected.text
    assert _snapshot(db_session) == before
    assert before["AccountPayment"] == 0
    assert _status(db_session, account.id) == "aberto"


def test_decider_cannot_execute(db_session: Session) -> None:
    account = _make_account(db_session, email="exec.decisor@example.com")
    _, approver, request_id = _approved_request(
        db_session, account, business_today()
    )
    before = _snapshot(db_session)

    response = _execute(approver, account.id, request_id)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "approval_forbidden"
    assert _snapshot(db_session) == before
    assert _status(db_session, account.id) == "aberto"


def test_full_flow_records_payment_date_event_and_reads(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="exec.completo@example.com")
    paid_on = business_today() - timedelta(days=5)
    requester, approver, request_id = _approved_request(
        db_session, account, paid_on
    )
    assert _snapshot(db_session)["AccountPayment"] == 0

    response = _execute(requester, account.id, request_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["output"]["new_status"] == "pago"
    assert body["output"]["paid_on"] == paid_on.isoformat()

    assert _status(db_session, account.id) == "pago"
    payment = db_session.execute(select(AccountPayment)).scalar_one()
    assert payment.account_id == account.id
    assert payment.paid_on == paid_on
    assert payment.approval_request_id == request_id
    event = db_session.execute(
        select(AccountEvent).where(AccountEvent.account_id == account.id)
    ).scalar_one()
    assert (event.previous_status, event.new_status) == ("aberto", "pago")
    assert _snapshot(db_session) == {
        "ApprovalRequest": 1,
        "ApprovalRequestInput": 1,
        "ApprovalConsumption": 1,
        "SkillInvocation": 1,
        "AccountEvent": 1,
        "AccountPayment": 1,
    }

    listed = requester.get("/accounts/")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["id"] == account.id)
    assert row["status"] == "pago"
    assert row["paid_on"] == paid_on.isoformat()
    single = requester.get(f"/accounts/{account.id}")
    assert single.json()["paid_on"] == paid_on.isoformat()

    approvals = approver.get("/approvals", params={"status": "approved"})
    assert approvals.status_code == 200, approvals.text
    item = next(
        item for item in approvals.json()["items"] if item["request_id"] == request_id
    )
    assert item["effective_date"] == paid_on.isoformat()


def test_replay_is_idempotent_and_does_not_duplicate_payment(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="exec.replay@example.com")
    paid_on = business_today()
    requester, _, request_id = _approved_request(db_session, account, paid_on)
    first = _execute(requester, account.id, request_id)
    assert first.status_code == 200, first.text
    after_first = _snapshot(db_session)

    second = _execute(requester, account.id, request_id)

    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["output"]["paid_on"] == paid_on.isoformat()
    assert _snapshot(db_session) == after_first
    assert after_first["AccountPayment"] == 1


def test_tampered_summary_date_breaks_digest_and_changes_nothing(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="exec.adulterado@example.com")
    paid_on = business_today() - timedelta(days=2)
    requester, _, request_id = _approved_request(db_session, account, paid_on)
    summary = db_session.execute(select(ApprovalRequestInput)).scalar_one()
    summary.effective_date = paid_on + timedelta(days=1)
    db_session.commit()
    before = _snapshot(db_session)

    response = _execute(requester, account.id, request_id)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "approval_conflict"
    assert _snapshot(db_session) == before
    assert before["AccountPayment"] == 0
    assert _status(db_session, account.id) == "aberto"


def test_old_request_without_summary_executes_as_before(
    client: TestClient, db_session: Session
) -> None:
    account = _make_account(db_session, email="exec.antigo@example.com")
    version_id = _register_skill(db_session)
    created = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account.id,
                "expected_status": "aberto",
            }
        },
        headers={"Idempotency-Key": "old-1"},
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request"]["request_id"]
    _decide(_approver(db_session), request_id, "approved")

    response = _execute(client, account.id, request_id)

    assert response.status_code == 200, response.text
    assert "paid_on" not in response.json()["output"]
    assert _status(db_session, account.id) == "pago"
    assert _snapshot(db_session)["AccountPayment"] == 0
    row = client.get(f"/accounts/{account.id}").json()
    assert row["paid_on"] is None


def test_paid_account_with_payment_record_cannot_be_deleted(
    client: TestClient, db_session: Session
) -> None:
    account = _make_account(db_session, email="exec.delete@example.com")
    requester, _, request_id = _approved_request(
        db_session, account, business_today()
    )
    assert _execute(requester, account.id, request_id).status_code == 200

    response = client.delete(f"/accounts/{account.id}")

    assert response.status_code == 409, response.text
    assert db_session.get(Account, account.id) is not None
    assert _snapshot(db_session)["AccountPayment"] == 1


def test_new_request_is_blocked_while_approved_payment_awaits_completion(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="pedido.aprovado@example.com")
    requester, _, request_id = _approved_request(
        db_session, account, business_today()
    )
    before = _snapshot(db_session)

    blocked = _request(requester, account.id, business_today(), key="after-approval")

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == (
        "Já existe um pagamento aprovado aguardando conclusão para este título."
    )
    assert _snapshot(db_session) == before
    assert before["ApprovalRequest"] == 1

    # A aprovacao bloqueada segue valida: A conclui normalmente.
    done = _execute(requester, account.id, request_id)
    assert done.status_code == 200, done.text
    assert _status(db_session, account.id) == "pago"
    assert _snapshot(db_session)["AccountPayment"] == 1


def test_account_reads_without_payment_row_return_null_paid_on(
    db_session: Session,
) -> None:
    account = _make_account(db_session, email="leitura.semlinha@example.com")
    reader = _requester(db_session)

    listed = reader.get("/accounts/")
    assert listed.status_code == 200, listed.text
    assert [row["paid_on"] for row in listed.json()] == [None]
    assert reader.get(f"/accounts/{account.id}").json()["paid_on"] is None
