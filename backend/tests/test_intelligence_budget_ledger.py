"""INTELLIGENCE-IMPL-0 -- budget ledger invariants BI-1..BI-6, T1..T8.

Verifies CURRENT accounting conservation (verifiable). It does not,
and cannot, verify an immutable transition history: that needs the
later intelligence_budget_entries table (see docs/intelligence/
BUDGET_LEDGER.md, "Audit scope").
"""

import random
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database.database import SessionLocal
from app.database.database import engine
from app.intelligence.gateway import budget as budget_module
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.pricing import Reservation
from app.models.intelligence_budget import IntelligenceBudget
from app.models.intelligence_budget import IntelligenceModelCall


pytestmark = pytest.mark.contract

BACKEND = Path(__file__).resolve().parents[1]
INVALID = GatewayErrorCode.INVALID_TRANSITION
EVIDENCE = GatewayErrorCode.EVIDENCE_REQUIRED


def make_ledger(**overrides) -> BudgetLedger:
    options = {
        "unknown_fraction_bps": 2500,
        "recovery_grace_seconds": 30,
    }
    options.update(overrides)
    return BudgetLedger(SessionLocal, **options)


def make_budget(
    ledger: BudgetLedger,
    *,
    ref: str = "dep",
    kind: str = "deployment_period",
    limit_micro: int = 1000,
    limit_tokens: int = 1000,
) -> int:
    now = utcnow()
    periodic = kind != "objective"
    return ledger.create_budget(
        scope_kind=kind,
        scope_ref=ref,
        limit_micro=limit_micro,
        limit_tokens=limit_tokens,
        period_start=now - timedelta(hours=1) if periodic else None,
        period_end=now + timedelta(hours=1) if periodic else None,
    )


def reserve(
    ledger: BudgetLedger,
    key: str,
    *,
    deployment: int | None = None,
    user: int | None = None,
    objective: int | None = None,
    micro: int = 100,
    tokens: int = 100,
    deadline_seconds: int = 60,
) -> int:
    return ledger.reserve(
        call_key=key,
        purpose="lab.ledger",
        provider="fake",
        model_profile="lab-fast",
        model_id="fake-small-v0",
        reservation=Reservation(micro, tokens),
        deadline_seconds=deadline_seconds,
        deployment_budget_id=deployment,
        user_budget_id=user,
        objective_budget_id=objective,
    )


def bucket_tuple(ledger: BudgetLedger, budget_id: int) -> tuple:
    s = ledger.snapshot(budget_id)
    return (
        s.reserved_micro,
        s.settled_micro,
        s.unknown_micro,
        s.overrun_micro,
        s.reserved_tokens,
        s.settled_tokens,
        s.unknown_tokens,
        s.overrun_tokens,
    )


def to_state(ledger: BudgetLedger, budget_id: int, key: str, state: str):
    call_id = reserve(ledger, key, deployment=budget_id)

    if state == "reserved":
        return call_id
    if state == "released":
        ledger.release_never_sent(call_id)
        return call_id

    ledger.mark_in_flight(call_id, timeout_seconds=60)

    if state == "settled":
        ledger.settle(call_id, actual_micro=40, actual_tokens=40)
    elif state == "unknown":
        ledger.mark_unknown(call_id, error_class="timeout")

    return call_id


# ----------------------------------------------------------------------
# T1-T3, overrun, BI-3/BI-4
# ----------------------------------------------------------------------


def test_t1_t2_t3_happy_path_moves_value_between_buckets() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)

    call_id = reserve(ledger, "k1", deployment=budget_id)
    assert bucket_tuple(ledger, budget_id) == (100, 0, 0, 0, 100, 0, 0, 0)

    ledger.mark_in_flight(call_id, timeout_seconds=60, request_digest="a" * 64)
    call = ledger.get_call(call_id)
    assert call.state == "in_flight" and call.sent_at is not None
    assert call.request_digest == "a" * 64
    assert bucket_tuple(ledger, budget_id) == (100, 0, 0, 0, 100, 0, 0, 0)

    ledger.settle(
        call_id, actual_micro=60, actual_tokens=70, response_digest="b" * 64
    )
    assert bucket_tuple(ledger, budget_id) == (0, 60, 0, 0, 0, 70, 0, 0)
    call = ledger.get_call(call_id)
    assert call.state == "settled" and call.settled_at is not None
    assert (call.actual_micro, call.actual_tokens) == (60, 70)
    assert ledger.verify_conservation(budget_id).consistent


