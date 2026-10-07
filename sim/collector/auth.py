"""
Login do observador: cliente SEPARADO do cliente GET-only. Sabe fazer uma
unica coisa (`POST /auth/login`) e guarda a sessao do `sim-harness-observer`
(re-login por 401 ou quando a expiracao virtual esta a < margin minutos).
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timedelta
from http.cookies import SimpleCookie
from pathlib import Path

LOGIN_PATH = "/auth/login"


class LoginFailed(RuntimeError):
    pass


def _cookie(set_cookies) -> str:
    jar = SimpleCookie()
    for header in set_cookies:
        jar.load(header)
    return "; ".join(f"{key}={morsel.value}" for key, morsel in jar.items())


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ObserverSession:
    def __init__(self, transport, base_url: str, api_key: str, email: str, password: str, state_file,
                 margin_minutes: int = 30) -> None:
        self._transport = transport
        self._base_url = base_url
        self._api_key = api_key
        self._email = email
        self._password = password
        self._state_file = Path(state_file)
        self._margin = timedelta(minutes=margin_minutes)
        self.logins = 0

    def cookie(self, now_virtual_utc: datetime) -> str:
        if self._state_file.exists():
            saved = json.loads(self._state_file.read_text(encoding="utf-8"))
            if _utc(saved["expires_at"]) - now_virtual_utc > self._margin:
                return saved["cookie"]
        return self.login()

    def invalidate(self) -> None:
        if self._state_file.exists():
            self._state_file.unlink()

    def login(self) -> str:
        headers = {"X-API-Key": self._api_key, "Accept": "application/json", "Content-Type": "application/json"}
        body = json.dumps({"email": self._email, "password": self._password}).encode("utf-8")
        response = self._transport.request("POST", self._base_url + LOGIN_PATH, headers, body, 30)
        data = response.json() or {}
        if response.status != 200 or "expires_at" not in data:
            raise LoginFailed(f"login do observador = {response.status}")
        cookie = _cookie(response.set_cookies)
        if not cookie:
            raise LoginFailed("login do observador sem cookie")
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps({"cookie": cookie, "expires_at": data["expires_at"]}), encoding="utf-8")
        self.logins += 1
        return cookie
