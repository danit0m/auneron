"""Qwen3-4B with reasoning ON (think=true) -- offline, no database, no model.

The Ollama runtime is played by a scripted transport (a callable, no
socket). The ledger is a minimal in-memory stub. Covers: the request body
(think, sampling, num_predict), where the answer is taken from (content
only), classification of empty / truncated / non-JSON answers, the rule
that the reasoning TEXT is never stored, logged or used, the profile, the
shadow and evaluator settings, the timeout ceiling and the new budget.
"""

import json
import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.intelligence.gateway.adapters.ollama import OllamaProvider
from app.intelligence.gateway.contracts import DETERMINISTIC_SAMPLING
from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.profiles import LOCAL_OLLAMA_PROVIDER_KEY
from app.intelligence.gateway.profiles import LOCAL_QWEN3_4B_THINK_OUTPUT_TOKENS
from app.intelligence.gateway.profiles import QWEN3_THINKING_SAMPLING
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import build_local_ollama_profile
from app.intelligence.gateway.redaction import request_digest
from app.services import overdue_detection_shadow as shadow


MODEL = "qwen3:4b"
DIGEST = "a" * 64
SCHEMA = shadow.OUTPUT_SCHEMA
TEST_URL = (
    "postgresql+psycopg://auneron:test_password@localhost:5999/auneron_test"
)
# Reasoning text that looks like a valid answer and carries a marker that
# must not appear anywhere.
SECRET_THINKING = (
    'ZZ-THINKING-MARKER {"decision": "mark_overdue", "reason": "from '
    'the reasoning"} ZZ-THINKING-MARKER'
)
GOOD_CONTENT = json.dumps({"decision": "no_action", "reason": "due today"})


class ScriptedTransport:
    def __init__(self, chat_document):
        self.chat_document = chat_document
        self.bodies: list[dict] = []

    def __call__(self, method, path, body, timeout_s):
        if path == "/api/version":
            return 200, json.dumps({"version": "0.40.16"}).encode()

        if path == "/api/tags":
            return 200, json.dumps(
                {"models": [{"name": MODEL, "digest": DIGEST}]}
            ).encode()

        self.bodies.append(json.loads(body.decode("utf-8")))
        return 200, json.dumps(self.chat_document).encode()


def chat_document(content=GOOD_CONTENT, thinking=SECRET_THINKING, **extra):
    message = {"role": "assistant", "content": content}

    if thinking is not None:
        message["thinking"] = thinking

    document = {
        "model": MODEL,
        "message": message,
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 700,
        "eval_count": 900,
    }
    document.update(extra)
    return document


def thinking_request(**overrides):
    profile = build_local_ollama_profile()
    values = dict(
        model_id=MODEL,
        instructions="system",
        input_text="{}",
        output_schema=SCHEMA,
        max_output_tokens=shadow.MAX_OUTPUT_TOKENS,
        timeout_seconds=300,
        think=profile.think,
        sampling=profile.sampling,
    )
    values.update(overrides)
    return ProviderRequest(**values)


def invoke(document, **request_overrides):
    transport = ScriptedTransport(document)
    provider = OllamaProvider(
        approved_digests={MODEL: DIGEST}, transport=transport
    )
    return provider.invoke(thinking_request(**request_overrides)), transport


# ----------------------------------------------------------------------
# profile
# ----------------------------------------------------------------------


def test_profile_has_reasoning_on_with_the_recommended_sampling() -> None:
    profile = build_local_ollama_profile()

    assert profile.name == "local-qwen3-4b"
    assert profile.think is True
    assert profile.sampling == QWEN3_THINKING_SAMPLING
    assert (
        profile.sampling.temperature,
        profile.sampling.top_p,
        profile.sampling.top_k,
        profile.sampling.seed,
    ) == (0.6, 0.95, 20, 0)
    assert profile.max_output_tokens_cap == 4096
    assert profile.max_output_tokens_cap == LOCAL_QWEN3_4B_THINK_OUTPUT_TOKENS
    assert profile.price_key == "local-zero"


