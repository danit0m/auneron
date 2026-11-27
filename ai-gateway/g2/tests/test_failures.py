"""Item 6 do plano: falhas e recuperacao (Ollama cai e volta, Gateway reinicia, cliente)."""

import subprocess
import sys
import time

import pytest

from harness import PYEXE, SRC, Gateway, build_request, dumps, good_payload


class Restart:
    """Para o Ollama falso e o religa na MESMA porta (sempre restaura ao sair)."""

    def __init__(self, ollama):
        self.o = ollama
        self.port = ollama.port

    def __enter__(self):
        self.o.stop()
        return self

    def __exit__(self, *a):
        if self.o.loop is None:
            self.o.start(self.port)


def test_ollama_down_then_back_without_restarting_gateway(gwf, ollama):
    g = gwf()
    assert g.post(good_payload()).status == 200
    with Restart(ollama):
        t0 = time.monotonic()
        r = g.post(good_payload(request_id="req-down-0001"))
        assert r.status == 503 and r.code == "unavailable"
        assert time.monotonic() - t0 < 6
        h = g.get()
        assert h.status == 503 and h.json() == {"status": "unavailable"}
    # Ollama voltou
    assert g.get().json() == {"status": "ready"}
    assert g.post(good_payload(request_id="req-up-00001")).status == 200


def test_ollama_dies_mid_generation_frees_the_slot(gwf, ollama):
    g = gwf()
    ollama.drop_chat = True
    assert g.post(good_payload()).code == "unavailable"
    ollama.drop_chat = False
    assert g.post(good_payload(request_id="req-after-0003")).status == 200


def test_gateway_restart_recovers_and_appends_log(gwf, ollama):
    g = gwf()
    assert g.post(good_payload()).status == 200
    g.restart()
    assert g.get().status == 200
    assert g.post(good_payload(request_id="req-after-0004")).status == 200
    starts = [r for r in g.log_records() if r["event"] == "startup"]
    assert len(starts) == 2


def test_gateway_starts_while_ollama_is_down(gwf, ollama):
    with Restart(ollama):
        g = gwf()
        assert g.get().status == 503
    assert g.get().status == 200


def test_gateway_process_leaves_no_listener_after_stop(gwf):
    g = gwf()
    port = g.port
    g.stop()
    import socket

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)


def test_unexpected_exception_does_not_kill_server_or_leak(gwf, ollama):
    g = gwf()
    ollama.chat_raw = b"\xff\xfe not json at all"
    r = g.post(good_payload())
    assert r.status == 502
    assert g.get().status == 200
    assert g.stderr_path.read_bytes() == b""


# ------------------------------------------------------------- cliente de referencia

class FakeSock:
    def __init__(self, behavior):
        self.sent = []
        self.behavior = behavior

    def settimeout(self, t):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, n):
        raise ConnectionResetError()

    def makefile(self, *a, **k):
        raise ConnectionResetError()

    def close(self):
        pass


@pytest.fixture()
def client_env(pki, monkeypatch):
    import socket

    from auneron_ai_gateway import client as cmod

    state = {"connects": 0, "timeouts": [], "wrap": [], "socks": []}

    def fake_connect(addr, timeout=None):
        state["connects"] += 1
        state["timeouts"].append(timeout)
        return FakeSock(None)

    monkeypatch.setattr(socket, "create_connection", fake_connect)
    clock = {"t": 1000.0}
    cl = cmod.GatewayClient(
        4443, pki.ca_path, pki["client_ok"].cert_path, pki["client_ok"].key_path, clock=lambda: clock["t"]
    )

    class Ctx:
        def wrap_socket(self, raw, server_hostname=None):
            state["wrap"].append(server_hostname)
            s = FakeSock(None)
            state["socks"].append(s)
            return s

    return cl, Ctx(), state, clock, cmod


