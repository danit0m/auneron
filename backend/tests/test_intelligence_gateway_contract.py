"""INTELLIGENCE-IMPL-0 -- gateway contract (FakeProvider, PostgreSQL).

No network, no credential, no paid call: every provider interaction
is the deterministic in-process FakeProvider.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.database.database import SessionLocal
from app.database.database import engine
from app.intelligence.gateway.adapters.fake import FakeProvider
from app.intelligence.gateway.adapters.fake import http_error
from app.intelligence.gateway.adapters.fake import not_sent
from app.intelligence.gateway.adapters.fake import ok
from app.intelligence.gateway.adapters.fake import raw
from app.intelligence.gateway.adapters.fake import refusal
from app.intelligence.gateway.adapters.fake import timeout
from app.intelligence.gateway.adapters.fake import truncated
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.circuit_breaker import CircuitBreaker
from app.intelligence.gateway.concurrency import ConcurrencyLimiter
from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.gateway import strict_json_loads
from app.intelligence.gateway.pricing import PRICE_TABLE
from app.intelligence.gateway.pricing import cost_micro
from app.intelligence.gateway.pricing import input_token_upper_bound
from app.intelligence.gateway.pricing import worst_case_reservation
from app.intelligence.gateway.profiles import ModelProfile
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import build_lab_registry


pytestmark = pytest.mark.contract

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


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
    ref: str = "deployment",
    limit_micro: int = 10_000_000,
    limit_tokens: int = 10_000_000,
) -> int:
    now = utcnow()
    return ledger.create_budget(
        scope_kind="deployment_period",
        scope_ref=ref,
        limit_micro=limit_micro,
        limit_tokens=limit_tokens,
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )


def make_gateway(
    ledger: BudgetLedger,
    script,
    *,
    mode: str = "shadow",
    max_attempts: int = 2,
    on_invoke=None,
    breaker: CircuitBreaker | None = None,
    limiter: ConcurrencyLimiter | None = None,
) -> tuple[Gateway, FakeProvider]:
    adapter = FakeProvider(script, on_invoke=on_invoke)
    gateway = Gateway(
        registry=build_lab_registry(),
        adapters={"fake": adapter},
        ledger=ledger,
        limits=GatewayLimits(
            max_attempts=max_attempts,
            call_timeout_seconds=5,
            reservation_margin_bps=2000,
        ),
        mode_provider=lambda: mode,
        breaker=breaker,
        limiter=limiter,
    )
    return gateway, adapter


def make_request(budget_id: int, **overrides) -> ModelCallRequest:
    fields = {
        "call_key": "contract-1",
        "purpose": "lab.contract",
        "profile": "lab-fast",
        "instructions": "Return a JSON object.",
        "input_text": "synthetic input",
        "output_schema": SCHEMA,
        "max_output_tokens": 64,
        "deployment_budget_id": budget_id,
    }
    fields.update(overrides)
    return ModelCallRequest(**fields)


def reservation_for(request: ModelCallRequest, price_key: str):
    return worst_case_reservation(
        input_upper_bound_tokens=input_token_upper_bound(
            request.instructions,
            request.input_text,
            json.dumps(dict(request.output_schema), sort_keys=True),
        ),
        max_output_tokens=request.max_output_tokens,
        price=PRICE_TABLE[price_key],
        margin_bps=2000,
    )


# ----------------------------------------------------------------------
# pricing arithmetic
# ----------------------------------------------------------------------


def test_cost_is_ceiling_of_integer_micro_arithmetic() -> None:
    price = PRICE_TABLE["fake-small"]

    assert cost_micro(0, 0, price) == 0
    assert cost_micro(1, 0, price) == 1
    assert cost_micro(0, 1, price) == 4
    assert cost_micro(10, 10, price) == 50
    # 1 token at 1 micro/Mtok-equivalent never rounds down to zero.
    assert cost_micro(1, 0, PRICE_TABLE["fake-small"]) >= 1


def test_reservation_is_worst_case_with_margin() -> None:
    price = PRICE_TABLE["fake-small"]
    base = cost_micro(100, 50, price)
    reservation = worst_case_reservation(
        input_upper_bound_tokens=100,
        max_output_tokens=50,
        price=price,
        margin_bps=2000,
    )

    assert reservation.micro >= base
    assert reservation.micro == -(-base * 12000 // 10000)
    assert reservation.tokens == -(-150 * 12000 // 10000)
    assert worst_case_reservation(
        input_upper_bound_tokens=0,
        max_output_tokens=1,
        price=price,
        margin_bps=0,
    ).micro >= 1


def test_input_upper_bound_counts_utf8_bytes_not_characters() -> None:
    assert input_token_upper_bound("abc") == 3
    assert input_token_upper_bound("ação") == len("ação".encode("utf-8"))
    assert input_token_upper_bound("a", "bc") == 3


# ----------------------------------------------------------------------
# pre-flight refusals (ledger untouched)
# ----------------------------------------------------------------------


def test_mode_off_refuses_before_touching_ledger() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(ledger, [], mode="off")

    with pytest.raises(GatewayError) as caught:
        gateway.call(make_request(budget_id))

    assert caught.value.code is GatewayErrorCode.MODE_DISABLED
    assert adapter.calls == []
    assert ledger.snapshot(budget_id).reserved_micro == 0


@pytest.mark.parametrize("mode", ["governed", "bounded", "", "ON", "x"])
def test_unsupported_modes_are_refused(mode: str) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(ledger, [], mode=mode)

    with pytest.raises(GatewayError) as caught:
        gateway.call(make_request(budget_id))

    assert caught.value.code is GatewayErrorCode.MODE_NOT_SUPPORTED
    assert adapter.calls == []


@pytest.mark.parametrize(
    "classification",
    [DataClassification.REAL, "real", "synthetic", None, 1],
)
def test_only_the_synthetic_enum_member_is_accepted(
    classification,
) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(ledger, [])

    with pytest.raises(GatewayError) as caught:
        gateway.call(
            make_request(
                budget_id, data_classification=classification
            )
        )

    assert caught.value.code is GatewayErrorCode.REAL_DATA_REFUSED
    assert adapter.calls == []
    assert ledger.snapshot(budget_id).reserved_micro == 0


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"profile": "nope"}, GatewayErrorCode.UNKNOWN_PROFILE),
        ({"call_key": "  "}, GatewayErrorCode.INVALID_REQUEST),
        ({"call_key": "k" * 121}, GatewayErrorCode.INVALID_REQUEST),
        ({"purpose": ""}, GatewayErrorCode.INVALID_REQUEST),
        ({"max_output_tokens": 0}, GatewayErrorCode.INVALID_REQUEST),
        ({"max_output_tokens": 513}, GatewayErrorCode.INVALID_REQUEST),
        (
            {"output_schema": {"type": "not-a-type"}},
            GatewayErrorCode.INVALID_REQUEST,
        ),
        (
            {"input_text": "x" * 40_000},
            GatewayErrorCode.INVALID_REQUEST,
        ),
        (
            {"deployment_budget_id": None},
            GatewayErrorCode.INVALID_REQUEST,
        ),
    ],
)
def test_malformed_requests_are_refused_before_the_ledger(
    overrides, code
) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(ledger, [])

    with pytest.raises(GatewayError) as caught:
        gateway.call(make_request(budget_id, **overrides))

    assert caught.value.code is code
    assert adapter.calls == []
    assert ledger.snapshot(budget_id).reserved_micro == 0


# ----------------------------------------------------------------------
# structured output
# ----------------------------------------------------------------------


def test_valid_structured_output_settles_and_returns_parsed() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(
        ledger, [ok({"ok": True}, input_tokens=10, output_tokens=10)]
    )

    result = gateway.call(make_request(budget_id))

    assert result.ok and result.parsed == {"ok": True}
    assert result.error_code is None
    assert result.final_state == "settled"
    assert result.attempts == 1
    assert result.actual_micro == 50 and result.actual_tokens == 20
    snap = ledger.snapshot(budget_id)
    assert (snap.reserved_micro, snap.unknown_micro) == (0, 0)
    assert snap.settled_micro == 50 and snap.overrun_micro == 0
    assert ledger.verify_conservation(budget_id).consistent
    call = ledger.get_call(result.call_ids[0])
    assert call.state == "settled" and call.provider == "fake"
    assert call.data_classification == "synthetic"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[1,2]",
        '{"ok": "yes"}',
        '{"ok": true, "extra": 1}',
        '{"ok": true, "ok": false}',
        '{"ok": NaN}',
        '{"ok": Infinity}',
        "",
    ],
)
def test_invalid_output_is_rejected_but_still_charged(body: str) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(
        ledger, [raw(body), ok({"ok": True})]
    )

    result = gateway.call(make_request(budget_id))

    assert not result.ok
    assert result.error_code == "invalid_schema_response"
    assert result.final_state == "settled"
    assert result.attempts == 1  # never retried or fell back
    assert len(adapter.calls) == 1
    assert ledger.snapshot(budget_id).settled_micro == 50


def test_refusal_is_charged_and_never_retried() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(ledger, [refusal(), ok({"ok": True})])

    result = gateway.call(make_request(budget_id))

    assert (result.ok, result.error_code) == (False, "refusal")
    assert result.final_state == "settled" and result.actual_micro > 0
    assert len(adapter.calls) == 1 and result.attempts == 1


def test_truncation_is_charged_and_never_retried() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(
        ledger, [truncated(), ok({"ok": True})]
    )

    result = gateway.call(make_request(budget_id))

    assert (result.ok, result.error_code) == (False, "truncated")
    assert result.final_state == "settled" and result.actual_micro > 0
    assert len(adapter.calls) == 1 and result.attempts == 1


def test_strict_json_loader_rejects_duplicates_and_non_finite() -> None:
    assert strict_json_loads('{"a": 1}') == {"a": 1}

    for body in ('{"a": 1, "a": 2}', "NaN", "[Infinity]", "-Infinity"):
        with pytest.raises(ValueError):
            strict_json_loads(body)


def test_strict_json_loader_rejects_nested_duplicate_keys_and_non_finite() -> None:
    # An LLM response may conceal ambiguous keys inside a plan step.
    malformed = (
        '{"steps": [{"params": {"amount": 1, "amount": 2}}]}',
        '{"steps": [{"params": {"amount": NaN}}]}',
        '{"steps": [{"params": {"amount": Infinity}}]}',
    )
    for body in malformed:
        with pytest.raises(ValueError):
            strict_json_loads(body)


# ----------------------------------------------------------------------
# failures, retries, fallbacks
# ----------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_transient_http_errors_are_unknown_and_not_retried(
    status: int,
) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(
        ledger, [http_error(status), ok({"ok": True})]
    )

    result = gateway.call(make_request(budget_id))

    assert (result.ok, result.error_code) == (False, "provider_failure")
    assert result.final_state == "unknown" and result.attempts == 1
    assert len(adapter.calls) == 1
    snap = ledger.snapshot(budget_id)
    assert snap.unknown_micro > 0 and snap.reserved_micro == 0
    call = ledger.get_call(result.call_ids[0])
    assert call.error_class == f"http_{status}"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_http_errors_hold_unknown_then_fall_back(
    status: int,
) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, adapter = make_gateway(
        ledger, [http_error(status), ok({"ok": True})]
    )

    result = gateway.call(make_request(budget_id))

    assert result.ok and result.attempts == 2
    assert result.profiles_used == ("lab-fast", "lab-strong")
    first = ledger.get_call(result.call_ids[0])
    second = ledger.get_call(result.call_ids[1])
    assert first.state == "unknown" and first.error_class == f"http_{status}"
    assert second.state == "settled"
    assert second.parent_call_id == first.id and second.attempt == 2
    assert first.call_key != second.call_key
    # the first attempt's hold is still counted: never presumed free
    snap = ledger.snapshot(budget_id)
    assert snap.unknown_micro == first.reserved_micro
    assert ledger.verify_conservation(budget_id).consistent


def test_timeout_is_unknown_never_free() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, _ = make_gateway(ledger, [timeout()], max_attempts=1)

    result = gateway.call(make_request(budget_id))

    assert (result.ok, result.final_state) == (False, "unknown")
    snap = ledger.snapshot(budget_id)
    assert snap.unknown_micro > 0 and snap.settled_micro == 0


def test_proven_not_sent_is_released_with_evidence_and_retried() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, _ = make_gateway(
        ledger, [not_sent(), ok({"ok": True})]
    )

    result = gateway.call(make_request(budget_id))

    assert result.ok and result.attempts == 2
    first = ledger.get_call(result.call_ids[0])
    assert first.state == "released"
    assert first.release_basis == "never_sent"
    assert first.evidence_ref == "adapter_attested:not_sent"
    assert first.sent_at is not None  # the mark was durable first
    snap = ledger.snapshot(budget_id)
    assert snap.unknown_micro == 0 and snap.reserved_micro == 0


def test_no_fallback_or_retry_after_budget_rejection() -> None:
    ledger = make_ledger(unknown_fraction_bps=10_000)
    request_probe = make_request(0)
    first_hold = reservation_for(request_probe, "fake-small")
    budget_id = make_budget(
        ledger,
        limit_micro=first_hold.micro + 1,
        limit_tokens=10_000_000,
    )
    gateway, adapter = make_gateway(
        ledger, [timeout(), ok({"ok": True})]
    )

    result = gateway.call(make_request(budget_id))

    assert not result.ok
    assert result.error_code == "budget_exceeded"
    assert len(result.call_ids) == 1  # second attempt never reserved
    assert len(adapter.calls) == 1
    assert ledger.verify_conservation(budget_id).consistent


def test_every_attempt_takes_a_new_reservation() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway, _ = make_gateway(
        ledger, [timeout(), timeout()], max_attempts=2
    )

    result = gateway.call(make_request(budget_id))

    assert not result.ok and result.attempts == 2
    assert len(set(result.call_ids)) == 2
    snap = ledger.snapshot(budget_id)
    held = sum(
        ledger.get_call(call_id).reserved_micro
        for call_id in result.call_ids
    )
    assert snap.unknown_micro == held


# ----------------------------------------------------------------------
# no transaction during the provider call; write-ahead mark
# ----------------------------------------------------------------------


def test_no_database_transaction_is_open_during_provider_call() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    observed: dict[str, object] = {}

    def probe(_request) -> None:
        with engine.connect() as connection:
            observed["idle_in_tx"] = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state LIKE 'idle in transaction%'"
                )
            ).scalar_one()
            observed["state"] = connection.execute(
                text(
                    "SELECT state FROM intelligence_model_calls "
                    "WHERE call_key = 'contract-1#a1'"
                )
            ).scalar_one()

    gateway, _ = make_gateway(
        ledger, [ok({"ok": True})], on_invoke=probe
    )

    result = gateway.call(make_request(budget_id))

    assert result.ok
    assert observed["idle_in_tx"] == 0
    # The write-ahead mark was COMMITTED (visible to another
    # connection) before the adapter was invoked.
    assert observed["state"] == "in_flight"


# ----------------------------------------------------------------------
# profiles, breaker, concurrency limiter
# ----------------------------------------------------------------------


def test_registry_rejects_duplicates_unknown_fallbacks_and_providers() -> None:
    profile = ModelProfile(
        name="a",
        provider="fake",
        model_id="m",
        price_key="fake-small",
        max_output_tokens_cap=10,
    )

    with pytest.raises(GatewayError):
        ProfileRegistry([profile, profile])

    with pytest.raises(GatewayError) as caught:
        ProfileRegistry(
            [
                ModelProfile(
                    name="b",
                    provider="fake",
                    model_id="m",
                    price_key="fake-small",
                    max_output_tokens_cap=10,
                    fallbacks=("ghost",),
                )
            ]
        )
    assert caught.value.code is GatewayErrorCode.UNKNOWN_PROFILE

    for provider in ("openai", "anthropic", "ollama"):
        with pytest.raises(GatewayError) as caught:
            ProfileRegistry(
                [
                    ModelProfile(
                        name="c",
                        provider=provider,
                        model_id="m",
                        price_key="fake-small",
                        max_output_tokens_cap=10,
                    )
                ]
            )
        assert caught.value.code is GatewayErrorCode.UNKNOWN_PROVIDER

    with pytest.raises(GatewayError):
        ProfileRegistry(
            [
                ModelProfile(
                    name="d",
                    provider="fake",
                    model_id="m",
                    price_key="unpriced",
                    max_output_tokens_cap=10,
                )
            ]
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_circuit_breaker_opens_half_opens_and_closes() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30, clock=clock
    )

    assert breaker.allow("p") and breaker.state("p") == "closed"
    breaker.record_failure("p")
    assert breaker.state("p") == "closed"
    breaker.record_failure("p")
    assert breaker.state("p") == "open" and not breaker.allow("p")

    clock.now += 31
    assert breaker.state("p") == "half_open"
    assert breaker.allow("p")  # single probe
    assert not breaker.allow("p")  # second concurrent probe refused
    breaker.record_failure("p")  # failed probe re-opens
    assert breaker.state("p") == "open"

    clock.now += 31
    assert breaker.allow("p")
    breaker.record_success("p")
    assert breaker.state("p") == "closed" and breaker.allow("p")

    breaker.record_failure("p")
    breaker.cancel_probe("p")
    assert breaker.state("p") == "closed"


def test_breaker_opens_in_the_gateway_after_repeated_timeouts() -> None:
    ledger = make_ledger(unknown_fraction_bps=10_000)
    budget_id = make_budget(ledger)
    breaker = CircuitBreaker(
        failure_threshold=1, cooldown_seconds=30, clock=_Clock()
    )
    gateway, adapter = make_gateway(
        ledger,
        [timeout(), timeout(), ok({"ok": True})],
        max_attempts=2,
        breaker=breaker,
    )

    first = gateway.call(make_request(budget_id, call_key="b-0"))
    assert first.error_code == "provider_failure"
    assert first.profiles_used == ("lab-fast", "lab-strong")

    refused = gateway.call(make_request(budget_id, call_key="b-1"))

    assert refused.error_code == "circuit_open"
    assert refused.call_ids == ()
    assert len(adapter.calls) == 2  # never reached the adapter again


def test_open_profile_is_skipped_for_its_fallback_without_an_attempt() -> None:
    ledger = make_ledger(unknown_fraction_bps=10_000)
    budget_id = make_budget(ledger)
    breaker = CircuitBreaker(
        failure_threshold=1, cooldown_seconds=30, clock=_Clock()
    )
    breaker.record_failure("lab-fast")
    gateway, adapter = make_gateway(
        ledger, [ok({"ok": True})], max_attempts=1, breaker=breaker
    )

    result = gateway.call(make_request(budget_id))

    assert result.ok and result.profiles_used == ("lab-strong",)
    assert result.attempts == 1 and len(adapter.calls) == 1


def test_refusal_and_schema_errors_do_not_open_the_breaker() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    gateway, _ = make_gateway(
        ledger,
        [refusal(), raw("nope"), ok({"ok": True})],
        breaker=breaker,
    )

    gateway.call(make_request(budget_id, call_key="r-0"))
    gateway.call(make_request(budget_id, call_key="r-1"))
    final = gateway.call(make_request(budget_id, call_key="r-2"))

    assert final.ok
    assert breaker.state("lab-fast") == "closed"


def test_concurrency_limiter_rejects_instead_of_queueing() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    limiter = ConcurrencyLimiter(1)
    holder: dict[str, object] = {}

    def nested(_request) -> None:
        inner, _ = make_gateway(
            ledger, [ok({"ok": True})], limiter=limiter
        )
        holder["inner"] = inner.call(
            make_request(budget_id, call_key="inner")
        )

    outer, _ = make_gateway(
        ledger, [ok({"ok": True})], limiter=limiter, on_invoke=nested
    )

    result = outer.call(make_request(budget_id, call_key="outer"))

    assert result.ok
    assert holder["inner"].error_code == "concurrency_limit"
    assert holder["inner"].call_ids == ()  # nothing was reserved
    with pytest.raises(ValueError):
        ConcurrencyLimiter(0)
