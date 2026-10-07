"""
Tabela EXATA de despacho (Design Freeze V1 sec. 5): op da agenda -> chamada
HTTP real. Funcao pura `build_request` (sem rede, sem estado alem das
referencias lidas) e `effects` (o que a resposta registra no StateStore).

`decision` da agenda ("approve"/"reject") e traduzido para o valor real do
produto ("approved"/"rejected"); o teste estatico confere contra o schema do
backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import timedelta

from sim.driver.errors import HarnessError
from sim.driver.errors import MissingRef

PUBLIC_OPS = (
    "create_receivable", "change_due_date", "consult_nba", "materialize_escalation", "record_assessment",
    "request_mark_paid", "decide_mark_paid", "execute_mark_paid",
    "materialize_mark_overdue", "decide_mark_overdue", "execute_mark_overdue", "restart_backend",
)
HARNESS_OPS = frozenset({"restart_backend"})
DECISION_VALUE = {"approve": "approved", "reject": "rejected"}
# Retry cego: SO onde o produto e idempotente por desenho (D-1.5-8).
RETRY_SAFE = frozenset({
    "change_due_date", "materialize_mark_overdue", "execute_mark_overdue", "request_mark_paid",
    "execute_mark_paid", "materialize_escalation", "record_assessment", "consult_nba",
})
NO_BLIND_RETRY = frozenset({"create_receivable", "decide_mark_overdue", "decide_mark_paid"})

# Estrategia de reconciliacao por op (Design Freeze V1.1 E-2 + V1 sec. 6).
RECONCILIATION = {
    "create_receivable": "scan_3x_paginated_then_adopt_or_single_resend",
    "change_due_date": "retry_then_read_and_compare",
    "decide_mark_overdue": "read_approval_status_then_resend_once",
    "decide_mark_paid": "read_approval_status_then_resend_once",
    "materialize_mark_overdue": "idempotent_retry",
    "execute_mark_overdue": "idempotent_retry",
    "request_mark_paid": "idempotent_retry_same_key",
    "execute_mark_paid": "idempotent_retry",
    "materialize_escalation": "idempotent_retry",
    "record_assessment": "idempotent_retry_same_key",
    "consult_nba": "idempotent_retry",
    "restart_backend": "harness_op",
}

# (op, metodo, template do path, persona) -- documentacao executavel da tabela
DISPATCH_TABLE = (
    ("create_receivable", "POST", "/accounts/", "actor"),
    ("change_due_date", "PUT", "/accounts/{account_id}", "actor"),
    ("materialize_mark_overdue", "POST",
     "/recommendations/mark-overdue/accounts/{account_id}/episodes/{due}/materialize", "actor"),
    ("decide_mark_overdue", "POST", "/approvals/{approval_id}/decision", "actor"),
    ("execute_mark_overdue", "POST",
     "/recommendations/mark-overdue/accounts/{account_id}/episodes/{due}/execute", "actor"),
    ("request_mark_paid", "POST", "/approvals/skill-executions/{version_id}", "actor"),
    ("decide_mark_paid", "POST", "/approvals/{approval_id}/decision", "actor"),
    ("execute_mark_paid", "POST", "/accounts/{account_id}/execute-mark-paid", "actor"),
    ("materialize_escalation", "POST",
     "/recommendations/human-escalation/accounts/{account_id}/episodes/{due}/materialize", "actor"),
    ("record_assessment", "POST", "/work-items/{work_item_id}/human-assessment", "actor"),
    ("consult_nba", "GET",
     "/recommendations/next-best-action/accounts/{account_id}/episodes/{due}", "actor"),
    ("restart_backend", "HARNESS", "restart do sim-backend (mecanismo S-9, executado pelo harness)", "harness"),
)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    body: dict | None = None
    headers: dict = field(default_factory=dict)
    retry_safe: bool = False


def iso_day(d0: date, day: int) -> str:
    return (d0 + timedelta(days=day)).isoformat()


class Context:
    """Visao de leitura do StateStore para montar requests."""

    def __init__(self, store, d0: date, version_id: int) -> None:
        self.store = store
        self.d0 = d0
        self.version_id = version_id

    def account_id(self, rec_ref: str) -> int:
        ref = self.store.get_ref(rec_ref)
        if not ref or "account_id" not in ref:
            raise MissingRef(f"rec_ref sem conta mapeada: {rec_ref}")
        return int(ref["account_id"])

    def ref(self, name: str) -> dict:
        value = self.store.get_ref(name)
        if value is None:
            raise MissingRef(f"ref sem mapeamento: {name}")
        return value


def _due(op: dict, ctx: Context) -> str:
    if "vencimento_day" in op:
        return iso_day(ctx.d0, int(op["vencimento_day"]))
    return ctx.ref(op["ref"])["due"]


def build_request(op: dict, ctx: Context) -> Request:
    name = op["op"]
    retry = name in RETRY_SAFE
    if name in HARNESS_OPS:
        raise HarnessError(f"{name} e op de harness: o Driver nao a despacha")
    if name == "create_receivable":
        body = {"cliente": op["cliente"], "email": op["email"], "whatsapp": op["whatsapp"],
                "valor": op["valor"], "vencimento": iso_day(ctx.d0, int(op["vencimento_day"]))}
        return Request("POST", "/accounts/", body, retry_safe=False)
    if name == "change_due_date":
        account = ctx.account_id(op["rec_ref"])
        return Request("PUT", f"/accounts/{account}", {"vencimento": iso_day(ctx.d0, int(op["vencimento_day"]))},
                       retry_safe=retry)
    if name == "materialize_mark_overdue":
        account = ctx.account_id(op["rec_ref"])
        return Request("POST", f"/recommendations/mark-overdue/accounts/{account}/episodes/{_due(op, ctx)}/materialize",
                       retry_safe=retry)
    if name == "execute_mark_overdue":
        account = ctx.account_id(op["rec_ref"])
        return Request("POST", f"/recommendations/mark-overdue/accounts/{account}/episodes/{_due(op, ctx)}/execute",
                       retry_safe=retry)
    if name in ("decide_mark_overdue", "decide_mark_paid"):
        decision = DECISION_VALUE.get(op["decision"])
        if decision is None:
            raise HarnessError(f"decisao desconhecida na agenda: {op['decision']}")
        approval = ctx.ref(op["ref"])["approval_id"]
        return Request("POST", f"/approvals/{approval}/decision", {"decision": decision}, retry_safe=False)
    if name == "request_mark_paid":
        account = ctx.account_id(op["rec_ref"])
        body = {"input_payload": {"account_id": account, "expected_status": op["expected_status"]}}
        return Request("POST", f"/approvals/skill-executions/{ctx.version_id}", body,
                       {"Idempotency-Key": op["idempotency_key"]}, retry_safe=retry)
    if name == "execute_mark_paid":
        account = ctx.account_id(op["rec_ref"])
        approval = ctx.ref(op["ref"])["approval_id"]
        return Request("POST", f"/accounts/{account}/execute-mark-paid",
                       {"approval_request_id": approval, "expected_status": op["expected_status"]}, retry_safe=retry)
    if name == "materialize_escalation":
        account = ctx.account_id(op["rec_ref"])
        return Request("POST",
                       f"/recommendations/human-escalation/accounts/{account}/episodes/{_due(op, ctx)}/materialize",
                       retry_safe=retry)
    if name == "record_assessment":
        work_item = ctx.ref(op["ref"])["work_item_id"]
        return Request("POST", f"/work-items/{work_item}/human-assessment", {"assessment_code": op["code"]},
                       {"Idempotency-Key": op["idempotency_key"]}, retry_safe=retry)
    if name == "consult_nba":
        account = ctx.account_id(op["rec_ref"])
        return Request("GET", f"/recommendations/next-best-action/accounts/{account}/episodes/{_due(op, ctx)}",
                       retry_safe=retry)
    raise HarnessError(f"op fora da tabela de despacho: {name}")


def effects(op: dict, status: int, body, ctx: Context) -> dict:
    """Registra no StateStore o que a resposta (2xx) produziu. Devolve notas
    para a evidencia (ex.: `accepted_change`)."""
    if not 200 <= status < 300 or not isinstance(body, dict):
        return {}
    store = ctx.store
    name = op["op"]
    if name == "create_receivable":
        store.set_ref(op["rec_ref"], {"account_id": int(body["id"])})
        store.set_due(op["rec_ref"], int(op["vencimento_day"]))
        return {"account_id": int(body["id"])}
    if name == "change_due_date":
        previous = store.get_due(op["rec_ref"])
        accepted = previous != int(op["vencimento_day"])
        store.set_due(op["rec_ref"], int(op["vencimento_day"]))
        return {"accepted_change": accepted, "previous_due_day": previous, "new_due_day": int(op["vencimento_day"])}
    if name == "materialize_mark_overdue":
        store.set_ref(op["ref"], {"kind": "mark_overdue", "rec_ref": op["rec_ref"],
                                  "approval_id": int(body["approval_request"]["request_id"]),
                                  "work_item_id": int(body["work_item"]["id"]),
                                  "due": iso_day(ctx.d0, int(op["vencimento_day"]))})
        return {"approval_id": int(body["approval_request"]["request_id"])}
    if name == "request_mark_paid":
        request = body["request"]
        if int(request["skill_version_id"]) != ctx.version_id or request["skill_key"] != "account.mark_paid":
            raise HarnessError("version_id/skill_key da aprovacao diverge do manifesto do lab (D-1.5-9)")
        due_day = store.get_due(op["rec_ref"])
        if due_day is None:
            raise HarnessError(f"vencimento corrente desconhecido para {op['rec_ref']}")
        store.set_ref(op["ref"], {"kind": "mark_paid", "rec_ref": op["rec_ref"],
                                  "approval_id": int(request["request_id"]), "due": iso_day(ctx.d0, due_day)})
        return {"approval_id": int(request["request_id"])}
    if name == "materialize_escalation":
        store.set_ref(op["ref"], {"kind": "escalation", "rec_ref": op["rec_ref"],
                                  "work_item_id": int(body["work_item"]["id"]),
                                  "due": iso_day(ctx.d0, int(op["vencimento_day"]))})
        return {"work_item_id": int(body["work_item"]["id"])}
    return {}
