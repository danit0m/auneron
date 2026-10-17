"""INTELLIGENCE-IMPL-0 -- PostgreSQL concurrency of the budget ledger.

Real threads, real connections, real row locks (READ COMMITTED). The
admission predicate lives in the UPDATE...WHERE, so these tests prove it
holds under contention: no admitted-budget overrun, no leaked partial
hold, no deadlock, and balances that still equal their call rows.
"""

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.database.database import SessionLocal
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.pricing import Reservation


pytestmark = pytest.mark.concurrency

WORKERS = 50
JOIN_TIMEOUT = 120


def make_ledger(**overrides) -> BudgetLedger:
    options = {
        "unknown_fraction_bps": 10_000,
        "recovery_grace_seconds": 0,
    }
    options.update(overrides)
    return BudgetLedger(SessionLocal, **options)


def make_budget(ledger, ref, *, limit, kind="deployment_period") -> int:
    now = utcnow()
    periodic = kind != "objective"
    return ledger.create_budget(
        scope_kind=kind,
        scope_ref=ref,
        limit_micro=limit,
        limit_tokens=limit,
        period_start=now - timedelta(hours=1) if periodic else None,
        period_end=now + timedelta(hours=1) if periodic else None,
    )


def reserve(ledger, key, *, r=100, **budgets) -> int:
    return ledger.reserve(
        call_key=key,
        purpose="lab.concurrency",
        provider="fake",
        model_profile="lab-fast",
        model_id="fake-small-v0",
        reservation=Reservation(r, r),
        deadline_seconds=60,
        **budgets,
    )


def run_parallel(tasks):
    """Runs every task at the same instant; returns (value|exception)s."""
    barrier = threading.Barrier(len(tasks))

    def runner(task):
        barrier.wait(timeout=JOIN_TIMEOUT)

        try:
            return task()
        except BaseException as error:  # noqa: BLE001 - collected
            return error

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(runner, task) for task in tasks]
        return [future.result(timeout=JOIN_TIMEOUT) for future in futures]


def codes(results) -> Counter:
    return Counter(
        item.code.value if isinstance(item, GatewayError) else "ok"
        for item in results
        if not isinstance(item, BaseException) or isinstance(item, GatewayError)
    )


def assert_no_unexpected(results) -> None:
    unexpected = [
        item
        for item in results
        if isinstance(item, BaseException)
        and not isinstance(item, GatewayError)
    ]
    assert unexpected == []


def test_fifty_concurrent_reservations_never_overrun_the_budget() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, "dep", limit=2000)  # room for 20 x 100

    results = run_parallel(
        [
            (lambda i=i: reserve(ledger, f"c-{i}", deployment_budget_id=budget_id))
            for i in range(WORKERS)
        ]
    )

    assert_no_unexpected(results)
    assert codes(results) == Counter({"ok": 20, "budget_exceeded": 30})
    snap = ledger.snapshot(budget_id)
    assert (snap.reserved_micro, snap.reserved_tokens) == (2000, 2000)
    assert ledger.verify_conservation(budget_id).consistent
    assert len({r for r in results if isinstance(r, int)}) == 20


def test_fifty_concurrent_reservations_on_three_budgets_are_all_or_nothing() -> None:
    ledger = make_ledger()
    user = make_budget(ledger, "u", limit=5000, kind="user_period")
    objective = make_budget(ledger, "o", limit=1000, kind="objective")
    deployment = make_budget(ledger, "d", limit=5000)

    results = run_parallel(
        [
            (
                lambda i=i: reserve(
                    ledger,
                    f"t-{i}",
                    user_budget_id=user,
                    objective_budget_id=objective,
                    deployment_budget_id=deployment,
                )
            )
            for i in range(WORKERS)
        ]
    )

    assert_no_unexpected(results)
    assert codes(results) == Counter({"ok": 10, "budget_exceeded": 40})
    for budget_id in (user, objective, deployment):
        # rejected calls left no partial hold on the roomy budgets
        assert ledger.snapshot(budget_id).reserved_micro == 1000
        assert ledger.verify_conservation(budget_id).consistent


