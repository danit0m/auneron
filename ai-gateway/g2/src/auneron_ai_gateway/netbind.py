"""Socket de escuta exclusivo em 127.0.0.1 (sem reuso de endereco; porta exclusiva no Windows)."""

from __future__ import annotations

import socket


def bind_exclusive(host: str, port: int, backlog: int = 8) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))
        sock.listen(backlog)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock
