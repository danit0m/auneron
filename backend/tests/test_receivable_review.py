from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.accounts import review_receivable_facts
from app.models.account import Account
from app.schemas.receivable_review import ReceivableReviewRequest


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"balance": {"cents": -1, "evidence": "ERP"}},
        {"balance": {"cents": True, "evidence": "ERP"}},
        {"hold": {"blocked": True, "reason": " "}},
        {"hold": {"blocked": "false", "reason": "Conferido"}},
    ],
)
def test_rejects_unconfirmed_financial_facts(payload):
    with pytest.raises(ValidationError):
        ReceivableReviewRequest.model_validate(payload)


def test_records_financial_evidence_without_changing_original_amount():
    account = Account(id=7, status="aberto", valor=900)
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = account
    operator = SimpleNamespace(user=SimpleNamespace(id=3))
    payload = ReceivableReviewRequest.model_validate({
        "balance": {"cents": 0, "evidence": "ERP line 42"},
        "hold": {"blocked": True, "reason": "Revisao pendente"},
    })
    result = review_receivable_facts(7, payload, operator, db)

    assert account.valor == 900
    assert result.balance_cents == 0
    assert result.balance_source == "manual:ERP line 42"
    assert result.balance_reviewed_by == result.hold_reviewed_by == 3
    assert result.balance_observed_at == result.hold_reviewed_at
    assert result.hold_reason == "Revisao pendente"
    assert result.financial_hold is True
    db.commit.assert_called_once()


def test_rejects_positive_balance_on_paid_account_before_writing():
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = Account(id=7, status="pago")
    operator = SimpleNamespace(user=SimpleNamespace(id=3))
    payload = ReceivableReviewRequest.model_validate({
        "balance": {"cents": 100, "evidence": "ERP"},
    })
    with pytest.raises(HTTPException) as exc:
        review_receivable_facts(7, payload, operator, db)
    assert exc.value.status_code == 409
    db.flush.assert_not_called()
    db.commit.assert_not_called()
