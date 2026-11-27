"""Servidor: uvicorn (protocolo h11) com porta de identidade mTLS ANTES de qualquer parsing.

Arquitetura:
- socket de escuta proprio (exclusivo) e laco de accept proprio: o limite de 4 conexoes vale
  ANTES do handshake TLS;
- `GatewayProtocol` estende `uvicorn.protocols.http.h11_impl.H11Protocol`. Em `connection_made`
  (que o asyncio so chama DEPOIS do handshake) confere `ssl_object.getpeercert(binary_form=True)`
  contra a lista fixa. Recusado: aborta o transporte SEM chamar `super()` e sem ler bytes;
  `data_received` ignora tudo se nao autenticado;
- prazos de cabecalho/ocioso implementados no protocolo (uvicorn nao tem prazo de cabecalho).
Acoplamento: depende da interface interna de H11Protocol/ServerState (versao fixada e teste
que falha se ela mudar: tests/test_uvicorn_interface.py).
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from typing import Any

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from .app import ROUTES, GatewayApp  # noqa: F401  (ROUTES reexportado para testes)
from .config import Config, is_allowed_bind_host
from .glog import AuditLog
from .identity import check_identity
from .netbind import bind_exclusive
from .ollama import OllamaClient

LIMIT_CONCURRENCY = 16  # pedidos simultaneos em todas as conexoes (teto interno do uvicorn)
H11_MAX_INCOMPLETE_EVENT_SIZE = 16 * 1024  # cabecalhos


class GatewayProtocol(H11Protocol):
    """Protocolo HTTP/1.1 do uvicorn com identidade mTLS por conexao."""

    # preenchidos por make_protocol_class
    allowed_fingerprints: tuple[str, ...] = ()
    header_timeout: float = 10.0
    idle_timeout: float = 15.0
    audit: AuditLog | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._authenticated = False
        self._in_head = False
        self._timer: asyncio.TimerHandle | None = None
        self.closed: asyncio.Future[None] = self.loop.create_future()

    # ---------------------------------------------------------------- identidade
    def _identity(self, transport: asyncio.Transport) -> str:
        """"ok" ou o motivo da recusa. Unica fonte de identidade: o certificado TLS."""
        return check_identity(transport.get_extra_info("ssl_object"), self.allowed_fingerprints)

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        verdict = self._identity(transport)
        if verdict != "ok":
            self._authenticated = False
            if self.audit is not None:
                self.audit.event("conn_refused", code=verdict)
            transport.abort()  # sem super(): nenhum estado HTTP e criado, nenhum byte e lido
            return
        self._authenticated = True
        super().connection_made(transport)
        self._arm(self.idle_timeout)

    def connection_lost(self, exc: Exception | None) -> None:
        self._disarm()
        if self._authenticated:
            super().connection_lost(exc)
        if not self.closed.done():
            self.closed.set_result(None)

    def data_received(self, data: bytes) -> None:
        if not self._authenticated:
            return  # nada e interpretado
        starting = self.cycle is None or self.cycle.response_complete
        if starting and not self._in_head:
            self._in_head = True
            self._arm(self.header_timeout)  # relogio do cabecalho comeca no 1o byte
        before = self.scope
        super().data_received(data)
        if self._in_head and self.scope is not before:  # cabecalho completo
            self._in_head = False
            self._disarm()

    def eof_received(self) -> bool | None:
        if not self._authenticated:
            return False
        return super().eof_received()

    def pause_writing(self) -> None:
        if self._authenticated:
            super().pause_writing()

    def resume_writing(self) -> None:
        if self._authenticated:
            super().resume_writing()

    # ---------------------------------------------------------------- prazos
    def _arm(self, seconds: float) -> None:
        self._disarm()
        self._timer = self.loop.call_later(seconds, self._deadline)

    def _disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _deadline(self) -> None:
        self._timer = None
        if self.transport is not None:
            self.transport.abort()  # cabecalho lento / conexao ociosa: fecha


def make_protocol_class(
    cfg: Config, audit: AuditLog, base: type[GatewayProtocol] = GatewayProtocol
) -> type[GatewayProtocol]:
    return type(
        "BoundGatewayProtocol",
        (base,),
        {
            "allowed_fingerprints": tuple(sorted(cfg.allowed_fingerprints)),
            "header_timeout": cfg.header_timeout,
            "idle_timeout": cfg.idle_timeout,
            "audit": audit,
        },
    )


class GatewayServer:
    def __init__(
        self,
        cfg: Config,
        ssl_ctx: ssl.SSLContext,
        ollama: OllamaClient,
        log: AuditLog,
        protocol_base: type[GatewayProtocol] = GatewayProtocol,
    ) -> None:
        if not is_allowed_bind_host(cfg.host):
            raise ValueError("host nao permitido")
        if not isinstance(ssl_ctx, ssl.SSLContext) or ssl_ctx.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("mTLS obrigatorio")
        self.cfg = cfg
        self.ssl_ctx: ssl.SSLContext | None = ssl_ctx
        self.log = log
        self.app = GatewayApp(cfg, ollama, log)
        self._protocol_class = make_protocol_class(cfg, log, protocol_base)
        self._active_conns = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lsock: socket.socket | None = None
        self.bound_port: int | None = None
        self._uv_config = uvicorn.Config(
            self.app,
            interface="asgi3",
            http="h11",
            ws="none",
            lifespan="off",
            proxy_headers=False,
            server_header=False,
            date_header=False,
            access_log=False,
            log_config=None,
            log_level="critical",
            timeout_keep_alive=int(max(1, round(cfg.idle_timeout))),
            h11_max_incomplete_event_size=H11_MAX_INCOMPLETE_EVENT_SIZE,
            limit_concurrency=LIMIT_CONCURRENCY,
        )
        self._uv_config.load()
        self._server_state = ServerState()
        self._app_state: dict[str, Any] = {}

    # ------------------------------------------------------------ ciclo de vida
    def bind(self) -> int:
        self._lsock = bind_exclusive(self.cfg.host, self.cfg.port)
        self.bound_port = self._lsock.getsockname()[1]
        return self.bound_port

    async def serve_forever(self) -> None:
        assert self._lsock is not None
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(self._loop_exception)
        try:
            while True:
                conn, _addr = await loop.sock_accept(self._lsock)
                if self._active_conns >= self.cfg.max_connections:
                    conn.close()  # antes de qualquer handshake
                    self.log.event("conn_limit", code="connection_limit")
                    continue
                self._active_conns += 1
                task = loop.create_task(self._serve_socket(conn))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            for t in list(self._tasks):
                t.cancel()
            if self._lsock is not None:
                self._lsock.close()

    def _loop_exception(self, _loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        # nunca imprime a mensagem/contexto: so o codigo
        self.log.event("internal", code="loop_exception")

    def _new_protocol(self) -> GatewayProtocol:
        return self._protocol_class(self._uv_config, self._server_state, self._app_state)

    async def _serve_socket(self, conn: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        proto = self._new_protocol()
        try:
            await loop.connect_accepted_socket(
                lambda: proto,
                conn,
                ssl=self.ssl_ctx,
                ssl_handshake_timeout=self.cfg.handshake_timeout if self.ssl_ctx is not None else None,
            )
            await proto.closed
        except asyncio.CancelledError:
            raise
        except Exception:
            # handshake falhou (sem certificado, CA errada, vencido, timeout...)
            self.log.event("conn_refused", code="handshake_failed")
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._active_conns -= 1
