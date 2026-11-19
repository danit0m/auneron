"""
Helper de teste: registro da DATA EFETIVA de pagamento
(account_payments.paid_on) sem passar pelo corredor governado.

NAO e um modulo de teste (nao coleta testes) e NAO altera `conftest.py`.

A data oficial de pagamento e `account_payments.paid_on`; o evento
'pago' (account_events) e so auditoria. Os testes de comportamento e
classificacao precisam das duas coisas: o evento (rastreabilidade) e a
linha de account_payments (data de negocio).
"""

from __future__ import annotations

import uuid
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_payment import AccountPayment
from app.models.approval import ApprovalRequest
from app.services.skill_service import SkillService


def _skill_version_id(db: Session) -> int:
    service = SkillService(db)
    skill = service.register_skill(
        skill_key=f"payment-date.test.{uuid.uuid4().hex[:12]}",
        provider="auneron.core",
        display_name="Payment date test",
        description="Skill para testes da data efetiva de pagamento.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.payment_date:test",
        execution_mode="read_only",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    return service.publish_version(draft.id).version.id


def make_effective_payment(
    db: Session,
    *,
    account: Account,
    paid_on: date,
) -> AccountPayment:
    now = datetime.now(timezone.utc)
    request = ApprovalRequest(
        action_type="skill_execution",
        skill_version_id=_skill_version_id(db),
        requester_actor_type="system",
        requester_reference="system:payment-date-test",
        requester_user_id=None,
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint="a" * 64,
        input_digest="b" * 64,
        risk_level="low",
        required_permission="approval:decide",
        status="pending",
        target_account_id=None,
        target_user_id=None,
        expires_at=now + timedelta(hours=1),
        resolved_at=None,
        created_at=now,
    )
    db.add(request)
    db.flush()

    payment = AccountPayment(
        account_id=account.id,
        paid_on=paid_on,
        approval_request_id=request.id,
    )
    db.add(payment)
    db.flush()
    return payment