def test_overrun_is_explicit_and_counted_in_admission() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, limit_micro=200, limit_tokens=200)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)

    ledger.settle(call_id, actual_micro=130, actual_tokens=100)

    snap = ledger.snapshot(budget_id)
    assert (snap.settled_micro, snap.overrun_micro) == (100, 30)
    assert snap.confirmed_micro == 130  # BI-4: confirmed spend visible
    assert (snap.settled_tokens, snap.overrun_tokens) == (100, 0)
    assert ledger.verify_conservation(budget_id).consistent

    # BI-2 counts O: 130 consumed, so 71 more does not fit, 70 does.
    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "k2", deployment=budget_id, micro=71, tokens=10)
    assert caught.value.code is GatewayErrorCode.BUDGET_EXCEEDED

    reserve(ledger, "k3", deployment=budget_id, micro=70, tokens=10)
    assert ledger.verify_conservation(budget_id).consistent


def test_settled_below_reservation_frees_the_difference() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, limit_micro=100, limit_tokens=100)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    ledger.settle(call_id, actual_micro=0, actual_tokens=0)

    assert bucket_tuple(ledger, budget_id) == (0, 0, 0, 0, 0, 0, 0, 0)
    reserve(ledger, "k2", deployment=budget_id)  # full limit usable again


# ----------------------------------------------------------------------
# T4, T5, T6, T7, T8 and BI-5
# ----------------------------------------------------------------------


def test_t4_unknown_keeps_the_worst_case_hold() -> None:
    ledger = make_ledger(unknown_fraction_bps=10_000)
    budget_id = make_budget(ledger, limit_micro=150, limit_tokens=150)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)

    ledger.mark_unknown(call_id, error_class="http_429")

    assert bucket_tuple(ledger, budget_id) == (0, 0, 100, 0, 0, 0, 100, 0)
    call = ledger.get_call(call_id)
    assert call.state == "unknown" and call.unknown_at is not None
    assert call.error_class == "http_429" and call.actual_micro is None
    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "k2", deployment=budget_id)
    assert caught.value.code is GatewayErrorCode.BUDGET_EXCEEDED


def test_t5_release_from_reserved_needs_no_evidence() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = reserve(ledger, "k1", deployment=budget_id)

    ledger.release_never_sent(call_id)

    call = ledger.get_call(call_id)
    assert call.state == "released"
    assert call.release_basis == "never_sent" and call.sent_at is None
    assert bucket_tuple(ledger, budget_id) == (0,) * 8


def test_t5_release_from_in_flight_requires_attested_evidence() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    before = bucket_tuple(ledger, budget_id)

    for evidence in (None, "", "   "):
        with pytest.raises(GatewayError) as caught:
            ledger.release_never_sent(call_id, evidence_ref=evidence)
        assert caught.value.code is EVIDENCE

    assert ledger.get_call(call_id).state == "in_flight"
    assert bucket_tuple(ledger, budget_id) == before

    ledger.release_never_sent(call_id, evidence_ref="adapter_attested:x")
    call = ledger.get_call(call_id)
    assert call.state == "released" and call.sent_at is not None
    assert call.evidence_ref == "adapter_attested:x"
    assert bucket_tuple(ledger, budget_id) == (0,) * 8


