"""TLS em memoria (ssl.MemoryBIO): handshake real do OpenSSL sem rede, imune a interceptacao.

Tambem monta um transporte falso para exercitar o protocolo REAL (GatewayProtocol/H11Protocol)
com o ssl_object real do lado servidor.
"""

from __future__ import annotations

import asyncio
import ssl
import types
from pathlib import Path
from typing import Any

HOST = "ai.auneron.internal"


def server_ctx(pki, name="server", min_tls="1.3"):
    from auneron_ai_gateway.startup import build_ssl_context

    return build_ssl_context(
        types.SimpleNamespace(
            ca_file=pki.ca_path, cert_file=pki[name].cert_path, key_file=pki[name].key_path, min_tls=min_tls
        )
    )


def client_ctx_mem(pki, name, ca=None, maxv=None):
    x = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    x.load_verify_locations(cafile=str(ca or pki.ca_path))
    if name:
        x.load_cert_chain(str(pki[name].cert_path), str(pki[name].key_path))
    if maxv:
        x.maximum_version = maxv
    return x


class Mem:
    def __init__(self, sctx, cctx, hostname=HOST):
        self.si, self.so, self.ci, self.co = (ssl.MemoryBIO() for _ in range(4))
        self.s = sctx.wrap_bio(self.si, self.so, server_side=True)
        self.c = cctx.wrap_bio(self.ci, self.co, server_hostname=hostname)

    def handshake(self) -> str | None:
        """None = ok; senao o motivo da falha (lado:razao)."""
        sd = cd = False
        for _ in range(40):
            for who in "cs":
                o = self.c if who == "c" else self.s
                if cd if who == "c" else sd:
                    continue
                try:
                    o.do_handshake()
                    if who == "c":
                        cd = True
                    else:
                        sd = True
                except ssl.SSLWantReadError:
                    pass
                except ssl.SSLError as e:
                    return f"{who}:{e.reason or e.verify_message}"
                self.si.write(self.co.read())
                self.ci.write(self.so.read())
            if cd and sd:
                return None
        return "incompleto"

    # dados de aplicacao
    def client_send(self, data: bytes) -> bytes:
        """Cliente escreve; devolve os bytes em claro que o servidor le."""
        self.c.write(data)
        self.si.write(self.co.read())
        out = b""
        while True:
            try:
                out += self.s.read(65536)
            except ssl.SSLWantReadError:
                return out

    def client_read(self) -> bytes:
        self.ci.write(self.so.read())
        out = b""
        while True:
            try:
                chunk = self.c.read(65536)
            except (ssl.SSLWantReadError, ssl.SSLZeroReturnError):
                return out
            if not chunk:
                return out
            out += chunk


class MemTransport(asyncio.Transport):
    def __init__(self, mem: Mem):
        super().__init__()
        self.mem = mem
        self.protocol: Any = None
        self.aborted = False
        self.closing = False
        self.written = 0

    def get_extra_info(self, name, default=None):
        if name == "ssl_object":
            return self.mem.s
        return default

    def write(self, data):
        self.written += len(data)
        self.mem.s.write(bytes(data))

    def is_closing(self):
        return self.closing or self.aborted

    def close(self):
        self.closing = True
        asyncio.get_event_loop().call_soon(self._lost)

    def abort(self):
        self.aborted = True
        asyncio.get_event_loop().call_soon(self._lost)

    def _lost(self):
        if self.protocol is not None:
            self.protocol.connection_lost(None)
            self.protocol = None

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass

    def get_write_buffer_size(self):
        return 0
