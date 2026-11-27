"""Unidades: JSON estrito, subconjunto de esquema, limitador, parser HTTP, configuracao, gate TLS."""

import asyncio
import hashlib
import ssl
from types import SimpleNamespace

import pytest

from auneron_ai_gateway import contract as C
from auneron_ai_gateway.limits import RateLimiter


# ------------------------------------------------------------- JSON estrito
@pytest.mark.parametrize(
    "raw",
    [b'{"a":1,"a":2}', b'{"a":NaN}', b'[Infinity]', b'[-Infinity]', b"[1e999]", b"\xff", b'"\\ud800"',
     b'["\\udc00"]', b"\xef\xbb\xbf{}", b"{", b"", b"[" * 100 + b"]" * 100, b"{'a':1}", b"[1,]", b"01",
     b'{"a":' + b"[" * 40 + b"]" * 40 + b"}"],
)
def test_strict_json_rejects(raw):
    with pytest.raises(C.JsonError):
        C.strict_json_loads(raw)


def test_strict_json_accepts_normal_and_depth_limit():
    assert C.strict_json_loads(b'{"a":[1,2,{"b":"\\u00e9\\ud83d\\ude00"}]}')["a"][2]["b"] == "é\U0001f600"
    C.strict_json_loads(b"[" * 32 + b"]" * 32)
    with pytest.raises(C.JsonError):
        C.strict_json_loads(b"[" * 33 + b"]" * 33)
    # colchetes dentro de string nao contam como profundidade
    C.strict_json_loads(b'["' + b"[" * 100 + b'"]')


def test_strict_json_error_message_has_no_input():
    try:
        C.strict_json_loads(b'{"secret-marker": NaN}')
    except C.JsonError as e:
        assert "secret" not in str(e)


# ------------------------------------------------------------- esquema
def ok_schema():
    return {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 5},
            "s": {"type": "string", "minLength": 1, "maxLength": 3, "enum": ["a", "bb"]},
            "l": {"type": "array", "items": {"type": "boolean"}, "minItems": 1, "maxItems": 2},
            "x": {"type": ["string", "null"]}, "c": {"const": 7}},
            "required": ["n"], "additionalProperties": False}


@pytest.mark.parametrize(
    "inst,good",
    [({"n": 1}, True), ({"n": 6}, False), ({"n": -1}, False), ({"n": True}, False), ({"n": 1.5}, False),
     ({"n": 2.0}, True), ({}, False), ({"n": 1, "z": 1}, False), ({"n": 1, "s": "a"}, True),
     ({"n": 1, "s": "zz"}, False), ({"n": 1, "s": ""}, False), ({"n": 1, "l": []}, False),
     ({"n": 1, "l": [True, False, True]}, False), ({"n": 1, "l": [1]}, False), ({"n": 1, "x": None}, True),
     ({"n": 1, "x": 3}, False), ({"n": 1, "c": 7}, True), ({"n": 1, "c": 8}, False), ({"n": 1, "c": True}, False),
     ([], False), ("x", False)],
)
def test_validate_instance(inst, good):
    s = ok_schema()
    if good:
        C.validate_instance(s, inst)
    else:
        with pytest.raises(C.SchemaMismatch):
            C.validate_instance(s, inst)


def test_schema_definition_accepts_ok_and_rejects_unsafe():
    C.validate_schema_definition(ok_schema())
    for bad in ({"type": "array"}, {"type": "object", "$ref": "x"}, {"type": "object", "pattern": "a"},
                {"type": "object", "properties": {"a": {"$ref": "#/x"}}}, {"type": "object", "enum": []},
                {"type": "object", "required": [1]}, {"type": "object", "minItems": -1}):
        with pytest.raises(C.SchemaDefinitionError):
            C.validate_schema_definition(bad)


# ------------------------------------------------------------- limitador
def test_rate_limiter_window():
    t = {"now": 0.0}
    rl = RateLimiter(3, 60.0, clock=lambda: t["now"])
    assert [rl.try_acquire() for _ in range(3)] == [None, None, None]
    assert rl.try_acquire() == 60
    t["now"] = 59.9
    assert rl.try_acquire() >= 1
    t["now"] = 60.0
    assert rl.try_acquire() is None


