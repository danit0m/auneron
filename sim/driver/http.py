"""
Cliente HTTP do Driver: `X-API-Key` + cookie de sessao, allowlist ESTRUTURAL
(metodo + path) e UM unico host permitido (`sim-backend:8000`, pela rede
interna `auneron_sim_driver`). Qualquer outra coisa levanta
`AllowlistViolation` ANTES de tocar a rede.

Mutacoes de `/brain` (resolve/reopen/DELETE) nunca entram (D-1.5-4).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

ALLOWED_BASE_URLS = frozenset({"http://sim-backend:8000"})
_DATE = r"\d{4}-\d{2}-\d{2}"

# (metodo, regex do path SEM query, chaves de query permitidas)
DRIVER_ROUTES = (
    ("POST", r"/auth/login", ()),
    ("GET", r"/auth/me", ()),
    ("POST", r"/accounts/", ()),
    ("PUT", r"/accounts/\d+", ()),
    ("GET", r"/accounts/", ("cliente", "skip", "limit")),
    ("GET", r"/accounts/\d+", ()),
    ("POST", r"/accounts/\d+/execute-mark-paid", ()),
    ("POST", rf"/recommendations/mark-overdue/accounts/\d+/episodes/{_DATE}/(materialize|execute)", ()),
    ("POST", rf"/recommendations/human-escalation/accounts/\d+/episodes/{_DATE}/materialize", ()),
    ("GET", rf"/recommendations/next-best-action/accounts/\d+/episodes/{_DATE}", ()),
    ("POST", r"/approvals/skill-executions/\d+", ()),
    ("GET", r"/approvals/\d+", ()),
    ("POST", r"/approvals/\d+/decision", ()),
    ("POST", r"/work-items/\d+/human-assessment", ()),
)
_COMPILED = tuple((m, re.compile(p), q) for m, p, q in DRIVER_ROUTES)


class AllowlistViolation(RuntimeError):
    """Metodo/path/host fora da allowlist: HARNESS_ERROR (run INVALID)."""


class TransportError(RuntimeError):
    """Timeout, conexao perdida etc.: resultado AMBIGUO."""


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


def split_path(path: str):
    base, _, query = path.partition("?")
    keys = [part.split("=", 1)[0] for part in query.split("&") if part]
    return base, keys


def check_route(method: str, path: str) -> None:
    base, keys = split_path(path)
    for allowed_method, pattern, allowed_keys in _COMPILED:
        if method == allowed_method and pattern.fullmatch(base):
            extra = [k for k in keys if k not in allowed_keys]
            if extra:
                raise AllowlistViolation(f"query fora da allowlist: {extra} em {base}")
            return
    raise AllowlistViolation(f"{method} {base} fora da allowlist do Driver")


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


class DriverHttp:
    def __init__(self, transport, base_url: str, api_key: str, timeout: float = 30) -> None:
        if base_url not in ALLOWED_BASE_URLS:
            raise AllowlistViolation(f"host fora da allowlist do Driver: {base_url}")
        self.transport = transport
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def request(self, method: str, path: str, body=None, headers: dict | None = None,
                cookie: str | None = None) -> Response:
        check_route(method, path)
        send = {"X-API-Key": self.api_key, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            send["Content-Type"] = "application/json"
        if cookie:
            send["Cookie"] = cookie
        send.update(headers or {})
        return self.transport.request(method, self.base_url + path, send, data, self.timeout)


def cookie_header(set_cookies) -> str:
    jar = SimpleCookie()
    for header in set_cookies:
        jar.load(header)
    return "; ".join(f"{key}={morsel.value}" for key, morsel in jar.items())