def test_shadow_budget_of_tokens_fits_the_profile_cap() -> None:
    assert shadow.MAX_OUTPUT_TOKENS == 4096
    assert (
        shadow.MAX_OUTPUT_TOKENS
        <= build_local_ollama_profile().max_output_tokens_cap
    )


def test_default_provider_request_is_unchanged_non_reasoning() -> None:
    request = ProviderRequest(
        model_id=MODEL,
        instructions="s",
        input_text="u",
        output_schema=SCHEMA,
        max_output_tokens=8,
        timeout_seconds=5,
    )
    assert request.think is False
    assert request.sampling == DETERMINISTIC_SAMPLING
    assert ProviderResponse(
        status=ProviderStatus.COMPLETED,
        output_text="{}",
        input_tokens=1,
        output_tokens=1,
    ).thinking_length is None


# ----------------------------------------------------------------------
# request body
# ----------------------------------------------------------------------


def test_chat_body_carries_think_sampling_and_num_predict() -> None:
    _, transport = invoke(chat_document())
    sent = transport.bodies[0]

    assert sent["think"] is True
    assert sent["stream"] is False
    assert sent["format"] == SCHEMA
    assert sent["options"] == {
        "temperature": 0.6,
        "top_k": 20,
        "top_p": 0.95,
        "seed": 0,
        "num_ctx": 8192,
        "num_predict": 4096,
    }


def test_legacy_request_still_sends_think_false_and_greedy() -> None:
    _, transport = invoke(
        chat_document(thinking=None),
        think=False,
        sampling=DETERMINISTIC_SAMPLING,
        max_output_tokens=64,
    )
    sent = transport.bodies[0]
    assert sent["think"] is False
    assert sent["options"]["temperature"] == 0
    assert sent["options"]["top_k"] == 1
    assert sent["options"]["num_predict"] == 64


# ----------------------------------------------------------------------
# response handling: content only, thinking never used
# ----------------------------------------------------------------------


def test_answer_is_content_and_only_the_thinking_length_is_kept() -> None:
    response, _ = invoke(chat_document())

    assert response.status is ProviderStatus.COMPLETED
    assert response.output_text == GOOD_CONTENT
    assert response.thinking_length == len(SECRET_THINKING)
    assert response.output_tokens == 900  # includes reasoning tokens
    assert "ZZ-THINKING-MARKER" not in repr(response)


def test_thinking_that_looks_like_json_is_not_the_answer() -> None:
    response, _ = invoke(chat_document(content=""))

    assert response.status is ProviderStatus.COMPLETED
    assert response.output_text == ""
    assert "mark_overdue" not in (response.output_text or "")
    assert response.thinking_length == len(SECRET_THINKING)


def test_content_with_prose_is_returned_as_is_for_the_gateway_to_refuse() -> None:
    response, _ = invoke(chat_document(content="Let me think... no_action"))
    assert response.output_text == "Let me think... no_action"


def test_truncated_by_length_is_truncated_even_with_json_in_thinking() -> None:
    response, _ = invoke(
        chat_document(content="", done_reason="length", eval_count=4096)
    )

    assert response.status is ProviderStatus.TRUNCATED
    assert response.output_tokens == 4096
    assert response.thinking_length == len(SECRET_THINKING)


def test_no_thinking_reported_means_none() -> None:
    response, _ = invoke(chat_document(thinking=None))
    assert response.thinking_length is None


# ----------------------------------------------------------------------
# gateway + shadow + stub ledger
# ----------------------------------------------------------------------


