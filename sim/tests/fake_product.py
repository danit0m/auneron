"""
Produto FALSO para os testes estaticos do Driver/Collector (NAO e uma replica
do Auneron: so reproduz os FORMATOS de resposta lidos no Discovery SIM-1.5 e
as regras de separacao de deveres, para provar dispatch, estado, reconciliacao
e concorrencia sem Docker). Nenhum dado de negocio real.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlsplit

from sim.driver.http import Response
from sim.driver.http import TransportError

MANAGERS = ("sim-gerente-fin", "sim-coordenador-fin")


def reply(status: int, body=None, cookies=()) -> Response:
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    return Response(status, {}, data, cookies)


class FakeProduct:
    def __init__(self, version_id: int = 7, ttl_minutes: int = 480) -> None:
        self.lock = threading.RLock()
        self.version_id = version_id
        self.ttl = timedelta(minutes=ttl_minutes)
        self.now = datetime(2026, 1, 5, 11, 0, tzinfo=timezone.utc)   # relogio virtual do fake
        self.accounts: dict = {}
        self.approvals: dict = {}
        self.work_items: dict = {}
        self.keys: dict = {}
        self.sessions: dict = {}
        self.calls: list = []
        self.faults: list = []          # [(regex, kind, remaining)]
        self.next_id = {"account": 1, "approval": 1, "work": 1}
        self.logins = 0
        self.transport_error = TransportError
        self.brain: dict = {}
        self.list_script: list = []     # respostas roteirizadas de GET /accounts/

    # -- injecao de falhas ---------------------------------------------------
    def fail(self, pattern: str, kind: str, times: int = 1, method: str | None = None) -> None:
        """kind: timeout (nada acontece) | lost (efeito acontece, resposta se perde) | 500."""
        self.faults.append([re.compile(pattern), kind, times, method])

    def _fault(self, method: str, path: str):
        for fault in self.faults:
            if fault[2] > 0 and fault[0].search(path) and (fault[3] is None or fault[3] == method):
                fault[2] -= 1
                return fault[1]
        return None

    # -- transporte ----------------------------------------------------------
    def request(self, method, url, headers, body, timeout) -> Response:
        parts = urlsplit(url)
        path = parts.path
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        data = json.loads(body.decode("utf-8")) if body else None
        with self.lock:
            self.calls.append((method, path + (("?" + parts.query) if parts.query else ""), dict(headers)))
        fault = self._fault(method, path)
        if fault == "timeout":
            raise self.transport_error("TimeoutError")
        if fault == "500":
            return reply(500, {"detail": "boom"})
        with self.lock:
            response = self._route(method, path, query, data, headers)
        if fault == "lost":
            raise self.transport_error("ConnectionResetError")
        return response

    # -- sessao --------------------------------------------------------------
    def _persona(self, headers):
        cookie = headers.get("Cookie", "")
        match = re.search(r"auneron_session=([^;]+)", cookie)
        return self.sessions.get(match.group(1)) if match else None

    def expire_sessions(self) -> None:
        self.sessions.clear()

    # -- rotas ---------------------------------------------------------------
    def _route(self, method, path, query, data, headers) -> Response:
        if path == "/auth/login" and method == "POST":
            persona = data["email"].split("@")[0]
            token = f"tok-{persona}-{self.logins}"
            self.logins += 1
            self.sessions[token] = persona
            expires = (self.now + self.ttl).isoformat().replace("+00:00", "Z")
            return reply(200, {"user": {"id": 1, "email": data["email"]}, "authenticated_at": self.now.isoformat(),
                               "expires_at": expires, "elevated_until": None},
                         [f"auneron_session={token}; HttpOnly; Path=/; Secure"])
        persona = self._persona(headers)
        if persona is None:
            return reply(401, {"detail": "sessao"})

        if path == "/accounts/" and method == "POST":
            account_id = self.next_id["account"]
            self.next_id["account"] += 1
            account = {"id": account_id, "cliente": data["cliente"], "email": data["email"],
                       "whatsapp": data["whatsapp"], "valor": float(data["valor"]), "vencimento": data["vencimento"],
                       "status": "aberto"}
            self.accounts[account_id] = account
            return reply(201, account)
        if path == "/accounts/" and method == "GET" and self.list_script:
            return reply(200, self.list_script.pop(0))
        if path == "/accounts/" and method == "GET":
            items = [a for a in self.accounts.values() if query.get("cliente", "").lower() in a["cliente"].lower()]
            items.sort(key=lambda a: (a["vencimento"], a["id"]))
            skip, limit = int(query.get("skip", 0)), int(query.get("limit", 50))
            return reply(200, items[skip:skip + limit])
        match = re.fullmatch(r"/accounts/(\d+)", path)
        if match:
            account = self.accounts.get(int(match.group(1)))
            if account is None:
                return reply(404, {"detail": "nao"})
            if method == "GET":
                return reply(200, account)
            if method == "PUT":
                account["vencimento"] = data["vencimento"]
                return reply(200, account)
        match = re.fullmatch(r"/recommendations/(mark-overdue|human-escalation)/accounts/(\d+)/episodes/([\d-]+)/materialize", path)
        if match and method == "POST":
            kind, account_id, due = match.group(1), int(match.group(2)), match.group(3)
            key = (kind, account_id, due)
            duplicate = key in self.keys
            if not duplicate:
                work = self._new("work")
                self.work_items[work] = {"id": work, "kind": kind, "assessments": {}, "account_id": account_id}
                payload = {"work_item": {"id": work}, "created": True, "duplicate": False}
                if kind == "mark-overdue":
                    approval = self._new_approval("mark_overdue", persona, account_id, None)
                    payload["approval_request"] = self._request_view(approval)
                self.keys[key] = payload
            else:
                payload = dict(self.keys[key], created=False, duplicate=True)
            return reply(200 if duplicate else 201, payload)
        match = re.fullmatch(r"/recommendations/mark-overdue/accounts/(\d+)/episodes/([\d-]+)/execute", path)
        if match and method == "POST":
            payload = self.keys.get(("mark-overdue", int(match.group(1)), match.group(2)))
            if payload is None:
                return reply(404, {"detail": "sem work item"})
            approval = self.approvals[payload["approval_request"]["request_id"]]
            return self._execute(approval, persona, payload["work_item"]["id"])
        match = re.fullmatch(r"/recommendations/next-best-action/accounts/(\d+)/episodes/([\d-]+)", path)
        if match and method == "GET":
            return reply(200, {"decision": {"decision_type": "single_action", "selected_actions": ["account.mark_overdue"]},
                               "applied_rules": ["R0"], "recommendation_snapshot_id": 1})
        match = re.fullmatch(r"/approvals/skill-executions/(\d+)", path)
        if match and method == "POST":
            if int(match.group(1)) != self.version_id:
                return reply(404, {"detail": "versao"})
            key = headers.get("Idempotency-Key")
            if key in self.keys:
                return reply(200, {"request": self._request_view(self.keys[key]), "created": False, "duplicate": True})
            payload = data["input_payload"]
            approval = self._new_approval("mark_paid", persona, payload["account_id"], payload["expected_status"])
            self.keys[key] = approval
            return reply(201, {"request": self._request_view(approval), "created": True, "duplicate": False})
        match = re.fullmatch(r"/approvals/(\d+)/decision", path)
        if match and method == "POST":
            approval = self.approvals.get(int(match.group(1)))
            if approval is None:
                return reply(404, {"detail": "nao"})
            if approval["status"] != "pending":
                return reply(409, {"detail": "estado"})
            if approval["requester"] == persona:
                return reply(403, {"detail": "separacao"})
            approval["status"] = data["decision"]
            approval["decider"] = persona
            return reply(200, {"request": self._request_view(approval), "decision": {"decision": data["decision"]}})
        match = re.fullmatch(r"/approvals/(\d+)", path)
        if match and method == "GET":
            approval = self.approvals.get(int(match.group(1)))
            if approval is None:
                return reply(404, {"detail": "nao"})
            return reply(200, {"request": self._request_view(approval), "decision": None})
        match = re.fullmatch(r"/accounts/(\d+)/execute-mark-paid", path)
        if match and method == "POST":
            approval = self.approvals.get(data["approval_request_id"])
            if approval is None or approval["status"] != "approved":
                return reply(409, {"detail": "sem aprovacao"})
            return self._execute(approval, persona, None, account_id=int(match.group(1)))
        match = re.fullmatch(r"/work-items/(\d+)/human-assessment", path)
        if match and method == "POST":
            work = self.work_items.get(int(match.group(1)))
            if work is None:
                return reply(404, {"detail": "nao"})
            key = headers.get("Idempotency-Key")
            duplicate = key in work["assessments"]
            work["assessments"][key] = data["assessment_code"]
            return reply(200 if duplicate else 201, {"duplicate": duplicate, "created": not duplicate})
        if method == "GET":
            read = self._read_route(path, query)
            if read is not None:
                return read
        return reply(404, {"detail": f"rota inexistente {method} {path}"})

    # -- rotas de LEITURA (formatos do Discovery; sem verdade de negocio) -----
    def _read_route(self, path, query):
        if path == "/auth/me":
            return reply(200, {"user": {"id": 1}})
        if re.fullmatch(r"/accounts/\d+/classification", path):
            return reply(200, {"account_id": 1, "email": None, "status": "not_classified_yet", "classification": None})
        if path == "/brain/":
            skip, limit = int(query.get("skip", 0)), int(query.get("limit", 100))
            rows = self.brain.get(int(query["account_id"]), [])
            return reply(200, rows[skip:skip + limit])
        if path == "/memories":
            return reply(200, {"items": [], "page": {"limit": 100, "has_more": False, "next_cursor": None}})
        if re.fullmatch(r"/recommendations/(mark-overdue|human-escalation)/accounts/\d+/episodes/[\d-]+", path):
            return reply(200, {"status": "eligible", "system_recommendable": True, "reason": None})
        if path == "/approvals":
            after, limit = int(query.get("after_id", 0)), int(query.get("limit", 50))
            ids = sorted(i for i in self.approvals if i > after)
            page = ids[:limit]
            nxt = page[-1] if len(ids) > limit else None
            return reply(200, {"items": [self._request_view(self.approvals[i]) for i in page], "next_cursor": nxt})
        match = re.fullmatch(r"/work-items/(\d+)", path)
        if match:
            work = self.work_items.get(int(match.group(1)))
            return reply(200, {"id": work["id"]}) if work else reply(404, {"detail": "nao"})
        match = re.fullmatch(r"/work-items/(\d+)/escalation-observations", path)
        if match:
            return reply(200, {"items": [], "next_cursor": None})
        if path == "/work-items":
            rows = [w for w in self.work_items.values()
                    if "account_id" not in query or w.get("account_id") == int(query["account_id"])]
            return reply(200, {"items": rows[: int(query.get("limit", 50))]})
        if re.fullmatch(r"/outcomes/accounts/\d+/episodes/[\d-]+", path):
            return reply(200, {"effect_verification": {"result": None}, "evidence": []})
        return None

    # -- auxiliares ----------------------------------------------------------
    def _new(self, kind: str) -> int:
        value = self.next_id[kind]
        self.next_id[kind] += 1
        return value

    def _new_approval(self, kind, requester, account_id, expected_status) -> dict:
        approval_id = self._new("approval")
        approval = {"request_id": approval_id, "kind": kind, "requester": requester, "status": "pending",
                    "decider": None, "account_id": account_id, "expected_status": expected_status, "consumed": False}
        self.approvals[approval_id] = approval
        return approval

    def _request_view(self, approval: dict) -> dict:
        return {"request_id": approval["request_id"], "skill_version_id": self.version_id,
                "skill_key": "account.mark_paid" if approval["kind"] == "mark_paid" else "account.mark_overdue",
                "status": approval["status"], "target_account_id": approval["account_id"],
                "expires_at": (self.now + timedelta(days=1)).isoformat()}

    def _execute(self, approval: dict, persona: str, work_item_id, account_id=None) -> Response:
        if approval["status"] != "approved":
            return reply(409, {"detail": "nao aprovada"})
        if approval["decider"] == persona:
            return reply(403, {"detail": "executor e decisor"})
        duplicate = approval["consumed"]
        approval["consumed"] = True
        if approval["kind"] == "mark_paid":
            self.accounts[account_id or approval["account_id"]]["status"] = "pago"
        return reply(200, {"approval_request_id": approval["request_id"], "invocation_status": "succeeded",
                           "duplicate": duplicate, "output": {}, "work_item_id": work_item_id})
