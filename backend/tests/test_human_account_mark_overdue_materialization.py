"""
P1.2B -- Human Account Mark-Overdue Materialization Service V1: corredor
humano de account.mark_overdue, paralelo ao corredor agent-only
existente (25M/25O). Cobre RBAC em duas camadas (work:create na rota +
autorização cumulativa skill:execute/skill:execute_mutating/
clients.manage revalidada no serviço, falhando fechado ANTES de
qualquer escrita), revalidação de eligibility, convergência entre
operadores diferentes via WorkItem (não via idempotência de
ApprovalRequest, que é por requester), e o caso TERMINAL-DUPLICATE
(reabertura não suportada -- P1.3).
"""

from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession, hash_password
from app.models.account import Account
from app.models.approval import ApprovalRequest
from app.models.nba_recommendation_snapshot import (
    NbaRecommendationSnapshot,
)
from app.models.user import User
from app.models.work import WorkItem
from app.services.human_account_mark_overdue_materialization_service import (
    HumanAccountMarkOverdueMaterializationService,
)
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"


class _FakeSession:
    """Duck-typed AuthSession stand-in: is_session_elevated() only
    reads .elevated_until, e account.mark_overdue e mutating (nao
    exige sessao elevada) -- nenhum registro real de auth_sessions e
    necessario para exercitar autoridade de um segundo usuario."""

    elevated_until = None


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


def _create_user(
    db_session: Session, *, email: str, role: str
) -> User:
    user = User(
        name="Operador Teste P1.2B",
        email=email,
        password_hash=hash_password("Senha-Teste-123!"),
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_overdue_account(
    db_session: Session,
    *,
    due_date: date,
    status: str = "aberto",
    email: str = "cliente.mark-overdue.materialize@example.com",
) -> Account:
    account = Account(
        cliente="Cliente Mark Overdue Materialization Teste",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=due_date,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _materialize_url(account_id: int, due_date: date) -> str:
    return (
        "/recommendations/mark-overdue/accounts/"
        f"{account_id}/episodes/{due_date.isoformat()}/materialize"
    )


def _work_item_count(db_session: Session) -> int:
    return db_session.execute(
        text("SELECT COUNT(*) FROM work_items")
    ).scalar_one()


def _approval_request_count(db_session: Session) -> int:
    return db_session.execute(
        text("SELECT COUNT(*) FROM approval_requests")
    ).scalar_one()


def _nba_url(account_id: int, due_date: date) -> str:
    return (
        "/recommendations/next-best-action/accounts/"
        f"{account_id}/episodes/{due_date.isoformat()}"
    )


def _create_snapshot(
    client: TestClient,
    *,
    account_id: int,
    due_date: date,
    require_mark_overdue: bool = True,
) -> int:
    response = client.get(_nba_url(account_id, due_date))
    assert response.status_code == 200, response.text
    payload = response.json()

    if require_mark_overdue:
        assert "account.mark_overdue" in payload["decision"][
            "selected_actions"
        ], (
            "fixture do teste precisa que account.mark_overdue "
            "esteja recomendado -- ajuste o estado da conta."
        )

    return payload["recommendation_snapshot_id"]


def test_materialize_creates_work_item_and_approval_for_eligible_episode(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(db_session, due_date=due_date)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 201
    payload = response.json()

    assert payload["created"] is True
    assert payload["duplicate"] is False

    work_item = payload["work_item"]
    assert work_item["work_key"] == (
        f"account_mark_overdue:v1:{account.id}:"
        f"{due_date.isoformat()}"
    )
    assert work_item["scope"]["type"] == "account"
    assert work_item["scope"]["account_id"] == account.id
    assert work_item["work_type"] == "task"
    assert work_item["status"] == "ready"
    assert work_item["origin"]["type"] == "user"
    assert (
        work_item["origin"]["reference"]
        == work_item["work_key"]
    )

    approval_request = payload["approval_request"]
    assert approval_request["skill_key"] == "account.mark_overdue"
    assert approval_request["status"] == "pending"
    assert approval_request["requester_actor_type"] == "user"
    assert approval_request["target_account_id"] == account.id


def test_materialize_second_call_same_actor_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=12)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.same-actor@example.com",
    )

    first = client.post(_materialize_url(account.id, due_date))
    assert first.status_code == 201
    first_work_id = first.json()["work_item"]["id"]
    first_approval_id = first.json()["approval_request"][
        "request_id"
    ]

    second = client.post(_materialize_url(account.id, due_date))
    assert second.status_code == 200
    payload = second.json()

    assert payload["created"] is False
    assert payload["duplicate"] is True
    assert payload["work_item"]["id"] == first_work_id
    assert (
        payload["approval_request"]["request_id"]
        == first_approval_id
    )

    assert _work_item_count(db_session) == 1
    assert _approval_request_count(db_session) == 1


def test_materialize_different_user_converges_same_work_item_and_approval(
    db_session: Session,
) -> None:
    """
    Invariante 3 do Freeze: dois operadores diferentes materializando
    o mesmo episódio convergem para o mesmo WorkItem e a mesma
    ApprovalRequest -- nunca uma segunda ApprovalRequest. A
    convergência vem de uq_work_items_account_key (account_id,
    work_key), não da idempotência de ApprovalRequest (que é por
    requester e por isso não converge sozinha entre usuários
    diferentes).
    """

    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=8)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.multi-operator@example.com",
    )

    operator_a = _create_user(
        db_session,
        email="operador.a.mark-overdue@example.com",
        role="manager",
    )
    operator_b = _create_user(
        db_session,
        email="operador.b.mark-overdue@example.com",
        role="manager",
    )

    service = HumanAccountMarkOverdueMaterializationService(
        db_session
    )

    result_a = service.materialize(
        account=account,
        due_date=due_date,
        authenticated=AuthenticatedSession(
            user=operator_a, session=_FakeSession()
        ),
    )
    assert result_a.created is True
    assert result_a.duplicate is False

    result_b = service.materialize(
        account=account,
        due_date=due_date,
        authenticated=AuthenticatedSession(
            user=operator_b, session=_FakeSession()
        ),
    )
    assert result_b.created is False
    assert result_b.duplicate is True
    assert result_b.work_item.id == result_a.work_item.id
    assert (
        result_b.approval_request.id
        == result_a.approval_request.id
    )
    assert (
        result_b.approval_request.requester_reference
        == f"user:{operator_a.id}"
    )

    assert _work_item_count(db_session) == 1
    assert _approval_request_count(db_session) == 1