class StubLedger:
    def __init__(self):
        self.reserved: list[dict] = []
        self.in_flight: list[dict] = []
        self.settled: list[dict] = []

    def reserve(self, **kwargs):
        self.reserved.append(kwargs)
        return len(self.reserved)

    def mark_in_flight(self, call_id, **kwargs):
        self.in_flight.append({"call_id": call_id, **kwargs})

    def settle(self, call_id, **kwargs):
        self.settled.append({"call_id": call_id, **kwargs})

    def get_call(self, call_id):
        return SimpleNamespace(state="settled")


def make_gateway(document, ledger=None, timeout=300):
    transport = ScriptedTransport(document)
    ledger = ledger or StubLedger()
    gateway = Gateway(
        registry=ProfileRegistry([build_local_ollama_profile()]),
        adapters={
            LOCAL_OLLAMA_PROVIDER_KEY: OllamaProvider(
                approved_digests={MODEL: DIGEST}, transport=transport
            )
        },
        ledger=ledger,
        limits=GatewayLimits(
            max_attempts=1,
            call_timeout_seconds=timeout,
            reservation_margin_bps=2000,
        ),
        mode_provider=lambda: "shadow",
    )
    return gateway, transport, ledger


def shadow_request():
    return ModelCallRequest(
        call_key="think-test:1",
        purpose=shadow.SHADOW_PURPOSE,
        profile="local-qwen3-4b",
        instructions=shadow.INSTRUCTIONS,
        input_text=shadow.build_model_input(
            SimpleNamespace(
                due_date="2031-05-10",
                status="aberto",
                business_date="2031-05-10",
            )
        ),
        output_schema=shadow.OUTPUT_SCHEMA,
        max_output_tokens=shadow.MAX_OUTPUT_TOKENS,
        data_classification=DataClassification.SYNTHETIC,
        deployment_budget_id=1,
        prompt_template_id=shadow.PROMPT_TEMPLATE_ID,
        output_schema_id=shadow.OUTPUT_SCHEMA_ID,
    )


def test_gateway_sends_think_true_from_the_profile_and_accepts_content() -> None:
    gateway, transport, ledger = make_gateway(chat_document())
    result = gateway.call(shadow_request())

    assert result.ok and result.parsed["decision"] == "no_action"
    sent = transport.bodies[0]
    assert sent["think"] is True
    assert sent["options"]["num_predict"] == 4096
    assert sent["options"]["temperature"] == 0.6
    assert ledger.reserved[0]["prompt_template_id"] == (
        "overdue-shadow-prompt-v2-think"
    )
    # one worst-case reservation uses the 4096 output budget
    assert ledger.reserved[0]["reservation"].tokens > 4096


@pytest.mark.parametrize(
    "document, expected",
    [
        (chat_document(content=""), GatewayErrorCode.INVALID_SCHEMA_RESPONSE),
        (
            chat_document(content="not json at all"),
            GatewayErrorCode.INVALID_SCHEMA_RESPONSE,
        ),
        (
            chat_document(content="", done_reason="length"),
            GatewayErrorCode.TRUNCATED,
        ),
        (
            chat_document(content='{"decision": "mark_', done_reason="length"),
            GatewayErrorCode.TRUNCATED,
        ),
    ],
)
def test_gateway_classifies_unusable_answers_and_never_uses_thinking(
    document, expected
) -> None:
    gateway, _, _ = make_gateway(document)
    result = gateway.call(shadow_request())

    assert result.ok is False
    assert result.parsed is None
    assert result.error_code == expected.value


def test_reasoning_text_is_not_in_ledger_logs_result_or_digest(
    caplog,
) -> None:
    ledger = StubLedger()
    gateway, _, _ = make_gateway(chat_document(), ledger=ledger)

    with caplog.at_level(logging.DEBUG):
        result = gateway.call(shadow_request())

    everything = " ".join(
        [
            repr(ledger.reserved),
            repr(ledger.in_flight),
            repr(ledger.settled),
            repr(result),
            " ".join(r.getMessage() for r in caplog.records),
            " ".join(repr(r.__dict__) for r in caplog.records),
        ]
    )
    assert "ZZ-THINKING-MARKER" not in everything
    assert "from the reasoning" not in everything
    # the length is the only trace of the reasoning, and only in the log
    lengths = [
        getattr(r, "thinking_length", None) for r in caplog.records
    ]
    assert len(SECRET_THINKING) in lengths


