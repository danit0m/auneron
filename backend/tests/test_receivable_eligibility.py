from dataclasses import replace
from datetime import date

import unittest

from app.core.receivable_eligibility import ReceivableFacts, evaluate_mark_overdue


CASES = [
        ({}, "eligible"),
        ({"balance_cents": 2500}, "eligible"),
        ({"hold": True}, "blocked"),
        ({"hold": None}, "unknown_hold"),
        ({"balance_cents": None}, "unknown_balance"),
        ({"balance_cents": 0}, "zero_balance_without_payment"),
        ({"balance_cents": -1}, "invalid_facts"),
        ({"balance_cents": True}, "invalid_facts"),
        ({"status": "pago", "balance_cents": 0}, "paid"),
        ({"payment_confirmed": True}, "contradictory_facts"),
        ({"status": "atrasado"}, "already_overdue"),
        ({"grace_days": 1}, "not_due"),
        ({"due_date": date(2026, 11, 11)}, "not_due"),
]

 
class TestReceivableEligibility(unittest.TestCase):
    def test_cases(self):
        facts = ReceivableFacts("aberto", date(2026, 11, 10), 10000, False)
        for overrides, expected in CASES:
            with self.subTest(overrides=overrides):
                result = evaluate_mark_overdue(
                    replace(facts, **overrides), business_date=date(2026, 11, 11)
                )
                self.assertEqual(result.reason, expected)
                self.assertEqual(result.eligible, expected == "eligible")


    def test_legacy_face_value_does_not_confirm_balance(self):
        from decimal import Decimal

        from app.core.receivable_account_adapter import facts_from_account
        from app.models.account import Account

        account = Account(
            status="aberto", vencimento=date(2026, 11, 10), valor=Decimal("100.00")
        )
        facts = facts_from_account(account)
        self.assertIsNone(facts.balance_cents)
        self.assertIsNone(facts.hold)
        result = evaluate_mark_overdue(facts, business_date=date(2026, 11, 11))
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "unknown_balance")


    def test_confirmed_balance_and_hold_from_account(self):
        from datetime import datetime, timezone

        from app.core.receivable_account_adapter import facts_from_account
        from app.models.account import Account

        account = Account(
            status="aberto",
            vencimento=date(2026, 11, 10),
            balance_cents=2500,
            balance_source="synthetic:payment:1",
            financial_hold=False,
            grace_days=0,
        )
        self.assertIsNone(facts_from_account(account).balance_cents)
        account.balance_observed_at = datetime(2026, 11, 11, tzinfo=timezone.utc)
        business_date = date(2026, 11, 11)
        self.assertTrue(
            evaluate_mark_overdue(facts_from_account(account), business_date=business_date).eligible
        )
        account.financial_hold = True
        self.assertEqual(
            evaluate_mark_overdue(facts_from_account(account), business_date=business_date).reason,
            "blocked",
        )
