"""
Loop do Driver (Design Freeze V1 sec. 2-7). O Driver EXECUTA e REGISTRA; nunca
avalia, nunca le o Oracle, nunca toca o banco.

* Uma invocacao por slot `(day, at)` (o harness controla o clock e a ordem):
  `run_slot` executa as ops pendentes do slot em ordem de `seq`; ops do mesmo
  `group` rodam em concorrencia REAL.
* Estado persistente: toda op passa por pending -> sent -> done; uma op `sent`
  sem `done` (crash) e reconciliada antes de qualquer reenvio.
* Retry cego so onde o produto e idempotente; `create_receivable` e decisoes
  NUNCA recebem retry cego (reconciliacao fail-closed).
* `restart_backend` e op de HARNESS: devolvida em `harness_ops`.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from datetime import timezone
from urllib.parse import quote

from sim.driver.concurrency import run_concurrently
from sim.driver.dispatch import DECISION_VALUE
from sim.driver.dispatch import HARNESS_OPS
from sim.driver.dispatch import Context
from sim.driver.dispatch import build_request
from sim.driver.dispatch import effects
from sim.driver.dispatch import iso_day
from sim.driver.errors import Ambiguous
from sim.driver.errors import HarnessError
from sim.driver.http import AllowlistViolation
from sim.driver.http import TransportError
from sim.driver.reconcile import ABSENT
from sim.driver.reconcile import ADOPT
from sim.driver.reconcile import reconcile_create_receivable
from sim.driver.reconcile import reconcile_decision
from sim.driver.reconcile import reconcile_due
from sim.driver.sessions import LoginFailed
from sim.driver.state import DONE
from sim.driver.state import FAILED
from sim.driver.state import RECONCILED
from sim.driver.state import SENT
from sim.driver.state import TERMINAL


def virtual_utc(d0: date, day: int, at: str, offset_minutes: int) -> datetime:
    hours, minutes = (int(part) for part in at.split(":"))
    local = datetime.combine(d0 + timedelta(days=day), dtime(hours, minutes),
                             tzinfo=timezone(timedelta(minutes=offset_minutes)))
    return local.astimezone(timezone.utc)


class Driver:
    def __init__(self, *, params, ops: list, manifest: dict, http, sessions, store, evidence,
                 sleep=time.sleep, clock=time.time) -> None:
        self.params = params
        self.http = http
        self.sessions = sessions
        self.store = store
        self.evidence = evidence
        self.sleep = sleep
        self.clock = clock
        self.d0 = date.fromisoformat(params["d0"])
        self.offset = int(params["utc_offset_minutes"])
        self.version_id = int(manifest["mark_paid_version_id"])
        self.ctx = Context(store, self.d0, self.version_id)
        self.slots: dict = defaultdict(list)
        for op in sorted(ops, key=lambda item: item["seq"]):
            self.slots[(op["day"], op["at"])].append(op)
        store.register_ops(ops)
        self._now_virtual = virtual_utc(self.d0, 0, "00:00", self.offset)

    # ------------------------------------------------------------------ slots
    def slot_keys(self) -> list:
        return sorted(self.slots)

    def run_slot(self, day: int, at: str) -> dict:
        self._now_virtual = virtual_utc(self.d0, day, at, self.offset)
        behind = self.store.pending_before(day, at)
        if behind:
            raise HarnessError(f"slot {day} {at} fora de ordem: ops anteriores nao concluidas {behind[:5]}")
        harness: list = []
        executed = 0
        for unit in self._units(self.slots.get((day, at), [])):
            todo = [op for op in unit if self.store.op_status(op["seq"]) not in TERMINAL]
            harness.extend(op["seq"] for op in todo if op["op"] in HARNESS_OPS)
            todo = [op for op in todo if op["op"] not in HARNESS_OPS]
            if not todo:
                continue
            if len(todo) > 1 and todo[0].get("group"):
                outcomes = run_concurrently([lambda item=item: self._run_op(item) for item in todo], self.clock)
                self.evidence.append({
                    "kind": "concurrency", "day": day, "at": at, "group": todo[0]["group"], "mode": todo[0].get("mode"),
                    "seqs": [item["seq"] for item in todo],
                    "t_start": [round(o["t_start"], 6) for o in outcomes], "t_end": [round(o["t_end"], 6) for o in outcomes]})
            else:
                for item in todo:
                    self._run_op(item)
            executed += len(todo)
        return {"day": day, "at": at, "executed": executed, "harness_ops": harness, "counts": self.store.counts()}

    @staticmethod
    def _units(ops: list) -> list:
        grouped: dict = {}
        order: list = []
        for op in ops:
            key = op.get("group") or f"seq:{op['seq']}"
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(op)
        return [grouped[key] for key in order]

    def mark_harness_done(self, seq: int, note: str = "") -> None:
        self.store.mark(seq, DONE, note=note or "harness")
        self.evidence.append({"kind": "harness_op", "seq": seq, "note": note, "t_real": self.clock()})

    # --------------------------------------------------------------- execucao
    def _run_op(self, op: dict) -> dict:
        try:
            recovering = self.store.op_status(op["seq"]) == SENT
            return self._execute(op, recovering)
        except (HarnessError, AllowlistViolation, LoginFailed) as error:
            self.store.mark(op["seq"], FAILED, note=f"{type(error).__name__}: {error}")
            if isinstance(error, HarnessError):
                raise
            raise HarnessError(f"{type(error).__name__}: {error}") from error

    def _execute(self, op: dict, recovering: bool) -> dict:
        name = op["op"]
        spec = build_request(op, self.ctx)
        self.store.mark(op["seq"], SENT)
        if recovering and name in ("create_receivable", "decide_mark_overdue", "decide_mark_paid", "change_due_date"):
            return self._resolve(op, spec)
        try:
            response = self._call(op, spec)
        except Ambiguous as ambiguity:
            self._note(op, "ambiguous", detail=ambiguity.detail)
            return self._resolve(op, spec)
        return self._finish(op, response, "done")

    def _resolve(self, op: dict, spec) -> dict:
        name = op["op"]
        if name == "create_receivable":
            verdict, account_id = reconcile_create_receivable(
                self._accounts_page(op), op, self.d0, self.store.mapped_account_ids(),
                self.params["reconcile_create_receivable"], self.sleep,
                log=lambda record: self._note(op, **record))
            if verdict == ADOPT:
                self.store.set_ref(op["rec_ref"], {"account_id": account_id})
                self.store.set_due(op["rec_ref"], int(op["vencimento_day"]))
                return self._finish(op, None, "reconciled", extra={"adopted_account_id": account_id})
            assert verdict == ABSENT
            try:
                response = self._call(op, spec, once=True)
            except Ambiguous as second:
                raise HarnessError(f"segundo resultado ambiguo em create_receivable (seq {op['seq']}): {second.detail}")
            return self._finish(op, response, "done", extra={"resent_after_stable_absence": True})
        if name in ("decide_mark_overdue", "decide_mark_paid"):
            current = self._approval_status(op)
            if reconcile_decision(current, DECISION_VALUE[op["decision"]]) == "reconciled":
                return self._finish(op, None, "reconciled", extra={"approval_status": current})
            try:
                response = self._call(op, spec, once=True)
            except Ambiguous as second:
                raise HarnessError(f"segundo resultado ambiguo em {name} (seq {op['seq']}): {second.detail}")
            return self._finish(op, response, "done", extra={"resent_after_pending": True})
        if name == "change_due_date":
            target = iso_day(self.d0, int(op["vencimento_day"]))
            current = self._account_due(op)
            if current == target:
                reconcile_due(current, target)
                previous = self.store.get_due(op["rec_ref"])
                self.store.set_due(op["rec_ref"], int(op["vencimento_day"]))
                return self._finish(op, None, "reconciled", extra={
                    "accepted_change": previous != int(op["vencimento_day"]), "previous_due_day": previous,
                    "new_due_day": int(op["vencimento_day"])})
            try:
                response = self._call(op, spec, once=True)
            except Ambiguous as second:
                raise HarnessError(f"segundo resultado ambiguo em change_due_date: {second.detail}")
            return self._finish(op, response, "done")
        raise HarnessError(f"{name}: tentativas esgotadas sem resposta (seq {op['seq']})")

    # ------------------------------------------------------------------- HTTP
    def _call(self, op: dict, spec, once: bool = False):
        attempts = 1 if (once or not spec.retry_safe) else int(self.params["retry"]["max_attempts"])
        last: Ambiguous | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._send(op, spec, attempt)
            except Ambiguous as ambiguity:
                last = ambiguity
                if attempt < attempts:
                    self.sleep(float(self.params["retry"]["backoff_s"]))
        assert last is not None
        raise last

    def _send(self, op: dict, spec, attempt: int):
        persona = op["actor"]
        relogged = False
        while True:
            cookie = self.sessions.cookie_for(persona, self._now_virtual)
            started = self.clock()
            try:
                response = self.http.request(spec.method, spec.path, spec.body, spec.headers, cookie)
            except TransportError as error:
                self._log_http(op, spec, attempt, None, f"transport:{error}", started)
                raise Ambiguous(f"transport:{error}")
            self._log_http(op, spec, attempt, response.status, response.json(), started)
            if response.status == 401 and not relogged:
                relogged = True
                self.sessions.invalidate(persona)
                continue
            if response.status >= 500:
                raise Ambiguous(f"status {response.status}", response.status)
            return response

    def _get(self, op: dict, path: str):
        persona = op["actor"]
        cookie = self.sessions.cookie_for(persona, self._now_virtual)
        try:
            return self.http.request("GET", path, cookie=cookie)
        except TransportError as error:
            raise HarnessError(f"leitura de reconciliacao falhou: {error}") from error

    def _accounts_page(self, op: dict):
        cliente = quote(op["cliente"], safe="")

        def get_page(skip: int, limit: int):
            response = self._get(op, f"/accounts/?cliente={cliente}&skip={skip}&limit={limit}")
            return response.status, response.json()

        return get_page

    def _approval_status(self, op: dict):
        approval = self.ctx.ref(op["ref"])["approval_id"]
        response = self._get(op, f"/approvals/{approval}")
        body = response.json()
        if response.status != 200 or not isinstance(body, dict):
            raise HarnessError(f"leitura da aprovacao {approval} inconclusiva (status={response.status})")
        return body["request"]["status"]

    def _account_due(self, op: dict):
        account = self.ctx.account_id(op["rec_ref"])
        response = self._get(op, f"/accounts/{account}")
        body = response.json()
        if response.status != 200 or not isinstance(body, dict):
            raise HarnessError(f"leitura da conta {account} inconclusiva (status={response.status})")
        return body.get("vencimento")

    # --------------------------------------------------------------- evidencia
    def _base(self, op: dict) -> dict:
        record = {"seq": op["seq"], "day": op["day"], "at": op["at"], "op": op["op"], "actor": op["actor"]}
        for key in ("ref", "rec_ref", "group", "mode"):
            if key in op:
                record[key] = op[key]
        return record

    def _log_http(self, op, spec, attempt, status, response, started) -> None:
        record = self._base(op)
        record.update({"kind": "http", "attempt": attempt, "method": spec.method, "path": spec.path,
                       "request": spec.body, "idempotency_key": spec.headers.get("Idempotency-Key"),
                       "status": status, "response": response, "t_start": round(started, 6),
                       "t_end": round(self.clock(), 6)})
        self.evidence.append(record)

    def _note(self, op: dict, kind: str = "note", **fields) -> None:
        record = self._base(op)
        record.update({"kind": kind, "t_real": round(self.clock(), 6)})
        record.update({k: v for k, v in fields.items() if k != "kind"})
        self.evidence.append(record)

    def _finish(self, op: dict, response, outcome: str, extra: dict | None = None) -> dict:
        status = None if response is None else response.status
        body = None if response is None else response.json()
        notes = effects(op, status, body, self.ctx) if response is not None else {}
        record = self._base(op)
        record.update({"kind": "op_result", "http_status": status, "outcome": outcome,
                       "duplicate": body.get("duplicate") if isinstance(body, dict) else None,
                       "t_real": round(self.clock(), 6)})
        record.update(notes)
        record.update(extra or {})
        self.evidence.append(record)
        self.store.mark(op["seq"], DONE if outcome == "done" else RECONCILED, http_status=status)
        return record
