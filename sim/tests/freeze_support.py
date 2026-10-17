"""
SIM-FREEZE-IMPL-01 -- contrato de congelamento do SIM, sobre OBJETOS do Git.

Separa tres responsabilidades que antes dependiam da mesma igualdade absoluta
entre o checkout corrente e um commit historico:

1. Proveniencia historica imutavel: arvores de `backend/` e `frontend/` dos
   commits congelados sao literais (nunca recalculadas do checkout atual).
2. Escopo do SIM: commits que tocam o SIM nao tocam nada fora dele (inclui
   commits mistos, merges de varios pais, merges "malignos" e renomeacoes), e
   o SIM e aditivo sobre a baseline. Evolucao independente do backend, em
   commits proprios, NAO e violacao.
3. Identidade estrita produto x imagens: continua exigida SO nos gates de
   execucao viva (`sim/live/gates.py` LIVE-PREP e `sim/runner/preflight.py`
   P1/P2/P5), que este modulo nao altera.

Nada aqui escreve no repositorio sob teste: so le objetos do Git.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Commits historicos congelados (referencias explicitas, nunca derivadas de HEAD).
HISTORICAL_COMMITS: Mapping[str, str] = {
    "sim14_baseline": "ba7d70d079c6d649621d4a5fa9c34308793840c5",
    "lab_target": "d100ce23462989dd1047ceabf0a30dc7e5b0d62d",
    "sim15_base": "248b2fe62781103df449e7347f81dd44871365b9",
    "sim15e_base": "0d353766fbcbb9a85dbbfb7eab3cf36b035649b6",
}

# Arvores do produto contidas nas imagens provadas (git archive d100ce2).
# Sao FATOS sobre commits imutaveis; nao existe forma de "atualiza-las" sem
# que os testes de proveniencia falhem.
PINNED_PRODUCT_TREES: Mapping[str, str] = {
    "backend": "032d126bf9d04400fde47289a10e7ea38940d0df",
    "frontend": "eb48ab57ffacceaf459861f982b0ece5067ca6ad",
}

LAB_ALEMBIC_HEAD = "5c1e7a90d2b4"
LAB_IMAGE_NAMES: Mapping[str, str] = {
    "base": "auneron-sim-base",
    "backend": "auneron-sim-backend",
    "postgres": "auneron-sim-postgres",
}

SIM_PATHSPEC = ("sim", ".github/workflows/sim-ci.yml")
SIM_OWNED = re.compile(r"^(sim/|\.github/workflows/sim-ci\.yml$)")
PRODUCT_OWNED = re.compile(r"^(backend|frontend)/")


class FreezeUnavailable(RuntimeError):
    """Historia necessaria ausente num ambiente que NAO pode pular (CI)."""


@dataclass(frozen=True)
class Violation:
    kind: str
    commit: str
    paths: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.kind}@{self.commit[:12]}: {', '.join(self.paths)}"


def git(repo: Path | str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def commit_available(repo: Path | str, sha: str) -> bool:
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def require_commit(repo: Path | str, sha: str, *, ci: bool | None = None) -> bool:
    """True se presente. Ausente: False (o chamador pula) fora do CI; no CI
    pular em silencio e proibido -- levanta FreezeUnavailable."""
    if commit_available(repo, sha):
        return True

    if in_ci() if ci is None else ci:
        raise FreezeUnavailable(f"commit congelado ausente do historico: {sha}")

    return False


def tree_id(repo: Path | str, commit: str, path: str) -> str:
    return git(repo, "rev-parse", f"{commit}:{path}").stdout.strip()


def provenance_violations(
    repo: Path | str,
    commits: Mapping[str, str] = HISTORICAL_COMMITS,
    pins: Mapping[str, str] = PINNED_PRODUCT_TREES,
) -> list[str]:
    """Cada commit congelado deve conter EXATAMENTE as arvores pinadas."""
    problems: list[str] = []

    for name, sha in commits.items():
        for path, expected in pins.items():
            actual = tree_id(repo, sha, path)

            if actual != expected:
                problems.append(f"{name}:{path} {actual or 'ausente'} != {expected}")

    return problems


def _split_z(output: str) -> list[str]:
    return [item for item in output.split("\0") if item]


def commit_paths(repo: Path | str, sha: str) -> list[str]:
    """Caminhos de um commit comum; --no-renames expoe origem E destino."""
    result = git(
        repo, "diff-tree", "-r", "--no-commit-id", "--no-renames", "--root",
        "--name-only", "-z", sha,
    )
    return _split_z(result.stdout)


def merge_evil_paths(repo: Path | str, sha: str) -> list[str]:
    """Caminhos em que o resultado do merge difere de TODOS os pais."""
    result = git(
        repo, "diff-tree", "-r", "--cc", "--no-commit-id", "--no-renames",
        "--name-only", "-z", sha,
    )
    return _split_z(result.stdout)


def _rev_list(repo: Path | str, *args: str) -> list[str]:
    return git(repo, "rev-list", *args).stdout.split()


def mixed_commit_violations(
    repo: Path | str,
    base: str,
    head: str = "HEAD",
) -> list[Violation]:
    """
    Todo commit em base..head (inclusive os de ramos laterais trazidos por
    merges) que toque o SIM nao pode tocar nada fora dele. Merges: o resultado
    nao pode carregar conteudo proprio "maligno" fora do SIM quando o merge
    traz trabalho do SIM ou tambem altera o SIM.
    """
    violations: list[Violation] = []
    touches_sim: dict[str, bool] = {}

    for sha in _rev_list(repo, "--no-merges", "--reverse", f"{base}..{head}"):
        paths = commit_paths(repo, sha)
        sim = [p for p in paths if SIM_OWNED.match(p)]
        touches_sim[sha] = bool(sim)
        outside = [p for p in paths if not SIM_OWNED.match(p)]

        if sim and outside:
            product = [p for p in outside if PRODUCT_OWNED.match(p)]
            violations.append(
                Violation(
                    "sim_commit_touches_product" if product else "sim_commit_touches_outside",
                    sha,
                    tuple(outside),
                )
            )

    for sha in _rev_list(repo, "--merges", "--reverse", f"{base}..{head}"):
        evil = merge_evil_paths(repo, sha)

        if not evil:
            continue

        merged_in = _rev_list(repo, "--no-merges", sha, f"^{sha}^1")
        sim_involved = any(touches_sim.get(c) for c in merged_in) or any(
            SIM_OWNED.match(p) for p in evil
        )
        outside = [p for p in evil if not SIM_OWNED.match(p)]

        if sim_involved and outside:
            violations.append(Violation("evil_merge_touches_outside_sim", sha, tuple(outside)))

    return violations


def frozen_component_diff(
    repo: Path | str,
    base: str,
    paths: Sequence[str],
    *,
    cached: bool = True,
) -> list[str]:
    """Diff (arvore de trabalho e indice) de componentes congelados do SIM."""
    lines = git(repo, "diff", "--no-renames", "--name-status", base, "--", *paths).stdout.splitlines()

    if cached:
        lines += git(
            repo, "diff", "--cached", "--no-renames", "--name-status", base, "--", *paths
        ).stdout.splitlines()

    return list(dict.fromkeys(line for line in lines if line.strip()))


def additivity_violations(
    repo: Path | str,
    base: str,
    allowed: Sequence[str],
    *,
    head: str | None = None,
    historical_prefixes: Iterable[str] = (),
) -> list[str]:
    """
    O SIM e ADITIVO sobre `base` e confinado as regras `allowed`. head=None
    compara a arvore de trabalho (candidato ainda nao commitado); com head,
    compara commits. So o caminho do SIM e avaliado -- o backend evolui em
    seus proprios commits (ver mixed_commit_violations).
    """
    diff = _sim_diff(repo, base, head)
    problems = [f"nao aditivo: {line}" for line in diff if not line.startswith("A\t")]
    candidate = candidate_paths(repo, base, head=head, historical_prefixes=historical_prefixes)
    problems += [
        f"fora das regras autorizadas: {p}"
        for p in candidate
        if not any(re.match(rule, p) for rule in allowed)
    ]
    return problems


def _sim_diff(repo: Path | str, base: str, head: str | None) -> list[str]:
    args = ["diff", "--no-renames", "--name-status", base]

    if head is not None:
        args.append(head)

    return git(repo, *args, "--", *SIM_PATHSPEC).stdout.splitlines()


def candidate_paths(
    repo: Path | str,
    base: str,
    *,
    head: str | None = None,
    historical_prefixes: Iterable[str] = (),
) -> list[str]:
    """Caminhos do SIM ADICIONADOS desde `base` (e, sem head, tambem os nao rastreados)."""
    added = [line.split("\t", 1)[1] for line in _sim_diff(repo, base, head) if line.startswith("A\t")]

    if head is None:
        added += git(
            repo, "ls-files", "--others", "--exclude-standard", "--", *SIM_PATHSPEC
        ).stdout.splitlines()

    prefixes = tuple(historical_prefixes)
    return [p for p in added if not p.startswith(prefixes)] if prefixes else added


def product_divergence(repo: Path | str, commit: str, head: str = "HEAD") -> list[str]:
    """Caminhos do produto que diferem entre `commit` e `head` (gate vivo)."""
    return git(repo, "diff", "--no-renames", "--name-only", commit, head, "--", "backend", "frontend").stdout.split()


def lab_config_violations(config: Mapping) -> list[str]:
    """Coerencia da configuracao CONGELADA do laboratorio com os literais."""
    problems: list[str] = []
    target = config.get("target", {})
    commit = str(target.get("commit", ""))

    if commit != HISTORICAL_COMMITS["lab_target"]:
        problems.append(f"target.commit {commit!r} != commit congelado")

    if target.get("alembic_head") != LAB_ALEMBIC_HEAD:
        problems.append(f"target.alembic_head {target.get('alembic_head')!r} != {LAB_ALEMBIC_HEAD}")

    images = config.get("docker", {}).get("images", {})

    if dict(images) != dict(LAB_IMAGE_NAMES):
        problems.append(f"docker.images {dict(images)!r} != nomes congelados")

    return problems
