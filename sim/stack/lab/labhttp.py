"""
Cliente HTTP do lab: `X-API-Key` + sessao por cookie. No perfil
`production` o cookie e `Secure`, entao o cliente o repassa manualmente
sobre http (S4-3). So aceita o alvo da allowlist (guarda).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie

from sim.stack.lab.guard import require_http_target


class Response:
    def __init__(self, status: int, headers, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None

    @property
    def date(self):
        value = self.headers.get("Date")
        return parsedate_to_datetime(value) if value else None


class LabClient:
    def __init__(self, config, api_key: str) -> None:
        self.base = config["http"]["base_url"]
        require_http_target(self.base, config)
        self.api_key = api_key
        self.cookie: str | None = None

    def request(self, method: str, path: str, body=None, headers: dict | None = None,
                cookie: str | None = None, timeout: int = 30) -> Response:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("X-API-Key", self.api_key)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        session_cookie = cookie if cookie is not None else self.cookie
        if session_cookie:
            request.add_header("Cookie", session_cookie)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.headers, error.read())

    def login(self, email: str, password: str) -> Response:
        response = self.request("POST", "/auth/login", {"email": email, "password": password}, cookie="")
        if response.status == 200:
            jar = SimpleCookie()
            for header in response.headers.get_all("Set-Cookie") or []:
                jar.load(header)
            self.cookie = "; ".join(f"{k}={m.value}" for k, m in jar.items())
        return response
