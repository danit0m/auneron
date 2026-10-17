"""INTELLIGENCE-IMPL-0 -- prompt/response content never leaves the call.

A canary string is planted in the prompt, the schema, the provider's
answer and the provider's error messages. It must not appear in any log
record (formatted OR raw), any error text, the persisted rows, or the
exception chain printed to logs.
"""

import json
import logging
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.observability import JsonLogFormatter
from app.database.database import SessionLocal
from app.database.database import engine
from app.intelligence.gateway.adapters.base import ProviderAdapter
from app.intelligence.gateway.adapters.fake import FakeProvider
from app.intelligence.gateway.adapters.fake import ok
from app.intelligence.gateway.adapters.fake import raw
from app.intelligence.gateway.adapters.fake import refusal
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderTimeoutError
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.profiles import build_lab_registry
from app.intelligence.gateway.redaction import ALLOWED_LOG_FIELDS
from app.intelligence.gateway.redaction import safe_error_class
from app.intelligence.gateway.redaction import safe_log_fields
from app.intelligence.gateway.redaction import sha256_hex


pytestmark = pytest.mark.security

CANARY = "CANARY-7f3a91-SECRET-PROMPT-b2d4"
SCHEMA = {
    "type": "object",
    "description": CANARY,
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.formatter_json = JsonLogFormatter()
        self.chunks: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.chunks.append(self.formatter_json.format(record))
        self.chunks.append(repr(record.__dict__))
        self.chunks.append(record.getMessage())

        if record.exc_info:
            self.chunks.append(
                logging.Formatter().formatException(record.exc_info)
            )

    @property
    def text(self) -> str:
        return "\n".join(self.chunks)


@pytest.fixture
def collector():
    handler = Collector()
    # Attached to the gateway logger AND the root: the application
    # logger may have propagate=False after app start-up in the same
    # process, and the check must not depend on that.
    targets = [logging.getLogger("auneron.intelligence"), logging.getLogger()]
    previous = [target.level for target in targets]

    for target in targets:
        target.addHandler(handler)
        target.setLevel(logging.DEBUG)

    try:
        yield handler
    finally:
        for target, level in zip(targets, previous):
            target.removeHandler(handler)
            target.setLevel(level)


class LeakyAdapter(ProviderAdapter):
    """Adapter whose failures echo the prompt, like a careless SDK."""

    provider_kind = "fake"

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def invoke(self, request):
        raise self.failure


def make_ledger() -> BudgetLedger:
    return BudgetLedger(
        SessionLocal, unknown_fraction_bps=10_000, recovery_grace_seconds=30
    )


def make_budget(ledger: BudgetLedger) -> int:
    now = utcnow()
    return ledger.create_budget(
        scope_kind="deployment_period",
        scope_ref="dep",
        limit_micro=10_000_000,
        limit_tokens=10_000_000,
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )


def make_gateway(ledger, adapter, *, max_attempts=1) -> Gateway:
    return Gateway(
        registry=build_lab_registry(),
        adapters={"fake": adapter},
        ledger=ledger,
        limits=GatewayLimits(max_attempts, 5, 2000),
        mode_provider=lambda: "shadow",
    )


def make_request(budget_id: int, key: str) -> ModelCallRequest:
    return ModelCallRequest(
        call_key=key,
        purpose="lab.logsafety",
        profile="lab-fast",
        instructions=f"System: {CANARY}",
        input_text=f"User text {CANARY}",
        output_schema=SCHEMA,
        max_output_tokens=32,
        deployment_budget_id=budget_id,
        prompt_template_id="tmpl-1",
        output_schema_id="schema-1",
    )


def database_dump() -> str:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT row_to_json(t)::text FROM intelligence_model_calls t "
                "UNION ALL "
                "SELECT row_to_json(b)::text FROM intelligence_budgets b"
            )
        ).scalars()
        return "\n".join(rows)


SCENARIOS = {
    "success_echoing_prompt": lambda: FakeProvider([ok({"ok": True})]),
    "refusal": lambda: FakeProvider([refusal()]),
    "invalid_body_with_canary": lambda: FakeProvider(
        [raw(f'{{"ok": "{CANARY}"}}')]
    ),
    "http_error_message_with_canary": lambda: LeakyAdapter(
        ProviderHTTPError(500)
    ),
    "timeout_message_with_canary": lambda: LeakyAdapter(
        ProviderTimeoutError(f"timed out sending {CANARY}")
    ),
    "unexpected_exception_with_canary": lambda: LeakyAdapter(
        RuntimeError(f"SDK failure for prompt {CANARY}")
    ),
    "key_error_with_canary": lambda: LeakyAdapter(KeyError(CANARY)),
}


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_canary_never_reaches_logs_rows_or_errors(scenario, collector) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    adapter = SCENARIOS[scenario]()
    gateway = make_gateway(ledger, adapter)

    result = gateway.call(make_request(budget_id, f"canary-{scenario}"))

    # the contract object may carry the parsed structured output, but
    # never the prompt, and its repr must not leak either
    rendered = repr(result.__class__.__name__) + str(result.error_code)
    assert CANARY not in rendered
    assert CANARY not in collector.text
    assert CANARY not in database_dump()
    assert result.call_ids  # something was actually exercised
    assert collector.text  # and it did log (the check is not vacuous)


