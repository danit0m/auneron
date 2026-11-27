"""Item 1 do plano: acesso (mTLS) e partida fail-closed."""

import socket
import ssl
import subprocess
import sys

import pytest

from harness import SERVER_NAME, Gateway, build_request, dumps, good_payload, Conn, free_port

pytestmark = pytest.mark.needs_tls

REFUSED_CLIENTS = [
    "client_expired",
    "client_notyet",
    "client_wrongca",
    "client_unlisted",
    "client_wrongeku",
]


def test_valid_client_gets_health_and_generate(gw, ollama):
    r = gw.get()
    assert r.status == 200 and r.json() == {"status": "ready"}
    r = gw.post(good_payload())
    assert r.status == 200
    body = r.json()
    assert body["output"] == {"ok": True}
    assert body["digest"].startswith("0edcdef34593")


def test_second_listed_client_also_accepted(gw):
    assert gw.get(name="client_ok2").status == 200


def test_tls13_negotiated(gw):
    c = gw.conn()
    try:
        assert c.version() == "TLSv1.3"
    finally:
        c.close()


def test_no_client_certificate_refused(gw, ollama):
    assert gw.attempt(None, build_request("GET", "/v1/health", None)) is None
    assert ollama.calls == []


@pytest.mark.parametrize("name", REFUSED_CLIENTS)
def test_bad_client_certificates_refused(gw, ollama, name):
    raw = build_request("POST", "/v1/generate", dumps(good_payload()))
    assert gw.attempt(name, raw) is None
    assert ollama.calls == []  # nada chegou ao Ollama
    codes = {r.get("code") for r in gw.log_records() if r["event"] == "conn_refused"}
    assert codes  # recusa registrada, sem detalhes do certificado


def test_unlisted_valid_cert_is_closed_before_any_route(gw, ollama):
    """Certificado valido da CA propria mas fora da lista: conexao fecha sem resposta HTTP."""
    c = gw.conn("client_unlisted")
    try:
        c.send(build_request("GET", "/v1/health", None))
        assert c.read_response() is None
    finally:
        c.close()
    assert ollama.calls == []
    assert any(r.get("code") == "client_cert_not_allowed" for r in gw.log_records())


@pytest.mark.parametrize(
    "hdr",
    [
        "X-Client-Cert-Sha256",
        "X-Forwarded-Client-Cert",
        "X-SSL-Client-Fingerprint",
        "SSL-Client-Cert",
        "X-Client-Fingerprint",
        "Forwarded",
        "X-Real-IP",
        "Authorization",
    ],
)
def test_headers_cannot_impersonate_listed_client(gw, pki, hdr):
    allowed_fp = pki["client_ok"].fingerprint
    raw = build_request("GET", "/v1/health", None, headers={hdr: allowed_fp})
    assert gw.attempt("client_unlisted", raw) is None
    assert gw.attempt("client_wrongca", raw) is None


def test_headers_with_wrong_fingerprint_do_not_block_listed_client(gw, pki):
    raw = build_request(
        "GET", "/v1/health", None, headers={"X-Client-Cert-Sha256": pki["client_unlisted"].fingerprint}
    )
    assert gw.request(raw).status == 200


def test_fingerprint_bound_to_connection_no_swap_on_keepalive(gw, ollama):
    """A identidade vem do handshake: uma conexao de cliente nao listado nunca vira listada,
    e uma conexao listada continua sendo a mesma identidade em varias requisicoes."""
    good = gw.conn("client_ok")
    try:
        for _ in range(3):
            good.send(build_request("GET", "/v1/health", None, headers={"X-Client-Cert-Sha256": "00" * 32}))
            r = good.read_response()
            assert r and r.status == 200
        # nova conexao de outro cliente enquanto a primeira segue aberta
        assert gw.attempt("client_unlisted", build_request("GET", "/v1/health", None)) is None
        good.send(build_request("GET", "/v1/health", None))
        assert good.read_response().status == 200
    finally:
        good.close()


def test_pipelined_requests_same_identity(gw):
    c = gw.conn()
    try:
        c.send(build_request("GET", "/v1/health", None) * 3)
        for _ in range(3):
            r = c.read_response()
            assert r and r.status == 200
    finally:
        c.close()


def test_pipelining_after_bad_request_closes_connection(gw):
    c = gw.conn()
    try:
        c.send(build_request("GET", "/nada", None) + build_request("GET", "/v1/health", None))
        r = c.read_response()
        assert r.status == 404
        assert c.read_response() is None  # fechou: segunda requisicao nao e atendida
    finally:
        c.close()


def test_plain_http_without_tls_gets_nothing(gw):
    s = socket.create_connection(("127.0.0.1", gw.port), timeout=5)
    s.settimeout(5)
    s.sendall(build_request("GET", "/v1/health", None))
    try:
        data = s.recv(100)
    except OSError:
        data = b""
    s.close()
    assert not data.startswith(b"HTTP/")


def test_tls12_client_refused_by_default(gw):
    assert gw.attempt("client_ok", build_request("GET", "/v1/health", None), max_tls="1.2") is None


def test_tls12_allowed_only_when_configured(gwf):
    g = gwf(min_version="1.2")
    r = g.attempt("client_ok", build_request("GET", "/v1/health", None), max_tls="1.2")
    assert r is not None and r.status == 200


def test_client_rejects_fake_server_wrong_ca(gw, pki):
    """O cliente (com CA correta) recusa um servidor com certificado de outra CA."""
    from auneron_ai_gateway.client import GatewayClient, GatewayClientError  # noqa

    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(str(pki["server_fake"].cert_path), str(pki["server_fake"].key_path))
    _fake_server_refuses(pki, srv_ctx, "server_fake")


def test_client_rejects_wrong_hostname(pki):
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(str(pki["server_wronghost"].cert_path), str(pki["server_wronghost"].key_path))
    _fake_server_refuses(pki, srv_ctx, "server_wronghost")


def _fake_server_refuses(pki, srv_ctx, _label):
    from auneron_ai_gateway.client import GatewayClient, GatewayClientError

    import threading

    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(2)
    port = lsock.getsockname()[1]
    got = []

    def serve():
        lsock.settimeout(5)
        try:
            c, _ = lsock.accept()
            try:
                s = srv_ctx.wrap_socket(c, server_side=True)
                got.append(s.recv(100))
            except (OSError, ssl.SSLError):
                got.append(b"")
        except OSError:
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cl = GatewayClient(port, pki.ca_path, pki["client_ok"].cert_path, pki["client_ok"].key_path)
    with pytest.raises(GatewayClientError) as ei:
        cl.health()
    assert ei.value.code == "unavailable"
    t.join(6)
    lsock.close()
    assert all(not g.startswith(b"GET") for g in got)  # nenhuma requisicao enviada ao falso
