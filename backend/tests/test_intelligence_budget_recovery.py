"""INTELLIGENCE-IMPL-0 -- crash windows and restart recovery.

A process death is simulated with SimulatedCrash (a BaseException the
gateway cannot swallow). "Restart" means brand-new ledger, gateway and
adapter objects reading only what PostgreSQL committed.
"""

from datetime import timedelta

import pytest

from app.database.database import SessionLocal
from app.intelligence.gateway.adapters.fake import FakeProvider
from app.intelligence.gateway.adapters.fake import ok
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.errors import SimulatedCrash
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.profiles import build_lab_registry
from app.intelligence.gateway.pricing import Reservation


pytestmark = pytest.mark.contract

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
TIMEOUT = 5
GRACE = 30


class Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


class CrashBeforeMark(BudgetLedger):
    def mark_in_flight(self, call_id, **kwargs):
        raise SimulatedCrash()


class CrashAfterMark(BudgetLedger):
    def mark_in_flight(self, call_id, **kwargs):
        super().mark_in_flight(call_id, **kwargs)
        raise SimulatedCrash()


class CrashBeforeSettle(BudgetLedger):
    def settle(self, call_id, **kwargs):
        raise SimulatedCrash()


class AmbiguousMarkAfterCommit(BudgetLedger):
    def mark_in_flight(self, call_id, **kwargs):
        super().mark_in_flight(call_id, **kwargs)
        raise RuntimeError("commit acknowledgement lost")


class AmbiguousMarkBeforeCommit(BudgetLedger):
    def mark_in_flight(self, call_id, **kwargs):
        raise RuntimeError("connection reset before the mark")


def make_ledger(clock: Clock, cls=BudgetLedger) -> BudgetLedger:
    return cls(
        SessionLocal,
        unknown_fraction_bps=10_000,
        recovery_grace_seconds=GRACE,
        clock=clock,
    )


def make_budget(ledger: BudgetLedger, clock: Clock) -> int:
    return ledger.create_budget(
        scope_kind="deployment_period",
        scope_ref="dep",
        limit_micro=1_000_000,
        limit_tokens=1_000_000,
        period_start=clock.now - timedelta(days=1),
        period_end=clock.now + timedelta(days=1),
    )


def make_gateway(ledger, script=None, on_invoke=None):
    adapter = FakeProvider(script or [ok({"ok": True})], on_invoke=on_invoke)
    gateway = Gateway(
        registry=build_lab_registry(),
        adapters={"fake": adapter},
        ledger=ledger,
        limits=GatewayLimits(
            max_attempts=1,
            call_timeout_seconds=TIMEOUT,
            reservation_margin_bps=2000,
        ),
        mode_provider=lambda: "shadow",
    )
    return gateway, adapter


def request(budget_id: int, key: str = "rec-1") -> ModelCallRequest:
    return ModelCallRequest(
        call_key=key,
        purpose="lab.recovery",
        profile="lab-fast",
        instructions="Return JSON.",
        input_text="synthetic",
        output_schema=SCHEMA,
        max_output_tokens=32,
        deployment_budget_id=budget_id,
    )


def only_call(ledger: BudgetLedger, key: str):
    from app.models.intelligence_budget import IntelligenceModelCall

    with SessionLocal() as db:
        return (
            db.query(IntelligenceModelCall)
            .filter(IntelligenceModelCall.call_key == key)
            .one()
        )


def restart(clock: Clock) -> BudgetLedger:
    """A new process: fresh objects, only committed rows survive."""
    return make_ledger(clock)


# ----------------------------------------------------------------------
# the four crash windows
# ----------------------------------------------------------------------


def test_crash_before_the_in_flight_mark_releases_as_never_sent() -> None:
    clock = Clock()
    ledger = make_ledger(clock, CrashBeforeMark)
    budget_id = make_budget(ledger, clock)
    gateway, adapter = make_gateway(ledger)

    with pytest.raises(SimulatedCrash):
        gateway.call(request(budget_id))

    row = only_call(ledger, "rec-1#a1")
    assert row.state == "reserved" and row.sent_at is None
    assert adapter.calls == []  # nothing was ever sent
    assert ledger.snapshot(budget_id).reserved_micro > 0

    revived = restart(clock)
    clock.advance(TIMEOUT + GRACE + 1)
    report = revived.recover()

    assert report.released_never_sent == (row.id,)
    assert report.marked_unknown == ()
    after = revived.get_call(row.id)
    assert after.state == "released" and after.release_basis == "never_sent"
    snap = revived.snapshot(budget_id)
    assert (snap.reserved_micro, snap.unknown_micro) == (0, 0)
    assert revived.verify_conservation(budget_id).consistent


