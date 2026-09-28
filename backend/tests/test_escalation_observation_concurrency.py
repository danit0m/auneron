"""
VALUE-2.3 -- duas requisicoes concorrentes com a MESMA Idempotency-Key
sobre o MESMO episodio de escalonamento nao podem produzir duas
linhas -- a UniqueConstraint(escalation_work_item_id, idempotency_key)
arbitra, e a transacao perdedora reconcilia pelo mesmo caminho que uma
chamada sequencial usaria.
"""

import threading
from datetime import date
from datetime import timedelta

from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.models.account import Account
from app.models.escalation_observation import EscalationObservation
from app.models.user import User
from app.services.escalation_observation_service import (
    EscalationObservationService,
)
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.work_service import WorkActor


def test_two_concurrent_human_assessments_same_key_only_one_row(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = Account(
        cliente="Cliente Escalation Observation Concorrencia",
        email="concurrency-escalation-observation@example.com",
        whatsapp=None,
        valor=900,
        vencimento=due_date,
        status="atrasado",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    from app.core.authentication import hash_password

    actor_user = User(
        name="Escalation Observation Concurrency Actor",
        email="concurrency-escalation-observation-actor@example.com",
        password_hash=hash_password("not-used-Aa1!"),
        role="administrator",
        active=True,
    )
    db_session.add(actor_user)
    db_session.commit()
    db_session.refresh(actor_user)

    actor = WorkActor(
        actor_type="user",
        actor_reference=f"user:{actor_user.id}",
        actor_user_id=actor_user.id,
    )
    work_item = HumanEscalationMaterializationService(db_session).materialize(
        account=account, due_date=due_date, actor=actor
    ).work_item
    work_item_id = work_item.id
    db_session.commit()

    shared_key = "concurrent-idempotency-key"
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _attempt() -> None:
        session = SessionLocal()
        try:
            local_work_item = session.get(
                type(work_item), work_item_id
            )
            local_actor = WorkActor(
                actor_type="user",
                actor_reference=f"user:{actor_user.id}",
                actor_user_id=actor_user.id,
            )
            service = EscalationObservationService(session)
            barrier.wait(timeout=5)
            service.record_human_assessment(
                escalation_work_item=local_work_item,
                assessment_code="contact_made",
                actor=local_actor,
                declared_by_role="administrator",
                idempotency_key=shared_key,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            session.close()

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected errors: {errors!r}"

    rows = (
        db_session.query(EscalationObservation)
        .filter(
            EscalationObservation.escalation_work_item_id
            == work_item_id,
            EscalationObservation.idempotency_key == shared_key,
        )
        .all()
    )
    assert len(rows) == 1