def test_opposite_budget_orders_do_not_deadlock() -> None:
    ledger = make_ledger()
    first = make_budget(ledger, "a", limit=100_000, kind="user_period")
    second = make_budget(ledger, "b", limit=100_000)

    def forward(i):
        return reserve(
            ledger, f"f-{i}", user_budget_id=first, deployment_budget_id=second
        )

    def backward(i):
        return reserve(
            ledger, f"b-{i}", user_budget_id=second, deployment_budget_id=first
        )

    # user/deployment slots swapped: declared order is opposite
    results = run_parallel(
        [
            (lambda i=i: forward(i)) if i % 2 == 0 else (lambda i=i: backward(i))
            for i in range(60)
        ]
    )

    assert_no_unexpected(results)
    assert codes(results) == Counter({"ok": 60})
    for budget_id in (first, second):
        assert ledger.snapshot(budget_id).reserved_micro == 6000


def test_concurrent_full_lifecycles_keep_balances_equal_to_calls() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, "dep", limit=10_000)
    outcomes = ["settle", "overrun", "unknown", "release"]

    def lifecycle(i):
        call_id = reserve(ledger, f"l-{i}", deployment_budget_id=budget_id)
        ledger.mark_in_flight(call_id, timeout_seconds=60)
        outcome = outcomes[i % len(outcomes)]

        if outcome == "settle":
            ledger.settle(call_id, actual_micro=60, actual_tokens=60)
        elif outcome == "overrun":
            ledger.settle(call_id, actual_micro=140, actual_tokens=100)
        elif outcome == "unknown":
            ledger.mark_unknown(call_id, error_class="timeout")
        else:
            ledger.release_never_sent(call_id, evidence_ref="attested")

        return outcome

    results = run_parallel([(lambda i=i: lifecycle(i)) for i in range(WORKERS)])

    assert_no_unexpected(results)
    assert codes(results) == Counter({"ok": WORKERS})
    snap = ledger.snapshot(budget_id)
    counts = Counter(outcomes[i % 4] for i in range(WORKERS))
    assert snap.reserved_micro == 0
    assert snap.settled_micro == counts["settle"] * 60 + counts["overrun"] * 100
    assert snap.overrun_micro == counts["overrun"] * 40
    assert snap.unknown_micro == counts["unknown"] * 100
    assert snap.reserved_micro + snap.settled_micro + snap.unknown_micro <= snap.limit_micro
    assert ledger.verify_conservation(budget_id).consistent


def test_same_call_key_races_to_exactly_one_reservation() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, "dep", limit=100_000)

    results = run_parallel(
        [
            (lambda: reserve(ledger, "one-key", deployment_budget_id=budget_id))
            for _ in range(20)
        ]
    )

    assert_no_unexpected(results)
    assert codes(results) == Counter({"ok": 1, "duplicate_call_key": 19})
    assert ledger.snapshot(budget_id).reserved_micro == 100
    assert ledger.verify_conservation(budget_id).consistent


def test_settle_and_unknown_race_on_one_call_has_one_winner() -> None:
    for round_index in range(10):
        ledger = make_ledger()
        budget_id = make_budget(ledger, f"dep-{round_index}", limit=10_000)
        call_id = reserve(ledger, f"race-{round_index}", deployment_budget_id=budget_id)
        ledger.mark_in_flight(call_id, timeout_seconds=60)

        results = run_parallel(
            [
                lambda: ledger.settle(call_id, actual_micro=70, actual_tokens=70),
                lambda: ledger.mark_unknown(call_id, error_class="timeout"),
                lambda: ledger.settle(call_id, actual_micro=70, actual_tokens=70),
                lambda: ledger.release_never_sent(call_id, evidence_ref="attested"),
            ]
        )

        assert_no_unexpected(results)
        assert codes(results)["ok"] == 1  # exactly one transition wins
        assert codes(results)["invalid_transition"] == 3
        assert ledger.verify_conservation(budget_id).consistent
        assert ledger.get_call(call_id).state in ("settled", "unknown", "released")


