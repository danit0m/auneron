"""Acoplamento a API interna do uvicorn: versao fixada e interface verificada. Falha se mudar."""

import inspect
import re
from pathlib import Path

import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol, RequestResponseCycle
from uvicorn.server import ServerState

ROOT = Path(__file__).resolve().parent.parent
PINNED_UVICORN = "0.54.0"
PINNED_H11 = "0.16.0"


def test_versions_match_the_hash_pinned_requirements():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(rf"^uvicorn=={re.escape(PINNED_UVICORN)}\b", text, re.M)
    assert re.search(rf"^h11=={re.escape(PINNED_H11)}\b", text, re.M)
    assert uvicorn.__version__ == PINNED_UVICORN and h11.__version__ == PINNED_H11


def test_h11protocol_constructor_and_overridden_methods_exist_with_expected_signature():
    sig = inspect.signature(H11Protocol.__init__)
    assert list(sig.parameters) == ["self", "config", "server_state", "app_state", "_loop"]
    for name, params in {
        "connection_made": ["self", "transport"],
        "connection_lost": ["self", "exc"],
        "data_received": ["self", "data"],
        "eof_received": ["self"],
        "pause_writing": ["self"],
        "resume_writing": ["self"],
        "handle_events": ["self"],
        "on_response_complete": ["self"],
    }.items():
        assert list(inspect.signature(getattr(H11Protocol, name)).parameters) == params, name


def test_h11protocol_instance_attributes_used_by_gateway_exist():
    from uvicorn import Config

    cfg = Config(lambda *a: None, lifespan="off", ws="none", log_config=None, h11_max_incomplete_event_size=1000)
    cfg.load()
    import asyncio

    async def go():
        p = H11Protocol(cfg, ServerState(), {})
        for attr in ("transport", "cycle", "scope", "conn", "loop", "flow", "connections", "tasks",
                     "timeout_keep_alive", "timeout_keep_alive_task", "limit_concurrency"):
            assert hasattr(p, attr), attr
        assert p.cycle is None and p.scope is None and p.transport is None
        assert p.conn.their_state is h11.IDLE
        assert p.conn._max_incomplete_event_size == 1000  # h11_max_incomplete_event_size chega ao h11
        assert hasattr(RequestResponseCycle, "response_complete") or "response_complete" in inspect.getsource(
            RequestResponseCycle.__init__
        )

    asyncio.run(go())


def test_server_state_fields_and_config_options_used():
    s = ServerState()
    for attr in ("connections", "tasks", "default_headers", "total_requests"):
        assert hasattr(s, attr)
    assert s.default_headers == []  # sem Server/Date
    params = inspect.signature(uvicorn.Config.__init__).parameters
    for opt in ("interface", "http", "ws", "lifespan", "proxy_headers", "server_header", "date_header",
                "access_log", "log_config", "log_level", "timeout_keep_alive",
                "h11_max_incomplete_event_size", "limit_concurrency"):
        assert opt in params, opt


def test_gateway_protocol_overrides_and_never_calls_super_when_refusing():
    """connection_made: identidade ANTES de super(); recusa nunca chega ao super()."""
    import ast
    import textwrap

    from auneron_ai_gateway.server import GatewayProtocol

    assert issubclass(GatewayProtocol, H11Protocol)
    src = textwrap.dedent(inspect.getsource(GatewayProtocol.connection_made))
    fn = ast.parse(src).body[0]
    stmts = fn.body
    # 1) primeiro uso real: veredito; 2) if != ok: ... abort; return  3) so depois super()
    first_super = next(i for i, st in enumerate(stmts) if "super()" in ast.unparse(st))
    refuse = next(i for i, st in enumerate(stmts) if isinstance(st, ast.If))
    assert refuse < first_super
    refuse_body = ast.unparse(stmts[refuse])
    assert "abort()" in refuse_body and "return" in refuse_body and "super()" not in refuse_body
    assert "self._identity(" in ast.unparse(stmts[0])
    dr = textwrap.dedent(inspect.getsource(GatewayProtocol.data_received))
    assert "if not self._authenticated:" in dr and dr.index("if not self._authenticated") < dr.index("super()")


def test_uvicorn_is_configured_hardened(pki, tmp_path):
    """server_header/date/access log/docs/proxy_headers/lifespan/ws desligados; limites ligados."""
    from auneron_ai_gateway.glog import AuditLog
    from auneron_ai_gateway.ollama import OllamaClient
    from auneron_ai_gateway.server import H11_MAX_INCOMPLETE_EVENT_SIZE, LIMIT_CONCURRENCY, GatewayServer
    from auneron_ai_gateway.startup import build_ssl_context
    from test_units import make_cfg

    cfg = make_cfg(pki, idle_timeout=7)
    srv = GatewayServer(cfg, build_ssl_context(cfg), OllamaClient(2), AuditLog(tmp_path / "l"))
    c = srv._uv_config
    assert c.proxy_headers is False and c.server_header is False and c.date_header is False
    assert c.access_log is False and c.lifespan == "off" and c.ws == "none" and c.log_config is None
    assert c.limit_concurrency == LIMIT_CONCURRENCY == 16
    assert c.h11_max_incomplete_event_size == H11_MAX_INCOMPLETE_EVENT_SIZE == 16 * 1024
    assert c.timeout_keep_alive == 7
    assert c.http == "h11" and c.interface == "asgi3"
    assert c.ws_protocol_class is None  # sem WebSocket / upgrade
    assert srv._server_state.default_headers == []  # sem Server/Date
    assert not (set(dir(srv.app)) & {"openapi", "docs", "redoc"})
