"""
VALUE-2.4 -- GET /work-items/{work_item_id}/escalation-observations.

Cobre a matriz final congelada em VALUE-2.4/2.4A/2.4B (22 itens):
contrato de uniao discriminada, paginacao por id, filtro por
observation_type, RBAC (viewer==analyst, system negado), ausencia de
inferencia causal, sentinela de vocabulario, sessao unica compartilhada
entre os dois servicos, e evidencia do OpenAPI (oneOf + discriminator).
"""

import typing
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.database.database import get_db
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.escalation_observation import ASSESSMENT_CODES
from app.models.user import User
from app.schemas.escalation_observation import AssessmentCode
from app.services.escalation_observation_service import (
    EscalationObservationService,
)
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
        cliente="Cliente Escalation Read API",
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
        name="Escalation Read API Actor",
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


def _account_event(
    db_session: Session,
    *,
    account: Account,
    occurred_at: datetime,
    new_status: str = "pago",
) -> AccountEvent:
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="system",
        actor_reference="test:escalation-observation-read-api",
        previous_status="atrasado",
        new_status=new_status,
        occurred_at=occurred_at,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


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
    actor = WorkActor(
        actor_type="user",
        actor_reference=f"user:{actor_user.id}",
        actor_user_id=actor_user.id,
    )
    return account, actor_user, actor, work_item


def _record_observed_fact(
    db_session: Session,
    *,
    work_item,
    account: Account,
    occurred_at: datetime,
):
    event = _account_event(
        db_session, account=account, occurred_at=occurred_at
    )
    result = EscalationObservationService(db_session).record_observed_fact(
        escalation_work_item=work_item,
        account_event=event,
    )
    return result.observation


def _record_human_assessment(
    db_session: Session,
    *,
    work_item,
    actor: WorkActor,
    assessment_code: str,
    declared_by_role: str = "analyst",
    idempotency_key: str | None = None,
):
    result = EscalationObservationService(
        db_session
    ).record_human_assessment(
        escalation_work_item=work_item,
        assessment_code=assessment_code,
        actor=actor,
        declared_by_role=declared_by_role,
        idempotency_key=idempotency_key,
    )
    return result.observation


def _list_observations(
    client: TestClient,
    work_item_id: int,
    **params: object,
):
    return client.get(
        f"/work-items/{work_item_id}/escalation-observations",
        params=params,
    )


# ---------------------------------------------------------------------
# 1 -- lista vazia
# ---------------------------------------------------------------------


def test_empty_list_for_escalation_work_item_without_observations(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, _, work_item = _setup_escalation(
        db_session, email_prefix="empty-list"
    )

    response = _list_observations(client, work_item.id)

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "next_cursor": None,
    }


# ---------------------------------------------------------------------
# 2/14 -- ordem estavel por id, timestamps fora de ordem, campos por tipo
# ---------------------------------------------------------------------


def test_order_is_stable_by_id_even_with_out_of_order_timestamps(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="order-id"
    )

    # observed_at/declared_at sao validados pelo servico contra
    # work_item.created_at (nao podem ser anteriores) -- entao, para
    # provar que a API ordena por id e nao pelos timestamps de
    # dominio, criamos os dois registros normalmente e DEPOIS
    # fixamos os dois timestamps explicitamente, respeitando a
    # invariante do servico (>= work_item.created_at) mas em ordem
    # OPOSTA a ordem dos ids. Isso testa a ORDENACAO da API, nao a
    # validacao do servico (ja coberta em
    # test_escalation_observation_service.py).
    first = _record_human_assessment(
        db_session,
        work_item=work_item,
        actor=actor,
        assessment_code="contact_made",
        idempotency_key="order-id:first",
    )
    second = _record_observed_fact(
        db_session,
        work_item=work_item,
        account=account,
        occurred_at=datetime.now(timezone.utc),
    )

    assert first.id < second.id

    work_item_created_at = work_item.created_at
    assert work_item_created_at is not None

    second_observed_at = (
        work_item_created_at + timedelta(hours=1)
    )
    first_declared_at = (
        work_item_created_at + timedelta(hours=2)
    )

    # Ambos >= work_item.created_at (invariante do servico), mas a
    # ordem temporal (second antes de first) eh o OPOSTO da ordem dos
    # ids (first.id < second.id) -- exatamente o cenario que a API
    # precisa resistir.
    assert work_item_created_at <= second_observed_at
    assert second_observed_at < first_declared_at

    first.declared_at = first_declared_at
    second.observed_at = second_observed_at
    db_session.add(first)
    db_session.add(second)
    db_session.commit()

    response = _list_observations(client, work_item.id)
    assert response.status_code == 200

    items = response.json()["items"]
    assert [item["id"] for item in items] == [
        first.id,
        second.id,
    ]

    assert items[0]["observation_type"] == "human_assessment"
    assert set(items[0].keys()) == {
        "observation_type",
        "id",
        "escalation_work_item_id",
        "created_at",
        "assessment_code",
        "declared_by_user_id",
        "declared_at",
    }

    assert items[1]["observation_type"] == "observed_fact"
    assert set(items[1].keys()) == {
        "observation_type",
        "id",
        "escalation_work_item_id",
        "created_at",
        "linked_account_event_id",
        "observed_at",
    }


