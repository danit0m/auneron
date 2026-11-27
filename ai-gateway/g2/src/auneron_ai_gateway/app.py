"""Aplicacao ASGI: duas rotas, contrato estrito, limites, cancelamento. Sem framework."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pydantic import ValidationError

from .config import Config
from .contract import MAX_BODY_BYTES, GenerateRequest, JsonError, strict_json_loads
from .errors import GatewayError
from .glog import AuditLog
from .limits import RateLimiter
from .ollama import OllamaClient
from .profiles import PROFILES

ROUTES: dict[str, str] = {"/v1/generate": "POST", "/v1/health": "GET"}
_REASON_CLOSE = frozenset(
    {"bad_request", "payload_too_large", "request_timeout", "unsupported_media_type",
     "not_found", "method_not_allowed", "header_too_large", "deadline_exceeded", "internal_error"}
)


def _error_body(code: str) -> bytes:
    return json.dumps({"error": {"code": code}}, separators=(",", ":")).encode("ascii")


class GatewayApp:
    def __init__(self, cfg: Config, ollama: OllamaClient, log: AuditLog) -> None:
        self.cfg = cfg
        self.ollama = ollama
        self.log = log
        self._lock = asyncio.Lock()  # processamento simultaneo = 1
        self._inflight = 0  # executando + na fila
        self._rate = RateLimiter(cfg.per_minute)

    # ------------------------------------------------------------ envio
    @staticmethod
    async def _reply(
        send: Any,
        status: int,
        body: bytes,
        *,
        close: bool,
        extra: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]
        if close:
            headers.append((b"connection", b"close"))
        headers += extra or []
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _error(
        self,
        send: Any,
        code: str,
        *,
        close: bool | None = None,
        retry_after: int | None = None,
        allow: str | None = None,
    ) -> None:
        extra: list[tuple[bytes, bytes]] = []
        if retry_after is not None:
            extra.append((b"retry-after", str(retry_after).encode()))
        if allow is not None:
            extra.append((b"allow", allow.encode()))
        err = GatewayError(code)
        await self._reply(
            send, err.status, _error_body(code),
            close=(code in _REASON_CLOSE) if close is None else close, extra=extra,
        )

    # ------------------------------------------------------------ ASGI
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        t0 = time.monotonic()
        # caminho EXATO bruto: sem decodificacao, sem query, sem normalizacao
        raw = scope.get("raw_path", b"")
        route = None
        if not scope.get("query_string"):
            try:
                route = raw.decode("ascii")
            except UnicodeDecodeError:
                route = None
        expected = ROUTES.get(route) if route is not None else None
        if expected is None:
            await self._error(send, "not_found")
            return
        if scope["method"] != expected:
            await self._error(send, "method_not_allowed", allow=expected)
            return
        headers: dict[str, list[str]] = {}
        for k, v in scope["headers"]:
            headers.setdefault(k.decode("latin-1").lower(), []).append(v.decode("latin-1"))
        if route == "/v1/health":
            await self._health(scope, headers, send)
        else:
            await self._generate(headers, receive, send, t0)

    async def _health(self, scope: dict[str, Any], headers: dict[str, list[str]], send: Any) -> None:
        if "content-length" in headers and headers["content-length"] != ["0"] or "transfer-encoding" in headers:
            await self._error(send, "bad_request")
            return
        if self._inflight > 0:
            status, st = "busy", 200
        else:
            try:
                await self.ollama.version()
                status, st = "ready", 200
            except GatewayError:
                status, st = "unavailable", 503
        await self._reply(send, st, json.dumps({"status": status}).encode("ascii"), close=False)

    # ------------------------------------------------------------ corpo (stream com limite real)
    async def _read_body(self, headers: dict[str, list[str]], receive: Any) -> bytes:
        cl = headers.get("content-length")
        te = headers.get("transfer-encoding")
        if (cl is not None and te is not None) or (cl is not None and len(cl) != 1):
            raise GatewayError("bad_request")
        if te is not None and (len(te) != 1 or te[0].strip().lower() != "chunked"):
            raise GatewayError("bad_request")
        if cl is None and te is None:
            raise GatewayError("bad_request")
        if cl is not None:
            if not cl[0].isascii() or not cl[0].isdigit() or len(cl[0]) > 12:
                raise GatewayError("bad_request")
            if int(cl[0]) > MAX_BODY_BYTES:
                raise GatewayError("payload_too_large")
        body = bytearray()
        try:
            async with asyncio.timeout(self.cfg.body_timeout):
                while True:
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        raise ConnectionAbortedError
                    body += msg.get("body", b"")
                    if len(body) > MAX_BODY_BYTES:  # limite real, durante a leitura
                        raise GatewayError("payload_too_large")
                    if not msg.get("more_body", False):
                        break
        except TimeoutError:
            raise GatewayError("request_timeout") from None
        return bytes(body)

    async def _generate(
        self, headers: dict[str, list[str]], receive: Any, send: Any, t0: float
    ) -> None:
        cfg = self.cfg
        info: dict[str, Any] = {}
        try:
            ctype = (headers.get("content-type") or [""])[0].split(";")[0].strip().lower()
            if ctype != "application/json" or len(headers.get("content-type", [])) != 1:
                raise GatewayError("unsupported_media_type")
            try:
                body = await self._read_body(headers, receive)
            except ConnectionAbortedError:
                return
            info["in_bytes"] = len(body)
            try:
                data = strict_json_loads(body)
            except JsonError:
                raise GatewayError("invalid_json") from None
            try:
                req = GenerateRequest.model_validate(data)
            except ValidationError:
                raise GatewayError("invalid_contract") from None
            info["request_id"] = req.request_id
            if req.profile not in cfg.enabled_profiles:
                raise GatewayError("invalid_contract")
            info["profile"] = req.profile
            info["digest"] = PROFILES[req.profile].digest
        except GatewayError as exc:
            self._log_request(info, exc.code, t0, None)
            await self._error(send, exc.code)
            return

        # admissao: taxa e fila
        wait = self._rate.try_acquire()
        if wait is not None:
            self._log_request(info, "rate_limited", t0, None)
            await self._error(send, "rate_limited", close=False, retry_after=wait)
            return
        if self._inflight >= 1 + cfg.queue_size:
            self._log_request(info, "busy", t0, None)
            await self._error(send, "busy", close=False, retry_after=5)
            return

        timing: dict[str, float] = {}
        self._inflight += 1
        task: asyncio.Task[Any] = asyncio.create_task(self._run(req, timing))
        watcher = asyncio.create_task(self._watch_disconnect(receive))
        try:
            remaining = max(0.0, cfg.total_timeout - (time.monotonic() - t0))
            done, _ = await asyncio.wait(
                {task, watcher}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                try:
                    output = task.result()
                except GatewayError as exc:
                    self._log_request(info, exc.code, t0, timing)
                    await self._error(send, exc.code, close=False)
                    return
                except Exception:
                    self._log_request(info, "internal_error", t0, timing)
                    await self._error(send, "internal_error")
                    return
                payload = json.dumps(
                    {
                        "request_id": req.request_id,
                        "profile": req.profile,
                        "model": PROFILES[req.profile].model_id,
                        "digest": PROFILES[req.profile].digest,
                        "output": output,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("ascii")
                self._log_request(info, "ok", t0, timing, out_bytes=len(payload))
                await self._reply(send, 200, payload, close=False)
                return
            # cliente saiu ou prazo estourou: cancela a geracao (fecha o socket do Ollama)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if watcher in done:
                self._log_request(info, "client_gone", t0, timing)
                return
            self._log_request(info, "deadline_exceeded", t0, timing)
            await self._error(send, "deadline_exceeded")
        finally:
            watcher.cancel()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._inflight -= 1

    async def _run(self, req: GenerateRequest, timing: dict[str, float]) -> Any:
        q0 = time.monotonic()
        async with self._lock:
            timing["queue"] = time.monotonic() - q0
            m0 = time.monotonic()
            try:
                return await self.ollama.generate(
                    PROFILES[req.profile], req.instructions, req.input_text, req.output_schema
                )
            finally:
                timing["model"] = time.monotonic() - m0

    @staticmethod
    async def _watch_disconnect(receive: Any) -> None:
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return

    def _log_request(
        self,
        info: dict[str, Any],
        code: str,
        t0: float,
        timing: dict[str, float] | None,
        out_bytes: int | None = None,
    ) -> None:
        fields: dict[str, Any] = dict(info)
        fields["code"] = code
        fields["ms_total"] = int((time.monotonic() - t0) * 1000)
        if timing:
            fields["ms_queue"] = int(timing.get("queue", 0) * 1000)
            fields["ms_model"] = int(timing.get("model", 0) * 1000)
        if out_bytes is not None:
            fields["out_bytes"] = out_bytes
        if code == "ok":
            fields["status"] = 200
        else:
            try:
                fields["status"] = GatewayError(code).status
            except ValueError:
                pass
        self.log.event("request", **fields)
