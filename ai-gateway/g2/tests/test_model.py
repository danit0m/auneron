"""Item 4 do plano: modelo, digest, saida, perfis (padrao e raciocinio desligado por padrao)."""

import json

import pytest

from fake_ollama import DIGEST, THINK_DIGEST
from harness import SCHEMA, good_payload

THINK = "qwen3-4b-think"


def call(g, **over):
    return g.post(good_payload(**over))


def test_happy_path_returns_json_model_and_digest(gwf, ollama):
    g = gwf()
    r = call(g)
    assert r.status == 200
    b = r.json()
    assert b["output"] == {"ok": True}
    assert b["model"] == "qwen3:4b-instruct-2507-q4_K_M" and b["digest"] == DIGEST


def test_digest_changed_refuses_before_sending_text(gwf, ollama):
    g = gwf()
    ollama.digest = "ab" * 32
    r = call(g, input_text="MARCADOR-ENTRADA-NAO-ENVIAR")
    assert r.status == 502 and r.code == "model_mismatch"
    assert ollama.chat_bodies == []  # o texto nunca saiu do Gateway
    assert ("POST", "/api/chat") not in ollama.calls


def test_digest_prefix_sha256_accepted_uppercase_too(gwf, ollama):
    g = gwf()
    ollama.digest = "sha256:" + DIGEST.upper()
    assert call(g).status == 200


def test_model_missing(gwf, ollama):
    g = gwf()
    ollama.models_present = False
    r = call(g)
    assert r.status == 503 and r.code == "model_missing"
    assert ollama.chat_bodies == []


def test_model_answered_is_different(gwf, ollama):
    g = gwf()
    ollama.chat_model = "outro:7b"
    r = call(g)
    assert r.status == 502 and r.code == "model_mismatch"


def test_digest_swapped_during_generation_discards_output(gwf, ollama):
    g = gwf()
    ollama.digest_after_chat = "cd" * 32
    r = call(g)
    assert r.status == 502 and r.code == "model_mismatch"
    assert b"ok" not in r.body.replace(b"model_mismatch", b"")


@pytest.mark.parametrize(
    "content",
    ["not json", "", "[1,2]", '{"ok": "yes"}', '{"ok": true, "extra": 1}', "{}", '{"ok": true}{"ok": true}',
     '{"ok": true, "ok": false}', '{"ok": NaN}', "```json\n{\"ok\": true}\n```"],
)
def test_invalid_model_output(gwf, ollama, content):
    g = gwf()
    ollama.content = content
    r = call(g)
    assert r.status == 502 and r.code == "invalid_output"
    assert content.strip() == "" or content[:6].encode() not in r.body


def test_output_truncated_done_reason_length(gwf, ollama):
    g = gwf()
    ollama.done_reason = "length"
    r = call(g)
    assert r.status == 502 and r.code == "output_truncated"


def test_done_false_or_other_reason_is_error(gwf, ollama):
    g = gwf()
    ollama.done_reason = "load"
    assert call(g).code == "upstream_error"
    ollama.done_reason = "stop"
    ollama.done = False
    assert call(g, request_id="req-00000002").code == "upstream_error"


def test_output_over_64kib_refused(gwf, ollama):
    g = gwf()
    big = {"type": "object", "properties": {"t": {"type": "string"}}}
    ollama.content = json.dumps({"t": "x" * (64 * 1024)})
    r = call(g, output_schema=big)
    assert r.status == 502 and r.code == "output_too_large"


def test_output_exactly_under_limit_ok(gwf, ollama):
    g = gwf()
    big = {"type": "object", "properties": {"t": {"type": "string"}}}
    pad = 64 * 1024 - len(json.dumps({"t": ""}))
    ollama.content = json.dumps({"t": "x" * pad})
    r = call(g, output_schema=big)
    assert r.status == 200


def test_ollama_error_text_never_leaks(gwf, ollama):
    g = gwf()
    for kw in ({"chat_status": 500}, {"chat_status": 404}, {"tags_status": 500}):
        ollama.reset()
        for k, v in kw.items():
            setattr(ollama, k, v)
        r = call(g, request_id="req-" + str(abs(hash(str(kw))))[:8].rjust(8, "0"))
        assert r.status in (502, 503)
        assert b"OLLAMA-RAW" not in r.raw
        assert r.code in ("upstream_error", "unavailable", "model_missing")


def test_redirect_from_ollama_is_not_followed(gwf, ollama):
    g = gwf()
    ollama.redirect_to = f"http://127.0.0.1:{ollama.port}/redirected"
    r = call(g)
    assert r.status == 502 and r.code == "upstream_error"
    assert ("GET", "/redirected") not in ollama.calls


def test_oversized_ollama_response_refused(gwf, ollama):
    g = gwf()
    ollama.chat_raw = b'{"model":"x","pad":"' + b"a" * (600 * 1024) + b'"}'
    r = call(g)
    assert r.status == 502 and r.code == "upstream_error"