def test_client_hostname_and_timeouts_and_context(client_env):
    import ssl

    cl, ctx, state, clock, cmod = client_env
    assert cl._ctx.verify_mode == ssl.CERT_REQUIRED and cl._ctx.check_hostname is True
    assert cl._ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    cl._ctx = ctx
    with pytest.raises(cmod.GatewayClientError):
        cl.health()
    assert state["timeouts"] == [5.0]  # prazo de conexao 5 s
    assert state["wrap"] == ["ai.auneron.internal"]  # hostname verificado, conectando em 127.0.0.1
    assert cmod.SERVER_HOSTNAME == "ai.auneron.internal" and cmod.CONNECT_TIMEOUT == 5.0


def test_client_never_retries_after_send(client_env):
    cl, ctx, state, clock, cmod = client_env
    cl._ctx = ctx
    with pytest.raises(cmod.GatewayClientError) as ei:
        cl.generate(request_id="req-00000001", instructions="i", input_text="t", output_schema={"type": "object"})
    assert ei.value.code == "unavailable"
    assert state["connects"] == 1 and len(state["socks"]) == 1
    assert len(state["socks"][0].sent) == 1  # enviou uma vez, nunca repetiu


def test_circuit_breaker_opens_after_three_failures_for_60s(client_env):
    cl, ctx, state, clock, cmod = client_env
    cl._ctx = ctx
    for _ in range(3):
        with pytest.raises(cmod.GatewayClientError) as ei:
            cl.health()
        assert ei.value.code == "unavailable"
        clock["t"] += 1
    assert state["connects"] == 3
    with pytest.raises(cmod.CircuitOpen):
        cl.health()
    assert state["connects"] == 3  # nem tentou conectar
    clock["t"] += 30
    with pytest.raises(cmod.CircuitOpen):
        cl.health()
    clock["t"] += 31
    with pytest.raises(cmod.GatewayClientError) as ei:
        cl.health()
    assert ei.value.code == "unavailable" and state["connects"] == 4  # fechou: tenta de novo


def test_circuit_breaker_ignores_failures_spread_over_more_than_60s(client_env):
    cl, ctx, state, clock, cmod = client_env
    cl._ctx = ctx
    for _ in range(5):
        with pytest.raises(cmod.GatewayClientError) as ei:
            cl.health()
        assert ei.value.code == "unavailable"
        clock["t"] += 40  # 3 falhas nunca cabem em 60 s
    assert state["connects"] == 5


def test_client_connection_refused_is_unavailable_quickly(pki):
    from harness import free_port
    from auneron_ai_gateway.client import GatewayClient, GatewayClientError

    cl = GatewayClient(free_port(), pki.ca_path, pki["client_ok"].cert_path, pki["client_ok"].key_path)
    t0 = time.monotonic()
    with pytest.raises(GatewayClientError) as ei:
        cl.health()
    assert ei.value.code == "unavailable" and time.monotonic() - t0 < 5.5


@pytest.mark.needs_tls
def test_client_library_and_cli_against_real_gateway(gwf, pki):
    from auneron_ai_gateway.client import GatewayClient

    g = gwf()
    cl = GatewayClient(g.port, pki.ca_path, pki["client_ok"].cert_path, pki["client_ok"].key_path)
    assert cl.health() == "ready"
    out = cl.generate(request_id="req-client-001", instructions="i", input_text="t",
                      output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}})
    assert out["output"] == {"ok": True}
    import os, sysconfig

    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), sysconfig.get_paths()["purelib"]]))
    base = [PYEXE, "-m", "auneron_ai_gateway.client_cli", "--port", str(g.port), "--ca", str(pki.ca_path),
            "--cert", str(pki["client_ok"].cert_path), "--key", str(pki["client_ok"].key_path)]
    ok = subprocess.run(base + ["health"], capture_output=True, text=True, env=env, timeout=30)
    assert ok.returncode == 0 and ok.stdout.strip() == "ready"
    bad = base[:]
    bad[bad.index("--cert") + 1] = str(pki["client_unlisted"].cert_path)
    bad[bad.index("--key") + 1] = str(pki["client_unlisted"].key_path)
    r = subprocess.run(bad + ["health"], capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 1
