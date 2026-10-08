"""
Digest FINAL dos resultados (emenda B do PO): `evaluation.json`, `surprises.json` e `claim.json` ficam fora do digest do
pacote (que nao muda), mas ganham um digest proprio, VINCULADO ao ledger e ao digest do pacote.

* `results_manifest.json` lista {path, size, sha256} dos tres arquivos + o digest do pacote + a cauda do ledger no selo;
* `results_digest.txt` = sha256 de "<sha256> *<caminho>\\n" ordenado + linha `package=<digest do pacote>`;
* o evento `EVALUATED` do ledger grava o digest e o sha256 de cada arquivo (o ledger e encadeado e ancorado, e a cauda
  vai ao registro do host). Alterar QUALQUER resultado, o manifesto, o digest ou o ledger e detectado por `verify_results`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.runner import ledger as ledger_mod
from sim.runner import package
from sim.runner.errors import PackageError

RESULT_FILES = ("evaluation.json", "surprises.json", "claim.json")
MANIFEST_NAME = "results_manifest.json"
DIGEST_NAME = "results_digest.txt"
SCHEMA = "sim.run.results.v1"


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries(run_dir) -> list:
    root = Path(run_dir)
    out = []
    for name in RESULT_FILES:
        path = root / name
        if not path.is_file():
            raise PackageError(f"resultado ausente: {name}")
        out.append({"path": name, "size": path.stat().st_size, "sha256": _sha_file(path)})
    return out


def digest_of(file_entries: list, package_digest: str) -> str:
    text = "".join(f"{e['sha256']} *{e['path']}\n" for e in sorted(file_entries, key=lambda e: e["path"]))
    text += f"package={package_digest}\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seal_results(run_dir, *, ledger, extra_payload: dict | None = None) -> dict:
    """Grava o manifesto e o digest dos resultados e anexa `EVALUATED` ao ledger (uma unica vez)."""
    root = Path(run_dir)
    if (root / MANIFEST_NAME).exists():
        raise PackageError("resultados ja selados")
    package_manifest = json.loads((root / package.MANIFEST_NAME).read_bytes().decode("utf-8"))
    package_digest = package_manifest["digest"]
    listed = entries(root)
    digest = digest_of(listed, package_digest)
    manifest = {"schema": SCHEMA, "run_id": package_manifest["run_id"], "package_digest": package_digest, "files": listed,
                "digest": digest, "ledger_tail_before_evaluated": ledger.tail}
    (root / MANIFEST_NAME).write_bytes(canonical_bytes(manifest))
    (root / DIGEST_NAME).write_text(digest + "\n", encoding="utf-8", newline="\n")
    payload = {"results_digest": digest, "package_digest": package_digest, "files": {e["path"]: e["sha256"] for e in listed},
               **(extra_payload or {})}
    ledger.append("EVALUATED", payload)
    return {"digest": digest, "package_digest": package_digest, "files": {e["path"]: e["sha256"] for e in listed}}


def verify_results(run_dir, *, registry_digest: str | None = None) -> dict:
    """-> {ok, problems, digest}. Reconfere arquivos, manifesto, digest, evento `EVALUATED`, ledger e selo do pacote."""
    root = Path(run_dir)
    problems: list = []
    try:
        ledger = ledger_mod.RunLedger(root)
        ledger.verify()
        records = [r for r in ledger.events() if r["event"] == "EVALUATED"]
    except Exception as error:                                                     # noqa: BLE001 - qualquer falha e problema
        return {"ok": False, "problems": [f"ledger invalido: {error}"], "digest": None}
    if len(records) != 1:
        return {"ok": False, "problems": [f"esperado 1 evento EVALUATED, ha {len(records)}"], "digest": None}
    recorded = records[0]["payload"]
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_bytes().decode("utf-8"))
        text = (root / DIGEST_NAME).read_text(encoding="utf-8").strip()
        current = entries(root)
    except (OSError, ValueError, PackageError) as error:
        return {"ok": False, "problems": [f"resultados ilegiveis: {error}"], "digest": None}
    if text != recorded["results_digest"]:
        problems.append("results_digest.txt difere do digest gravado no ledger")
    if manifest.get("digest") != recorded["results_digest"]:
        problems.append("results_manifest.json difere do digest gravado no ledger")
    if digest_of(current, manifest.get("package_digest", "")) != recorded["results_digest"]:
        problems.append("os arquivos atuais nao produzem o digest gravado (resultado alterado)")
    for entry in current:
        if recorded["files"].get(entry["path"]) != entry["sha256"]:
            problems.append(f"{entry['path']}: sha256 difere do ledger")
    if registry_digest is not None and registry_digest != recorded["results_digest"]:
        problems.append("digest difere do registrado no registro do host")
    pkg = package.verify(root)
    if not pkg["ok"]:
        problems.append("o selo do pacote original foi quebrado: " + "; ".join(pkg["problems"]))
    elif pkg["digest"] != recorded["package_digest"]:
        problems.append("o digest do pacote difere do vinculado nos resultados")
    return {"ok": not problems, "problems": problems, "digest": recorded["results_digest"]}
