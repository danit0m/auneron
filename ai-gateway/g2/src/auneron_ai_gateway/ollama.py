"""Cliente do Ollama: so 127.0.0.1, sem proxy, sem redirect, 3 rotas fixas.

Usa http.client (biblioteca padrao, nao usa variaveis de proxy e nao segue redirect) em thread
dedicada. O cancelamento e real: fechar o socket a partir do loop interrompe a leitura bloqueada.
Sem parser HTTP proprio.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import http.client
import ipaddress
import json
import socket
from typing import Any

from .contract import (
    MAX_OUTPUT_BYTES,
    JsonError,
    SchemaMismatch,
    strict_json_loads,
    validate_instance,
)
from .errors import GatewayError
from .profiles import Profile

OLLAMA_HOST = "127.0.0.1"
ALLOWED_CALLS = frozenset(
    {("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat")}
)
MAX_VERSION_BYTES = 4 * 1024
MAX_TAGS_BYTES = 256 * 1024
MAX_CHAT_BYTES = 512 * 1024


def assert_loopback_peer(peer_host: str) -> None:
    try:
        ok = ipaddress.ip_address(peer_host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise GatewayError("upstream_error")


class _Cancelable:
    """Referencia ao socket em uso, para fechar de outra thread."""

    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        s = self.sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


class OllamaClient:
    def __init__(
        self,
        port: int,
        *,
        connect_timeout: float = 3.0,
        meta_timeout: float = 5.0,
        chat_timeout: float = 125.0,
    ) -> None:
        if not (1 <= int(port) <= 65535):
            raise ValueError("porta invalida")
        self.port = int(port)
        self.connect_timeout = connect_timeout
        self.meta_timeout = meta_timeout
        self.chat_timeout = chat_timeout
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="ollama")

    # ------------------------------------------------------------------ bloqueante
    def _blocking(
        self,
        method: str,
        path: str,
        payload: bytes | None,
        max_bytes: int,
        io_timeout: float,
        cancel: _Cancelable,
    ) -> bytes:
        try:
            sock = socket.create_connection((OLLAMA_HOST, self.port), timeout=self.connect_timeout)
        except OSError:
            raise GatewayError("unavailable") from None
        cancel.sock = sock
        conn = http.client.HTTPConnection(OLLAMA_HOST, self.port, timeout=io_timeout)
        conn.sock = sock
        try:
            if cancel.cancelled:
                raise GatewayError("unavailable")
            peer = sock.getpeername()
            assert_loopback_peer(str(peer[0]))
            sock.settimeout(io_timeout)
            headers = {"Accept": "application/json", "Connection": "close"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            if resp.status != 200:  # inclui 3xx: nunca segue redirect
                raise GatewayError("upstream_error")
            length = resp.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > max_bytes):
                raise GatewayError("upstream_error")
            data = resp.read(max_bytes + 1)  # limite real de bytes lidos
            if len(data) > max_bytes:
                raise GatewayError("upstream_error")
            return data
        except GatewayError:
            raise
        except (http.client.RemoteDisconnected, ConnectionError, socket.timeout, TimeoutError):
            raise GatewayError("unavailable") from None
        except OSError:
            raise GatewayError("unavailable") from None
        except http.client.HTTPException:
            raise GatewayError("upstream_error") from None
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                sock.close()
            except OSError:
                pass

    async def _call(
        self,
        method: str,
        path: str,
        payload: bytes | None,
        *,
        max_bytes: int,
        timeout: float,
    ) -> bytes:
        if (method, path) not in ALLOWED_CALLS:
            raise GatewayError("internal_error")
        cancel = _Cancelable()
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(
            self._pool, self._blocking, method, path, payload, max_bytes, timeout, cancel
        )
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())  # nunca "nao recuperada"
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            cancel.cancel()
            raise GatewayError("unavailable") from None
        except asyncio.CancelledError:
            cancel.cancel()  # cancelamento real: fecha o socket com o Ollama
            raise

    @staticmethod
    def _json(data: bytes) -> Any:
        try:
            return strict_json_loads(data)
        except JsonError:
            raise GatewayError("upstream_error") from None

    async def version(self) -> str:
        data = self._json(
            await self._call(
                "GET", "/api/version", None, max_bytes=MAX_VERSION_BYTES, timeout=self.meta_timeout
            )
        )
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise GatewayError("upstream_error")
        return data["version"]

    async def model_digest(self, profile: Profile) -> str | None:
        data = self._json(
            await self._call(
                "GET", "/api/tags", None, max_bytes=MAX_TAGS_BYTES, timeout=self.meta_timeout
            )
        )
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise GatewayError("upstream_error")
        for item in models:
            if isinstance(item, dict) and item.get("name") == profile.model_id:
                digest = item.get("digest")
                if not isinstance(digest, str):
                    raise GatewayError("upstream_error")
                digest = digest.lower()
                if digest.startswith("sha256:"):
                    digest = digest[7:]
                return digest
        return None

    async def verify_model(self, profile: Profile) -> None:
        digest = await self.model_digest(profile)
        if digest is None:
            raise GatewayError("model_missing")
        if digest != profile.digest:
            raise GatewayError("model_mismatch")

    async def generate(
        self,
        profile: Profile,
        instructions: str,
        input_text: str,
        output_schema: dict[str, Any],
    ) -> Any:
        """Confere digest, chama /api/chat, confere modelo e digest de novo, valida a saida."""
        await self.verify_model(profile)
        body = json.dumps(
            {
                "model": profile.model_id,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": input_text},
                ],
                "stream": False,
                "think": profile.think,
                "format": output_schema,
                "options": {
                    "temperature": profile.temperature,
                    "top_p": profile.top_p,
                    "top_k": profile.top_k,
                    "seed": profile.seed,
                    "num_predict": profile.num_predict,
                    "num_ctx": profile.num_ctx,
                },
                "keep_alive": profile.keep_alive,
            },
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        raw = await self._call(
            "POST", "/api/chat", body, max_bytes=MAX_CHAT_BYTES, timeout=self.chat_timeout
        )
        resp = self._json(raw)
        if not isinstance(resp, dict) or "error" in resp:
            raise GatewayError("upstream_error")
        if resp.get("model") != profile.model_id:
            raise GatewayError("model_mismatch")
        if resp.get("done") is not True:
            raise GatewayError("upstream_error")
        reason = resp.get("done_reason")
        if reason == "length":
            raise GatewayError("output_truncated")
        if reason != "stop":
            raise GatewayError("upstream_error")
        # message.thinking (raciocinio) e ignorado: nunca validado, devolvido ou logado
        message = resp.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise GatewayError("upstream_error")
        if len(content.encode("utf-8", "replace")) > min(MAX_OUTPUT_BYTES, profile.max_output_bytes):
            raise GatewayError("output_too_large")
        # o modelo nao pode ter sido trocado durante a geracao
        await self.verify_model(profile)
        try:
            out = strict_json_loads(content.encode("utf-8"))
            validate_instance(output_schema, out)
        except (JsonError, SchemaMismatch, UnicodeEncodeError):
            raise GatewayError("invalid_output") from None
        return out
