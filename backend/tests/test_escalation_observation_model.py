"""
VALUE-2.3 -- EscalationObservation: constraints reais de Postgres.
Cobre a disjuncao estrutural observed_fact/human_assessment, o
vocabulario fechado, e os dois ON DELETE (CASCADE em WorkItem,
RESTRICT em User e AccountEvent).
"""

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.escalation_observation import EscalationObservation
from app.models.user import User
from app.models.work import WorkItem
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.work_service import WorkActor


def _account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "atrasado",
) -> Account:
    account = Account(
        cliente="Cliente Escalation Observation Model",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _user(db_session: Session, *, email: str) -> User:
    from app.core.authentication import hash_password

    user = User(
        name="Escalation Observation Model Test",
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
) -> WorkItem:
    result = HumanEscalationMaterializationService(db_session).materialize(
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
        actor_reference="test:escalation-observation-model",
        previous_status="atrasado",
        new_status=new_status,
        occurred_at=occurred_at,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def test_observed_fact_valid_row_inserts(db_session: Session) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-observed-fact@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="model-observed-fact-actor@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )
    account_event = _account_event(
        db_session,
        account=account,
        occurred_at=datetime.now(timezone.utc),
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="observed_fact",
        linked_account_event_id=account_event.id,
        observed_at=account_event.occurred_at,
        idempotency_key=f"test:observed_fact:{account_event.id}",
    )
    db_session.add(observation)
    db_session.commit()

    assert observation.id is not None


def test_human_assessment_valid_row_inserts(db_session: Session) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-human-assessment@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email="model-human-assessment-actor@example.com"
    )
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="human_assessment",
        assessment_code="contact_made",
        declared_by_user_id=actor_user.id,
        declared_by_role="administrator",
        declared_at=datetime.now(timezone.utc),
    )
    db_session.add(observation)
    db_session.commit()

    assert observation.id is not None


def test_mixed_fields_across_categories_violates_disjoint_check(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-mixed@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="model-mixed-actor@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )
    account_event = _account_event(
        db_session, account=account, occurred_at=datetime.now(timezone.utc)
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="observed_fact",
        linked_account_event_id=account_event.id,
        observed_at=account_event.occurred_at,
        assessment_code="contact_made",  # nao deveria coexistir
    )
    db_session.add(observation)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invalid_observation_type_rejected(db_session: Session) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-invalid-type@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="model-invalid-type-actor@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="not_a_real_type",
        declared_by_user_id=actor_user.id,
        declared_by_role="administrator",
        assessment_code="contact_made",
        declared_at=datetime.now(timezone.utc),
    )
    db_session.add(observation)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_assessment_code_outside_vocabulary_rejected(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-bad-code@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="model-bad-code-actor@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="human_assessment",
        assessment_code="other",  # fora do vocabulario congelado
        declared_by_user_id=actor_user.id,
        declared_by_role="administrator",
        declared_at=datetime.now(timezone.utc),
    )
    db_session.add(observation)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_delete_work_item_removes_observations(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-cascade@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="model-cascade-actor@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="human_assessment",
        assessment_code="contact_made",
        declared_by_user_id=actor_user.id,
        declared_by_role="administrator",
        declared_at=datetime.now(timezone.utc),
    )
    db_session.add(observation)
    db_session.commit()
    observation_id = observation.id

    db_session.execute(delete(WorkItem).where(WorkItem.id == work_item.id))
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(EscalationObservation, observation_id) is None


def test_deleting_user_referenced_by_human_assessment_is_restricted(
    db_session: Session,
) -> None:
    """
    VALUE-2.3A (C1) -- ON DELETE RESTRICT em declared_by_user_id
    preserva a identidade historica do declarante: apagar um User
    referenciado por uma human_assessment deve falhar, nunca
    apagar/anonimizar a observacao silenciosamente. Confirma tambem
    que, apos o rollback, tanto o User quanto a Observation continuam
    existindo.
    """

    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email="model-restrict@example.com", vencimento=due_date
    )
    actor_user = _user(db_session, email="model-restrict-actor@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="human_assessment",
        assessment_code="payment_promised",
        declared_by_user_id=actor_user.id,
        declared_by_role="administrator",
        declared_at=datetime.now(timezone.utc),
    )
    db_session.add(observation)
    db_session.commit()
    observation_id = observation.id
    user_id = actor_user.id

    with pytest.raises(IntegrityError):
        db_session.execute(delete(User).where(User.id == user_id))
    db_session.rollback()

    assert db_session.get(User, user_id) is not None
    reloaded = db_session.get(EscalationObservation, observation_id)
    assert reloaded is not None
    assert reloaded.declared_by_user_id == user_id


def test_deleting_account_event_referenced_by_observed_fact_is_restricted(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session,
        email="model-restrict-account-event@example.com",
        vencimento=due_date,
    )
    actor_user = _user(
        db_session, email="model-restrict-account-event-actor@example.com"
    )
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )
    account_event = _account_event(
        db_session, account=account, occurred_at=datetime.now(timezone.utc)
    )

    observation = EscalationObservation(
        escalation_work_item_id=work_item.id,
        observation_type="observed_fact",
        linked_account_event_id=account_event.id,
        observed_at=account_event.occurred_at,
        idempotency_key=f"test:restrict:{account_event.id}",
    )
    db_session.add(observation)
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(
            delete(AccountEvent).where(AccountEvent.id == account_event.id)
        )
    db_session.rollback()
