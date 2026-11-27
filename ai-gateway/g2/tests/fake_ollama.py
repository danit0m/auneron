"""Ollama FALSO, mas real no socket: servidor HTTP em thread propria, 127.0.0.1, porta temporaria."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

MODEL = "qwen3:4b-instruct-2507-q4_K_M"
THINK_MODEL = "qwen3:4b"
THINK_DIGEST = "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
DIGEST = "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"


class FakeOllama:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.reset()

    # ---------------------------------------------------------------- estado
    def reset(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.chat_bodies: list[dict[str, Any]] = []
        self.digest = DIGEST
        self.digest_after_chat: str | None = None  # troca durante a geracao
        self.models_present = True
        self.chat_model: str | None = None  # None = ecoa o modelo pedido
        self.think_digest = THINK_DIGEST
        self.thinking: str | None = None
        self.content: Any = '{"ok": true}'  # str ou callable(body)->str
        self.done_reason = "stop"
        self.done = True
        self.chat_delay = 0.0
        self.chat_status = 200
        self.chat_raw: bytes | None = None  # corpo cru substitui a resposta
        self.chat_raw_status = 200
        self.endless = False  # resposta chunked sem fim
        self.bytes_sent = 0
        self.drop_chat = False  # fecha a conexao sem responder
        self.redirect_to: str | None = None
        self.tags_status = 200
        self.version_status = 200
        self.current = 0
        self.max_concurrent = 0
        self.disconnects = 0
        self.chat_started = 0
        self.chunked = False
        self.error_text = "OLLAMA-RAW-ERROR-TEXT-SHOULD-NEVER-LEAK"

    # ---------------------------------------------------------------- ciclo
    def start(self, port: int = 0) -> int:
        ready = threading.Event()

        def run() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            async def boot() -> None:
                self.server = await asyncio.start_server(self._conn, "127.0.0.1", port)
                self.port = self.server.sockets[0].getsockname()[1]

            self.loop.run_until_complete(boot())
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(10)
        return self.port

    def stop(self) -> None:
        if self.loop is None:
            return
        loop = self.loop

        async def shut() -> None:
            assert self.server is not None
            self.server.close()
            await self.server.wait_closed()
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task():
                    t.cancel()

        asyncio.run_coroutine_threadsafe(shut(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        assert self.thread is not None
        self.thread.join(10)
        loop.close()
        self.loop = None

    # ---------------------------------------------------------------- HTTP
    async def _respond(
        self, w: asyncio.StreamWriter, status: int, body: bytes, extra: str = ""
    ) -> None:
        if self.chunked and status == 200:
            half = len(body) // 2
            parts = [body[:half], body[half:]]
            payload = b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in parts if p) + b"0\r\n\r\n"
            head = f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n{extra}\r\n"
        else:
            payload = body
            head = f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n{extra}\r\n"
        w.write(head.encode() + payload)
        await w.drain()

    async def _conn(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            raw = await r.readuntil(b"\r\n\r\n")
            lines = raw.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ")
            n = 0
            for ln in lines[1:]:
                if ln.lower().startswith("content-length:"):
                    n = int(ln.split(":")[1])
            body = await r.readexactly(n) if n else b""
            self.calls.append((method, path))
            if path == "/api/version":
                if self.version_status != 200:
                    await self._respond(w, self.version_status, b'{"error":"%s"}' % self.error_text.encode())
                else:
                    await self._respond(w, 200, b'{"version":"0.0.0-fake"}')
            elif path == "/api/tags":
                if self.tags_status != 200:
                    await self._respond(w, self.tags_status, b'{"error":"%s"}' % self.error_text.encode())
                    return
                models = (
                    [
                        {"name": MODEL, "model": MODEL, "digest": self.digest, "size": 1},
                        {"name": THINK_MODEL, "digest": self.think_digest, "size": 1},
                    ]
                    if self.models_present
                    else [{"name": "outro:1b", "digest": "00" * 32}]
                )
                await self._respond(w, 200, json.dumps({"models": models}).encode())
            elif path == "/api/chat":
                await self._chat(r, w, body)
            elif path == "/redirected":
                await self._respond(w, 200, b'{"version":"redirect-followed"}')
            else:
                await self._respond(w, 404, b'{"error":"nope"}')
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

    async def _chat(self, r: asyncio.StreamReader, w: asyncio.StreamWriter, body: bytes) -> None:
        self.chat_started += 1
        self.chat_bodies.append(json.loads(body))
        self.current += 1
        self.max_concurrent = max(self.max_concurrent, self.current)
        try:
            if self.drop_chat:
                w.transport.abort()
                return
            if self.redirect_to:
                await self._respond(w, 302, b"", f"Location: {self.redirect_to}\r\n")
                return
            if self.chat_delay:
                sleeper = asyncio.ensure_future(asyncio.sleep(self.chat_delay))
                watcher = asyncio.ensure_future(r.read(1))
                done, _ = await asyncio.wait({sleeper, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:  # cliente (Gateway) fechou a conexao
                    self.disconnects += 1
                    sleeper.cancel()
                    return
                watcher.cancel()
            if self.digest_after_chat is not None:
                self.digest = self.digest_after_chat
            if self.chat_status != 200:
                await self._respond(w, self.chat_status, b'{"error":"%s"}' % self.error_text.encode())
                return
            if self.endless:
                w.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
                piece = b"10000\r\n" + b"a" * 0x10000 + b"\r\n"
                try:
                    while True:
                        w.write(piece)
                        self.bytes_sent += 0x10000
                        await asyncio.wait_for(w.drain(), 5)
                        await asyncio.sleep(0)
                except (ConnectionError, asyncio.TimeoutError, OSError):
                    return
            if self.chat_raw is not None:
                await self._respond(w, self.chat_raw_status, self.chat_raw)
                return
            content = self.content(json.loads(body)) if callable(self.content) else self.content
            req = json.loads(body)
            resp = {
                "model": self.chat_model or req["model"],
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": content},
                "done": self.done,
                "done_reason": self.done_reason,
            }
            if self.thinking is not None:
                resp["message"]["thinking"] = self.thinking
            await self._respond(w, 200, json.dumps(resp).encode())
        finally:
            self.current -= 1
