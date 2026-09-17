"""
PR-4 -- testes de contrato do guard de restore/cleanup e da
disciplina de invocação de subprocess. Só lógica pura -- mockado, sem
Docker/DB real. O drill end-to-end (pg_dump/pg_restore reais, banco de
recovery real) fica fora do pytest -q, mesma disciplina já
estabelecida no PR-3 para o build Docker real.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

import scripts._postgres_recovery_common as common
from scripts._postgres_recovery_common import DatabaseIdentity
from scripts._postgres_recovery_common import RECOVERY_DATABASE
from scripts._postgres_recovery_common import RestoreTargetForbiddenError
from scripts._postgres_recovery_common import assert_safe_cleanup_target
from scripts._postgres_recovery_common import assert_safe_restore_target
from scripts._postgres_recovery_common import normalized_identity


CONFIGURED_DSN = (
    "postgresql+psycopg://auneron:x@localhost:5433/auneron_test"
)
RECOVERY_DSN = (
    "postgresql+psycopg://auneron:x@localhost:5433/"
    f"{RECOVERY_DATABASE}"
)


# ---------------------------------------------------------------------
# normalized_identity -- reuso de make_url, dataclass imutável
# ---------------------------------------------------------------------


def test_normalized_identity_extracts_host_port_database() -> None:
    identity = normalized_identity(
        "postgresql+psycopg://user:pass@myhost:5433/mydb"
    )
    assert identity == DatabaseIdentity(
        host="myhost", port=5433, database="mydb"
    )


def test_normalized_identity_defaults_port_to_5432() -> None:
    identity = normalized_identity(
        "postgresql+psycopg://user:pass@myhost/mydb"
    )
    assert identity.port == 5432


def test_database_identity_never_carries_credentials() -> None:
    fields = {
        field.name for field in dataclasses.fields(DatabaseIdentity)
    }
    assert fields == {"host", "port", "database"}


# ---------------------------------------------------------------------
# assert_safe_restore_target -- matriz obrigatória
# ---------------------------------------------------------------------


def test_reject_target_identical_to_configured_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(common.settings, "database_url", RECOVERY_DSN)

    with pytest.raises(RestoreTargetForbiddenError):
        assert_safe_restore_target(
            target_dsn=RECOVERY_DSN,
            target_database=RECOVERY_DATABASE,
        )


def test_reject_auneron_as_target() -> None:
    dsn = "postgresql+psycopg://auneron:x@localhost:5433/auneron"

    with pytest.raises(RestoreTargetForbiddenError):
        assert_safe_restore_target(
            target_dsn=dsn, target_database="auneron"
        )


def test_reject_auneron_test_as_target() -> None:
    dsn = CONFIGURED_DSN

    with pytest.raises(RestoreTargetForbiddenError):
        assert_safe_restore_target(
            target_dsn=dsn, target_database="auneron_test"
        )


def test_reject_target_other_than_recovery_database() -> None:
    dsn = "postgresql+psycopg://auneron:x@localhost:5433/some_other_db"

    with pytest.raises(RestoreTargetForbiddenError):
        assert_safe_restore_target(
            target_dsn=dsn, target_database="some_other_db"
        )


def test_reject_dsn_and_target_database_disagreement() -> None:
    with pytest.raises(RestoreTargetForbiddenError):
        assert_safe_restore_target(
            target_dsn=RECOVERY_DSN,
            target_database="a_different_name",
        )


def test_accept_correct_recovery_target_with_distinct_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        common.settings, "database_url", CONFIGURED_DSN
    )

    identity = assert_safe_restore_target(
        target_dsn=RECOVERY_DSN,
        target_database=RECOVERY_DATABASE,
    )

    assert identity.database == RECOVERY_DATABASE


def test_missing_target_database_argument_is_rejected_by_argparse(
    monkeypatch,
) -> None:
    import scripts.restore_postgres as restore_script

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "restore_postgres.py",
            "--backup-file",
            "x",
            "--target-dsn",
            "y",
        ],
    )

    with pytest.raises(SystemExit):
        restore_script.parse_args()


# ---------------------------------------------------------------------
# cleanup guard -- mesma barreira, reaplicada
# ---------------------------------------------------------------------


def test_cleanup_rejects_non_recovery_databases() -> None:
    for forbidden in ("auneron", "auneron_test", "anything_else"):
        with pytest.raises(RestoreTargetForbiddenError):
            assert_safe_cleanup_target(database=forbidden)


def test_cleanup_accepts_recovery_database() -> None:
    assert_safe_cleanup_target(
        database=RECOVERY_DATABASE
    )  # não levanta


# ---------------------------------------------------------------------
# Contrato de fonte: --disable-triggers proibido, shell=True proibido
# ---------------------------------------------------------------------


def test_restore_command_never_uses_disable_triggers() -> None:
    source = Path("scripts/restore_postgres.py").read_text(
        encoding="utf-8"
    )
    assert "--disable-triggers" not in source


def test_docker_helpers_never_use_shell_true() -> None:
    source = Path(
        "scripts/_postgres_recovery_common.py"
    ).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell = True" not in source


def test_docker_helpers_build_argument_lists_not_strings() -> None:
    source = Path(
        "scripts/_postgres_recovery_common.py"
    ).read_text(encoding="utf-8")
    assert '["docker", "exec"' in source
    assert '["docker", "cp"' in source
