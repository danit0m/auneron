"""Fuzzing deterministico do HTTP contra o servidor REAL (uvicorn/h11 + app), mais matriz de smuggling.

Roda sem a camada TLS (o fuzz mira o parser/app; a identidade tem teste proprio). Semente fixa.
"""

import json
import random
import socket
import time

import pytest

from harness import build_request, dumps, good_payload

N_RANDOM = 2400
KNOWN = {200, 400, 404, 405, 408, 413, 415, 422, 429, 431, 502, 503, 504}


def base_requests():
    ok = dumps(good_payload(request_id="req-fuzz-0001"))
    return [
        build_request("POST", "/v1/generate", ok),
        build_request("POST", "/v1/generate", ok, chunked=True),
        build_request("GET", "/v1/health", None),
        build_request("GET", "/nada", None),
        build_request("POST", "/v1/generate", b"{}"),
    ]


def mutate(rng: random.Random, raw: bytes) -> bytes:
    b = bytearray(raw)
    op = rng.randrange(14)
    n = len(b)
    if op == 0:
        return bytes(b[: rng.randrange(n + 1)])
    if op == 1:
        for _ in range(rng.randrange(1, 6)):
            b[rng.randrange(n)] = rng.randrange(256)
        return bytes(b)
    if op == 2:
        pos = rng.randrange(n)
        return bytes(b[:pos]) + bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40))) + bytes(b[pos:])
    if op == 3:
        i = rng.randrange(n)
        j = min(n, i + rng.randrange(1, 60))
        return bytes(b[:i] + b[j:])
    if op == 4:
        lines = raw.split(b"\r\n")
        k = rng.randrange(len(lines))
        lines.insert(k, lines[rng.randrange(len(lines))])
        return b"\r\n".join(lines)
    if op == 5:
        forms = [b"-1", b"+5", b"0x10", b"1e3", b"99999999999999999999", b"", b" 7", b"7 7", b"\x00", b"1,1"]
        return raw.replace(b"Content-Length: ", b"Content-Length: " + rng.choice(forms) + b"\r\nX-Pad: ", 1)
    if op == 6:
        junk = rng.choice([b"zz", b"-1", b"FFFFFFFFFFFFFFFF", b"1;x=y", b"0", b"", b"\xff"])
        return raw.replace(b"\r\n\r\n", b"\r\n\r\n" + junk + b"\r\n", 1)
    if op == 7:
        return raw.replace(b"HTTP/1.1", rng.choice([b"HTTP/2.0", b"HTTP/0.9", b"http/1.1", b"HTTP/1.", b"HTTP/1.1 "]), 1)
    if op == 8:
        meth = rng.choice([b"get", b"PO ST", b"\x00", b"A" * 300, b"CONNECT", b"TRACE", b"POST/"])
        return meth + raw[raw.index(b" "):]
    if op == 9:
        return raw.replace(b"\r\n\r\n", b"\r\n" + b"X-" + b"A" * rng.choice([100, 9000, 20000]) + b": 1\r\n\r\n", 1)
    if op == 10:
        return raw.replace(b"Host:", rng.choice([b"Host :", b"H\x00st:", b"Host\t:", b":", b"Host"]), 1)
    if op == 11:
        return bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
    if op == 12:
        return raw + raw[: rng.randrange(n)]  # pipelining com resto truncado
    path = rng.choice([b"/v1/generate?" + b"a=" * 50, b"/%76%31/health", b"/v1/health/../x", b"//", b"/" + b"a" * 3000,
                       b"http://x/v1/health", b"/v1/generate#", b"*"])
    first = raw.split(b" ", 2)
    return first[0] + b" " + path + b" " + first[2]


def corpus(n=N_RANDOM, seed=20261124):
    rng = random.Random(seed)
    base = base_requests()
    return [mutate(rng, rng.choice(base)) for _ in range(n)]


