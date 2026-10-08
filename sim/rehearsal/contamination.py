"""
Controles de contaminacao entre a fixture `[SIM-HARNESS]` e o NH (SIM-1.5F A.9):

* formato dos marcadores (C-1) e DISJUNCAO fixture x outra agenda (C-2);
* varredura de marcadores em arvores de arquivos (texto, jsonl, `.gz`, SQLite) (C-7) com controle positivo;
* juiz puro do check P14 do preflight do NH (C-6): fatos de contaminacao do banco/volumes.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from sim.rehearsal.schedule import DOMAIN
from sim.rehearsal.schedule import MARKER

MARKERS = (MARKER, DOMAIN)                         # comparacao SEM distinguir maiusculas
CLIENT_PREFIX = f"[{MARKER}] "
REF_PREFIX = "SH-"
SCENARIO_FRAGMENT = "sim-harness-sr1"
IDENTITY_FIELDS = ("cliente", "email", "whatsapp", "rec_ref", "ref", "idempotency_key")


def format_problems(ops: list) -> list:
    """Toda operacao da fixture carrega os marcadores esperados (C-1)."""
    problems = []
    for op in ops:
        if op["op"] == "restart_backend":
            continue
        if op["op"] == "create_receivable":
            if not op["cliente"].startswith(CLIENT_PREFIX) or not op["email"].endswith("@" + DOMAIN):
                problems.append(f"seq {op['seq']}: cliente/e-mail sem marcador")
            if not op["whatsapp"].startswith("+55 00 9999-"):
                problems.append(f"seq {op['seq']}: whatsapp fora do padrao")
        for field in ("rec_ref", "ref"):
            if op.get(field) and not op[field].startswith(REF_PREFIX):
                problems.append(f"seq {op['seq']}: {field} sem prefixo {REF_PREFIX}")
        if op.get("idempotency_key") and SCENARIO_FRAGMENT not in op["idempotency_key"]:
            problems.append(f"seq {op['seq']}: chave de idempotencia sem o cenario da fixture")
    return problems


def identities(ops: list) -> set:
    out = set()
    for op in ops:
        for field in IDENTITY_FIELDS:
            if op.get(field):
                out.add((field, op[field]))
    return out


def disjointness(fixture_ops: list, other_ops: list) -> list:
    """Interseccao de identidades (cliente, e-mail, whatsapp, rec_ref, ref, chave). Vazia = disjuntas (C-2)."""
    return sorted(identities(fixture_ops) & identities(other_ops))


def scan_bytes(data: bytes, markers: tuple = MARKERS) -> list:
    lowered = data.lower()
    return [m for m in markers if m.lower().encode("utf-8") in lowered]


def scan_tree(root, markers: tuple = MARKERS, *, allow_paths: tuple = ()) -> dict:
    """Varre TODOS os arquivos (decodifica `.gz`; SQLite e lido como bytes). `allow_paths`: caminhos relativos permitidos
    (ex.: o `growth_model.json`, unica linhagem autorizada). -> {"files": n, "hits": [{"path","markers"}]}"""
    root = Path(root)
    hits, count = [], 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        count += 1
        rel = path.relative_to(root).as_posix()
        if rel in allow_paths:
            continue
        data = path.read_bytes()
        if path.suffix == ".gz":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        found = scan_bytes(data, markers) + scan_bytes(rel.encode("utf-8"), markers)
        if found:
            hits.append({"path": rel, "markers": sorted(set(found))})
    return {"files": count, "hits": hits}


def judge_p14(facts: dict) -> list:
    """P14 do preflight do NH. Fato ausente = reprovado. -> lista de falhas (vazia = limpo)."""
    required = ("volumes_created_after_previous_terminal", "driver_state_present", "collector_state_present",
                "business_rows", "marker_rows", "previous_runs_non_terminal")
    missing = [k for k in required if k not in facts]
    if missing:
        return [f"fatos P14 ausentes: {missing}"]
    fails = []
    if facts["volumes_created_after_previous_terminal"] is not True:
        fails.append("volumes anteriores ao termino do ensaio (stack nao e novo)")
    if facts["driver_state_present"] or facts["collector_state_present"]:
        fails.append("estado do Driver/Collector ja existe antes do D0")
    if facts["business_rows"] != 0:
        fails.append(f"{facts['business_rows']} linhas de negocio antes do D0")
    if facts["marker_rows"] != 0:
        fails.append(f"{facts['marker_rows']} linhas com marcadores da fixture no produto")
    if facts["previous_runs_non_terminal"]:
        fails.append("ha run anterior nao terminal no registro do host")
    return fails
