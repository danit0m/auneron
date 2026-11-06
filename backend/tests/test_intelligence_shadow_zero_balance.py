"""Freeze 3: an unpaid account with a CONFIRMED zero balance is an `ineligible` abstention.

Pure and synthetic, no model, no database. The change lives in the reference oracle
(`_assess`) and only converts what would have been an eligible proposal: every other code
and every precedence stays as it was. An unknown balance is not a confirmed zero.
"""

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

import intelligence_abstention_contract as ac
import intelligence_shadow_scenarios as sc

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-10-15"
BASE = sc.AccountFacts("ACC-Z1", "2026-10-01", 3, False, 12500, "current", False, None)


def scenario(*accounts, kind="assess_overdue", sid="z01"):
    placeholder = sc.Expected("abstain", "not_due", ())  # the oracle derives its own
    return sc.Scenario(sid, "zero_balance", kind, AS_OF, tuple(accounts), (), (), placeholder)


def derived(*accounts, **kw):
    expected = sc.derive_expected(scenario(*accounts, **kw))
    return expected.outcome, expected.abstain_code, expected.mutations


def with_balance(account, balance):
    return dataclasses.replace(account, balance_cents=balance)


def test_an_unpaid_due_account_with_a_confirmed_zero_balance_is_ineligible() -> None:
    assert derived(with_balance(BASE, 0)) == ("abstain", "ineligible", ())
    assert derived(BASE)[0] == "propose"  # the same account with a balance is still proposed
    verdict = ac.check_eligibility(scenario(with_balance(BASE, 0)))
    assert (verdict.eligible, verdict.abstain_code) == (False, "ineligible")


@pytest.mark.parametrize("unknown", [None, False, 0.0, "0", "", b"0"])
def test_an_unknown_or_non_integer_balance_is_not_a_confirmed_zero(unknown) -> None:
    assert derived(with_balance(BASE, unknown))[0] == "propose"  # previous behaviour kept


def test_the_confirmed_zero_must_be_a_real_integer_zero() -> None:
    assert derived(with_balance(BASE, 0))[1] == "ineligible"
    assert derived(with_balance(BASE, 1))[0] == "propose"
    assert derived(with_balance(BASE, 12500))[0] == "propose"


VARIATIONS = {
    "hold": dict(hold=True),
    "not_due": dict(due_date="2026-10-20"),
    "boundary_day_not_due": dict(due_date="2026-10-12"),
    "already_overdue": dict(status="overdue", overdue_since="2026-10-05"),
    "escalated": dict(status="overdue", overdue_since="2026-09-10"),
    "overdue_without_since": dict(status="overdue", overdue_since=None),
    "hold_and_not_due": dict(hold=True, due_date="2026-10-20"),
}


@pytest.mark.parametrize("name", sorted(VARIATIONS))
def test_precedence_of_every_other_code_is_unchanged_by_the_zero_balance_check(name) -> None:
    account = dataclasses.replace(BASE, **VARIATIONS[name])

    assert derived(with_balance(account, 0)) == derived(with_balance(account, 12500)), name


def test_paid_with_a_balance_is_still_contradictory_and_paid_with_zero_is_still_paid() -> None:
    assert derived(dataclasses.replace(BASE, paid=True, balance_cents=100))[1] == "contradictory_facts"
    assert derived(dataclasses.replace(BASE, paid=True, balance_cents=0))[1] == "paid"


def test_with_several_accounts_the_zero_balance_one_is_left_out_of_the_proposal() -> None:
    other = dataclasses.replace(BASE, ref="ACC-Z2")
    zero = with_balance(BASE, 0)

    outcome, code, mutations = derived(zero, other)
    assert (outcome, code) == ("propose", None)
    assert [m.target_ref for m in mutations] == ["ACC-Z2"]
    assert derived(zero, with_balance(other, 0))[:2] == ("abstain", "ineligible")


def test_zero_balance_next_to_an_account_that_is_not_due_stays_an_ambiguous_fixture() -> None:
    not_due = dataclasses.replace(BASE, ref="ACC-Z2", due_date="2026-10-20")

    with pytest.raises(ValueError, match="ambiguous"):
        sc.derive_expected(scenario(with_balance(BASE, 0), not_due))


def test_a_payment_request_is_unaffected() -> None:
    assert derived(with_balance(BASE, 0), kind="request_payment")[:2] == ("abstain", "out_of_capability")


def test_none_of_the_12_development_scenarios_changes_result() -> None:
    # hand written, taken from the frozen matrix before this change
    before = {
        "s01": "propose", "s02": "propose", "s03": "not_due", "s04": "paid", "s05": "already_overdue",
        "s06": "out_of_capability", "s07": "ineligible", "s08": "contradictory_facts",
        "s09": "not_due", "s10": "out_of_capability", "s11": "propose", "s12": "not_due",
    }
    for s in sc.SCENARIOS:
        expected = sc.derive_expected(s)
        assert expected == s.expected, s.scenario_id
        assert (expected.abstain_code or "propose") == before[s.scenario_id], s.scenario_id
        assert not any(type(a.balance_cents) is int and a.balance_cents == 0 and not a.paid
                       for a in s.accounts), s.scenario_id  # none of them reaches the new branch
    assert sc.scenario_set_digest() == (
        "sha256:79989f85a9e1f5d86a1363ce033968989f83429a0813d4d1bb4a9266cd63f0f6")  # unchanged


def test_the_change_is_confined_to_one_branch_of_the_oracle() -> None:
    source = Path(sc.__file__).read_text(encoding="utf-8")
    assert source.count("balance_cents") >= 1 and "ineligible" in source
    block = source.split("def _assess", 1)[1].split("def derive_expected", 1)[0]
    # the zero check sits inside the `as_of >= first_overdue` branch, after every other code
    assert block.index("if as_of >= first_overdue:") < block.index("type(account.balance_cents) is int")
    assert block.index('return "not_due"') > block.index("type(account.balance_cents) is int")
    assert block.count('return "ineligible"') == 2  # hold, and the new zero balance


def test_freeze_3_manifest_and_the_preserved_freeze_2() -> None:
    d = ROOT / "backend/docs/intelligence"
    v2_bytes = (d / "SHADOW_EVALUATION_FREEZE.v2.json").read_bytes()
    v2 = json.loads(v2_bytes)
    v3 = json.loads((d / "SHADOW_EVALUATION_FREEZE.json").read_text(encoding="utf-8"))

    assert hashlib.sha256(v2_bytes).hexdigest() == (
        "e4c204bbb68b9b3b055f73ef6154b480ad1b9a230fdab30d77fe0762456e1479")  # intact
    assert v2["freeze_version"] == 2 and v3["freeze_version"] == 3 and v3["supersedes_freeze_version"] == 2
    assert v3["previous_freeze"] == {
        "file": "SHADOW_EVALUATION_FREEZE.v2.json", "sha256": hashlib.sha256(v2_bytes).hexdigest()}
    assert v2["files"]["backend/tests/intelligence_shadow_scenarios.py"] == (
        "f5a5d62c7efc57b4b37717e3ea3af9d3691cfd679ac71717a890cdadae8c7c4a")
    changed = {k for k in v3["files"] if v3["files"][k] != v2["files"][k]}
    assert changed == {"backend/tests/intelligence_shadow_scenarios.py"}  # scorer, adapter unchanged
    assert v3["scenario_set_digest"] == v2["scenario_set_digest"]  # the scenario data did not change
    assert v3["scorer_version"] == v2["scorer_version"] and v3["adapter_version"] == v2["adapter_version"]
    assert "zero balance" in v3["reason"] and v3["date"] == "2026-11-06"
