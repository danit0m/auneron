"""
Allowlist do Evidence Collector (Design Freeze V1.1 E-3.3): SOMENTE GET e
somente rotas cuja leitura nao grava nada no produto (heuristica estatica +
DR-2 live; o AST gate sozinho nao e prova de pureza).

`GET /recommendations/next-best-action/...` NAO esta aqui: cada chamada
persiste um NbaRecommendationSnapshot (evidencia do Outcome). Mutacoes de
`/brain` nunca entram (D-1.5-4).
"""

from __future__ import annotations

import re

ALLOWED_BASE_URLS = frozenset({"http://sim-backend:8000"})
_DATE = r"\d{4}-\d{2}-\d{2}"

# (nome, regex do path SEM query, chaves de query permitidas)
COLLECTOR_ROUTES = (
    ("account", r"/accounts/\d+", ()),
    ("accounts_scan", r"/accounts/", ("cliente", "skip", "limit")),
    ("classification", r"/accounts/\d+/classification", ()),
    ("brain", r"/brain/", ("account_id", "skip", "limit")),
    ("memories", r"/memories", ("scope_type", "account_id", "memory_key", "status", "limit", "cursor")),
    ("mark_overdue_eligibility", rf"/recommendations/mark-overdue/accounts/\d+/episodes/{_DATE}", ()),
    ("human_escalation_eligibility", rf"/recommendations/human-escalation/accounts/\d+/episodes/{_DATE}", ()),
    ("approvals", r"/approvals", ("limit", "after_id", "status", "risk_level")),
    ("approval", r"/approvals/\d+", ()),
    ("work_item", r"/work-items/\d+", ()),
    ("observations", r"/work-items/\d+/escalation-observations", ("limit", "after_id", "observation_type")),
    ("work_items", r"/work-items", ("scope_type", "account_id", "limit")),
    ("outcome", rf"/outcomes/accounts/\d+/episodes/{_DATE}", ()),
    ("auth_me", r"/auth/me", ()),
)
_COMPILED = tuple((name, re.compile(pattern), keys) for name, pattern, keys in COLLECTOR_ROUTES)

# Leituras que parecem inofensivas, mas ESCREVEM no produto.
KNOWN_WRITING_GETS = ("/recommendations/next-best-action/",)


class CapabilityViolation(RuntimeError):
    """Metodo/path/host fora da capability do Collector: HARNESS_ERROR."""


def split_path(path: str):
    base, _, query = path.partition("?")
    return base, [part.split("=", 1)[0] for part in query.split("&") if part]


def route_name(path: str) -> str:
    base, keys = split_path(path)
    for prefix in KNOWN_WRITING_GETS:
        if base.startswith(prefix):
            raise CapabilityViolation(f"leitura que persiste no produto, proibida ao Collector: {base}")
    for name, pattern, allowed in _COMPILED:
        if pattern.fullmatch(base):
            extra = [key for key in keys if key not in allowed]
            if extra:
                raise CapabilityViolation(f"query fora da allowlist: {extra} em {base}")
            return name
    raise CapabilityViolation(f"GET {base} fora da allowlist do Collector")
