"""mTLS provado SEM rede: handshake real do OpenSSL em memoria + protocolo real do uvicorn.

Roda mesmo com antivirus interceptando TLS em loopback.
"""

import asyncio
import ssl

import pytest

from harness import build_request
from memtls import HOST, Mem, MemTransport, client_ctx_mem, server_ctx
from test_units import make_cfg

T12 = ssl.TLSVersion.TLSv1_2

# nome, servidor, cliente(nome,ca,maxv), min_tls, esperado
CASES = [
    ("certificado valido e listado", "server", ("client_ok", None, None), "1.3", "aceito"),
    ("sem certificado", "server", (None, None, None), "1.3", "recusado"),
    ("cliente valido nao listado", "server", ("client_unlisted", None, None), "1.3", "recusado"),
    ("cliente 2 fora da lista deste teste", "server", ("client_ok2", None, None), "1.3", "recusado"),
    ("cliente vencido", "server", ("client_expired", None, None), "1.3", "recusado"),
    ("cliente ainda nao valido", "server", ("client_notyet", None, None), "1.3", "recusado"),
    ("cliente de CA errada", "server", ("client_wrongca", None, None), "1.3", "recusado"),
    ("cliente com EKU errado", "server", ("client_wrongeku", None, None), "1.3", "recusado"),
    ("servidor falso (CA errada)", "server_fake", ("client_ok", None, None), "1.3", "recusado"),
    ("hostname errado no servidor", "server_wronghost", ("client_ok", None, None), "1.3", "recusado"),
    ("cliente confia em outra CA", "server", ("client_ok", "wrong", None), "1.3", "recusado"),
    ("TLS 1.2 com minimo 1.3", "server", ("client_ok", None, T12), "1.3", "recusado"),
    ("TLS 1.2 quando permitido", "server", ("client_ok", None, T12), "1.2", "aceito"),
    ("TLS 1.2 sem certificado", "server", (None, None, T12), "1.2", "recusado"),
]


def decide(pki, server_name, client, min_tls):
    from auneron_ai_gateway.identity import check_identity

    cname, ca, maxv = client
    mem = Mem(
        server_ctx(pki, server_name, min_tls),
        client_ctx_mem(pki, cname, pki.wrong_ca_path if ca == "wrong" else None, maxv),
    )
    why = mem.handshake()
    if why is not None:
        return "recusado", why, mem
    verdict = check_identity(mem.s, (pki["client_ok"].fingerprint,))
    return ("aceito" if verdict == "ok" else "recusado"), verdict, mem


@pytest.mark.parametrize("name,srv,client,min_tls,expected", CASES, ids=[c[0] for c in CASES])
def test_memory_handshake_and_identity_decision(pki, name, srv, client, min_tls, expected):
    final, detail, mem = decide(pki, srv, client, min_tls)
    assert final == expected, (name, detail)


def test_there_are_exactly_14_cases():
    assert len(CASES) == 14


def test_tls13_is_negotiated_and_data_flows(pki):
    final, _, mem = decide(pki, "server", ("client_ok", None, None), "1.3")
    assert final == "aceito" and mem.s.version() == "TLSv1.3"
    assert mem.client_send(b"oi") == b"oi"


# ------------------------------------------------------------- protocolo REAL com TLS em memoria
def make_real_protocol(pki, ollama, tmp_path):
    from auneron_ai_gateway.glog import AuditLog
    from auneron_ai_gateway.ollama import OllamaClient
    from auneron_ai_gateway.server import GatewayServer
    from auneron_ai_gateway.startup import build_ssl_context

    cfg = make_cfg(pki, ollama_port=ollama.port, log_dir=tmp_path / "log")
    log = AuditLog(tmp_path / "log")
    srv = GatewayServer(cfg, build_ssl_context(cfg), OllamaClient(ollama.port), log)
    return srv._new_protocol(), log


async def drive(proto, mem, tr, raw: bytes, wait=0.4) -> bytes:
    tr.protocol = proto
    proto.connection_made(tr)
    data = mem.client_send(raw)
    proto.data_received(data)
    await asyncio.sleep(wait)
    return mem.client_read()


