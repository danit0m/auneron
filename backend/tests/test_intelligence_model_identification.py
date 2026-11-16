"""INTELLIGENCE S2 -- exact local-model identification in the ledger.

PostgreSQL required (TEST_DATABASE_URL). No network: the Ollama runtime
is a scripted in-process transport; no real model is called.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy.exc import DataError
from sqlalchemy.exc import IntegrityError

from app.database.database import SessionLocal
from app.intelligence.gateway.adapters.fake import FakeProvider
from app.intelligence.gateway.adapters.fake import ok
from app.intelligence.gateway.adapters.ollama import OllamaProvider
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.budget import utcnow
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.pricing import Reservation
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import build_lab_registry
from app.intelligence.gateway.profiles import build_local_ollama_profile
from app.models.intelligence_budget import IntelligenceModelCall


pytestmark = pytest.mark.contract

MODEL = "qwen3:4b"
DIGEST = "ab" * 32
VERSION = "0.9.9"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def make_ledger() -> BudgetLedger:
    return BudgetLedger(
        SessionLocal,
        unknown_fraction_bps=2500,
        recovery_grace_seconds=30,
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


class ScriptedRuntime:
    """Loopback-free transport: plays /api/version, /api/tags, /api/chat."""

    def __init__(self, *, digest=DIGEST, chat=None):
        self.digest = digest
        self.chat = chat
        self.seen: list[tuple[str, str]] = []

    def __call__(self, method, path, body, timeout_s):
        self.seen.append((method, path))

        if path == "/api/version":
            return 200, json.dumps({"version": VERSION}).encode()

        if path == "/api/tags":
            return 200, json.dumps(
                {
                    "models": [
                        {"name": MODEL, "model": MODEL, "digest": self.digest}
                    ]
                }
            ).encode()

        result = self.chat

        if isinstance(result, BaseException):
            raise result

        status, payload = result
        data = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode()
        )
        return status, data

    def chat_calls(self):
        return [s for s in self.seen if s[1] == "/api/chat"]


def chat_ok(content='{"ok": true}', **extra):
    document = {
        "model": MODEL,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 5,
    }
    document.update(extra)
    return 200, document


def make_gateway(ledger, runtime):
    adapter = OllamaProvider(
        approved_digests={MODEL: DIGEST}, transport=runtime
    )
    return Gateway(
        registry=ProfileRegistry([build_local_ollama_profile()]),
        adapters={"ollama": adapter},
        ledger=ledger,
        limits=GatewayLimits(
            max_attempts=1,
            call_timeout_seconds=5,
            reservation_margin_bps=2000,
        ),
        mode_provider=lambda: "shadow",
    )


def request_for(budget_id: int, profile="local-qwen3-4b"):
    return ModelCallRequest(
        call_key="s2-1",
        purpose="lab.s2",
        profile=profile,
        instructions="Return a JSON object.",
        input_text="synthetic input",
        output_schema=SCHEMA,
        max_output_tokens=64,
        deployment_budget_id=budget_id,
    )


def run(runtime):
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    result = make_gateway(ledger, runtime).call(request_for(budget_id))
    call = ledger.get_call(result.call_ids[0])
    return result, call, ledger, budget_id


def assert_identification_empty(call) -> None:
    assert call.model_digest is None
    assert call.provider_reported_model is None
    assert call.runtime_version is None
    assert call.latency_ms is None


# ----------------------------------------------------------------------
# full Gateway.call with OllamaProvider and the real ledger
# ----------------------------------------------------------------------


def test_success_records_exact_model_identification() -> None:
    runtime = ScriptedRuntime(chat=chat_ok())
    result, call, ledger, budget_id = run(runtime)

    assert result.ok and result.parsed == {"ok": True}
    assert call.provider == "ollama"
    assert call.state == "settled" and call.error_class is None
    assert call.model_digest == DIGEST
    assert call.provider_reported_model == MODEL
    assert call.runtime_version == VERSION
    assert isinstance(call.latency_ms, int) and call.latency_ms >= 0
    assert call.response_digest is not None
    assert call.data_classification == "synthetic"
    assert ledger.verify_conservation(budget_id).consistent


def test_prefixed_digest_is_stored_without_the_prefix() -> None:
    runtime = ScriptedRuntime(digest="sha256:" + DIGEST.upper(), chat=chat_ok())
    _, call, _, _ = run(runtime)

    assert call.model_digest == DIGEST


def test_model_not_approved_is_never_sent_with_no_identification() -> None:
    runtime = ScriptedRuntime(digest="cd" * 32, chat=chat_ok())
    result, call, ledger, budget_id = run(runtime)

    assert not result.ok and result.parsed is None
    assert call.state == "released"
    assert call.release_basis == "never_sent"
    assert call.evidence_ref == "adapter_attested:digest_mismatch"
    assert runtime.chat_calls() == []  # the prompt never left
    assert_identification_empty(call)
    assert ledger.verify_conservation(budget_id).consistent


def test_timeout_is_unknown_with_no_identification() -> None:
    runtime = ScriptedRuntime(chat=TimeoutError("deadline"))
    result, call, _, _ = run(runtime)

    assert not result.ok and result.parsed is None
    assert call.state == "unknown"
    assert call.error_class == "ProviderTimeoutError"
    assert_identification_empty(call)


def test_invalid_envelope_is_unknown_and_the_output_is_not_used() -> None:
    runtime = ScriptedRuntime(chat=(200, b"not json"))
    result, call, _, _ = run(runtime)

    assert not result.ok and result.parsed is None
    assert call.state == "unknown"
    assert call.error_class == "ProviderInvalidResponseError"
    assert call.response_digest is None
    assert_identification_empty(call)


def test_diverging_reported_model_is_unknown_and_the_output_is_dropped() -> None:
    runtime = ScriptedRuntime(chat=chat_ok(model="other:7b"))
    result, call, _, _ = run(runtime)

    assert not result.ok and result.parsed is None
    assert call.state == "unknown"
    assert call.error_class == "ProviderModelMismatchError"
    assert call.response_digest is None
    assert_identification_empty(call)


def test_invalid_schema_content_is_settled_and_identified_but_not_used() -> None:
    runtime = ScriptedRuntime(chat=chat_ok(content='{"ok": "yes"}'))
    result, call, _, _ = run(runtime)

    assert not result.ok and result.parsed is None
    assert result.error_code == "invalid_schema_response"
    # Charged and identified; the schema failure lives only in the
    # ModelCallResult, not in the ledger (no new column by design).
    assert call.state == "settled" and call.error_class is None
    assert call.model_digest == DIGEST
    assert call.runtime_version == VERSION


def test_fake_provider_leaves_identification_null() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    gateway = Gateway(
        registry=build_lab_registry(),
        adapters={"fake": FakeProvider([ok({"ok": True})])},
        ledger=ledger,
        limits=GatewayLimits(1, 5, 2000),
        mode_provider=lambda: "shadow",
    )
    result = gateway.call(request_for(budget_id, profile="lab-fast"))
    call = ledger.get_call(result.call_ids[0])

    assert result.ok
    assert call.provider == "fake" and call.state == "settled"
    assert_identification_empty(call)


def test_late_response_reconciled_by_the_gateway_keeps_identification() -> None:
    ledger = make_ledger()
    budget_id = make_budget(ledger)
    call_id = ledger.reserve(
        call_key="late-1",
        purpose="lab",
        provider="ollama",
        model_profile="local-qwen3-4b",
        model_id=MODEL,
        reservation=Reservation(10, 10),
        deadline_seconds=60,
        deployment_budget_id=budget_id,
    )
    ledger.mark_in_flight(call_id, timeout_seconds=60)
    ledger.mark_unknown(call_id, error_class="crash_after_send")
    ledger.reconcile_unknown_settled(
        call_id,
        actual_micro=1,
        actual_tokens=1,
        evidence_ref="late_response:n/a",
        reconciled_by_reference="system:gateway",
        provider_reported_model=MODEL,
        model_digest=DIGEST,
        runtime_version=VERSION,
        latency_ms=7,
    )
    call = ledger.get_call(call_id)

    assert call.state == "settled" and call.reconciled_at is not None
    assert call.model_digest == DIGEST and call.latency_ms == 7


# ----------------------------------------------------------------------
# database CHECKs (defence if application code is wrong)
# ----------------------------------------------------------------------


def _row(budget_id: int, **overrides) -> dict:
    now = utcnow()
    values = {
        "call_key": "direct-1",
        "purpose": "lab",
        "provider": "ollama",
        "model_profile": "local-qwen3-4b",
        "model_id": MODEL,
        "deployment_budget_id": budget_id,
        "reserved_micro": 10,
        "reserved_tokens": 10,
        "deadline_at": now + timedelta(minutes=1),
    }
    values.update(overrides)
    return values


def _settled(**overrides) -> dict:
    now = utcnow()
    values = {
        "state": "settled",
        "sent_at": now,
        "settled_at": now,
        "actual_micro": 1,
        "actual_tokens": 1,
    }
    values.update(overrides)
    return values


def _inserts(budget_id: int, **overrides) -> bool:
    session = SessionLocal()

    try:
        session.add(IntelligenceModelCall(**_row(budget_id, **overrides)))
        session.flush()
        return True
    except IntegrityError:
        return False
    finally:
        session.rollback()
        session.close()


def test_checks_reject_malformed_identification() -> None:
    budget_id = make_budget(make_ledger())
    bad = {
        # every value fits VARCHAR(64) so the CHECK (not the column
        # length) is what rejects it
        "digest with prefix, 64 chars": dict(
            model_digest="sha256:" + "ab" * 28 + "a"
        ),
        "short digest (63)": dict(model_digest="a" * 63),
        "upper-case digest": dict(model_digest=DIGEST.upper()),
        "non-hex digest (64)": dict(model_digest="g" * 64),
        "negative latency": dict(latency_ms=-1),
        "blank runtime version": dict(runtime_version="  "),
        "blank reported model": dict(provider_reported_model=" "),
        "unknown provider": dict(provider="openai"),
    }

    for name, overrides in bad.items():
        assert not _inserts(budget_id, **overrides), name

    assert _inserts(
        budget_id,
        model_digest=DIGEST,
        runtime_version=VERSION,
        provider_reported_model=MODEL,
        latency_ms=0,
    )


def test_oversized_digest_with_prefix_is_rejected_by_the_column() -> None:
    budget_id = make_budget(make_ledger())
    session = SessionLocal()

    try:
        session.add(
            IntelligenceModelCall(
                **_row(budget_id, model_digest="sha256:" + DIGEST)
            )
        )

        with pytest.raises(DataError):
            session.flush()
    finally:
        session.rollback()
        session.close()


def test_ollama_settled_rows_must_carry_digest_and_version() -> None:
    budget_id = make_budget(make_ledger())

    assert not _inserts(budget_id, **_settled())
    assert not _inserts(budget_id, **_settled(model_digest=DIGEST))
    assert not _inserts(budget_id, **_settled(runtime_version=VERSION))
    assert _inserts(
        budget_id,
        **_settled(model_digest=DIGEST, runtime_version=VERSION),
    )


def test_the_coherence_check_does_not_bind_other_states_or_the_fake() -> None:
    budget_id = make_budget(make_ledger())
    now = utcnow()

    # ollama in_flight / unknown: identification is legitimately NULL
    assert _inserts(budget_id, state="in_flight", sent_at=now)
    assert _inserts(
        budget_id, state="unknown", sent_at=now, unknown_at=now
    )
    # fake settled: identification stays NULL
    assert _inserts(budget_id, provider="fake", **_settled())
    # admin-reconciled ollama settlement is evidence-based, not
    # adapter-identified
    assert _inserts(
        budget_id,
        **_settled(
            reconciled_at=now,
            reconciled_by_reference="admin",
            evidence_ref="ticket-1",
        ),
    )
