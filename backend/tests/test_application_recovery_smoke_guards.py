"""
P2 -- testes de contrato do orquestrador de Application Recovery
Smoke (Layer B). Só lógica pura -- mockado, sem Docker/DB real. O
drill end-to-end (container real, banco de recovery real) fica fora
do pytest -q, mesma disciplina já estabelecida no PR-3/PR-4.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import scripts.application_recovery_smoke as smoke
from scripts.application_recovery_smoke import (
    ApplicationRecoverySmokeBlockedError,
)
from scripts.application_recovery_smoke import (
    ApplicationRecoverySmokeFailedError,
)
from scripts.application_recovery_smoke import (
    RECOVERY_DATABASE,
)
from scripts.application_recovery_smoke import (
    assert_only_allowlisted_mutation,
)
from scripts.application_recovery_smoke import (
    discover_single_network,
)
from scripts.application_recovery_smoke import (
    load_smoke_credentials,
)


# ---------------------------------------------------------------------
# discover_single_network -- 0/1/>1 redes
# ---------------------------------------------------------------------


def _fake_inspect_result(networks: dict) -> MagicMock:
    result = MagicMock()
    result.stdout = json.dumps(networks)
    return result


def test_discover_single_network_accepts_exactly_one() -> None:
    with patch(
        "scripts.application_recovery_smoke.subprocess.run",
        return_value=_fake_inspect_result(
            {"auneron_internal": {}}
        ),
    ):
        network = discover_single_network()

    assert network == "auneron_internal"


def test_discover_single_network_rejects_zero() -> None:
    with patch(
        "scripts.application_recovery_smoke.subprocess.run",
        return_value=_fake_inspect_result({}),
    ):
        with pytest.raises(
            ApplicationRecoverySmokeFailedError
        ):
            discover_single_network()


def test_discover_single_network_rejects_more_than_one() -> None:
    with patch(
        "scripts.application_recovery_smoke.subprocess.run",
        return_value=_fake_inspect_result(
            {"net_a": {}, "net_b": {}}
        ),
    ):
        with pytest.raises(
            ApplicationRecoverySmokeFailedError
        ):
            discover_single_network()


# ---------------------------------------------------------------------
# load_smoke_credentials -- sem default, sem fallback
# ---------------------------------------------------------------------


def test_credentials_none_when_email_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMOKE_LOGIN_EMAIL", raising=False)
    monkeypatch.setenv(
        "SMOKE_LOGIN_PASSWORD_FILE", "/tmp/does-not-matter"
    )

    assert load_smoke_credentials() is None


def test_credentials_none_when_password_file_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SMOKE_LOGIN_EMAIL", "someone@example.com"
    )
    monkeypatch.delenv(
        "SMOKE_LOGIN_PASSWORD_FILE", raising=False
    )

    assert load_smoke_credentials() is None


def test_credentials_blocked_when_password_file_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "SMOKE_LOGIN_EMAIL", "someone@example.com"
    )
    monkeypatch.setenv(
        "SMOKE_LOGIN_PASSWORD_FILE",
        str(tmp_path / "nonexistent"),
    )

    with pytest.raises(
        ApplicationRecoverySmokeBlockedError
    ):
        load_smoke_credentials()


def test_credentials_blocked_when_password_file_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("   ")

    monkeypatch.setenv(
        "SMOKE_LOGIN_EMAIL", "someone@example.com"
    )
    monkeypatch.setenv(
        "SMOKE_LOGIN_PASSWORD_FILE", str(password_file)
    )

    with pytest.raises(
        ApplicationRecoverySmokeBlockedError
    ):
        load_smoke_credentials()


def test_credentials_loaded_when_both_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("s3nha-real\n")

    monkeypatch.setenv(
        "SMOKE_LOGIN_EMAIL", "someone@example.com"
    )
    monkeypatch.setenv(
        "SMOKE_LOGIN_PASSWORD_FILE", str(password_file)
    )

    credentials = load_smoke_credentials()

    assert credentials is not None
    assert credentials.email == "someone@example.com"
    assert credentials.password == "s3nha-real"


# ---------------------------------------------------------------------
# assert_only_allowlisted_mutation
# ---------------------------------------------------------------------


def test_allowlist_accepts_only_expected_auth_sessions_delta() -> None:
    before = {
        "accounts": 5,
        "auth_sessions": 0,
        "work_items": 2,
    }
    after = {
        "accounts": 5,
        "auth_sessions": 1,
        "work_items": 2,
    }

    assert_only_allowlisted_mutation(
        before=before, after=after
    )  # não levanta


def test_allowlist_rejects_unexpected_table_mutation() -> None:
    before = {"accounts": 5, "auth_sessions": 0}
    after = {"accounts": 6, "auth_sessions": 1}

    with pytest.raises(
        ApplicationRecoverySmokeFailedError
    ):
        assert_only_allowlisted_mutation(
            before=before, after=after
        )


def test_allowlist_rejects_auth_sessions_delta_other_than_one() -> None:
    before = {"auth_sessions": 0}
    after = {"auth_sessions": 2}

    with pytest.raises(
        ApplicationRecoverySmokeFailedError
    ):
        assert_only_allowlisted_mutation(
            before=before, after=after
        )


def test_allowlist_rejects_table_set_drift() -> None:
    before = {"accounts": 5}
    after = {"accounts": 5, "extra_table": 1}

    with pytest.raises(
        ApplicationRecoverySmokeFailedError
    ):
        assert_only_allowlisted_mutation(
            before=before, after=after
        )


# ---------------------------------------------------------------------
# target fixo -- RECOVERY_DATABASE reaproveitado, nunca reconstruído
# ---------------------------------------------------------------------


def test_recovery_database_constant_is_reused_from_pr4() -> None:
    from scripts._postgres_recovery_common import (
        RECOVERY_DATABASE as PR4_RECOVERY_DATABASE,
    )

    assert RECOVERY_DATABASE == PR4_RECOVERY_DATABASE
    assert RECOVERY_DATABASE == "auneron_recovery_drill"


# ---------------------------------------------------------------------
# Contrato de fonte -- sem senha literal, sem shell=True, sem -e
# DATABASE_URL=
# ---------------------------------------------------------------------


def test_source_never_uses_shell_true() -> None:
    source = Path(
        "scripts/application_recovery_smoke.py"
    ).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell = True" not in source


def test_source_never_passes_database_url_via_dash_e() -> None:
    source = Path(
        "scripts/application_recovery_smoke.py"
    ).read_text(encoding="utf-8")
    assert "DATABASE_URL=" not in source


def test_source_builds_docker_commands_as_argument_lists() -> None:
    source = Path(
        "scripts/application_recovery_smoke.py"
    ).read_text(encoding="utf-8")
    assert '"docker",\n            "run"' in source or (
        '"docker",' in source and '"run",' in source
    )
