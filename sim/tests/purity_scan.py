"""
Gate ESTATICO heuristico de ausencia de efeito colateral nas leituras do
Collector (Design Freeze V1.1 E-3.3). Le o CODIGO do backend como TEXTO/AST
(nunca o importa) e segue o grafo de chamadas a partir do handler da rota.

LIMITE ASSUMIDO (PO): isto e uma heuristica defensiva, NAO uma prova formal de
pureza transacional. Chamadas por objetos nao resolvidos (ex.: `self.repo.x()`)
nao sao seguidas. O DR-2 live continua obrigatorio.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parents[2] / "backend" / "app"
DB_RECEIVER_HINTS = ("db", "session")
WRITE_ATTRS_DB = {"commit", "flush", "add", "add_all", "delete", "merge", "bulk_save_objects",
                  "bulk_insert_mappings", "rollback", "execute_write"}
WRITE_ATTRS_ANY = {"persist"}

HANDLERS = {
    "account": ("api/routes/accounts.py", "get_account"),
    "accounts_scan": ("api/routes/accounts.py", "list_accounts"),
    "classification": ("api/routes/accounts.py", "get_account_classification"),
    "brain": ("api/routes/brain.py", "list_knowledge"),
    "memories": ("api/routes/memory.py", "recall_memories"),
    "mark_overdue_eligibility": ("api/routes/mark_overdue_recommendation.py",
                                 "get_account_mark_overdue_eligibility"),
    "human_escalation_eligibility": ("api/routes/human_escalation.py",
                                     "get_account_human_escalation_eligibility"),
    "approvals": ("api/routes/approvals.py", "list_approval_requests"),
    "approval": ("api/routes/approvals.py", "get_approval_request"),
    "work_item": ("api/routes/work.py", "get_work_item"),
    "observations": ("api/routes/work.py", "list_escalation_observations"),
    "work_items": ("api/routes/work.py", "list_work_items"),
    "outcome": ("api/routes/outcome.py", "get_account_outcome_episode"),
    "auth_me": ("api/routes/auth.py", "me"),
}
CONTROL_NEGATIVE = ("api/routes/nba_policy.py", "get_account_nba_decision")     # DEVE ser detectado


class Index:
    def __init__(self) -> None:
        self.modules: dict = {}
        for path in BACKEND_APP.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(BACKEND_APP).with_suffix("")
            name = "app." + ".".join(rel.parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            self.modules[name] = {"path": str(path.relative_to(BACKEND_APP)).replace("\\", "/"), "tree": tree,
                                  "funcs": {}, "classes": {}, "imports": {}}
            info = self.modules[name]
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info["funcs"][node.name] = node
                elif isinstance(node, ast.ClassDef):
                    info["classes"][node.name] = {m.name: m for m in node.body
                                                  if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
                elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                    for alias in node.names:
                        info["imports"][alias.asname or alias.name] = (node.module, alias.name)

    def by_path(self, rel: str) -> str:
        return next(name for name, info in self.modules.items() if info["path"] == rel)

    def resolve_name(self, module: str, name: str):
        info = self.modules[module]
        if name in info["funcs"]:
            return module, None, info["funcs"][name]
        if name in info["imports"]:
            target_module, target = info["imports"][name]
            if target_module in self.modules:
                target_info = self.modules[target_module]
                if target in target_info["funcs"]:
                    return target_module, None, target_info["funcs"][target]
        return None

    def resolve_class(self, module: str, name: str):
        info = self.modules[module]
        if name in info["classes"]:
            return module, name
        if name in info["imports"]:
            target_module, target = info["imports"][name]
            if target_module in self.modules and target in self.modules[target_module]["classes"]:
                return target_module, target
        return None


def _receiver_text(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def scan(index: Index, rel_path: str, handler: str, max_depth: int = 4, visited_out: set | None = None) -> list:
    """Achados `(funcao, chamada)` no grafo de chamadas do handler."""
    module = index.by_path(rel_path)
    start = index.modules[module]["funcs"][handler]
    findings: list = []
    visited: set = set()
    queue = [(module, None, start, 0)]
    while queue:
        mod, cls, func, depth = queue.pop(0)
        key = (mod, cls, func.name)
        if key in visited:
            continue
        visited.add(key)
        qualified = f"{mod}.{cls + '.' if cls else ''}{func.name}"
        annotations = {}
        for arg in func.args.args + func.args.kwonlyargs:
            if arg.annotation is not None:
                annotations[arg.arg] = _receiver_text(arg.annotation).strip("'\" ").split("[")[0].split("|")[0].strip()
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Attribute):
                receiver = _receiver_text(target.value)
                if target.attr in WRITE_ATTRS_ANY or (
                        target.attr in WRITE_ATTRS_DB and any(hint in receiver.lower() for hint in DB_RECEIVER_HINTS)):
                    findings.append((qualified, f"{receiver}.{target.attr}()"))
            if depth >= max_depth:
                continue
            resolved = None
            if isinstance(target, ast.Name):
                found = index.resolve_name(mod, target.id)
                if found:
                    resolved = found
            elif isinstance(target, ast.Attribute):
                base = target.value
                class_name = None
                if isinstance(base, ast.Name):
                    class_name = annotations.get(base.id) or (base.id if base.id[:1].isupper() else None)
                    if base.id == "self" and cls:
                        class_name = cls
                elif isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                    class_name = base.func.id
                if class_name:
                    located = index.resolve_class(mod if class_name == cls else mod, class_name)
                    if located and target.attr in index.modules[located[0]]["classes"][located[1]]:
                        resolved = (located[0], located[1], index.modules[located[0]]["classes"][located[1]][target.attr])
            if resolved:
                queue.append((resolved[0], resolved[1], resolved[2], depth + 1))
    if visited_out is not None:
        visited_out.update(f"{mod}.{cls + '.' if cls else ''}{name}" for mod, cls, name in visited)
    return findings
