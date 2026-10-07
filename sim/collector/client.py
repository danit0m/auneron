"""
Cliente GET-only do Evidence Collector. A capability e ESTRUTURAL:

* nao existe `post`/`put`/`patch`/`delete` neste cliente;
* `request` recusa localmente qualquer metodo que nao seja GET, ANTES da rede;
* so paths da allowlist; so o host `sim-backend:8000`.

O login fica em `auth.py` (cliente separado que so sabe autenticar).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from sim.collector.allowlist import ALLOWED_BASE_URLS
from sim.collector.allowlist import CapabilityViolation
from sim.collector.allowlist import route_name


class TransportError(RuntimeError):
    pass


class Response:
    def __init__(self, status: int, headers: dict | None = None, body: bytes = b"", set_cookies=()) -> None:
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.set_cookies = list(set_cookies)

    def json(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return None


class UrllibTransport:
    def request(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> Response:
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return Response(reply.status, dict(reply.headers), reply.read(), reply.headers.get_all("Set-Cookie") or [])
        except urllib.error.HTTPError as error:
            return Response(error.code, dict(error.headers), error.read(), error.headers.get_all("Set-Cookie") or [])
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise TransportError(type(error).__name__) from error


class GetOnlyClient:
    def __init__(self, transport, base_url: str, api_key: str, timeout: float = 30) -> None:
        if base_url not in ALLOWED_BASE_URLS:
            raise CapabilityViolation(f"host fora da allowlist do Collector: {base_url}")
        self._transport = transport
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout

    def request(self, method: str, path: str, cookie: str | None = None) -> Response:
        if method.upper() != "GET":
            raise CapabilityViolation("o Collector so executa GET")
        route_name(path)
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        return self._transport.request("GET", self._base_url + path, headers, None, self._timeout)

    def get(self, path: str, cookie: str | None = None) -> Response:
        return self.request("GET", path, cookie)