def test_t6_provider_policy_table_is_empty_so_nothing_is_released() -> None:
    assert budget_module.PROVIDER_NO_CHARGE_POLICIES == {}

    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    before = bucket_tuple(ledger, budget_id)

    for error_class in ("http_400", "http_429", "http_500", "timeout"):
        with pytest.raises(GatewayError) as caught:
            ledger.release_by_provider_policy(
                call_id, error_class=error_class
            )
        assert caught.value.code is EVIDENCE

    assert ledger.get_call(call_id).state == "in_flight"
    assert bucket_tuple(ledger, budget_id) == before


def test_t6_works_only_through_a_documented_policy_entry(monkeypatch) -> None:
    monkeypatch.setitem(
        budget_module.PROVIDER_NO_CHARGE_POLICIES,
        ("fake", "http_400"),
        "PO-APPROVED-DOC:fake-no-charge-400",
    )
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)

    with pytest.raises(GatewayError):
        ledger.release_by_provider_policy(call_id, error_class="http_500")

    ledger.release_by_provider_policy(call_id, error_class="http_400")

    call = ledger.get_call(call_id)
    assert call.state == "released"
    assert call.release_basis == "provider_documented"
    assert call.evidence_ref == "PO-APPROVED-DOC:fake-no-charge-400"
    assert bucket_tuple(ledger, budget_id) == (0,) * 8


def test_t7_reconcile_unknown_to_settled_with_evidence() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = to_state(ledger, budget_id, "k1", "unknown")

    ledger.reconcile_unknown_settled(
        call_id,
        actual_micro=130,
        actual_tokens=60,
        evidence_ref="provider-invoice:INV-1",
        reconciled_by_reference="admin:ops-1",
    )

    call = ledger.get_call(call_id)
    assert call.state == "settled" and call.actual_micro == 130
    assert call.evidence_ref == "provider-invoice:INV-1"
    assert call.reconciled_by_reference == "admin:ops-1"
    assert call.reconciled_at is not None
    assert call.unknown_at is not None  # earlier state evidence kept
    assert bucket_tuple(ledger, budget_id) == (0, 100, 0, 30, 0, 60, 0, 0)
    assert ledger.verify_conservation(budget_id).consistent


def test_t8_reconcile_unknown_to_released_with_evidence() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = to_state(ledger, budget_id, "k1", "unknown")

    ledger.reconcile_unknown_released(
        call_id,
        evidence_ref="provider-support:ticket-9",
        reconciled_by_reference="admin:ops-1",
    )

    call = ledger.get_call(call_id)
    assert call.state == "released"
    assert call.release_basis == "admin_reconciliation"
    assert call.reconciled_by_reference == "admin:ops-1"
    assert bucket_tuple(ledger, budget_id) == (0,) * 8


@pytest.mark.parametrize(
    "evidence, reconciler",
    [(None, "admin:1"), ("", "admin:1"), ("ev", None), ("ev", "  "), (" ", " ")],
)
def test_bi5_unknown_is_never_released_without_evidence_and_reconciler(
    evidence, reconciler
) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = to_state(ledger, budget_id, "k1", "unknown")
    before = bucket_tuple(ledger, budget_id)

    with pytest.raises(GatewayError) as caught:
        ledger.reconcile_unknown_released(
            call_id,
            evidence_ref=evidence,
            reconciled_by_reference=reconciler,
        )
    assert caught.value.code is EVIDENCE

    with pytest.raises(GatewayError) as caught:
        ledger.reconcile_unknown_settled(
            call_id,
            actual_micro=1,
            actual_tokens=1,
            evidence_ref=evidence,
            reconciled_by_reference=reconciler,
        )
    assert caught.value.code is EVIDENCE

    assert ledger.get_call(call_id).state == "unknown"
    assert bucket_tuple(ledger, budget_id) == before


# ----------------------------------------------------------------------
# transition matrix: only legal edges succeed, rejections are inert
# ----------------------------------------------------------------------