def test_materialize_ineligible_already_paid_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=5)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        status="pago",
        email="cliente.mark-overdue.pago@example.com",
    )

    before_work = _work_item_count(db_session)
    before_approval = _approval_request_count(db_session)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 409
    assert "already_paid" in response.json()["detail"]
    assert _work_item_count(db_session) == before_work
    assert _approval_request_count(db_session) == before_approval


def test_materialize_ineligible_not_overdue_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() + timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.futuro@example.com",
    )

    before = _work_item_count(db_session)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 409
    assert "not_overdue" in response.json()["detail"]
    assert _work_item_count(db_session) == before


def test_materialize_ineligible_status_not_open_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=5)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        status="atrasado",
        email="cliente.mark-overdue.ja-atrasado@example.com",
    )

    before = _work_item_count(db_session)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 409
    assert "status_not_open" in response.json()["detail"]
    assert _work_item_count(db_session) == before


def test_materialize_requires_work_create_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.viewer@example.com",
    )

    _set_role(db_session, "viewer")

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 403
    assert _work_item_count(db_session) == 0


def test_materialize_work_create_alone_is_insufficient_fails_closed(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    Invariante central do Freeze: work:create autoriza a rota, mas
    NÃO substitui a autorização cumulativa de account.mark_overdue
    (skill:execute + skill:execute_mutating + clients.manage).
    analyst tem work:create mas não tem skill:execute_mutating/
    clients.manage -- deve falhar fechado ANTES de qualquer WorkItem
    ou ApprovalRequest ser persistida.
    """

    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.analyst@example.com",
    )

    _set_role(db_session, "analyst")

    before_work = _work_item_count(db_session)
    before_approval = _approval_request_count(db_session)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 403
    assert _work_item_count(db_session) == before_work
    assert _approval_request_count(db_session) == before_approval


def test_materialize_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.post(
        "/recommendations/mark-overdue/accounts/999999/"
        f"episodes/{due_date}/materialize"
    )

    assert response.status_code == 404


def test_materialize_terminal_work_item_returns_409_and_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.terminal@example.com",
    )

    first = client.post(_materialize_url(account.id, due_date))
    assert first.status_code == 201
    work_item_payload = first.json()["work_item"]
    work_item_id = work_item_payload["id"]
    current_version = work_item_payload["version"]

    user = _current_user(db_session)
    work_service = WorkManagerService(db_session)
    work_service.transition_status(
        work_item_id,
        expected_version=current_version,
        actor=WorkActor(
            actor_type="user",
            actor_reference=f"user:{user.id}",
            actor_user_id=user.id,
        ),
        status="cancelled",
        reason="Teste TERMINAL-DUPLICATE -- encerrado sem "
        "resolver o recebível.",
    )

    before_work = _work_item_count(db_session)
    before_approval = _approval_request_count(db_session)

    second = client.post(_materialize_url(account.id, due_date))

    assert second.status_code == 409
    body = second.json()
    assert "duplicate" not in body or body.get("duplicate") is not True
    assert _work_item_count(db_session) == before_work
    assert _approval_request_count(db_session) == before_approval

    reloaded = db_session.get(WorkItem, work_item_id)
    assert reloaded.status == "cancelled"


def test_materialize_fails_closed_when_skill_not_registered(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    Sem P1.2A (registro do catálogo), account.mark_overdue não existe
    como Skill real -- o corredor humano deve falhar fechado (409),
    nunca assumir/inventar um SkillVersion.
    """

    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.sem-catalogo@example.com",
    )

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 409
    assert _work_item_count(db_session) == 0
    assert _approval_request_count(db_session) == 0