# ------------------------------------------------------------- TLS (sem rede)
def make_cfg(pki, **kw):
    from pathlib import Path

    from auneron_ai_gateway.config import Config

    base = dict(
        host="127.0.0.1", port=1, max_connections=4, handshake_timeout=1, header_timeout=1, body_timeout=1,
        idle_timeout=1, write_timeout=1, ca_file=pki.ca_path, cert_file=pki["server"].cert_path,
        key_file=pki["server"].key_path, min_tls="1.3",
        allowed_fingerprints=frozenset({pki["client_ok"].fingerprint}), ollama_port=2, queue_size=2,
        per_minute=10, total_timeout=120.0, log_dir=Path("."), retention_days=30,
        enabled_profiles=frozenset({"qwen3-4b-instruct-2507"}), exposure_unverifiable="refuse",
    )
    base.update(kw)
    return Config(**base)


def test_ssl_context_is_mtls_required_and_tls13(pki):
    from auneron_ai_gateway.startup import build_ssl_context

    ctx = build_ssl_context(make_cfg(pki))
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
    assert ctx.verify_flags & ssl.VERIFY_X509_STRICT
    assert ctx.options & ssl.OP_NO_COMPRESSION
    ctx12 = build_ssl_context(make_cfg(pki, min_tls="1.2"))
    assert ctx12.minimum_version == ssl.TLSVersion.TLSv1_2 and ctx12.verify_mode == ssl.CERT_REQUIRED
    assert len(ctx.get_ca_certs()) == 1  # so a CA propria


class FakeSSLObj:
    def __init__(self, der):
        self.der = der

    def getpeercert(self, binary_form=False):
        assert binary_form
        return self.der


def der_of(pair):
    return ssl.PEM_cert_to_DER_cert(pair.cert_path.read_text())


def test_identity_gate_decisions(pki):
    from auneron_ai_gateway.identity import check_identity

    allow = (pki["client_ok"].fingerprint,)
    assert check_identity(FakeSSLObj(der_of(pki["client_ok"])), allow) == "ok"
    for name in ("client_unlisted", "client_expired", "client_wrongca", "client_wrongeku", "client_notyet",
                 "client_ok2"):
        assert check_identity(FakeSSLObj(der_of(pki[name])), allow) == "client_cert_not_allowed", name
    assert check_identity(None, allow) == "client_cert_missing"  # sem TLS
    assert check_identity(FakeSSLObj(None), allow) == "client_cert_missing"  # sem certificado
    assert check_identity(FakeSSLObj(b""), allow) == "client_cert_missing"
    assert check_identity(FakeSSLObj(der_of(pki["client_ok"])), ()) == "client_cert_not_allowed"  # lista vazia
    flipped = bytearray(der_of(pki["client_ok"]))
    flipped[-1] ^= 1
    assert check_identity(FakeSSLObj(bytes(flipped)), allow) == "client_cert_not_allowed"  # 1 bit
    assert hashlib.sha256(der_of(pki["client_ok"])).hexdigest() == pki["client_ok"].fingerprint

    class Boom:
        def getpeercert(self, binary_form=False):
            raise ssl.SSLError("x")

    assert check_identity(Boom(), allow) == "client_cert_missing"  # excecao = falha fechada


def test_server_constructor_refuses_weak_context_and_bad_host(pki, tmp_path):
    from auneron_ai_gateway.glog import AuditLog
    from auneron_ai_gateway.ollama import OllamaClient
    from auneron_ai_gateway.server import GatewayServer

    cfg = make_cfg(pki)
    weak = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    weak.verify_mode = ssl.CERT_OPTIONAL
    for ctx in (weak, None):
        with pytest.raises(ValueError):
            GatewayServer(cfg, ctx, OllamaClient(2), AuditLog(tmp_path / "x"))
    good = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    good.verify_mode = ssl.CERT_REQUIRED
    for host in ("0.0.0.0", "::", "localhost", "10.0.0.1"):
        with pytest.raises(ValueError):
            GatewayServer(make_cfg(pki, host=host), good, OllamaClient(2), AuditLog(tmp_path / "y"))


def test_config_host_helper():
    from auneron_ai_gateway.config import is_allowed_bind_host

    assert is_allowed_bind_host("127.0.0.1")
    for h in ("0.0.0.0", "::", "::1", "localhost", "127.0.0.2", "", None, "127.0.0.1 "):
        assert not is_allowed_bind_host(h)
