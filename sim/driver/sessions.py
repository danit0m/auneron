"""
Gerenciador de sessoes por persona (HG-3). Uma sessao por persona; login
preguicoso; re-login quando a resposta e 401 ou quando a expiracao (relogio
VIRTUAL) esta a menos de `margin` minutos. O cookie `Secure` e repassado
manualmente (perfil production sobre http interno).

Persistida no StateStore (o cookie e segredo sintetico: o diretorio de estado
fica fora do repositorio, em volume do lab).
"""

from __future__ import annotations

import threading
from datetime import datetime
from datetime import timedelta

from sim.driver.http import cookie_header


class LoginFailed(RuntimeError):
    pass


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PersonaSessions:
    def __init__(self, http, store, passwords: dict, email_domain: str, margin_minutes: int) -> None:
        self.http = http
        self.store = store
        self.passwords = passwords
        self.email_domain = email_domain
        self.margin = timedelta(minutes=margin_minutes)
        self._lock = threading.Lock()
        self.logins = 0

    def email(self, persona: str) -> str:
        return f"{persona}@{self.email_domain}"

    def cookie_for(self, persona: str, now_virtual_utc: datetime) -> str:
        with self._lock:
            saved = self.store.load_session(persona)
            if saved is not None and parse_utc(saved[1]) - now_virtual_utc > self.margin:
                return saved[0]
            return self._login(persona)

    def invalidate(self, persona: str) -> None:
        with self._lock:
            self.store.drop_session(persona)

    def _login(self, persona: str) -> str:
        if persona not in self.passwords:
            raise LoginFailed(f"persona sem credencial no Driver: {persona}")
        response = self.http.request("POST", "/auth/login",
                                     {"email": self.email(persona), "password": self.passwords[persona]})
        if response.status != 200:
            raise LoginFailed(f"login {persona} = {response.status}")
        body = response.json() or {}
        cookie = cookie_header(response.set_cookies)
        if not cookie or "expires_at" not in body:
            raise LoginFailed(f"login {persona}: resposta sem cookie/expires_at")
        self.store.save_session(persona, cookie, body["expires_at"])
        self.logins += 1
        return cookie
