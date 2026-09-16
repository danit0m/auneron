"""
PR-3 -- Legacy SQLite Migration Isolation V1. Testes obrigatorios do
Executable Contract (congelado com Tomaz em 16/09/2026): o
.dockerignore da raiz exclui os artefatos SQLite legados e o proprio
script da imagem de runtime; o guard de production aborta ANTES de
qualquer operacao de banco (SQLite ou PostgreSQL), provado por
side-effect, nao so por SystemExit; e development/test continuam
podendo executar a ferramenta exatamente como antes -- isolamos a
ferramenta de produção, nao destruimos sua capacidade de reprodução.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.migrate_sqlite_to_postgres as migrate_script


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------
# Packaging boundary -- .dockerignore da raiz
# ---------------------------------------------------------------------


def test_dockerignore_excludes_legacy_sqlite_artifacts_and_script() -> (
    None
):
    dockerignore_path = REPO_ROOT / ".dockerignore"
    assert dockerignore_path.is_file(), (
        "O .dockerignore precisa existir na RAIZ do repositório -- "
        "o build context real (docker-compose.prod.yml) é `context: ..`, "
        "não backend/."
    )

    content = dockerignore_path.read_text(encoding="utf-8")
    lines = {line.strip() for line in content.splitlines()}

    assert "**/*.db" in lines
    assert "**/*.sqlite" in lines
    assert "**/*.sqlite3" in lines
    assert (
        "backend/scripts/migrate_sqlite_to_postgres.py" in lines
    )


def test_dockerignore_lives_at_repo_root_not_inside_backend() -> None:
    assert not (
        REPO_ROOT / "backend" / ".dockerignore"
    ).exists(), (
        "Um .dockerignore dentro de backend/ nunca seria lido pelo "
        "Docker, já que o build context real é a raiz do repositório."
    )


# ---------------------------------------------------------------------
# Runtime boundary -- guard de production, provado por side-effect
# ---------------------------------------------------------------------


def test_production_guard_aborts_before_any_database_operation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        migrate_script.settings, "environment", "production"
    )
    monkeypatch.setattr(
        "sys.argv",
        ["migrate_sqlite_to_postgres.py", "--execute"],
    )

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "Nenhuma operação de banco (SQLite ou PostgreSQL) pode "
            "acontecer quando environment=='production' -- o guard "
            "deve abortar antes disso."
        )

    monkeypatch.setattr(
        migrate_script, "load_source_data", _forbidden
    )
    monkeypatch.setattr(migrate_script, "migrate", _forbidden)
    monkeypatch.setattr(
        migrate_script.engine, "connect", _forbidden
    )
    monkeypatch.setattr(migrate_script.engine, "begin", _forbidden)

    with pytest.raises(SystemExit) as excinfo:
        migrate_script.main()

    assert "production" in str(excinfo.value)


def test_guard_does_not_fire_outside_production(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        migrate_script.settings, "environment", "development"
    )
    monkeypatch.setattr(
        "sys.argv", ["migrate_sqlite_to_postgres.py"]
    )

    class _PastTheGuardSentinel(Exception):
        pass

    def _raise_sentinel():
        raise _PastTheGuardSentinel(
            "execução chegou além do guard de produção"
        )

    monkeypatch.setattr(
        migrate_script, "load_source_data", _raise_sentinel
    )

    with pytest.raises(_PastTheGuardSentinel):
        migrate_script.main()