OPERATIONS = {
    "mark_in_flight": lambda ledger, cid: ledger.mark_in_flight(
        cid, timeout_seconds=60
    ),
    "settle": lambda ledger, cid: ledger.settle(
        cid, actual_micro=10, actual_tokens=10
    ),
    "mark_unknown": lambda ledger, cid: ledger.mark_unknown(
        cid, error_class="timeout"
    ),
    "release_never_sent": lambda ledger, cid: ledger.release_never_sent(cid),
    "release_never_sent_with_evidence": lambda ledger, cid: (
        ledger.release_never_sent(cid, evidence_ref="attested")
    ),
    "release_by_policy": lambda ledger, cid: (
        ledger.release_by_provider_policy(cid, error_class="http_500")
    ),
    "reconcile_settled": lambda ledger, cid: ledger.reconcile_unknown_settled(
        cid,
        actual_micro=10,
        actual_tokens=10,
        evidence_ref="ev",
        reconciled_by_reference="admin",
    ),
    "reconcile_released": lambda ledger, cid: ledger.reconcile_unknown_released(
        cid, evidence_ref="ev", reconciled_by_reference="admin"
    ),
}

OK = "ok"
EXPECTED = {
    "reserved": {
        "mark_in_flight": OK,
        "release_never_sent": OK,
        "release_never_sent_with_evidence": OK,
    },
    "in_flight": {
        "settle": OK,
        "mark_unknown": OK,
        "release_never_sent": EVIDENCE,
        "release_never_sent_with_evidence": OK,
        "release_by_policy": EVIDENCE,
    },
    "unknown": {"reconcile_settled": OK, "reconcile_released": OK},
    "settled": {},
    "released": {},
}


@pytest.mark.parametrize("state", list(EXPECTED))
@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_transition_matrix(state: str, operation: str) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = to_state(ledger, budget_id, "matrix", state)
    before = bucket_tuple(ledger, budget_id)
    expected = EXPECTED[state].get(operation, INVALID)

    if expected == OK:
        OPERATIONS[operation](ledger, call_id)
        assert ledger.get_call(call_id).state != state
    else:
        with pytest.raises(GatewayError) as caught:
            OPERATIONS[operation](ledger, call_id)
        assert caught.value.code is expected
        # A rejected transition is inert: same state, same balances.
        assert ledger.get_call(call_id).state == state
        assert bucket_tuple(ledger, budget_id) == before

    assert ledger.verify_conservation(budget_id).consistent


def test_repeating_a_transition_is_deterministically_rejected() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    ledger.settle(call_id, actual_micro=50, actual_tokens=50)
    after = bucket_tuple(ledger, budget_id)

    for _ in range(3):
        with pytest.raises(GatewayError) as caught:
            ledger.settle(call_id, actual_micro=50, actual_tokens=50)
        assert caught.value.code is INVALID
        with pytest.raises(GatewayError):
            ledger.mark_in_flight(call_id, timeout_seconds=60)

    assert bucket_tuple(ledger, budget_id) == after  # no double charge


def test_unknown_call_id_is_rejected() -> None:
    ledger = make_ledger()

    with pytest.raises(GatewayError) as caught:
        ledger.settle(999_999, actual_micro=1, actual_tokens=1)

    assert caught.value.code is GatewayErrorCode.CALL_NOT_FOUND


# ----------------------------------------------------------------------
# admission (BI-2) rules
# ----------------------------------------------------------------------


def test_admission_is_all_or_nothing_across_three_budgets() -> None:
    ledger = make_ledger()
    user = make_budget(ledger, ref="u", kind="user_period")
    objective = make_budget(ledger, ref="o", kind="objective", limit_micro=50)
    deployment = make_budget(ledger, ref="d")

    with pytest.raises(GatewayError) as caught:
        reserve(
            ledger,
            "k1",
            user=user,
            objective=objective,
            deployment=deployment,
            micro=100,
        )

    assert caught.value.code is GatewayErrorCode.BUDGET_EXCEEDED
    for budget_id in (user, objective, deployment):
        assert bucket_tuple(ledger, budget_id) == (0,) * 8  # no leaked hold
    with ledger._session_factory() as db:
        assert db.query(IntelligenceModelCall).count() == 0

    call_id = reserve(
        ledger,
        "k2",
        user=user,
        objective=objective,
        deployment=deployment,
        micro=50,
        tokens=50,
    )
    for budget_id in (user, objective, deployment):
        assert ledger.snapshot(budget_id).reserved_micro == 50
    assert call_id