def test_request_digest_tells_reasoning_from_non_reasoning() -> None:
    common = dict(
        model_id=MODEL,
        instructions="i",
        input_text="t",
        output_schema=SCHEMA,
        max_output_tokens=256,
    )
    legacy = request_digest(**common)
    assert request_digest(**common, think=False) == legacy
    assert request_digest(**common, think=True) != legacy
    assert request_digest(
        **common, think=True, sampling=QWEN3_THINKING_SAMPLING
    ) != request_digest(**common, think=True)


# ----------------------------------------------------------------------
# shadow constants, timeout ceiling, budget
# ----------------------------------------------------------------------


def test_prompt_text_is_still_v2_and_template_id_marks_reasoning() -> None:
    assert shadow.PROMPT_VERSION == "overdue_shadow_v2"
    assert shadow.REASONING_MODE == "think"
    assert shadow.PROMPT_TEMPLATE_ID == "overdue-shadow-prompt-v2-think"


def settings(**overrides):
    values = {"APP_ENV": "test", "DATABASE_URL": TEST_URL}
    values.update(overrides)
    return Settings(_env_file=None, **values)


SHADOW_ON = {
    "OVERDUE_SHADOW_ENABLED": True,
    "INTELLIGENCE_MODE": "shadow",
    "OVERDUE_SHADOW_DATA_CLASSIFICATION": "synthetic",
    "OVERDUE_SHADOW_DEPLOYMENT_BUDGET_ID": 7,
    "OVERDUE_SHADOW_MODEL_DIGEST": DIGEST,
}


def test_timeout_above_120_only_for_the_local_shadow() -> None:
    assert settings(
        INTELLIGENCE_CALL_TIMEOUT_SECONDS=300, **SHADOW_ON
    ).intelligence_call_timeout_seconds == 300

    # general configuration: still capped at 120
    assert settings(
        INTELLIGENCE_CALL_TIMEOUT_SECONDS=120
    ).intelligence_call_timeout_seconds == 120

    with pytest.raises(ValidationError):
        settings(INTELLIGENCE_CALL_TIMEOUT_SECONDS=121)

    with pytest.raises(ValidationError):
        settings(INTELLIGENCE_CALL_TIMEOUT_SECONDS=300)

    # shadow mode without the overdue shadow flag: still refused
    with pytest.raises(ValidationError):
        settings(
            INTELLIGENCE_CALL_TIMEOUT_SECONDS=300,
            INTELLIGENCE_MODE="shadow",
        )

    # hard ceiling
    with pytest.raises(ValidationError):
        settings(INTELLIGENCE_CALL_TIMEOUT_SECONDS=301, **SHADOW_ON)


def test_new_shadow_budget_covers_the_4096_worst_case() -> None:
    from scripts import create_shadow_budget as script

    assert script.SCOPE_REF == "s5-shadow-overdue-synthetic-think-4096"
    assert script.SCOPE_REF != "s5-shadow-overdue-synthetic"
    reservation = script.one_call_reservation(2000)
    # the old budget (256 output tokens, margin 2000) was 1064
    assert reservation.tokens > 1064
    assert reservation.tokens >= 4096


def test_evaluator_uses_the_shadow_profile_settings() -> None:
    from scripts import eval_overdue_shadow as ev

    assert ev.CALL_TIMEOUT_SECONDS == 300
    assert ev._PROFILE.think is True
    assert ev._PROFILE.sampling == QWEN3_THINKING_SAMPLING
    assert shadow.MAX_OUTPUT_TOKENS <= ev._PROFILE.max_output_tokens_cap