def talk(port, data: bytes, wait=3.0, half_close=True, want=None) -> tuple[list[int], bool]:
    """Envia, faz half-close e le ate EOF. Retorna (status das respostas, travou?)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.settimeout(wait)
    buf = b""
    hung = False
    try:
        try:
            s.sendall(data)
        except OSError:
            pass
        if half_close:
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                hung = True
                break
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if want is not None and buf.count(b"HTTP/1.1 ") >= want:
                break
        else:
            hung = True
    finally:
        s.close()
    statuses = []
    pos = 0
    while True:
        i = buf.find(b"HTTP/1.1 ", pos)
        if i < 0:
            break
        try:
            statuses.append(int(buf[i + 9 : i + 12]))
        except ValueError:
            pass
        pos = i + 9
    return statuses, hung


@pytest.fixture()
def fuzz_gw(gwf):
    return gwf(tls=False, header_timeout=1.0, body_timeout=1.0, idle_timeout=2.0, handshake_timeout=1.0)


def test_corpus_is_deterministic_and_large():
    a, b = corpus(), corpus()
    assert a == b and len(a) >= 2000 and len(set(a)) > 1000


def test_random_http_fuzz_server_survives_and_stays_consistent(fuzz_gw, ollama):
    g = fuzz_gw
    statuses_seen = set()
    hangs = 0
    for data in corpus():
        sts, hung = talk(g.port, data)
        hangs += hung
        statuses_seen.update(sts)
    assert hangs == 0, f"{hangs} conexoes travaram"
    assert statuses_seen <= KNOWN, statuses_seen - KNOWN  # nunca 500 do app
    assert 500 not in statuses_seen
    # servidor vivo e consistente
    assert g.get().status == 200
    assert g.proc.poll() is None
    assert g.stderr_path.read_bytes() == b""
    recs = g.log_records()
    bad = [r for r in recs if r.get("code") in ("unexpected_exception", "loop_exception", "internal_error")]
    assert not bad, bad[:3]
    # nenhuma vaga de conexao vazou: 4 conexoes simultaneas ainda cabem
    held = [socket.create_connection(("127.0.0.1", g.port)) for _ in range(4)]
    try:
        for s in held:
            s.sendall(build_request("GET", "/v1/health", None))
        for s in held:
            s.settimeout(3)
            assert s.recv(100).startswith(b"HTTP/1.1 200")
    finally:
        for s in held:
            s.close()
    # so o modelo falso foi chamado por requisicoes validas (<=10/min) e nenhuma rota extra
    assert set(ollama.calls) <= {("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat")}


# ------------------------------------------------------------- smuggling CL/TE (matriz)
HIDDEN_ID = "req-hidden-0001"


def smuggle_case(headers: bytes, first_body: bytes) -> bytes:
    hidden = build_request("POST", "/v1/generate", dumps(good_payload(request_id=HIDDEN_ID)))
    return (b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n" + headers
            + b"\r\n" + first_body + hidden)


JUNK = b'{"junk": 1}'
N = str(len(JUNK)).encode()
CHUNKED_JUNK = hex(len(JUNK))[2:].encode() + b"\r\n" + JUNK + b"\r\n0\r\n\r\n"

AMBIGUOUS = {
    "cl+te": (b"Content-Length: " + N + b"\r\nTransfer-Encoding: chunked\r\n", CHUNKED_JUNK),
    "te+cl": (b"Transfer-Encoding: chunked\r\nContent-Length: " + N + b"\r\n", JUNK),
    "cl+te (cl curto)": (b"Content-Length: 3\r\nTransfer-Encoding: chunked\r\n", CHUNKED_JUNK),
    "cl duplicado diferente": (b"Content-Length: " + N + b"\r\nContent-Length: 3\r\n", JUNK),
    "cl lista": (b"Content-Length: " + N + b", 3\r\n", JUNK),
    "te nao chunked": (b"Transfer-Encoding: gzip\r\n", CHUNKED_JUNK),
    "te chunked,identity": (b"Transfer-Encoding: chunked, identity\r\n", CHUNKED_JUNK),
    "te xchunked": (b"Transfer-Encoding: xchunked\r\n", CHUNKED_JUNK),
    "te espaco antes dos dois pontos": (b"Transfer-Encoding : chunked\r\n", CHUNKED_JUNK),
    "cl com sinal": (b"Content-Length: +" + N + b"\r\n", JUNK),
    "cl hex": (b"Content-Length: 0x" + N + b"\r\n", JUNK),
    "cl negativo": (b"Content-Length: -" + N + b"\r\n", JUNK),
    "cl com lixo": (b"Content-Length: " + N + b"x\r\n", JUNK),
    "cl vazio": (b"Content-Length: \r\n", JUNK),
    "te duplicado": (b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n", CHUNKED_JUNK),
    "te+te cl": (b"Transfer-Encoding: chunked\r\nTransfer-Encoding: identity\r\nContent-Length: " + N + b"\r\n", JUNK),
}
LEGIT = {
    "cl exato": (b"Content-Length: " + N + b"\r\n", JUNK),
    "chunked exato": (b"Transfer-Encoding: chunked\r\n", CHUNKED_JUNK),
    "TE maiusculo": (b"transfer-encoding: CHUNKED\r\n", CHUNKED_JUNK),
}


@pytest.mark.parametrize("name", sorted(AMBIGUOUS))
def test_ambiguous_framing_never_executes_the_hidden_request(gwf, ollama, name):
    g = gwf(tls=False, header_timeout=1.0, body_timeout=1.0)
    headers, body = AMBIGUOUS[name]
    sts, hung = talk(g.port, smuggle_case(headers, body), wait=3.0)
    assert not hung
    assert all(s in (400, 408, 413, 422, 501) for s in sts), (name, sts)
    time.sleep(0.2)
    assert not any(r.get("request_id") == HIDDEN_ID for r in g.log_records()), name
    assert ollama.chat_bodies == [] and ("POST", "/api/chat") not in ollama.calls


@pytest.mark.parametrize("name", sorted(LEGIT))
def test_unambiguous_framing_is_pipelined_exactly_once(gwf, ollama, name):
    g = gwf(tls=False)
    headers, body = LEGIT[name]
    sts, hung = talk(g.port, smuggle_case(headers, body), wait=5.0, half_close=False, want=2)
    assert not hung
    assert len(sts) == 2 and sts[1] == 200, sts  # junk -> erro de contrato; oculto -> 200
    assert sts[0] in (400, 422)
    hidden = [r for r in g.log_records() if r.get("request_id") == HIDDEN_ID]
    assert len(hidden) == 1 and hidden[0]["code"] == "ok"
    assert len(ollama.chat_bodies) == 1


def test_obs_fold_transfer_encoding_is_consistent_never_ambiguous(gwf, ollama):
    """h11 pode recusar ou desdobrar o obs-fold; o que nao pode e interpretar de dois modos."""
    g = gwf(tls=False)
    sts, hung = talk(g.port, smuggle_case(b"Transfer-Encoding:\r\n chunked\r\n", CHUNKED_JUNK),
                     wait=4.0, half_close=False, want=2)
    hidden = [r for r in g.log_records() if r.get("request_id") == HIDDEN_ID]
    if hidden:  # desdobrado como chunked: enquadramento coerente (1o = erro de contrato, 2o = oculto legitimo)
        assert sts[0] in (400, 422) and sts[1:] == [200]
    else:  # recusado
        assert all(s in (400, 408, 413, 422) for s in sts)