def test_crash_after_mark_before_network_is_held_unknown_conservatively() -> None:
    clock = Clock()
    ledger = make_ledger(clock, CrashAfterMark)
    budget_id = make_budget(ledger, clock)
    gateway, adapter = make_gateway(ledger)

    with pytest.raises(SimulatedCrash):
        gateway.call(request(budget_id))

    row = only_call(ledger, "rec-1#a1")
    assert row.state == "in_flight" and row.sent_at is not None
    assert adapter.calls == []  # never actually transmitted ...

    revived = restart(clock)
    clock.advance(TIMEOUT + GRACE + 1)
    report = revived.recover()

    # ... yet we cannot prove it, so the hold is kept as unknown.
    assert report.marked_unknown == (row.id,)
    assert report.released_never_sent == ()
    assert revived.get_call(row.id).state == "unknown"
    snap = revived.snapshot(budget_id)
    assert snap.unknown_micro == row.reserved_micro and snap.reserved_micro == 0
    assert revived.verify_conservation(budget_id).consistent


def test_crash_after_send_is_unknown_and_never_released() -> None:
    clock = Clock()
    ledger = make_ledger(clock)
    budget_id = make_budget(ledger, clock)

    def die(_request):
        raise SimulatedCrash()

    gateway, adapter = make_gateway(ledger, on_invoke=die)

    with pytest.raises(SimulatedCrash):
        gateway.call(request(budget_id))

    row = only_call(ledger, "rec-1#a1")
    assert row.state == "in_flight" and len(adapter.calls) == 1

    revived = restart(clock)
    clock.advance(TIMEOUT + GRACE + 1)
    report = revived.recover()

    assert report.marked_unknown == (row.id,)
    after = revived.get_call(row.id)
    assert after.state == "unknown" and after.release_basis is None
    assert after.error_class == "crash_after_send"
    assert revived.snapshot(budget_id).unknown_micro == row.reserved_micro


def test_crash_after_response_before_settlement_is_unknown() -> None:
    clock = Clock()
    ledger = make_ledger(clock, CrashBeforeSettle)
    budget_id = make_budget(ledger, clock)
    gateway, adapter = make_gateway(ledger)

    with pytest.raises(SimulatedCrash):
        gateway.call(request(budget_id))

    assert len(adapter.calls) == 1  # the provider answered (and billed)

    revived = restart(clock)
    clock.advance(TIMEOUT + GRACE + 1)
    revived.recover()

    row = only_call(revived, "rec-1#a1")
    assert row.state == "unknown"  # held until evidence reconciles it
    assert revived.snapshot(budget_id).unknown_micro == row.reserved_micro
    assert revived.verify_conservation(budget_id).consistent


# ----------------------------------------------------------------------
# recovery timing, idempotence, safety
# ----------------------------------------------------------------------


def test_recovery_waits_for_deadline_plus_grace() -> None:
    clock = Clock()
    ledger = make_ledger(clock, CrashAfterMark)
    budget_id = make_budget(ledger, clock)
    gateway, _ = make_gateway(ledger)

    with pytest.raises(SimulatedCrash):
        gateway.call(request(budget_id))

    revived = restart(clock)
    clock.advance(TIMEOUT + GRACE - 1)  # still inside the grace window
    report = revived.recover()

    assert report.marked_unknown == () and report.released_never_sent == ()
    assert only_call(revived, "rec-1#a1").state == "in_flight"

    clock.advance(2)
    assert len(revived.recover().marked_unknown) == 1


def test_recovery_is_idempotent_and_leaves_terminal_states_alone() -> None:
    clock = Clock()
    ledger = make_ledger(clock)
    budget_id = make_budget(ledger, clock)
    gateway, _ = make_gateway(ledger, [ok({"ok": True})])
    done = gateway.call(request(budget_id, "done"))
    assert done.final_state == "settled"

    other = make_ledger(clock, CrashAfterMark)
    crashing, _ = make_gateway(other)

    with pytest.raises(SimulatedCrash):
        crashing.call(request(budget_id, "stuck"))

    clock.advance(TIMEOUT + GRACE + 1)
    first = ledger.recover()
    snapshot_after_first = ledger.snapshot(budget_id)
    second = ledger.recover()

    assert len(first.marked_unknown) == 1
    assert second.marked_unknown == () and second.released_never_sent == ()
    assert ledger.snapshot(budget_id) == snapshot_after_first
    assert ledger.get_call(done.call_ids[0]).state == "settled"
    assert ledger.verify_conservation(budget_id).consistent


