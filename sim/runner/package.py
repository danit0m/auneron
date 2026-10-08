"""
Pacote de evidencia selavel (SIM-1.5E B.7). `package_manifest.json` lista {path, size, sha256} de CADA arquivo;
`evidence_digest.txt` = sha256 de "<sha256> *<caminho>\\n" ordenado (mesma construcao dos digests de candidato).

O ledger e a ancora crescem DEPOIS do selo (PACKAGE_SEALED, COMPLETED, anotacoes), entao ficam fora do digest: o
manifesto registra a CAUDA do ledger no instante do selo e `verify` exige que o ledger atual a CONTENHA (prefixo
intacto). `evaluation.json` e `claim.json` sao pos-run: ficam fora do digest e sao conferidos a parte.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import ledger as ledger_mod
from sim.runner.errors import PackageError

MANIFEST_NAME = "package_manifest.json"
DIGEST_NAME = "evidence_digest.txt"
PACKAGE_SCHEMA = "sim.run.package.v1"
EXCLUDED = frozenset({MANIFEST_NAME, DIGEST_NAME, ledger_mod.LEDGER_NAME, ledger_mod.ANCHOR_NAME, "claim.json",
                      "evaluation.json", "RUN.LOCK"})
REQUIRED_PATHS = ("run_id.json", "rim.json")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_entries(run_dir) -> list:
    root = Path(run_dir)
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in EXCLUDED or ".." in rel.split("/"):
            continue
        entries.append({"path": rel, "size": path.stat().st_size, "sha256": _sha_file(path)})
    return entries


def digest_of(entries: list) -> str:
    text = "".join(f"{e['sha256']} *{e['path']}\n" for e in sorted(entries, key=lambda e: e["path"]))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seal(run_dir, *, run_id: str, ledger_tail: dict, partial: bool = False) -> str:
    root = Path(run_dir)
    if (root / MANIFEST_NAME).exists():
        raise PackageError("pacote ja selado")
    entries = list_entries(root)
    present = {e["path"] for e in entries}
    absent = [p for p in REQUIRED_PATHS if p not in present]
    if absent and not partial:
        raise PackageError(f"pacote sem arquivos obrigatorios: {absent}")
    digest = digest_of(entries)
    manifest = {"schema": PACKAGE_SCHEMA, "run_id": run_id, "partial": bool(partial), "ledger_tail_at_seal": ledger_tail,
                "files": entries, "digest": digest}
    (root / MANIFEST_NAME).write_bytes(canonical_bytes(manifest))
    (root / DIGEST_NAME).write_text(digest + "\n", encoding="utf-8", newline="\n")
    for name in (MANIFEST_NAME, DIGEST_NAME):
        try:
            os.chmod(root / name, 0o444)
        except OSError:
            pass
    return digest


def verify(run_dir, *, expected_digest: str | None = None) -> dict:
    """-> {ok, problems, digest}. Nunca levanta por conteudo adulterado: devolve os problemas."""
    root = Path(run_dir)
    problems: list = []
    manifest_path, digest_path = root / MANIFEST_NAME, root / DIGEST_NAME
    if not manifest_path.exists() or not digest_path.exists():
        return {"ok": False, "problems": ["pacote nao selado (manifesto ou digest ausente)"], "digest": None}
    try:
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    except ValueError:
        return {"ok": False, "problems": ["manifesto ilegivel"], "digest": None}
    recorded = digest_path.read_text(encoding="utf-8").strip()
    if manifest.get("digest") != recorded:
        problems.append("evidence_digest.txt difere do digest do manifesto")
    if digest_of(manifest.get("files", [])) != manifest.get("digest"):
        problems.append("o digest do manifesto nao corresponde a sua lista de arquivos")
    if expected_digest is not None and recorded != expected_digest:
        problems.append("digest difere do registrado no registro do host")
    current = {e["path"]: e for e in list_entries(root)}
    listed = {e["path"]: e for e in manifest.get("files", [])}
    for path, entry in listed.items():
        if ".." in path.split("/") or path.startswith("/"):
            problems.append(f"caminho inseguro no manifesto: {path}")
        elif path not in current:
            problems.append(f"arquivo ausente: {path}")
        elif current[path]["sha256"] != entry["sha256"] or current[path]["size"] != entry["size"]:
            problems.append(f"arquivo alterado: {path}")
    for path in sorted(set(current) - set(listed)):
        problems.append(f"arquivo nao listado no manifesto: {path}")
    tail = manifest.get("ledger_tail_at_seal") or {}
    try:
        records = ledger_mod.read_events(root / ledger_mod.LEDGER_NAME)
    except Exception as error:                                       # noqa: BLE001 - qualquer falha do ledger e problema
        records = []
        problems.append(f"ledger ilegivel: {error}")
    if tail.get("seq", 0) > 0:
        if len(records) < tail["seq"] or records[tail["seq"] - 1]["hash"] != tail.get("hash"):
            problems.append("o ledger atual nao contem a cauda registrada no selo (truncado ou reescrito)")
    return {"ok": not problems, "problems": problems, "digest": recorded}


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))
