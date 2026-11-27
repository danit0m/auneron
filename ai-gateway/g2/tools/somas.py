"""Gera SOMAS.txt (LF): SHA-256 de todos os arquivos do projeto (sem .venv, caches e resultados gerados)."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".tools", "__pycache__", ".pytest_cache"}
SKIP_FILES = {"SOMAS.txt", "mut_out.txt", "mut_err.txt", "mutation_results.json", "suite_run1.txt",
              "suite_run2.txt", "suite_run3.txt", "RELATORIO.md"}
lines = []
for p in sorted(ROOT.rglob("*")):
    if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.name not in SKIP_FILES:
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT).as_posix()}")
(ROOT / "SOMAS.txt").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))  # bytes: sempre LF
print(len(lines), "arquivos")