@pytest.mark.parametrize("status", [201, 204, 301, 302, 400, 404, 500, 503])
def test_non_200_from_ollama_is_error_even_with_valid_looking_body(gwf, ollama, status):
    g = gwf()
    ollama.chat_raw = json.dumps(
        {"model": "qwen3:4b-instruct-2507-q4_K_M", "message": {"role": "assistant", "content": '{"ok": true}'},
         "done": True, "done_reason": "stop"}
    ).encode()
    ollama.chat_raw_status = status
    r = call(g)
    assert r.status == 502 and r.code == "upstream_error", status


def test_enabled_profiles_default_is_only_the_approved_one(pki, ollama):
    from auneron_ai_gateway.config import load_config
    from harness import Gateway

    g = Gateway(pki, ollama.port)
    text = g.config_text()
    head, _, tail = text.partition("[profiles]")
    _, _, rest = tail.partition("[logging]")
    p = g.tmp / "noprof.toml"
    p.write_text(head + "[logging]" + rest, encoding="utf-8")
    assert load_config(p).enabled_profiles == {"qwen3-4b-instruct-2507"}
    g.cleanup()


def test_oversized_chunked_ollama_response_is_bounded_while_reading(gwf, ollama):
    """Sem Content-Length (chunked) o limite vale durante a leitura: nunca le tudo."""
    g = gwf()
    ollama.chunked = True
    ollama.chat_raw = b'{"model":"x","pad":"' + b"a" * (700 * 1024) + b'"}'
    r = call(g)
    assert r.status == 502 and r.code == "upstream_error"


def test_endless_ollama_response_stops_at_the_byte_limit(gwf, ollama):
    """Resposta sem fim: o Gateway para de ler no limite (512 KiB), nao no prazo, e fecha o socket."""
    g = gwf(total_timeout=6)
    ollama.endless = True
    r = call(g)
    assert r.status == 502 and r.code == "upstream_error"  # nao 504: parou pelo limite de bytes
    import time

    time.sleep(0.5)
    assert ollama.bytes_sent < 64 * 1024 * 1024  # buffers do SO inflam o numero; sem limite seriam centenas de MiB


def test_chunked_ollama_response_supported(gwf, ollama):
    g = gwf()
    ollama.chunked = True
    assert call(g).status == 200


def test_only_allowed_ollama_endpoints_are_used(gwf, ollama):
    g = gwf()
    call(g)
    g.get()
    assert set(ollama.calls) <= {("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat")}


def test_ollama_client_refuses_connection_whose_peer_is_not_loopback(ollama, monkeypatch):
    import asyncio
    from auneron_ai_gateway import ollama as om
    from auneron_ai_gateway.errors import GatewayError

    sent = []

    class FakeSock:
        def getpeername(self):
            return ("10.0.0.9", 11434)

        def settimeout(self, t):
            pass

        def sendall(self, d):
            sent.append(d)

        def close(self):
            pass

        def shutdown(self, how):
            pass

    monkeypatch.setattr(om.socket, "create_connection", lambda *a, **k: FakeSock())

    async def go():
        with pytest.raises(GatewayError) as ei:
            await om.OllamaClient(ollama.port).version()
        assert ei.value.code == "upstream_error"

    asyncio.run(go())
    assert sent == []  # nada foi enviado a um peer fora de loopback


def test_ollama_client_refuses_paths_outside_allowlist_and_non_loopback_peer(ollama):
    import asyncio
    from auneron_ai_gateway.errors import GatewayError
    from auneron_ai_gateway.ollama import OllamaClient, assert_loopback_peer

    c = OllamaClient(ollama.port)  # sempre o Ollama FALSO; nunca a porta real
    for m, p in [("POST", "/api/pull"), ("DELETE", "/api/delete"), ("POST", "/api/create"),
                 ("GET", "/api/tags/"), ("POST", "/api/tags"), ("GET", "/api/chat"), ("POST", "/api/embed")]:
        with pytest.raises(GatewayError) as ei:
            asyncio.run(c._call(m, p, None, max_bytes=10, timeout=1))
        assert ei.value.code == "internal_error"
    for bad in ("10.0.0.5", "192.168.1.2", "0.0.0.0", "::2", "example.com", ""):
        with pytest.raises(GatewayError):
            assert_loopback_peer(bad)
    assert_loopback_peer("127.0.0.1")
    assert_loopback_peer("::1")


def test_ollama_client_host_is_fixed_loopback_and_uses_no_proxy_or_redirect_machinery():
    import ast
    import inspect

    from auneron_ai_gateway import ollama

    assert ollama.OLLAMA_HOST == "127.0.0.1"
    tree = ast.parse(inspect.getsource(ollama))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not (mods & {"urllib", "urllib.request", "requests", "httpx", "aiohttp"})  # sem proxy/redirect automaticos
    assert "http.client" in mods