def test_real_protocol_serves_listed_client_over_real_tls_in_memory(pki, ollama, tmp_path):
    mem = Mem(server_ctx(pki), client_ctx_mem(pki, "client_ok"))
    assert mem.handshake() is None

    async def go():
        proto, log = make_real_protocol(pki, ollama, tmp_path)
        tr = MemTransport(mem)
        out = await drive(proto, mem, tr, build_request("GET", "/v1/health", None))
        assert out.startswith(b"HTTP/1.1 200") and b'"ready"' in out
        out = await drive_more(proto, mem, build_request("GET", "/nada", None))
        assert out.startswith(b"HTTP/1.1 404")
        log.close()

    asyncio.run(go())


async def drive_more(proto, mem, raw, wait=0.3):
    proto.data_received(mem.client_send(raw))
    await asyncio.sleep(wait)
    return mem.client_read()


@pytest.mark.parametrize("cname", ["client_unlisted", "client_ok2"])
def test_real_protocol_aborts_unlisted_client_without_parsing(pki, ollama, tmp_path, cname):
    mem = Mem(server_ctx(pki), client_ctx_mem(pki, cname))
    assert mem.handshake() is None  # TLS passa (certificado valido da CA); a identidade recusa

    async def go():
        from auneron_ai_gateway.server import GatewayProtocol  # noqa: F401

        proto, log = make_real_protocol(pki, ollama, tmp_path)
        # lista contem so client_ok: client_unlisted/ok2 nao estao
        proto.allowed_fingerprints = (pki["client_ok"].fingerprint,)
        tr = MemTransport(mem)
        out = await drive(proto, mem, tr, build_request(
            "GET", "/v1/health", None, headers={"X-Client-Cert-Sha256": pki["client_ok"].fingerprint}))
        assert out == b""  # nada respondido
        assert tr.aborted and tr.written == 0
        assert proto.transport is None  # super().connection_made nunca foi chamado
        assert proto.cycle is None and proto.scope is None  # nada foi interpretado
        assert proto.conn.their_state.__name__ == "IDLE"  # h11 nao viu byte algum
        proto.data_received(b"GET /v1/health HTTP/1.1\r\n\r\n")  # ignorado
        assert proto.eof_received() is False  # EOF de nao autenticado: fecha, nunca delega ao h11
        proto.pause_writing()
        proto.resume_writing()  # sem fluxo criado: nao pode levantar
        assert proto.cycle is None and proto.scope is None
        await asyncio.sleep(0.1)
        assert any(r.get("code") == "client_cert_not_allowed" for r in _records(tmp_path))
        log.close()

    asyncio.run(go())


def _records(tmp_path):
    import json

    out = []
    for f in (tmp_path / "log").glob("gateway.log*"):
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def test_real_protocol_aborts_when_ssl_object_missing(pki, ollama, tmp_path):
    """Sem ssl_object (conexao sem TLS) a identidade falha fechada."""

    class NoTls(MemTransport):
        def get_extra_info(self, name, default=None):
            return default

    mem = Mem(server_ctx(pki), client_ctx_mem(pki, "client_ok"))

    async def go():
        proto, log = make_real_protocol(pki, ollama, tmp_path)
        tr = NoTls(mem)
        tr.protocol = proto
        proto.connection_made(tr)
        proto.data_received(b"GET /v1/health HTTP/1.1\r\n\r\n")
        assert tr.aborted and tr.written == 0 and proto.cycle is None
        log.close()

    asyncio.run(go())


def test_real_protocol_header_deadline_aborts_slow_head(pki, ollama, tmp_path):
    mem = Mem(server_ctx(pki), client_ctx_mem(pki, "client_ok"))
    assert mem.handshake() is None

    async def go():
        proto, log = make_real_protocol(pki, ollama, tmp_path)
        proto.header_timeout = 0.3
        tr = MemTransport(mem)
        tr.protocol = proto
        proto.connection_made(tr)
        proto.data_received(mem.client_send(b"GET /v1/health HTTP/1.1\r\nX-A: "))  # cabecalho incompleto
        await asyncio.sleep(0.8)
        assert tr.aborted
        log.close()

    asyncio.run(go())