def test_all_three_budgets_follow_the_call_through_settlement() -> None:
    ledger = make_ledger()
    user = make_budget(ledger, ref="u", kind="user_period")
    objective = make_budget(ledger, ref="o", kind="objective")
    deployment = make_budget(ledger, ref="d")
    call_id = reserve(
        ledger, "k1", user=user, objective=objective, deployment=deployment
    )
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    ledger.settle(call_id, actual_micro=130, actual_tokens=20)

    for budget_id in (user, objective, deployment):
        assert bucket_tuple(ledger, budget_id) == (0, 100, 0, 30, 0, 20, 0, 0)
        assert ledger.verify_conservation(budget_id).consistent


def test_duplicate_call_key_is_rejected_without_leaking_a_hold() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    reserve(ledger, "same", deployment=budget_id)

    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "same", deployment=budget_id)

    assert caught.value.code is GatewayErrorCode.DUPLICATE_CALL_KEY
    assert ledger.snapshot(budget_id).reserved_micro == 100


def test_missing_or_inactive_budgets_are_rejected() -> None:
    ledger = make_ledger()
    now = utcnow()
    expired = ledger.create_budget(
        scope_kind="deployment_period",
        scope_ref="old",
        limit_micro=1000,
        limit_tokens=1000,
        period_start=now - timedelta(hours=3),
        period_end=now - timedelta(hours=2),
    )

    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "k1", deployment=expired)
    assert caught.value.code is GatewayErrorCode.BUDGET_NOT_ACTIVE

    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "k2", deployment=424242)
    assert caught.value.code is GatewayErrorCode.BUDGET_NOT_FOUND

    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "k3")
    assert caught.value.code is GatewayErrorCode.INVALID_REQUEST

    for bad in (0, -1):
        with pytest.raises(GatewayError):
            reserve(ledger, "k4", deployment=expired, micro=bad)


def test_tokens_dimension_is_enforced_independently() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, limit_micro=10_000, limit_tokens=150)
    reserve(ledger, "k1", deployment=budget_id, micro=10, tokens=100)

    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "k2", deployment=budget_id, micro=10, tokens=100)

    assert caught.value.code is GatewayErrorCode.BUDGET_EXCEEDED


def test_unknown_fraction_breaker_stops_admission_until_reconciled() -> None:
    ledger = make_ledger(unknown_fraction_bps=2500)
    budget_id = make_budget(ledger, limit_micro=1000, limit_tokens=1000)
    unknown_ids = []

    for index in range(2):
        cid = reserve(ledger, f"u{index}", deployment=budget_id)
        ledger.mark_in_flight(cid, timeout_seconds=60)
        ledger.mark_unknown(cid, error_class="timeout")
        unknown_ids.append(cid)

    assert not ledger.unknown_fraction_exceeded(budget_id)  # 200 < 250
    cid = reserve(ledger, "u2", deployment=budget_id)
    ledger.mark_in_flight(cid, timeout_seconds=60)
    ledger.mark_unknown(cid, error_class="timeout")
    unknown_ids.append(cid)

    assert ledger.unknown_fraction_exceeded(budget_id)  # 300 >= 250
    with pytest.raises(GatewayError) as caught:
        reserve(ledger, "blocked", deployment=budget_id)
    assert caught.value.code is GatewayErrorCode.UNKNOWN_FRACTION_BREAKER
    assert ledger.snapshot(budget_id).reserved_micro == 0

    ledger.reconcile_unknown_released(
        unknown_ids[0],
        evidence_ref="provider-support:t1",
        reconciled_by_reference="admin:1",
    )
    assert not ledger.unknown_fraction_exceeded(budget_id)
    reserve(ledger, "resumed", deployment=budget_id)  # admission resumes


