"""Cliente de referencia (biblioteca pequena). mTLS, hostname verificado, disjuntor.

Nao repete automaticamente: depois que a requisicao foi enviada, qualquer falha e devolvida.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
from collections import deque
from pathlib import Path
from typing import Any

SERVER_HOSTNAME = "ai.auneron.internal"
CONNECT_TIMEOUT = 5.0
BREAKER_FAILURES = 3
BREAKER_WINDOW = 60.0


class GatewayClientError(Exception):
    def __init__(self, code: str, status: int | None = None, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after


class CircuitOpen(GatewayClientError):
    def __init__(self) -> None:
        super().__init__("circuit_open")


class GatewayClient:
    def __init__(
        self,
        port: int,
        ca_file: Path,
        cert_file: Path,
        key_file: Path,
        *,
        host: str = "127.0.0.1",
        server_hostname: str = SERVER_HOSTNAME,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = 130.0,
        clock=time.monotonic,
    ) -> None:
        self.host = host
        self.port = port
        self.server_hostname = server_hostname
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._clock = clock
        self._failures: deque[float] = deque()
        self._open_until = 0.0
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
        ctx.load_verify_locations(cafile=str(ca_file))
        ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
        self._ctx = ctx

    # --- disjuntor
    def _check_breaker(self) -> None:
        now = self._clock()
        if now < self._open_until:
            raise CircuitOpen()

    def _record_failure(self) -> None:
        now = self._clock()
        self._failures.append(now)
        while self._failures and now - self._failures[0] > BREAKER_WINDOW:
            self._failures.popleft()
        if len(self._failures) >= BREAKER_FAILURES:
            self._open_until = now + BREAKER_WINDOW
            self._failures.clear()

    def _record_success(self) -> None:
        self._failures.clear()

    # --- chamada
    def _request(self, method: str, path: str, body: bytes | None) -> tuple[int, dict[str, str], bytes]:
        self._check_breaker()
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            try:
                sock = self._ctx.wrap_socket(raw, server_hostname=self.server_hostname)
            except BaseException:
                raw.close()
                raise
        except (OSError, ssl.SSLError):
            self._record_failure()
            raise GatewayClientError("unavailable") from None
        # a partir daqui a requisicao pode ter sido enviada: nunca repetir
        try:
            sock.settimeout(self.read_timeout)
            headers = {"Host": self.server_hostname, "Connection": "close"}
            if body is not None:
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
            lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in headers.items()]
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + (body or b""))
            resp = http.client.HTTPResponse(sock)
            resp.begin()
            data = resp.read(2 * 1024 * 1024)
            status = resp.status
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
        except (OSError, http.client.HTTPException, ssl.SSLError):
            self._record_failure()
            raise GatewayClientError("unavailable") from None
        finally:
            sock.close()
        if status in (502, 503, 504):
            self._record_failure()
        else:
            self._record_success()
        return status, hdrs, data

    def health(self) -> str:
        status, _h, data = self._request("GET", "/v1/health", None)
        try:
            return str(json.loads(data)["status"])
        except (ValueError, KeyError, TypeError):
            raise GatewayClientError("bad_response", status) from None

    def generate(
        self,
        *,
        request_id: str,
        instructions: str,
        input_text: str,
        output_schema: dict[str, Any],
        profile: str = "qwen3-4b-instruct-2507",
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "profile": profile,
                "instructions": instructions,
                "input_text": input_text,
                "output_schema": output_schema,
                "data_classification": "synthetic",
                "request_id": request_id,
            }
        ).encode("utf-8")
        status, hdrs, data = self._request("POST", "/v1/generate", payload)
        try:
            obj = json.loads(data)
        except ValueError:
            raise GatewayClientError("bad_response", status) from None
        if status != 200:
            code = obj.get("error", {}).get("code", "unknown") if isinstance(obj, dict) else "unknown"
            ra = hdrs.get("retry-after")
            raise GatewayClientError(
                str(code), status, int(ra) if ra and ra.isdigit() else None
            )
        return obj