# ---------------------------------------------------------------------
# 3 -- paginacao por after_id
# ---------------------------------------------------------------------


def test_pagination_after_id_does_not_repeat_items(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="pagination"
    )

    created = [
        _record_human_assessment(
            db_session,
            work_item=work_item,
            actor=actor,
            assessment_code="contact_made",
            idempotency_key=f"pagination:{i}",
        )
        for i in range(3)
    ]

    first_page = _list_observations(
        client, work_item.id, limit=2
    )
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert [
        item["id"] for item in first_body["items"]
    ] == [created[0].id, created[1].id]
    assert first_body["next_cursor"] == created[1].id

    second_page = _list_observations(
        client,
        work_item.id,
        limit=2,
        after_id=first_body["next_cursor"],
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert [
        item["id"] for item in second_body["items"]
    ] == [created[2].id]
    assert second_body["next_cursor"] is None


# ---------------------------------------------------------------------
# 4 -- limit respeitado, next_cursor via limit+1
# ---------------------------------------------------------------------


def test_limit_respected_and_next_cursor_correct(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, actor, work_item = _setup_escalation(
        db_session, email_prefix="limit"
    )

    for i in range(5):
        _record_human_assessment(
            db_session,
            work_item=work_item,
            actor=actor,
            assessment_code="unreachable",
            idempotency_key=f"limit:{i}",
        )

    response = _list_observations(client, work_item.id, limit=3)
    body = response.json()

    assert len(body["items"]) == 3
    assert body["next_cursor"] is not None


# ---------------------------------------------------------------------
# 5/6 -- filtro por observation_type, filtro invalido
# ---------------------------------------------------------------------


def test_filter_by_observation_type_isolates_each_kind(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="filter-type"
    )

    fact = _record_observed_fact(
        db_session,
        work_item=work_item,
        account=account,
        occurred_at=datetime.now(timezone.utc),
    )
    assessment = _record_human_assessment(
        db_session,
        work_item=work_item,
        actor=actor,
        assessment_code="payment_promised",
        idempotency_key="filter-type:assessment",
    )

    only_facts = _list_observations(
        client, work_item.id, observation_type="observed_fact"
    ).json()
    assert [i["id"] for i in only_facts["items"]] == [fact.id]

    only_assessments = _list_observations(
        client, work_item.id, observation_type="human_assessment"
    ).json()
    assert [i["id"] for i in only_assessments["items"]] == [
        assessment.id
    ]


def test_invalid_observation_type_filter_returns_422(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, _, work_item = _setup_escalation(
        db_session, email_prefix="filter-invalid"
    )

    response = _list_observations(
        client, work_item.id, observation_type="not-a-real-type"
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------
# 7 -- limites 0/101
# ---------------------------------------------------------------------


def test_limit_zero_and_101_return_422(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, _, work_item = _setup_escalation(
        db_session, email_prefix="limit-bounds"
    )

    assert _list_observations(
        client, work_item.id, limit=0
    ).status_code == 422
    assert _list_observations(
        client, work_item.id, limit=101
    ).status_code == 422


# ---------------------------------------------------------------------
# 8/9 -- 404 inexistente / inacessivel
# ---------------------------------------------------------------------


def test_nonexistent_work_item_returns_404(
    client: TestClient,
) -> None:
    response = _list_observations(client, 999_999_999)
    assert response.status_code == 404


def test_nonexistent_work_item_uses_frozen_404_envelope(
    client: TestClient,
) -> None:
    # "Inacessivel" e "inexistente" devem ser indistinguiveis para quem
    # chama. Hoje, todo papel com work:read tambem tem clients.view
    # (nenhum role fica no meio-termo "le trabalho mas nao le conta") --
    # entao o unico caminho de 404 realmente construivel em teste de
    # integracao e o de WorkItem inexistente. O mapeamento de erro
    # (_raise_work_http_error) usa o MESMO WorkNotFoundError para os
    # dois casos, garantindo a mesma forma de resposta se o caso
    # "existe mas inacessivel" um dia se tornar alcancavel (ex.: conta
    # fica sem clients.view para um papel novo).
    response = _list_observations(client, 999_999_999)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "work_not_found"


# ---------------------------------------------------------------------
# 10/11 -- system negado, viewer == analyst
# ---------------------------------------------------------------------


def test_system_role_is_denied(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, _, work_item = _setup_escalation(
        db_session, email_prefix="system-denied"
    )

    _set_role(db_session, "system")
    response = _list_observations(client, work_item.id)

    # "system" nao constitui uma sessao humana valida -- e um
    # principal nao-interativo. require_user_session
    # (app/core/authentication.py:362-376) revalida o papel a cada
    # requisicao e interrompe a requisicao ANTES do RBAC (work:read)
    # ser avaliado, porque a credencial em si nao representa uma
    # sessao valida. O resultado correto e 401 (fronteira de
    # autenticacao), nao 403 (RBAC) -- semanticas diferentes, nao
    # "mais forte" uma que a outra. Contrato amendado no VALUE-2.4
    # POST-APPLY V2.
    assert response.status_code == 401


def test_viewer_and_analyst_receive_identical_contract(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="viewer-analyst"
    )
    _record_human_assessment(
        db_session,
        work_item=work_item,
        actor=actor,
        assessment_code="partial_agreement",
        idempotency_key="viewer-analyst:1",
    )

    _set_role(db_session, "viewer")
    viewer_body = _list_observations(
        client, work_item.id
    ).json()

    _set_role(db_session, "analyst")
    analyst_body = _list_observations(
        client, work_item.id
    ).json()

    assert viewer_body == analyst_body
    assert (
        "declared_by_user_id"
        in viewer_body["items"][0]
    )


# ---------------------------------------------------------------------
# 12/13 -- idempotency_key e declared_by_role nunca aparecem
# ---------------------------------------------------------------------


def test_idempotency_key_and_declared_by_role_never_serialized(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="no-leak"
    )
    _record_human_assessment(
        db_session,
        work_item=work_item,
        actor=actor,
        assessment_code="contact_made",
        declared_by_role="manager",
        idempotency_key="no-leak:1",
    )
    _record_observed_fact(
        db_session,
        work_item=work_item,
        account=account,
        occurred_at=datetime.now(timezone.utc),
    )

    body = _list_observations(client, work_item.id).json()

    for item in body["items"]:
        assert "idempotency_key" not in item
        assert "declared_by_role" not in item


# ---------------------------------------------------------------------
# 15 -- assessment_code fora do vocabulario impossivel no schema
# ---------------------------------------------------------------------


def test_assessment_code_outside_closed_vocabulary_is_impossible() -> (
    None
):
    from pydantic import ValidationError

    from app.schemas.escalation_observation import (
        HumanAssessmentObservationResponse,
    )

    now = datetime.now(timezone.utc)

    try:
        HumanAssessmentObservationResponse(
            observation_type="human_assessment",
            id=1,
            escalation_work_item_id=1,
            created_at=now,
            assessment_code="not_a_real_code",
            declared_by_user_id=1,
            declared_at=now,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError(
            "assessment_code fora do vocabulario foi aceito."
        )


# ---------------------------------------------------------------------
# 16 -- cursor de outro WorkItem nao vaza
# ---------------------------------------------------------------------


def test_after_id_from_another_work_item_does_not_leak(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, actor_a, work_item_a = _setup_escalation(
        db_session, email_prefix="cursor-leak-a"
    )
    _, _, actor_b, work_item_b = _setup_escalation(
        db_session, email_prefix="cursor-leak-b"
    )

    observation_a = _record_human_assessment(
        db_session,
        work_item=work_item_a,
        actor=actor_a,
        assessment_code="contact_made",
        idempotency_key="cursor-leak:a",
    )
    observation_b = _record_human_assessment(
        db_session,
        work_item=work_item_b,
        actor=actor_b,
        assessment_code="contact_made",
        idempotency_key="cursor-leak:b",
    )

    response = _list_observations(
        client,
        work_item_b.id,
        after_id=observation_a.id,
    )

    assert response.status_code == 200
    body = response.json()
    # O cursor de outro WorkItem eh usado apenas como limite numerico
    # (id > after_id) -- ele PODE influenciar a pagina numericamente,
    # mas nunca causa lookup global nem vazamento: observation_a nunca
    # aparece (nao pertence a work_item_b), e observation_b (do
    # WorkItem certo, com id maior) continua retornando normalmente.
    ids = [item["id"] for item in body["items"]]
    assert observation_a.id not in ids
    assert ids == [observation_b.id]


# ---------------------------------------------------------------------
# 17 -- nenhuma chave de causalidade/resumo
# ---------------------------------------------------------------------


def test_no_causal_or_summary_field_ever_appears(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="no-causal"
    )
    _record_human_assessment(
        db_session,
        work_item=work_item,
        actor=actor,
        assessment_code="payment_promised",
        idempotency_key="no-causal:1",
    )
    _record_observed_fact(
        db_session,
        work_item=work_item,
        account=account,
        occurred_at=datetime.now(timezone.utc),
    )

    body = _list_observations(client, work_item.id).json()
    forbidden = {
        "outcome",
        "is_successful",
        "success",
        "resumo",
        "outcome_summary",
    }

    assert not (forbidden & set(body.keys()))
    for item in body["items"]:
        assert not (forbidden & set(item.keys()))


# ---------------------------------------------------------------------
# 18 -- todos os campos congelados presentes
# ---------------------------------------------------------------------


def test_all_frozen_fields_present_per_type(
    client: TestClient,
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup_escalation(
        db_session, email_prefix="frozen-fields"
    )
    _record_observed_fact(
        db_session,
        work_item=work_item,
        account=account,
        occurred_at=datetime.now(timezone.utc),
    )
    _record_human_assessment(
        db_session,
        work_item=work_item,
        actor=actor,
        assessment_code="unreachable",
        idempotency_key="frozen-fields:1",
    )

    body = _list_observations(client, work_item.id).json()
    by_type = {
        item["observation_type"]: item
        for item in body["items"]
    }

    assert set(by_type["observed_fact"].keys()) == {
        "observation_type",
        "id",
        "escalation_work_item_id",
        "created_at",
        "linked_account_event_id",
        "observed_at",
    }
    assert set(by_type["human_assessment"].keys()) == {
        "observation_type",
        "id",
        "escalation_work_item_id",
        "created_at",
        "assessment_code",
        "declared_by_user_id",
        "declared_at",
    }


# ---------------------------------------------------------------------
# 19 -- sentinela AssessmentCode x ASSESSMENT_CODES
# ---------------------------------------------------------------------


def test_assessment_code_literal_matches_model_vocabulary() -> None:
    assert set(typing.get_args(AssessmentCode)) == set(
        ASSESSMENT_CODES
    )


# ---------------------------------------------------------------------
# 20 -- WorkItem sem observations de escalonamento (nao-escalonamento)
# ---------------------------------------------------------------------


def test_authorized_non_escalation_work_item_returns_empty_list(
    client: TestClient,
    db_session: Session,
) -> None:
    actor = _current_user(db_session)

    work_item = WorkManagerService(db_session).create(
        work_type="task",
        title="Trabalho comum, nao eh escalonamento",
        work_key="value24.non-escalation.item",
        scope_type="global",
        origin_type="system",
        origin_reference="test:value24",
        actor=WorkActor(
            actor_type="user",
            actor_reference=f"user:{actor.id}",
            actor_user_id=actor.id,
        ),
    ).work_item

    response = _list_observations(client, work_item.id)

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "next_cursor": None,
    }


# ---------------------------------------------------------------------
# 21 -- OpenAPI: oneOf + discriminator
# ---------------------------------------------------------------------


def test_openapi_declares_discriminated_union() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]

    assert "ObservedFactObservationResponse" in components
    assert "HumanAssessmentObservationResponse" in components

    list_schema = components["EscalationObservationListResponse"]
    items_schema = list_schema["properties"]["items"]["items"]

    ref_names = {
        ref["$ref"].rsplit("/", 1)[-1]
        for ref in items_schema["oneOf"]
    }
    assert ref_names == {
        "ObservedFactObservationResponse",
        "HumanAssessmentObservationResponse",
    }
    assert (
        items_schema["discriminator"]["propertyName"]
        == "observation_type"
    )


# ---------------------------------------------------------------------
# 22 -- get_db executado uma vez, mesma Session para os dois servicos
# ---------------------------------------------------------------------


def test_get_db_executes_once_and_session_is_shared(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, _, work_item = _setup_escalation(
        db_session, email_prefix="shared-session"
    )

    call_count = 0
    seen_sessions: list[int] = []

    from app.database.database import SessionLocal

    def counting_get_db():
        nonlocal call_count
        call_count += 1
        session = SessionLocal()
        seen_sessions.append(id(session))
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = counting_get_db

    try:
        response = _list_observations(client, work_item.id)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert call_count == 1
    assert len(set(seen_sessions)) == 1
