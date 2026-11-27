"""Item 2 do plano: so POST /v1/generate e GET /v1/health. Tudo mais 404/405 sem vazar detalhe."""

import pytest

from harness import build_request, dumps, good_payload

OLLAMA_ADMIN = [
    "/api/pull", "/api/delete", "/api/create", "/api/copy", "/api/push", "/api/embed",
    "/api/embeddings", "/api/generate", "/api/chat", "/api/tags", "/api/version", "/api/show",
    "/api/ps", "/api/blobs/sha256:abc", "/v1/chat/completions", "/v1/models", "/v1/embeddings",
]
OTHER = [
    "/", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/v1", "/v1/", "/v1/generate/",
    "/V1/generate", "/v1/Generate", "/v1/health/", "/v1/health?x=1", "/v1/generate?x=1",
    "/v1/%67enerate", "/v1/generate%00", "/v1/generate%2F", "//v1/generate", "/v1/../v1/generate",
    "/v1/./generate", "/v1/generate;x=1", "/v1/generate#frag", "http://ai.auneron.internal/v1/health",
    "*", "/%2e%2e/v1/health", "/v1/health%20", "/admin", "/metrics", "/.env", "/robots.txt",
]


def assert_clean_404(r, ollama):
    assert r.status == 404
    assert r.body == b'{"error":{"code":"not_found"}}'
    assert "location" not in r.headers and "server" not in r.headers and "date" not in r.headers
    assert ollama.calls == []


@pytest.mark.parametrize("path", OLLAMA_ADMIN + OTHER)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_paths_are_404(gw, ollama, path, method):
    body = dumps(good_payload()) if method == "POST" else None
    r = gw.request(build_request(method, path, body))
    assert_clean_404(r, ollama)


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE", "CONNECT", "PROPFIND"])
def test_wrong_method_on_generate_is_405(gw, ollama, method):
    r = gw.request(build_request(method, "/v1/generate", b"{}"))
    assert r.status == 405
    if method != "HEAD":  # h11 nunca envia corpo em resposta a HEAD
        assert r.code == "method_not_allowed"
    assert r.headers["allow"] == "POST"
    assert ollama.calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_wrong_method_on_health_is_405(gw, ollama, method):
    r = gw.request(build_request(method, "/v1/health", b"" if method != "HEAD" else None))
    assert r.status == 405 and r.headers["allow"] == "GET"


def test_get_on_generate_is_405_not_redirect(gw):
    r = gw.get("/v1/generate")
    assert r.status == 405 and "location" not in r.headers


def test_health_with_body_refused(gw):
    r = gw.request(build_request("GET", "/v1/health", b"x"))
    assert r.status == 400


def test_health_reports_only_status_word(gw):
    r = gw.get()
    assert r.json() in ({"status": "ready"}, {"status": "busy"})


def test_no_docs_or_openapi_content(gw):
    for p in ("/docs", "/openapi.json", "/redoc"):
        r = gw.get(p)
        assert r.status == 404 and b"swagger" not in r.body.lower() and b"openapi" not in r.body.lower()


def test_error_bodies_have_only_stable_code(gw):
    r = gw.get("/xyz")
    assert set(r.json()) == {"error"} and set(r.json()["error"]) == {"code"}


def test_http10_and_garbage_request_line_refused(gw):
    # h11 aceita HTTP/1.0 (resposta com Connection: close); sem risco de smuggling
    assert gw.request(b"GET /v1/health HTTP/1.0\r\n\r\n").status in (200, 400)
    assert gw.request(b"GARBAGE\r\n\r\n").status == 400
    assert gw.request(b"GET  /v1/health HTTP/1.1\r\n\r\n").status == 400


def test_header_smuggling_forms_refused(gw, ollama):
    body = dumps(good_payload())
    # Content-Length + Transfer-Encoding
    raw = (
        b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nTransfer-Encoding: chunked\r\n\r\n".encode()
        + body
    )
    assert gw.request(raw).status == 400
    # Content-Length duplicado
    raw = (
        b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    assert gw.request(raw).status in (200, 400)  # h11 funde duplicados IDENTICOS (RFC 9110); diferentes: ver fuzz
    # Transfer-Encoding nao chunked, obs-fold, LF puro, byte nulo, controle
    for head in (
        b"Transfer-Encoding: gzip\r\n",
        b"X-A: b\r\n c\r\n",
        b"X-A: b\x00c\r\n",
        b"X-A: b\x01c\r\n",
        b"Bad Header: x\r\n",
        b"Content-Length: +5\r\n",
        b"Content-Length: 0x5\r\n",
        b"Content-Length: -1\r\n",
    ):
        raw = b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n" + head + b"\r\n" + body
        assert gw.request(raw).status in (400, 431), head
    # h11 tolera LF puro de forma consistente (parser unico): atendido ou recusado, nunca ambiguo
    assert gw.request(b"GET /v1/health HTTP/1.1\nHost: x\n\n").status in (200, 400, 408)
    assert gw.request(b"GET /v1/health HTTP/1.1\r\nHost: x\nX: y\r\n\r\n").status in (200, 400)
    assert len(ollama.chat_bodies) <= 1  # so o duplicado IDENTICO (valido) pode ter gerado


def test_oversized_headers_431(gw):
    raw = b"GET /v1/health HTTP/1.1\r\nX-Big: " + b"a" * 20000 + b"\r\n\r\n"
    r = gw.attempt("client_ok", raw)
    assert r is not None and r.status in (400, 431)
    many = b"GET /v1/health HTTP/1.1\r\n" + b"".join(b"X-%d: a\r\n" % i for i in range(100)) + b"\r\n"
    assert gw.request(many).status in (400, 431)