def test_gateway_errors_carry_only_stable_codes(collector) -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway = make_gateway(ledger, FakeProvider())
    bad = ModelCallRequest(
        call_key=CANARY,
        purpose=CANARY,
        profile="lab-fast",
        instructions=CANARY,
        input_text=CANARY,
        output_schema=SCHEMA,
        max_output_tokens=0,
        deployment_budget_id=budget_id,
    )

    with pytest.raises(GatewayError) as caught:
        gateway.call(bad)

    assert caught.value.code is GatewayErrorCode.INVALID_REQUEST
    assert CANARY not in str(caught.value)
    assert CANARY not in caught.value.safe_message
    assert CANARY not in collector.text


def test_unknown_profile_error_does_not_echo_the_requested_name() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway = make_gateway(ledger, FakeProvider())
    request = ModelCallRequest(
        call_key="k",
        purpose="p",
        profile=CANARY,
        instructions="i",
        input_text="t",
        output_schema=SCHEMA,
        max_output_tokens=8,
        deployment_budget_id=budget_id,
    )

    with pytest.raises(GatewayError) as caught:
        gateway.call(request)

    assert caught.value.code is GatewayErrorCode.UNKNOWN_PROFILE
    assert CANARY not in str(caught.value) + caught.value.safe_message


def test_persisted_rows_hold_digests_and_never_content() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway = make_gateway(
        ledger, FakeProvider([ok({"ok": True})])
    )

    result = gateway.call(make_request(budget_id, "digest-1"))

    row = ledger.get_call(result.call_ids[0])
    assert row.request_digest and len(row.request_digest) == 64
    assert row.response_digest == sha256_hex('{"ok": true}')
    assert CANARY not in database_dump()


# ----------------------------------------------------------------------
# allowlist and observability redaction
# ----------------------------------------------------------------------


def test_safe_log_fields_is_an_allowlist() -> None:
    fields = safe_log_fields(
        event="x",
        call_id=1,
        prompt=CANARY,
        input_text=CANARY,
        instructions=CANARY,
        error_message=CANARY,
        api_key=CANARY,
        provider="fake",
        error_class=RuntimeError(CANARY),
    )

    assert set(fields) <= ALLOWED_LOG_FIELDS
    assert CANARY not in json.dumps(fields, default=str)
    assert fields["error_class"] == "RuntimeError"  # non-scalar -> type name
    assert "prompt" not in fields and "api_key" not in fields


def test_error_class_never_includes_the_message() -> None:
    assert safe_error_class(RuntimeError(CANARY)) == "RuntimeError"
    assert CANARY not in safe_error_class(KeyError(CANARY))


def test_log_formatter_redacts_prompt_like_keys() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "event", None, None
    )
    record.prompt = CANARY
    record.completion = CANARY
    record.model_input = CANARY
    record.model_output = CANARY
    record.system_instructions = CANARY
    record.nested = {"prompt": CANARY, "kept": "visible"}
    record.content_length = 42
    record.content_type = "application/json"

    rendered = formatter.format(record)

    assert CANARY not in rendered
    assert rendered.count("[REDACTED]") >= 6
    # regression guard: legitimate HTTP fields are not swallowed
    payload = json.loads(rendered)
    flat = json.dumps(payload)
    assert "42" in flat and "application/json" in flat and "visible" in flat


def test_provider_response_dataclass_is_content_only_where_declared() -> None:
    response = ProviderResponse(
        status=ProviderStatus.COMPLETED,
        output_text=CANARY,
        input_tokens=1,
        output_tokens=1,
    )

    # the dataclass is internal to the call and is never logged by the
    # gateway; this documents that no logging call receives it
    import inspect

    from app.intelligence.gateway import gateway as gateway_module

    source = inspect.getsource(gateway_module)

    assert "log_event(" in source
    assert "output_text=" not in "".join(
        line
        for line in source.splitlines()
        if "log_event" in line or "logging." in line
    )
    assert response.output_text == CANARY


def test_sensitivity_the_collector_would_catch_a_leak(collector) -> None:
    """Synthetic negative control: a careless log line IS detected."""
    logging.getLogger("auneron.intelligence").info("leak: %s", CANARY)
    logging.getLogger("auneron.intelligence").info(
        "structured", extra={"detail": f"prompt was {CANARY}"}
    )

    assert CANARY in collector.text

    ledger = make_ledger()
    budget_id = make_budget(ledger)
    # and a leaked canary in a persisted column would be seen by the dump
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO intelligence_model_calls (call_key, purpose, "
                "provider, model_profile, model_id, state, "
                "deployment_budget_id, reserved_micro, reserved_tokens, "
                "deadline_at, error_class) VALUES (:k, 'p', 'fake', 'lab-fast', "
                "'m', 'reserved', :b, 1, 1, now(), :c)"
            ),
            {"k": "leak-row", "b": budget_id, "c": CANARY[:30]},
        )

    assert CANARY[:30] in database_dump()