def test_recovery_never_releases_or_settles_unknown_calls() -> None:
    clock = Clock()
    ledger = make_ledger(clock)
    budget_id = make_budget(ledger, clock)
    call_id = ledger.reserve(
        call_key="u1",
        purpose="lab",
        provider="fake",
        model_profile="lab-fast",
        model_id="fake-small-v0",
        reservation=Reservation(100, 100),
        deadline_seconds=TIMEOUT,
        deployment_budget_id=budget_id,
    )
    ledger.mark_in_flight(call_id, timeout_seconds=TIMEOUT)
    ledger.mark_unknown(call_id, error_class="timeout")

    clock.advance(10_000)
    for _ in range(3):
        report = ledger.recover()
        assert report.released_never_sent == () and report.marked_unknown == ()

    assert ledger.get_call(call_id).state == "unknown"
    assert ledger.snapshot(budget_id).unknown_micro == 100


def test_resubmitting_a_crashed_call_key_is_rejected_not_double_sent() -> None:
    clock = Clock()
    ledger = make_ledger(clock, CrashAfterMark)
    budget_id = make_budget(ledger, clock)
    gateway, _ = make_gateway(ledger)

    with pytest.raises(SimulatedCrash):
        gateway.call(request(budget_id))

    revived = restart(clock)
    retry_gateway, retry_adapter = make_gateway(revived)
    outcome = retry_gateway.call(request(budget_id))  # same call_key

    assert outcome.error_code == "duplicate_call_key"
    assert retry_adapter.calls == []


def test_mixed_abandoned_calls_are_resolved_each_by_its_own_rule() -> None:
    clock = Clock()
    budget_ledger = make_ledger(clock)
    budget_id = make_budget(budget_ledger, clock)

    def abandon(cls, key):
        gateway, _ = make_gateway(make_ledger(clock, cls))

        with pytest.raises(SimulatedCrash):
            gateway.call(request(budget_id, key))

        return only_call(budget_ledger, f"{key}#a1").id

    reserved_id = abandon(CrashBeforeMark, "m-reserved")
    in_flight_id = abandon(CrashAfterMark, "m-inflight")
    settled = make_gateway(budget_ledger)[0].call(request(budget_id, "m-settled"))

    clock.advance(TIMEOUT + GRACE + 1)
    report = restart(clock).recover()

    assert report.released_never_sent == (reserved_id,)
    assert report.marked_unknown == (in_flight_id,)
    ledger = restart(clock)
    assert ledger.get_call(settled.call_ids[0]).state == "settled"
    snap = ledger.snapshot(budget_id)
    assert snap.reserved_micro == 0
    assert snap.unknown_micro == ledger.get_call(in_flight_id).reserved_micro
    assert ledger.verify_conservation(budget_id).consistent


# ----------------------------------------------------------------------
# ambiguous mark outcomes and late responses
# ----------------------------------------------------------------------


def test_mark_commit_acknowledgement_lost_never_sends_and_releases() -> None:
    clock = Clock()
    ledger = make_ledger(clock, AmbiguousMarkAfterCommit)
    budget_id = make_budget(ledger, clock)
    gateway, adapter = make_gateway(ledger)

    outcome = gateway.call(request(budget_id))

    assert not outcome.ok and adapter.calls == []
    row = only_call(ledger, "rec-1#a1")
    assert row.state == "released" and row.release_basis == "never_sent"
    assert row.evidence_ref == "gateway:aborted_before_send"
    assert row.sent_at is not None  # the mark did commit
    assert ledger.snapshot(budget_id).reserved_micro == 0


def test_mark_failing_before_commit_releases_the_reservation() -> None:
    clock = Clock()
    ledger = make_ledger(clock, AmbiguousMarkBeforeCommit)
    budget_id = make_budget(ledger, clock)
    gateway, adapter = make_gateway(ledger)

    outcome = gateway.call(request(budget_id))

    assert not outcome.ok and adapter.calls == []
    row = only_call(ledger, "rec-1#a1")
    assert row.state == "released" and row.sent_at is None
    assert ledger.snapshot(budget_id).reserved_micro == 0


def test_late_response_after_recovery_is_reconciled_with_evidence() -> None:
    clock = Clock()
    ledger = make_ledger(clock)
    budget_id = make_budget(ledger, clock)

    def recovery_wins_the_race(_request):
        clock.advance(TIMEOUT + GRACE + 1)
        ledger.recover()  # another worker declared it unknown first

    gateway, _ = make_gateway(
        ledger,
        [ok({"ok": True}, input_tokens=10, output_tokens=10)],
        on_invoke=recovery_wins_the_race,
    )

    outcome = gateway.call(request(budget_id))

    assert outcome.ok and outcome.final_state == "settled"
    row = ledger.get_call(outcome.call_ids[0])
    assert row.state == "settled" and row.actual_micro == 50
    assert row.reconciled_by_reference == "system:gateway"
    assert row.evidence_ref == "late_response:fake-req"
    assert row.unknown_at is not None  # the unknown interval is on record
    snap = ledger.snapshot(budget_id)
    assert (snap.reserved_micro, snap.unknown_micro) == (0, 0)
    assert snap.settled_micro == 50
    assert ledger.verify_conservation(budget_id).consistent
