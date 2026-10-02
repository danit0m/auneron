"""
OPS-CI-1.1 -- o release guard (scripts/release_guard.py) passa a viver
dentro da suite local. A causa-raiz do CI vermelho por 40 pushes foi o
Git rastrear um novo template legitimo (.env.recovery-smoke.example) sem
que nenhum teste local acusasse a divergencia com a allowlist.

Politica testada com entradas controladas (sem Git, sem working tree);
uma unica propriedade do repositorio le o INDICE do Git
(`git ls-files`), nunca arquivos untracked, e nao depende de
frontend/dist (que so existe apos build).
"""

import shutil
from pathlib import Path

import pytest

import scripts.release_guard as guard


ALLOWED_EXAMPLES = [
    "backend/.env.example",
    "backend/.env.recovery-smoke.example",
    "backend/.env.test.example",
    "frontend/.env.example",
]

VIOLATING_PATHS = [
    ".env",
    "backend/.env",
    "backend/.env.local",
    "backend/.env.test",
    "backend/.env.recovery-smoke",
    "backend/.env.recovery-smoke.example.bak",
    "other/.env.recovery-smoke.example",
    "backend/sub/.env.recovery-smoke.example",
    "frontend/.env.production",
    "frontend/.env.local",
]

NOT_ENV_FILES = [
    "backend/.envrc",
    "backend/app/env.py",
    "frontend/environment.ts",
    "README.md",
]


# ---------------------------------------------------------------------
# Politica de arquivos .env rastreados (pura)
# ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ALLOWED_EXAMPLES)
def test_allowed_example_env_files_pass(path: str) -> None:
    assert guard.check_tracked_env_files([path]) == []


@pytest.mark.parametrize("path", VIOLATING_PATHS)
def test_other_env_like_files_are_violations(path: str) -> None:
    violations = guard.check_tracked_env_files([path])

    assert len(violations) == 1
    assert path in violations[0]


@pytest.mark.parametrize("path", NOT_ENV_FILES)
def test_names_outside_the_env_rule_are_ignored(path: str) -> None:
    assert guard.check_tracked_env_files([path]) == []


def test_allowlist_is_exact_paths_not_patterns() -> None:
    assert guard.ALLOWED_ENV_FILES == set(ALLOWED_EXAMPLES)

    for path in guard.ALLOWED_ENV_FILES:
        assert not any(char in path for char in "*?[]")
        assert "\\" not in path
        assert Path(path).name.startswith(".env")


def test_empty_file_list_has_no_violations() -> None:
    assert guard.check_tracked_env_files([]) == []


# ---------------------------------------------------------------------
# Fonte do frontend e bundle (tmp_path, sem tocar o repositorio)
# ---------------------------------------------------------------------


def test_frontend_source_with_forbidden_token_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    source = tmp_path / "frontend" / "src" / "a.ts"
    source.parent.mkdir(parents=True)
    source.write_text("const k = import.meta.env.VITE_API_KEY;")

    violations = guard.check_frontend_source(["frontend/src/a.ts"])

    assert len(violations) == 1
    assert "VITE_API_KEY" in violations[0]


def test_frontend_source_clean_and_non_frontend_paths_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    clean = tmp_path / "frontend" / "src" / "b.ts"
    clean.parent.mkdir(parents=True)
    clean.write_text("export const ok = 1;")
    backend_file = tmp_path / "backend" / "x.py"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("VITE_API_KEY = 'fora do frontend'")

    assert (
        guard.check_frontend_source(
            ["frontend/src/b.ts", "backend/x.py"]
        )
        == []
    )


def test_bundle_with_server_side_token_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "app.js").write_text("var x='AUNERON_API_KEY';")
    (dist / "notes.txt").write_text("AUNERON_API_KEY")
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard, "DIST_ROOT", dist)

    violations = guard.check_frontend_bundle()

    assert len(violations) == 1
    assert "app.js" in violations[0]


def test_clean_bundle_passes_and_missing_dist_is_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    dist = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(guard, "DIST_ROOT", dist)

    assert len(guard.check_frontend_bundle()) == 1

    dist.mkdir(parents=True)
    (dist / "app.js").write_text("console.log('ok')")

    assert guard.check_frontend_bundle() == []


# ---------------------------------------------------------------------
# Propriedade do repositorio (indice do Git)
# ---------------------------------------------------------------------


def test_every_tracked_env_like_file_is_allowlisted() -> None:
    if shutil.which("git") is None or not (
        guard.REPO_ROOT / ".git"
    ).exists():
        pytest.skip("Git/.git indisponivel (copia sem repositorio).")

    violations = guard.check_tracked_env_files(
        guard.tracked_files()
    )

    assert violations == [], (
        "Arquivo(s) .env* rastreado(s) fora de ALLOWED_ENV_FILES -- "
        "revise e, se for um template seguro, adicione o caminho exato "
        "em scripts/release_guard.py: " + "; ".join(violations)
    )