def test_cancellation_really_closes_the_socket_to_ollama(gwf, ollama):
    """Cancelar a chamada fecha o socket: o Ollama falso ve a desconexao."""
    import asyncio
    from auneron_ai_gateway.ollama import OllamaClient
    from auneron_ai_gateway.profiles import PROFILES

    ollama.chat_delay = 30.0
    c = OllamaClient(ollama.port)

    async def go():
        t = asyncio.create_task(c.generate(PROFILES["qwen3-4b-instruct-2507"], "i", "t", {"type": "object"}))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if ollama.chat_started:
                break
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        for _ in range(60):
            await asyncio.sleep(0.05)
            if ollama.disconnects:
                break

    asyncio.run(go())
    assert ollama.disconnects == 1


# ------------------------------------------------------------- perfis

def test_think_profile_disabled_by_default(gwf, ollama):
    g = gwf()
    r = call(g, profile=THINK)
    assert r.status == 422 and r.code == "invalid_contract"
    assert ollama.calls == []


def two(g_factory):
    return g_factory(enabled='"qwen3-4b-instruct-2507", "qwen3-4b-think"')


def test_think_profile_sends_defined_parameters_when_enabled(gwf, ollama):
    g = two(gwf)
    ollama.thinking = "RACIOCINIO-SECRETO-NAO-DEVOLVER"
    r = call(g, profile=THINK)
    assert r.status == 200
    b = r.json()
    assert b["model"] == "qwen3:4b" and b["digest"] == THINK_DIGEST and b["profile"] == THINK
    sent = ollama.chat_bodies[0]
    assert sent["model"] == "qwen3:4b" and sent["think"] is True
    assert sent["options"] == {
        "temperature": 0.6, "top_p": 0.95, "top_k": 20, "seed": 0, "num_predict": 4096, "num_ctx": 8192,
    }
    assert b"RACIOCINIO-SECRETO" not in r.raw  # thinking nunca devolvido


def test_think_profile_digest_checked_against_its_own_digest(gwf, ollama):
    g = two(gwf)
    ollama.think_digest = DIGEST  # digest do outro perfil: nao serve
    r = call(g, profile=THINK)
    assert r.code == "model_mismatch" and ollama.chat_bodies == []


def test_think_empty_content_with_full_thinking_is_invalid(gwf, ollama):
    g = two(gwf)
    ollama.content = ""
    ollama.thinking = "pensando muito " * 500
    r = call(g, profile=THINK)
    assert r.status == 502 and r.code == "invalid_output"
    assert b"pensando" not in r.raw


def test_think_length_with_empty_content_is_truncated(gwf, ollama):
    g = two(gwf)
    ollama.content = ""
    ollama.thinking = "pensando " * 2000
    ollama.done_reason = "length"
    r = call(g, profile=THINK)
    assert r.status == 502 and r.code == "output_truncated"


def test_thinking_is_not_validated_only_content_is(gwf, ollama):
    g = two(gwf)
    ollama.thinking = "{ nao eh json"
    ollama.content = '{"ok": true}'
    assert call(g, profile=THINK).status == 200
    assert call(g, profile="qwen3-4b-instruct-2507", request_id="req-00000009").status == 200


def test_thinking_never_logged(gwf, ollama):
    g = two(gwf)
    ollama.thinking = "RACIOCINIO-NO-LOG-JAMAIS"
    call(g, profile=THINK)
    text = "".join(f.read_text(encoding="utf-8") for f in g.log_dir.glob("gateway.log*"))
    assert "RACIOCINIO" not in text


def test_config_rejects_unknown_or_empty_enabled_profiles(pki, ollama):
    from auneron_ai_gateway.config import ConfigError, load_config
    from harness import Gateway

    for bad in ('"modelo-livre"', ""):
        g = Gateway(pki, ollama.port, enabled=bad)
        with pytest.raises(ConfigError):
            load_config(g.write_config())
        g.cleanup()


def test_profile_table_contract():
    from auneron_ai_gateway.profiles import PROFILES

    p = PROFILES["qwen3-4b-instruct-2507"]
    assert (p.model_id, p.digest, p.think, p.num_predict, p.temperature) == (
        "qwen3:4b-instruct-2507-q4_K_M", DIGEST, False, 1024, 0)
    t = PROFILES["qwen3-4b-think"]
    assert (t.model_id, t.digest, t.think, t.num_ctx, t.num_predict, t.temperature, t.top_p, t.top_k, t.seed) == (
        "qwen3:4b", THINK_DIGEST, True, 8192, 4096, 0.6, 0.95, 20, 0)
    assert all(x.max_output_bytes == 64 * 1024 for x in PROFILES.values())