# ----------------------------------------------------------------------
# BI-1: database CHECK constraints (defence if application code is wrong)
# ----------------------------------------------------------------------


def _expect_integrity_error(action) -> None:
    session = SessionLocal()

    try:
        with pytest.raises(IntegrityError):
            action(session)
            session.flush()
    finally:
        session.rollback()
        session.close()


def _call_kwargs(budget_id: int, **overrides) -> dict:
    now = utcnow()
    values = {
        "call_key": "direct-1",
        "purpose": "lab",
        "provider": "fake",
        "model_profile": "lab-fast",
        "model_id": "fake-small-v0",
        "deployment_budget_id": budget_id,
        "reserved_micro": 10,
        "reserved_tokens": 10,
        "deadline_at": now + timedelta(minutes=1),
    }
    values.update(overrides)
    return values


def test_check_constraints_reject_budget_overcommit_and_negatives() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, limit_micro=100, limit_tokens=100)

    def overcommit(db):
        db.execute(
            text(
                "UPDATE intelligence_budgets SET reserved_micro = 60, "
                "settled_micro = 30, unknown_micro = 11 WHERE id = :id"
            ),
            {"id": budget_id},
        )

    def token_overcommit(db):
        db.execute(
            text(
                "UPDATE intelligence_budgets SET unknown_tokens = 101 "
                "WHERE id = :id"
            ),
            {"id": budget_id},
        )

    def negative(db):
        db.execute(
            text(
                "UPDATE intelligence_budgets SET overrun_micro = -1 "
                "WHERE id = :id"
            ),
            {"id": budget_id},
        )

    for action in (overcommit, token_overcommit, negative):
        _expect_integrity_error(action)

    assert bucket_tuple(ledger, budget_id) == (0,) * 8


def test_check_constraints_reject_malformed_budgets() -> None:
    now = utcnow()
    cases = {
        "bad scope kind": dict(scope_kind="galaxy", scope_ref="x"),
        "blank ref": dict(
            scope_kind="objective", scope_ref="  "
        ),
        "objective with period": dict(
            scope_kind="objective",
            scope_ref="x",
            period_start=now,
            period_end=now + timedelta(hours=1),
        ),
        "periodic without period": dict(
            scope_kind="user_period", scope_ref="x"
        ),
        "inverted period": dict(
            scope_kind="user_period",
            scope_ref="x",
            period_start=now,
            period_end=now - timedelta(hours=1),
        ),
        "negative limit": dict(
            scope_kind="objective", scope_ref="x", limit_micro=-1
        ),
    }

    for name, overrides in cases.items():
        values = dict(limit_micro=10, limit_tokens=10)
        values.update(overrides)
        _expect_integrity_error(
            lambda db, v=values: db.add(IntelligenceBudget(**v))
        )


def test_budget_scope_uniqueness_covers_periodic_and_objective() -> None:
    ledger = make_ledger()
    now = utcnow()
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=1)
    ledger.create_budget(
        scope_kind="user_period",
        scope_ref="u1",
        limit_micro=1,
        limit_tokens=1,
        period_start=start,
        period_end=end,
    )
    ledger.create_budget(
        scope_kind="objective", scope_ref="o1", limit_micro=1, limit_tokens=1
    )

    for kind, ref, p_start, p_end in (
        ("user_period", "u1", start, end),
        ("objective", "o1", None, None),
    ):
        with pytest.raises(IntegrityError):
            ledger.create_budget(
                scope_kind=kind,
                scope_ref=ref,
                limit_micro=1,
                limit_tokens=1,
                period_start=p_start,
                period_end=p_end,
            )


