"""Item 5 do plano: carga, fila, cancelamento, prazo, clientes lentos, taxa."""

import socket
import threading
import time

import pytest

from harness import Conn, build_request, dumps, good_payload


def run_parallel(g, n, delay_between=0.0, **kw):
    out = [None] * n

    def work(i):
        out[i] = g.post(good_payload(request_id=f"req-par-{i:04d}"), **kw)

    threads = []
    for i in range(n):
        t = threading.Thread(target=work, args=(i,))
        t.start()
        threads.append(t)
        time.sleep(delay_between)
    for t in threads:
        t.join(60)
    return out


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_only_one_generation_at_a_time(gwf, ollama):
    g = gwf()
    ollama.chat_delay = 0.4
    res = run_parallel(g, 3, 0.1)
    assert [r.status for r in res] == [200, 200, 200]
    assert ollama.max_concurrent == 1
    assert ollama.chat_started == 3


def test_queue_of_two_then_429_busy(gwf, ollama):
    g = gwf()
    ollama.chat_delay = 2.0
    res = run_parallel(g, 4, 0.25)
    codes = sorted((r.status, r.code) for r in res)
    assert codes.count((200, None)) == 3
    busy = [r for r in res if r.status == 429]
    assert len(busy) == 1 and busy[0].code == "busy" and "retry-after" in busy[0].headers
    assert ollama.max_concurrent == 1


def test_rate_limit_10_per_minute(gwf, ollama):
    g = gwf()
    for i in range(10):
        assert g.post(good_payload(request_id=f"req-rate-{i:04d}")).status == 200
    r = g.post(good_payload(request_id="req-rate-9999"))
    assert r.status == 429 and r.code == "rate_limited"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert ollama.chat_started == 10  # a 11a nao chegou ao modelo


def test_invalid_requests_do_not_consume_rate(gwf, ollama):
    g = gwf()
    for _ in range(15):
        assert g.post(b"{}").status == 422
    assert g.post(good_payload()).status == 200


def test_client_disconnect_cancels_generation_and_frees_slot(gwf, ollama):
    g = gwf()
    ollama.chat_delay = 30.0
    c = g.conn()
    c.send(build_request("POST", "/v1/generate", dumps(good_payload())))
    assert wait_for(lambda: ollama.chat_started == 1)
    t0 = time.monotonic()
    c.close()
    assert wait_for(lambda: ollama.disconnects == 1, 5), "Gateway nao fechou a conexao com o Ollama"
    assert time.monotonic() - t0 < 5
    ollama.chat_delay = 0
    t1 = time.monotonic()
    r = g.post(good_payload(request_id="req-after-0001"))
    assert r.status == 200 and time.monotonic() - t1 < 3  # vaga liberada
    assert any(x.get("code") == "client_gone" for x in g.log_records())


def test_deadline_cancels_generation_and_frees_slot(gwf, ollama):
    g = gwf(total_timeout=1.5)
    ollama.chat_delay = 30.0
    t0 = time.monotonic()
    r = g.post(good_payload())
    el = time.monotonic() - t0
    assert r.status == 504 and r.code == "deadline_exceeded"
    assert 1.2 < el < 4.0
    assert wait_for(lambda: ollama.disconnects == 1, 5)
    ollama.chat_delay = 0
    assert g.post(good_payload(request_id="req-after-0002")).status == 200


def test_deadline_includes_time_in_queue(gwf, ollama):
    g = gwf(total_timeout=2.5)
    ollama.chat_delay = 1.6
    res = run_parallel(g, 2, 0.2)
    statuses = sorted(r.status for r in res)
    assert statuses == [200, 504]  # a 2a espera ~1.4 s na fila e estoura o prazo total


def test_queued_request_cancelled_when_client_leaves(gwf, ollama):
    g = gwf()
    ollama.chat_delay = 1.5
    first = threading.Thread(target=lambda: g.post(good_payload(request_id="req-first-0001")))
    first.start()
    assert wait_for(lambda: ollama.chat_started == 1)
    c = g.conn()
    c.send(build_request("POST", "/v1/generate", dumps(good_payload(request_id="req-queued-001"))))
    time.sleep(0.4)
    c.close()
    first.join(10)
    time.sleep(0.5)
    assert ollama.chat_started == 1  # o da fila nunca foi ao modelo


def test_no_automatic_retry_when_ollama_drops_midway(gwf, ollama):
    g = gwf()
    ollama.drop_chat = True
    r = g.post(good_payload())
    assert r.status == 503 and r.code == "unavailable"
    time.sleep(0.5)
    assert ollama.chat_started == 1


def test_connection_limit_is_four(gwf):
    g = gwf()
    conns = [g.conn() for _ in range(4)]
    try:
        for c in conns:
            c.send(build_request("GET", "/v1/health", None))
            assert c.read_response().status == 200
        # a 5a conexao e fechada antes de qualquer resposta
        c5 = None
        try:
            c5 = g.conn()
            c5.send(build_request("GET", "/v1/health", None))
            assert c5.read_response() is None
        except OSError:
            pass
        finally:
            if c5:
                c5.close()
        conns.pop().close()
        time.sleep(0.5)
        assert g.get().status == 200
    finally:
        for c in conns:
            c.close()


def test_idle_tcp_without_handshake_does_not_hold_slots(gwf):
    # 4 TCP sem handshake esgotam as vagas; o prazo (handshake com TLS, ocioso sem TLS) as devolve
    g = gwf(handshake_timeout=1.0, header_timeout=1.0, idle_timeout=1.0)
    socks = [socket.create_connection(("127.0.0.1", g.port)) for _ in range(4)]
    try:
        time.sleep(0.3)
        assert g.attempt("client_ok", build_request("GET", "/v1/health", None)) is None  # lotado
        time.sleep(1.5)
        assert g.get().status == 200
    finally:
        for s in socks:
            s.close()


def test_slowloris_headers_closed(gwf):
    g = gwf(header_timeout=1.0)
    c = g.conn()
    try:
        t0 = time.monotonic()
        try:
            for ch in b"GET /v1/health HTTP/1.1\r\nX-A: ":
                c.send(bytes([ch]))
                time.sleep(0.15)
                if time.monotonic() - t0 > 2.5:
                    break
        except OSError:
            pass  # servidor ja fechou: e o esperado
        r = c.read_response()
        assert r is None or r.status == 408
        assert c.closed_by_peer(3.0)
    finally:
        c.close()
    assert time.monotonic() - t0 < 8
    assert g.get().status == 200


def test_slowloris_body_closed(gwf, ollama):
    g = gwf(body_timeout=1.0)
    c = g.conn()
    try:
        body = dumps(good_payload())
        c.send(build_request("POST", "/v1/generate", body[:20], content_length=len(body)))
        t0 = time.monotonic()
        r = c.read_response()
        assert r is not None and r.status == 408
        assert time.monotonic() - t0 < 4
    finally:
        c.close()
    assert ollama.calls == []


def test_idle_keepalive_connection_closed(gwf):
    g = gwf(idle_timeout=1.0)
    c = g.conn()
    try:
        c.send(build_request("GET", "/v1/health", None))
        assert c.read_response().status == 200
        assert c.closed_by_peer(4.0)
    finally:
        c.close()


def test_slow_reader_of_response_does_not_block_others(gwf, ollama):
    g = gwf()
    c = g.conn()
    try:
        c.send(build_request("POST", "/v1/generate", dumps(good_payload())))  # nao le a resposta
        time.sleep(0.5)
        assert g.get().status == 200
    finally:
        c.close()