# --- DW-6.4B: recommendation_snapshot_id declarado -----------------------


def test_materialize_new_episode_with_valid_snapshot_creates_association(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case2@example.com",
    )
    snapshot_id = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )

    response = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )

    assert response.status_code == 201
    work_item_id = response.json()["work_item"]["id"]

    db_session.expire_all()
    reloaded = db_session.get(WorkItem, work_item_id)
    assert (
        reloaded.context_data["recommendation_snapshot_id"]
        == snapshot_id
    )


def test_materialize_repeat_with_same_snapshot_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 4: existente S1 + S1 -> idempotent success, nenhuma escrita
    nova."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case4@example.com",
    )
    snapshot_id = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )

    first = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )
    assert first.status_code == 201
    first_work_id = first.json()["work_item"]["id"]

    second = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )

    assert second.status_code == 200
    payload = second.json()
    assert payload["duplicate"] is True
    assert payload["work_item"]["id"] == first_work_id
    assert _work_item_count(db_session) == 1
    assert _approval_request_count(db_session) == 1


def test_materialize_repeat_with_different_snapshot_conflicts(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 5: existente S1 + S2 -> 409, mesmo com S1/S2 recomendando
    o mesmo conteudo -- o ID e a identidade da ocorrencia, nao o
    digest."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case5@example.com",
    )
    snapshot_1 = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )
    snapshot_2 = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )
    assert snapshot_2 != snapshot_1

    first = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_1},
    )
    assert first.status_code == 201

    before_work = _work_item_count(db_session)
    second = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_2},
    )

    assert second.status_code == 409
    assert _work_item_count(db_session) == before_work


def test_materialize_repeat_without_snapshot_preserves_existing_association(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 6: existente S1 + sem referencia -> idempotent success,
    preserva S1 -- ausencia de nova declaracao nunca apaga a
    original."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case6@example.com",
    )
    snapshot_id = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )

    first = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )
    assert first.status_code == 201
    work_item_id = first.json()["work_item"]["id"]

    second = client.post(_materialize_url(account.id, due_date))

    assert second.status_code == 200
    assert second.json()["duplicate"] is True

    db_session.expire_all()
    reloaded = db_session.get(WorkItem, work_item_id)
    assert (
        reloaded.context_data["recommendation_snapshot_id"]
        == snapshot_id
    )