def test_check_constraints_reject_unsafe_call_rows() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    now = utcnow()
    cases = {
        "real data": dict(data_classification="real"),
        "real provider": dict(provider="openai"),
        "bad state": dict(state="done"),
        "no budget": dict(deployment_budget_id=None),
        "attempt 2 orphan": dict(attempt=2),
        "attempt 1 with parent": dict(parent_call_id=1),
        "zero reservation": dict(reserved_micro=0),
        "reserved but sent": dict(sent_at=now),
        "in_flight without sent_at": dict(state="in_flight"),
        "settled without actuals": dict(
            state="settled", sent_at=now, settled_at=now
        ),
        "unknown without timestamp": dict(state="unknown", sent_at=now),
        "released without basis": dict(state="released", released_at=now),
        "released with actuals": dict(
            state="released",
            released_at=now,
            release_basis="never_sent",
            actual_micro=1,
            actual_tokens=1,
        ),
        "bad basis": dict(
            state="released", released_at=now, release_basis="because"
        ),
        "admin release without evidence": dict(
            state="released",
            released_at=now,
            release_basis="admin_reconciliation",
        ),
        "policy release without evidence": dict(
            state="released",
            released_at=now,
            release_basis="provider_documented",
        ),
        "in_flight never_sent without evidence": dict(
            state="released",
            sent_at=now,
            released_at=now,
            release_basis="never_sent",
        ),
        "reconciler without timestamp": dict(
            reconciled_by_reference="admin", evidence_ref="e"
        ),
        "bad digest": dict(request_digest="not-hex"),
        "upper-case digest": dict(response_digest="A" * 64),
    }

    for name, overrides in cases.items():
        _expect_integrity_error(
            lambda db, o=overrides: db.add(
                IntelligenceModelCall(**_call_kwargs(budget_id, **o))
            )
        )

    # control: the same builder with no overrides is accepted
    session = SessionLocal()
    try:
        session.add(IntelligenceModelCall(**_call_kwargs(budget_id)))
        session.flush()
    finally:
        session.rollback()
        session.close()


def test_a_corrupted_row_is_detected_by_conservation_check() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    ledger.settle(call_id, actual_micro=40, actual_tokens=40)
    assert ledger.verify_conservation(budget_id).consistent

    # Synthetic fault: someone edits a balance outside the ledger.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE intelligence_budgets SET settled_micro = "
                "settled_micro + 1 WHERE id = :id"
            ),
            {"id": budget_id},
        )

    report = ledger.verify_conservation(budget_id)
    assert not report.consistent
    assert report.stored["settled_micro"] == 41
    assert report.recomputed["settled_micro"] == 40


# ----------------------------------------------------------------------
# randomized interleavings against an independent model
# ----------------------------------------------------------------------


class _Model:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.R = self.S = self.U = self.O = 0

    def admits(self, r: int, theta_bps: int) -> bool:
        return (
            self.R + self.S + self.U + self.O + r <= self.limit
            and self.U * 10_000 < theta_bps * self.limit
        )


