"""
VALUE-2.3 -- EscalationObservationService: validacoes de
observed_fact, vocabulario/idempotencia de human_assessment, e a
disciplina de nao promover um tipo ao outro.
"""

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.user import User
from app.services.escalation_observation_service import (
    EscalationObservationConflictError,
)
from app.services.escalation_observation_service import (
    EscalationObservationService,
)
from app.services.escalation_observation_service import (
    EscalationObservationValidationError,
)
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
        cliente="Cliente Escalation Observation Service",
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
        name="Escalation Observation Service Test",
        email=email,
        password_hash=hash_password("not-used-Aa1!"),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _escalation_work_item(db_session, *, account, due_date, actor_user):
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
    db_session, *, account, occurred_at, new_status="pago"
):
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="system",
        actor_reference="test:escalation-observation-service",
        previous_status="atrasado",
        new_status=new_status,
        occurred_at=occurred_at,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def _setup(db_session: Session, *, email_prefix: str):
    due_date = date.today() - timedelta(days=10)
    account = _account(
        db_session, email=f"{email_prefix}-account@example.com", vencimento=due_date
    )
    actor_user = _user(
        db_session, email=f"{email_prefix}-actor@example.com"
    )
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due_date, actor_user=actor_user
    )
    actor = WorkActor(
        actor_type="user",
        actor_reference=f"user:{actor_user.id}",
        actor_user_id=actor_user.id,
    )
    return account, actor_user, actor, work_item


# ---------------------------------------------------------------------
# observed_fact
# ---------------------------------------------------------------------


def test_record_observed_fact_success(db_session: Session) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-observed-ok"
    )
    account_event = _account_event(
        db_session, account=account, occurred_at=datetime.now(timezone.utc)
    )

    service = EscalationObservationService(db_session)
    result = service.record_observed_fact(
        escalation_work_item=work_item,
        account_event=account_event,
    )

    assert result.created is True
    assert result.duplicate is False
    assert result.observation.observation_type == "observed_fact"
    assert (
        result.observation.idempotency_key
        == f"escalation_observation:observed_fact:account_event:{account_event.id}"
    )


def test_record_observed_fact_rejects_wrong_account(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-observed-wrong-account"
    )
    other_account = _account(
        db_session,
        email="svc-observed-wrong-account-other@example.com",
        vencimento=date.today() - timedelta(days=5),
    )
    account_event = _account_event(
        db_session,
        account=other_account,
        occurred_at=datetime.now(timezone.utc),
    )

    service = EscalationObservationService(db_session)
    with pytest.raises(EscalationObservationValidationError):
        service.record_observed_fact(
            escalation_work_item=work_item,
            account_event=account_event,
        )


def test_record_observed_fact_rejects_event_before_work_item(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-observed-before"
    )
    account_event = _account_event(
        db_session,
        account=account,
        occurred_at=work_item.created_at - timedelta(days=1),
    )

    service = EscalationObservationService(db_session)
    with pytest.raises(EscalationObservationValidationError):
        service.record_observed_fact(
            escalation_work_item=work_item,
            account_event=account_event,
        )


def test_record_observed_fact_rejects_disallowed_new_status(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-observed-bad-status"
    )
    account_event = _account_event(
        db_session,
        account=account,
        occurred_at=datetime.now(timezone.utc),
        new_status="atrasado",
    )

    service = EscalationObservationService(db_session)
    with pytest.raises(EscalationObservationValidationError):
        service.record_observed_fact(
            escalation_work_item=work_item,
            account_event=account_event,
        )


def test_record_observed_fact_called_twice_for_same_event_is_idempotent(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-observed-twice"
    )
    account_event = _account_event(
        db_session, account=account, occurred_at=datetime.now(timezone.utc)
    )

    service = EscalationObservationService(db_session)
    first = service.record_observed_fact(
        escalation_work_item=work_item, account_event=account_event
    )
    second = service.record_observed_fact(
        escalation_work_item=work_item, account_event=account_event
    )

    assert first.created is True
    assert second.created is False
    assert second.duplicate is True
    assert second.observation.id == first.observation.id


# ---------------------------------------------------------------------
# human_assessment
# ---------------------------------------------------------------------


def test_record_human_assessment_success_each_code(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-assessment-codes"
    )
    service = EscalationObservationService(db_session)

    codes = (
        "contact_made",
        "payment_promised",
        "payment_refused",
        "unreachable",
        "partial_agreement",
    )
    for code in codes:
        result = service.record_human_assessment(
            escalation_work_item=work_item,
            assessment_code=code,
            actor=actor,
            declared_by_role="administrator",
        )
        assert result.created is True
        assert result.observation.assessment_code == code


def test_record_human_assessment_rejects_invalid_code(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-assessment-invalid"
    )
    service = EscalationObservationService(db_session)

    with pytest.raises(EscalationObservationValidationError):
        service.record_human_assessment(
            escalation_work_item=work_item,
            assessment_code="other",
            actor=actor,
            declared_by_role="administrator",
        )


def test_record_human_assessment_same_key_same_content_is_idempotent(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-assessment-idem-same"
    )
    service = EscalationObservationService(db_session)

    first = service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="contact_made",
        actor=actor,
        declared_by_role="administrator",
        idempotency_key="client-key-1",
    )
    second = service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="contact_made",
        actor=actor,
        declared_by_role="administrator",
        idempotency_key="client-key-1",
    )

    assert first.created is True
    assert second.duplicate is True
    assert second.observation.id == first.observation.id


def test_record_human_assessment_same_key_divergent_content_conflicts(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-assessment-idem-divergent"
    )
    service = EscalationObservationService(db_session)

    service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="contact_made",
        actor=actor,
        declared_by_role="administrator",
        idempotency_key="client-key-2",
    )

    with pytest.raises(EscalationObservationConflictError):
        service.record_human_assessment(
            escalation_work_item=work_item,
            assessment_code="payment_refused",
            actor=actor,
            declared_by_role="administrator",
            idempotency_key="client-key-2",
        )


def test_record_human_assessment_without_key_creates_distinct_rows(
    db_session: Session,
) -> None:
    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-assessment-no-key"
    )
    service = EscalationObservationService(db_session)

    first = service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="contact_made",
        actor=actor,
        declared_by_role="administrator",
    )
    second = service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="contact_made",
        actor=actor,
        declared_by_role="administrator",
    )

    assert first.created is True
    assert second.created is True
    assert first.observation.id != second.observation.id


def test_multiple_observations_over_time_all_preserved(
    db_session: Session,
) -> None:
    """S2/S12 -- promise -> refusal -> observed_fact pago, todas
    preservadas, nenhuma sobrescreve a outra."""

    account, actor_user, actor, work_item = _setup(
        db_session, email_prefix="svc-multi-observation"
    )
    service = EscalationObservationService(db_session)

    promised = service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="payment_promised",
        actor=actor,
        declared_by_role="administrator",
    )
    refused = service.record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="payment_refused",
        actor=actor,
        declared_by_role="administrator",
    )
    account_event = _account_event(
        db_session, account=account, occurred_at=datetime.now(timezone.utc)
    )
    paid = service.record_observed_fact(
        escalation_work_item=work_item, account_event=account_event
    )

    ids = {promised.observation.id, refused.observation.id, paid.observation.id}
    assert len(ids) == 3
