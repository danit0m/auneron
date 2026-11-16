"""INTELLIGENCE S1 -- loopback-only Ollama adapter.

A scripted HTTP server on 127.0.0.1 (temporary port) plays the Ollama
runtime. No real Ollama, no external network. These tests need no
database.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

import pytest

from app.intelligence.gateway.adapters.ollama import NonLoopbackHostError
from app.intelligence.gateway.adapters.ollama import OllamaProvider
from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderInvalidResponseError
from app.intelligence.gateway.errors import ProviderModelMismatchError
from app.intelligence.gateway.errors import ProviderModelNotApprovedError
from app.intelligence.gateway.errors import ProviderNotSentError
from app.intelligence.gateway.errors import ProviderTimeoutError
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.pricing import PRICE_TABLE
from app.intelligence.gateway.profiles import KNOWN_PROVIDERS
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import build_local_ollama_profile


MODEL = "qwen3:4b"
DIGEST = "a" * 64
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


class ScriptedOllama:
    """Loopback server. `routes` maps (method, path) -> behavior."""

    def __init__(self, routes):
        self.routes = routes
        self.seen: list[tuple[str, str, bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.seen.append((self.command, self.path, body))
                behavior = outer.routes.get((self.command, self.path))

                if behavior is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if behavior == "sleep":
                    time.sleep(3)
                    return

                status, payload = behavior
                data = (
                    payload
                    if isinstance(payload, bytes)
                    else json.dumps(payload).encode("utf-8")
                )
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _serve
            do_POST = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

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
    return (200, document)


def routes(chat=None, digest=DIGEST, version="0.9.9"):
    table = {
        ("GET", "/api/version"): (200, {"version": version}),
        (
            "GET",
            "/api/tags",
        ): (
            200,
            {"models": [{"name": MODEL, "model": MODEL, "digest": digest}]},
        ),
    }

    if chat is not None:
        table[("POST", "/api/chat")] = chat

    return table


def provider_request(timeout_seconds=5):
    return ProviderRequest(
        model_id=MODEL,
        instructions="system text",
        input_text="user text",
        output_schema=SCHEMA,
        max_output_tokens=64,
        timeout_seconds=timeout_seconds,
    )


def adapter_for(server, **kwargs):
    return OllamaProvider(
        approved_digests={MODEL: DIGEST}, port=server.port, **kwargs
    )


# ---------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------


def test_valid_response_carries_identification() -> None:
    with ScriptedOllama(routes(chat=chat_ok())) as server:
        response = adapter_for(server).invoke(provider_request())

    assert response.status is ProviderStatus.COMPLETED
    assert json.loads(response.output_text) == {"ok": True}
    assert response.input_tokens == 12 and response.output_tokens == 5
    assert response.reported_model == MODEL
    assert response.model_digest == DIGEST
    assert response.runtime_version == "0.9.9"
    assert isinstance(response.latency_ms, int) and response.latency_ms >= 0

    method, path, body = server.chat_calls()[0]
    sent = json.loads(body)
    assert (method, path) == ("POST", "/api/chat")
    assert sent["model"] == MODEL and sent["stream"] is False
    assert sent["think"] is False and sent["format"] == SCHEMA
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["options"]["num_predict"] == 64
    # identification happened BEFORE the prompt was sent
    order = [s[1] for s in server.seen]
    assert order.index("/api/tags") < order.index("/api/chat")
    assert order.index("/api/version") < order.index("/api/chat")


def test_digest_prefix_and_case_are_normalized() -> None:
    table = routes(chat=chat_ok(), digest="sha256:" + DIGEST.upper())

    with ScriptedOllama(table) as server:
        assert adapter_for(server).invoke(provider_request()).model_digest == DIGEST


def test_length_stop_is_truncated() -> None:
    with ScriptedOllama(
        routes(chat=chat_ok('{"ok":', done_reason="length"))
    ) as server:
        response = adapter_for(server).invoke(provider_request())

    assert response.status is ProviderStatus.TRUNCATED


def test_missing_counters_fall_back_to_conservative_bounds() -> None:
    document = chat_ok()[1]
    del document["prompt_eval_count"], document["eval_count"]

    with ScriptedOllama(routes(chat=(200, document))) as server:
        response = adapter_for(server).invoke(provider_request())

    assert response.input_tokens == len(b"system text") + len(b"user text")
    assert response.output_tokens == 64


# ---------------------------------------------------------------------
# identification failures: prompt NEVER sent
# ---------------------------------------------------------------------


def test_digest_mismatch_is_rejected_before_the_prompt_is_sent() -> None:
    with ScriptedOllama(routes(chat=chat_ok(), digest="b" * 64)) as server:
        with pytest.raises(ProviderModelNotApprovedError) as caught:
            adapter_for(server).invoke(provider_request())

    assert isinstance(caught.value, ProviderNotSentError)
    assert caught.value.proven_not_sent is True
    assert "digest_mismatch" in caught.value.evidence
    assert server.chat_calls() == []


def test_model_without_approved_digest_is_rejected() -> None:
    with ScriptedOllama(routes(chat=chat_ok())) as server:
        adapter = OllamaProvider(
            approved_digests={"other:1b": DIGEST}, port=server.port
        )

        with pytest.raises(ProviderModelNotApprovedError):
            adapter.invoke(provider_request())

    assert server.chat_calls() == []


def test_model_not_installed_is_rejected() -> None:
    table = routes(chat=chat_ok())
    table[("GET", "/api/tags")] = (200, {"models": []})

    with ScriptedOllama(table) as server:
        with pytest.raises(ProviderModelNotApprovedError):
            adapter_for(server).invoke(provider_request())

    assert server.chat_calls() == []


def test_unreadable_version_or_tags_is_rejected() -> None:
    for path, answer in (
        ("/api/version", (500, {})),
        ("/api/version", (200, b"not json")),
        ("/api/tags", (200, {"models": "x"})),
        ("/api/tags", (503, {})),
    ):
        table = routes(chat=chat_ok())
        table[("GET", path)] = answer

        with ScriptedOllama(table) as server:
            with pytest.raises(ProviderModelNotApprovedError):
                adapter_for(server).invoke(provider_request())

        assert server.chat_calls() == [], path


def test_reported_model_other_than_requested_drops_the_output() -> None:
    table = routes(chat=chat_ok(model="another:7b"))

    with ScriptedOllama(table) as server:
        with pytest.raises(ProviderModelMismatchError):
            adapter_for(server).invoke(provider_request())


# ---------------------------------------------------------------------
# failure mapping on the chat call
# ---------------------------------------------------------------------


def test_timeout_is_classified() -> None:
    with ScriptedOllama(routes(chat="sleep")) as server:
        started = time.monotonic()

        with pytest.raises(ProviderTimeoutError):
            adapter_for(server).invoke(provider_request(timeout_seconds=1))

    assert time.monotonic() - started < 2.9  # absolute deadline honoured


def test_http_500_is_classified() -> None:
    with ScriptedOllama(routes(chat=(500, {"error": "boom"}))) as server:
        with pytest.raises(ProviderHTTPError) as caught:
            adapter_for(server).invoke(provider_request())

    assert caught.value.status_code == 500 and caught.value.transient


def test_invalid_json_envelope_is_classified() -> None:
    with ScriptedOllama(routes(chat=(200, b"{not json"))) as server:
        with pytest.raises(ProviderInvalidResponseError):
            adapter_for(server).invoke(provider_request())


@pytest.mark.parametrize(
    "document",
    [
        {"error": "model crashed"},
        {"model": MODEL, "done": False},
        {"model": MODEL, "done": True, "message": {"content": 5}},
        {"done": True, "message": {"content": "{}"}},
        [1, 2],
    ],
)
def test_invalid_envelope_shapes_are_classified(document) -> None:
    with ScriptedOllama(routes(chat=(200, document))) as server:
        with pytest.raises(ProviderInvalidResponseError):
            adapter_for(server).invoke(provider_request())


def test_invalid_schema_content_is_passed_to_the_gateway_to_classify() -> None:
    # Content that is not schema-valid is not the adapter's call: the
    # gateway validates it (INVALID_SCHEMA_RESPONSE). The adapter must
    # simply hand it over unmodified.
    with ScriptedOllama(routes(chat=chat_ok("not json at all"))) as server:
        response = adapter_for(server).invoke(provider_request())

    assert response.output_text == "not json at all"


def test_connection_refused_is_never_sent() -> None:
    with ScriptedOllama({}) as server:
        port = server.port

    adapter = OllamaProvider(approved_digests={MODEL: DIGEST}, port=port)

    with pytest.raises(ProviderNotSentError):
        adapter.invoke(provider_request())


# ---------------------------------------------------------------------
# loopback restriction
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["192.168.0.10", "10.0.0.1", "example.com", "0.0.0.0", "", "127.0.0.2",
     "localhost.evil.com", "8.8.8.8", "::", "[::1]"],
)
def test_non_loopback_host_is_refused_at_construction(host) -> None:
    with pytest.raises(NonLoopbackHostError):
        OllamaProvider(approved_digests={MODEL: DIGEST}, host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST"])
def test_loopback_hosts_are_accepted(host) -> None:
    OllamaProvider(approved_digests={MODEL: DIGEST}, host=host)


def test_non_loopback_host_is_refused_even_with_injected_transport() -> None:
    def transport(*args):
        raise AssertionError("must not be called")

    with pytest.raises(NonLoopbackHostError):
        OllamaProvider(
            approved_digests={MODEL: DIGEST},
            host="203.0.113.5",
            transport=transport,
        )


def test_adapter_requires_a_nonempty_approved_digest() -> None:
    with pytest.raises(ValueError):
        OllamaProvider(approved_digests={})

    with pytest.raises(ValueError):
        OllamaProvider(approved_digests={MODEL: " "})


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "g" * 64, "not-a-digest", "sha256:" + "a" * 63],
)
def test_approved_digest_must_be_64_hex_after_normalization(digest) -> None:
    with pytest.raises(ValueError):
        OllamaProvider(approved_digests={MODEL: digest})


def test_approved_digest_with_prefix_and_upper_case_is_accepted() -> None:
    OllamaProvider(approved_digests={MODEL: "SHA256:" + "AB" * 32})


def test_injected_transport_is_used_instead_of_the_network() -> None:
    calls = []

    def transport(method, path, body, timeout_s):
        calls.append((method, path))

        if path == "/api/version":
            return 200, b'{"version": "1"}'

        if path == "/api/tags":
            return 200, json.dumps(
                {"models": [{"name": MODEL, "digest": DIGEST}]}
            ).encode()

        return 200, json.dumps(chat_ok()[1]).encode()

    adapter = OllamaProvider(
        approved_digests={MODEL: DIGEST}, transport=transport
    )

    assert adapter.invoke(provider_request()).reported_model == MODEL
    assert calls[-1] == ("POST", "/api/chat")


# ---------------------------------------------------------------------
# registry / profile / gateway rules (no database)
# ---------------------------------------------------------------------


def test_ollama_is_known_with_zero_price_and_paid_providers_are_not() -> None:
    assert "ollama" in KNOWN_PROVIDERS
    assert not {"openai", "anthropic"} & KNOWN_PROVIDERS
    price = PRICE_TABLE[build_local_ollama_profile().price_key]
    assert price.input_micro_per_mtok == 0 == price.output_micro_per_mtok


def test_local_profile_is_configurable_and_registers() -> None:
    registry = ProfileRegistry(
        [build_local_ollama_profile(model_id="qwen3:4b-q4", max_output_tokens_cap=256)]
    )
    profile = registry.get("local-qwen3-4b")

    assert profile.provider == "ollama" and profile.model_id == "qwen3:4b-q4"
    assert profile.max_output_tokens_cap == 256


def _gateway(mode):
    adapter = OllamaProvider(
        approved_digests={MODEL: DIGEST}, transport=lambda *a: (500, b"")
    )
    return Gateway(
        registry=ProfileRegistry([build_local_ollama_profile()]),
        adapters={"ollama": adapter},
        ledger=None,
        limits=GatewayLimits(1, 1, 0),
        mode_provider=lambda: mode,
    )


def _call(classification=DataClassification.SYNTHETIC):
    return ModelCallRequest(
        call_key="k",
        purpose="p",
        profile="local-qwen3-4b",
        instructions="i",
        input_text="t",
        output_schema=SCHEMA,
        max_output_tokens=16,
        data_classification=classification,
        deployment_budget_id=1,
    )


def test_local_profile_is_refused_when_mode_is_off() -> None:
    with pytest.raises(GatewayError) as caught:
        _gateway("off")._preflight(_call())

    assert caught.value.code is GatewayErrorCode.MODE_DISABLED


def test_local_profile_is_refused_for_real_data_even_in_shadow() -> None:
    with pytest.raises(GatewayError) as caught:
        _gateway("shadow")._preflight(_call(DataClassification.REAL))

    assert caught.value.code is GatewayErrorCode.REAL_DATA_REFUSED


@pytest.mark.parametrize("mode", ["governed", "bounded"])
def test_local_profile_is_refused_in_other_modes(mode) -> None:
    with pytest.raises(GatewayError) as caught:
        _gateway(mode)._preflight(_call())

    assert caught.value.code is GatewayErrorCode.MODE_NOT_SUPPORTED


def test_local_profile_is_refused_in_shadow_inside_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(GatewayError) as caught:
        _gateway("shadow")._preflight(_call())

    assert caught.value.code is GatewayErrorCode.MODE_NOT_SUPPORTED


def test_local_profile_passes_preflight_only_in_shadow_with_synthetic(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    profile = _gateway("shadow")._preflight(_call())

    assert profile.name == "local-qwen3-4b"


def test_provider_response_defaults_keep_the_fake_contract() -> None:
    response = ProviderResponse(
        status=ProviderStatus.COMPLETED,
        output_text="{}",
        input_tokens=1,
        output_tokens=1,
    )

    assert response.reported_model is None
    assert response.model_digest is None
    assert response.runtime_version is None
    assert response.latency_ms is None