def test_materialize_legacy_without_association_rejects_retroactive_snapshot(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 7: legado sem associacao + S1 -> 409, nunca anexar
    provenance retroativamente."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case7@example.com",
    )

    first = client.post(_materialize_url(account.id, due_date))
    assert first.status_code == 201
    work_item_id = first.json()["work_item"]["id"]

    snapshot_id = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )

    second = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )

    assert second.status_code == 409
    assert _work_item_count(db_session) == 1

    db_session.expire_all()
    reloaded = db_session.get(WorkItem, work_item_id)
    assert (
        "recommendation_snapshot_id" not in reloaded.context_data
    )


def test_materialize_nonexistent_snapshot_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 8: snapshot inexistente -> 422 (referencia invalida do
    request, nao 404 -- nao e um recurso proprio)."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case8@example.com",
    )

    response = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": 999_999_999},
    )

    assert response.status_code == 422
    assert _work_item_count(db_session) == 0


def test_materialize_tampered_snapshot_returns_409(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 9: snapshot adulterado (payload alterado, digest original
    preservado) -> 409, nunca aceito silenciosamente."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case9@example.com",
    )
    snapshot_id = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )

    row = db_session.get(NbaRecommendationSnapshot, snapshot_id)
    tampered_payload = dict(row.snapshot_payload)
    tampered_payload["decision"] = dict(
        tampered_payload["decision"]
    )
    tampered_payload["decision"]["selected_actions"] = []
    db_session.execute(
        NbaRecommendationSnapshot.__table__.update()
        .where(NbaRecommendationSnapshot.id == snapshot_id)
        .values(snapshot_payload=tampered_payload)
    )
    db_session.commit()

    response = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )

    assert response.status_code == 409
    assert _work_item_count(db_session) == 0


def test_materialize_snapshot_from_another_account_returns_409(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 10: snapshot pertence a outra conta -> 409."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account_a = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case10-a@example.com",
    )
    account_b = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case10-b@example.com",
    )
    snapshot_for_a = _create_snapshot(
        client, account_id=account_a.id, due_date=due_date
    )

    response = client.post(
        _materialize_url(account_b.id, due_date),
        json={"recommendation_snapshot_id": snapshot_for_a},
    )

    assert response.status_code == 409
    assert _work_item_count(db_session) == 0


def test_materialize_snapshot_from_another_due_date_returns_409(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 11: snapshot pertence a outro episodio (due_date) da MESMA
    conta -> 409."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.snapshot-case11@example.com",
    )
    other_due_date = due_date - timedelta(days=3)
    snapshot_other_episode = _create_snapshot(
        client,
        account_id=account.id,
        due_date=other_due_date,
        require_mark_overdue=False,
    )

    response = client.post(
        _materialize_url(account.id, due_date),
        json={
            "recommendation_snapshot_id": snapshot_other_episode
        },
    )

    assert response.status_code == 409
    assert _work_item_count(db_session) == 0


def test_materialize_snapshot_not_recommending_mark_overdue_returns_409(
    client: TestClient,
    db_session: Session,
) -> None:
    """Caso 12: snapshot existe, integridade valida, mesma conta/
    episodio, mas account.mark_overdue nao esta em selected_actions ->
    409. decision_type sozinho nunca prova recomendacao da skill."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        status="atrasado",
        email="cliente.mark-overdue.snapshot-case12@example.com",
    )
    snapshot_id = _create_snapshot(
        client,
        account_id=account.id,
        due_date=due_date,
        require_mark_overdue=False,
    )

    # Corrige o estado diretamente para tornar a materializacao
    # elegivel -- o snapshot ja foi gerado com status="atrasado"
    # (mark_overdue indisponivel), devolvido intacto.
    account.status = "aberto"
    db_session.commit()

    response = client.post(
        _materialize_url(account.id, due_date),
        json={"recommendation_snapshot_id": snapshot_id},
    )

    assert response.status_code == 409
    assert _work_item_count(db_session) == 0


def test_materialize_without_body_continues_historical_behavior(
    client: TestClient,
    db_session: Session,
) -> None:
    """Regressao explicita: ausencia total do corpo continua valida --
    snapshot nunca e requisito para acao humana governada."""
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.no-body@example.com",
    )

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 201
    work_item_id = response.json()["work_item"]["id"]

    db_session.expire_all()
    reloaded = db_session.get(WorkItem, work_item_id)
    assert (
        "recommendation_snapshot_id" not in reloaded.context_data
    )