@pytest.mark.parametrize("seed", [11, 29, 47])
def test_random_interleaving_matches_an_independent_model(seed) -> None:
    rng = random.Random(seed)
    theta = 4000
    ledger = make_ledger(unknown_fraction_bps=theta)
    limit = 600
    budget_id = make_budget(ledger, limit_micro=limit, limit_tokens=limit)
    model = _Model(limit)
    calls: dict[int, dict] = {}
    counter = 0

    for _ in range(90):
        op = rng.choice(
            ["reserve", "reserve", "send", "settle", "unknown", "release", "reconcile"]
        )
        live = lambda *states: [  # noqa: E731
            cid for cid, c in calls.items() if c["state"] in states
        ]

        if op == "reserve":
            counter += 1
            r = rng.choice([10, 25, 50])
            expected = model.admits(r, theta)
            try:
                cid = reserve(
                    ledger,
                    f"r{counter}",
                    deployment=budget_id,
                    micro=r,
                    tokens=r,
                )
            except GatewayError:
                assert not expected
            else:
                assert expected
                model.R += r
                calls[cid] = {"state": "reserved", "r": r}
        elif op == "send" and live("reserved"):
            cid = rng.choice(live("reserved"))
            ledger.mark_in_flight(cid, timeout_seconds=60)
            calls[cid]["state"] = "in_flight"
        elif op in ("settle", "unknown", "release") and live("in_flight"):
            cid = rng.choice(live("in_flight"))
            r = calls[cid]["r"]
            model.R -= r

            if op == "settle":
                actual = rng.randint(0, r * 2)
                ledger.settle(cid, actual_micro=actual, actual_tokens=actual)
                model.S += min(actual, r)
                model.O += max(actual - r, 0)
                calls[cid]["state"] = "settled"
            elif op == "unknown":
                ledger.mark_unknown(cid, error_class="timeout")
                model.U += r
                calls[cid]["state"] = "unknown"
            else:
                ledger.release_never_sent(cid, evidence_ref="attested")
                calls[cid]["state"] = "released"
        elif op == "reconcile" and live("unknown"):
            cid = rng.choice(live("unknown"))
            r = calls[cid]["r"]
            model.U -= r

            if rng.random() < 0.5:
                actual = rng.randint(0, r * 2)
                ledger.reconcile_unknown_settled(
                    cid,
                    actual_micro=actual,
                    actual_tokens=actual,
                    evidence_ref="ev",
                    reconciled_by_reference="admin",
                )
                model.S += min(actual, r)
                model.O += max(actual - r, 0)
                calls[cid]["state"] = "settled"
            else:
                ledger.reconcile_unknown_released(
                    cid, evidence_ref="ev", reconciled_by_reference="admin"
                )
                calls[cid]["state"] = "released"

        snap = ledger.snapshot(budget_id)
        assert (
            snap.reserved_micro,
            snap.settled_micro,
            snap.unknown_micro,
            snap.overrun_micro,
        ) == (model.R, model.S, model.U, model.O)
        assert snap.reserved_micro + snap.settled_micro + snap.unknown_micro <= limit
        assert snap.confirmed_micro == model.S + model.O
        assert ledger.verify_conservation(budget_id).consistent


# ----------------------------------------------------------------------
# audit scope (PO caveat): current-state conservation, no history table
# ----------------------------------------------------------------------


def test_audit_scope_has_no_append_only_history_table() -> None:
    tables = set(inspect(engine).get_table_names())

    assert {"intelligence_budgets", "intelligence_model_calls"} <= tables
    assert "intelligence_budget_entries" not in tables
    assert not [t for t in tables if t.startswith("intelligence_") and t not in (
        "intelligence_budgets",
        "intelligence_model_calls",
    )]


def test_per_state_evidence_that_does_persist() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    first = reserve(ledger, "k1", deployment=budget_id)
    ledger.mark_in_flight(first, timeout_seconds=60)
    ledger.mark_unknown(first, error_class="timeout")
    ledger.reconcile_unknown_settled(
        first,
        actual_micro=70,
        actual_tokens=70,
        evidence_ref="invoice:1",
        reconciled_by_reference="admin:1",
    )

    call = ledger.get_call(first)

    # Persisted: one timestamp per state reached, evidence, reconciler.
    assert call.reserved_at and call.sent_at and call.unknown_at
    assert call.settled_at and call.reconciled_at
    assert (call.evidence_ref, call.reconciled_by_reference) == (
        "invoice:1",
        "admin:1",
    )
    # NOT persisted: the sequence/amounts of intermediate balances. The
    # budget row only holds the current balance (one version counter).
    assert ledger.snapshot(budget_id).version >= 3


def test_documentation_states_the_audit_limitation_precisely() -> None:
    text_ = (
        BACKEND / "docs" / "intelligence" / "BUDGET_LEDGER.md"
    ).read_text(encoding="utf-8")

    assert "intelligence_budget_entries" in text_
    assert "NOT append-only" in text_
    assert "current accounting conservation" in text_.lower()
    assert "immutable transition history" in text_.lower()