def test_recovery_racing_a_late_settle_stays_consistent() -> None:
    ledger = make_ledger(recovery_grace_seconds=0)
    budget_id = make_budget(ledger, "dep", limit=100_000)
    call_ids = []

    for index in range(20):
        call_id = reserve(ledger, f"rc-{index}", deployment_budget_id=budget_id)
        ledger.mark_in_flight(call_id, timeout_seconds=1)
        call_ids.append(call_id)

    future = utcnow() + timedelta(hours=1)
    tasks = [lambda: ledger.recover(now=future), lambda: ledger.recover(now=future)]
    tasks += [
        (lambda c=c: ledger.settle(c, actual_micro=50, actual_tokens=50))
        for c in call_ids
    ]

    results = run_parallel(tasks)

    assert_no_unexpected(results)
    states = Counter(ledger.get_call(c).state for c in call_ids)
    assert set(states) <= {"settled", "unknown"}  # never released
    assert sum(states.values()) == 20
    snap = ledger.snapshot(budget_id)
    assert snap.reserved_micro == 0
    assert snap.unknown_micro == states["unknown"] * 100
    assert ledger.verify_conservation(budget_id).consistent
    # recovery twice is idempotent: nothing left to recover
    again = ledger.recover(now=future)
    assert again.released_never_sent == () and again.marked_unknown == ()


def test_randomized_parallel_mix_never_breaks_bi1() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger, "dep", limit=3000)

    def worker(i):
        r = (50, 100, 200)[i % 3]
        try:
            call_id = reserve(ledger, f"m-{i}", r=r, deployment_budget_id=budget_id)
        except GatewayError:
            return "rejected"

        ledger.mark_in_flight(call_id, timeout_seconds=60)
        pick = (i * 7) % 5

        if pick == 0:
            ledger.mark_unknown(call_id, error_class="timeout")
        elif pick == 1:
            ledger.settle(call_id, actual_micro=r * 2, actual_tokens=r)
        else:
            ledger.settle(call_id, actual_micro=r // 2, actual_tokens=r // 2)

        return "done"

    results = run_parallel([(lambda i=i: worker(i)) for i in range(WORKERS)])

    assert_no_unexpected(results)
    snap = ledger.snapshot(budget_id)
    assert snap.reserved_micro + snap.settled_micro + snap.unknown_micro <= 3000
    assert snap.reserved_micro == 0
    assert ledger.verify_conservation(budget_id).consistent


def test_sensitivity_a_naive_read_then_write_admission_is_caught() -> None:
    """
    Synthetic negative control: an admission that READS the balance and
    then writes (instead of one atomic UPDATE...WHERE) loses updates under
    contention. The same 50-thread scenario must expose it -- first as
    wrong accounting, and at the very least as the BI-1 CHECK firing --
    proving the real tests above can detect that class of defect.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.database.database import engine

    ledger = make_ledger()
    budget_id = make_budget(ledger, "naive", limit=2000)

    def naive_reserve(_i):
        with engine.begin() as connection:
            held = connection.execute(
                text(
                    "SELECT reserved_micro FROM intelligence_budgets "
                    "WHERE id = :id"
                ),
                {"id": budget_id},
            ).scalar_one()

            if held + 100 > 2000:
                return "rejected"

            threading.Event().wait(0.02)  # widen the race window
            connection.execute(
                text(
                    "UPDATE intelligence_budgets SET reserved_micro = :v, "
                    "reserved_tokens = :v WHERE id = :id"
                ),
                {"v": held + 100, "id": budget_id},
            )
            return "admitted"

    results = run_parallel([(lambda i=i: naive_reserve(i)) for i in range(WORKERS)])

    check_failures = [r for r in results if isinstance(r, IntegrityError)]
    admitted = sum(1 for r in results if r == "admitted")
    final = ledger.snapshot(budget_id).reserved_micro

    # Lost updates: admitted > 20 while the balance reflects fewer holds
    # (accounting wrong) and/or the CHECK rejected overcommit.
    assert admitted > 20 or check_failures or final != admitted * 100
