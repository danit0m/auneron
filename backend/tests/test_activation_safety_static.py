"""
VALUE-3.4D-1 -- Activation Safety Foundation: guardas estaticas e de
render (compose DEV, independencia da producao, documento operacional).

* O compose DEV passa `MAINTENANCE_ENABLED: ${MAINTENANCE_ENABLED:-false}`
  SOMENTE ao backend: ausencia da variavel => `false`; `true` explicito
  continua possivel (nao pode ser valor fixo).
* Producao (`docker-compose.prod.yml`) e independente (nao e overlay), nao
  recebe o default e preserva o guard de `validate_environment`.
* Nenhum arquivo versionado configura um floor real.
* `EVIDENCE_ACTIVATION_PROCEDURE.md` existe, e operacionalmente completo e
  NAO contem T0 real, credencial ou dado de cliente.

Os testes de render usam `docker compose config` (somente leitura, nao sobe
nada) e sao pulados quando o Docker nao esta disponivel; o ambiente do
processo e isolado do `backend/.env` (`--env-file` vazio).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_production_hardening import production_settings


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEV_COMPOSE = BACKEND_DIR / "docker-compose.yml"
PROD_COMPOSE = BACKEND_DIR / "docker-compose.prod.yml"
PROCEDURE_DOC = (
    BACKEND_DIR / "docs/operations/EVIDENCE_ACTIVATION_PROCEDURE.md"
)

MAINTENANCE_LINE = (
    "      MAINTENANCE_ENABLED: ${MAINTENANCE_ENABLED:-false}"
)
FLOOR_NAME = "ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR"


def _service_blocks(text: str) -> dict[str, str]:
    text = text.replace("\r\n", "\n")
    services = re.search(
        r"^services:\n(.*?)(?=^\S)", text, re.DOTALL | re.MULTILINE
    )
    assert services is not None, "compose sem `services:`"
    parts = re.split(
        r"^  (\w[\w-]*):\n", services.group(1), flags=re.MULTILINE
    )
    return {
        parts[index]: parts[index + 1]
        for index in range(1, len(parts), 2)
    }


def _maintenance_problems(text: str) -> list[str]:
    problems: list[str] = []
    blocks = _service_blocks(text)

    backend_lines = [
        line
        for line in blocks.get("backend", "").splitlines()
        if "MAINTENANCE_ENABLED" in line
        and not line.lstrip().startswith("#")
    ]
    if backend_lines != [MAINTENANCE_LINE]:
        problems.append(
            "backend deve declarar exatamente "
            f"'{MAINTENANCE_LINE.strip()}'; achado={backend_lines}"
        )

    for name, block in blocks.items():
        if name == "backend":
            continue
        if any(
            "MAINTENANCE_ENABLED" in line
            and not line.lstrip().startswith("#")
            for line in block.splitlines()
        ):
            problems.append(f"servico '{name}' nao deve receber o default")

    return problems


# ---------------------------------------------------------------------
# Compose DEV: default fail-closed versionado
# ---------------------------------------------------------------------


def test_dev_compose_defaults_maintenance_to_false_only_for_backend() -> (
    None
):
    assert (
        _maintenance_problems(DEV_COMPOSE.read_text(encoding="utf-8"))
        == []
    )


@pytest.mark.parametrize(
    "mutation_name",
    [
        "removed",
        "default_true",
        "hardcoded_false",
        "no_default",
        "moved_to_migration",
    ],
)
def test_maintenance_default_guard_is_sensitive(
    mutation_name: str,
) -> None:
    text = DEV_COMPOSE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert MAINTENANCE_LINE in text

    mutated = {
        "removed": text.replace(MAINTENANCE_LINE + "\n", ""),
        "default_true": text.replace(
            MAINTENANCE_LINE,
            "      MAINTENANCE_ENABLED: ${MAINTENANCE_ENABLED:-true}",
        ),
        # valor fixo impediria o override explicito `true`
        "hardcoded_false": text.replace(
            MAINTENANCE_LINE, '      MAINTENANCE_ENABLED: "false"'
        ),
        "no_default": text.replace(
            MAINTENANCE_LINE,
            "      MAINTENANCE_ENABLED: ${MAINTENANCE_ENABLED}",
        ),
        "moved_to_migration": text.replace(MAINTENANCE_LINE + "\n", "").replace(
            "      DATABASE_APPLICATION_NAME: auneron-migration\n",
            "      DATABASE_APPLICATION_NAME: auneron-migration\n"
            + MAINTENANCE_LINE
            + "\n",
        ),
    }[mutation_name]
    assert mutated != text

    assert _maintenance_problems(mutated) != []


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except Exception:  # noqa: BLE001
        return False


def _render_backend_environment(
    tmp_path: Path, **extra_environment: str
) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("", encoding="utf-8")

    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "MAINTENANCE_ENABLED",
            "COMPOSE_FILE",
            "COMPOSE_PROFILES",
            FLOOR_NAME,
        }
    }
    environment["API_KEY"] = "compose-render-only-not-a-secret"
    environment.update(extra_environment)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(empty_env_file),
            "-f",
            "docker-compose.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["backend"]["environment"]


@pytest.mark.skipif(
    not _docker_available(), reason="Docker/Compose indisponivel"
)
@pytest.mark.parametrize(
    "extra_environment,expected",
    [
        ({}, "false"),
        ({"MAINTENANCE_ENABLED": "true"}, "true"),
        ({"MAINTENANCE_ENABLED": "false"}, "false"),
    ],
    ids=["absent_means_false", "explicit_true_allowed", "explicit_false"],
)
def test_rendered_dev_compose_maintenance_value(
    tmp_path: Path, extra_environment: dict, expected: str
) -> None:
    environment = _render_backend_environment(
        tmp_path, **extra_environment
    )

    assert environment["MAINTENANCE_ENABLED"] == expected


@pytest.mark.skipif(
    not _docker_available(), reason="Docker/Compose indisponivel"
)
def test_rendered_dev_compose_does_not_configure_a_floor(
    tmp_path: Path,
) -> None:
    # repasse nulo: sem variavel no ambiente, o floor continua ausente
    environment = _render_backend_environment(tmp_path)

    assert environment.get(FLOOR_NAME) is None


# ---------------------------------------------------------------------
# Producao: independente e com o guard preservado
# ---------------------------------------------------------------------


def test_production_compose_is_independent_and_has_no_dev_default() -> (
    None
):
    text = PROD_COMPOSE.read_text(encoding="utf-8")

    assert "MAINTENANCE_ENABLED" not in text
    assert not re.search(r"^\s*extends:", text, re.MULTILINE)
    assert not re.search(r"^\s*include:", text, re.MULTILINE)
    assert "docker-compose.yml" not in re.sub(
        r"#.*", "", text.replace("docker-compose.prod.yml", "")
    )


def test_production_still_forbids_maintenance_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="MAINTENANCE_ENABLED=false"):
        production_settings(monkeypatch, MAINTENANCE_ENABLED=False)

    assert production_settings(monkeypatch).maintenance_enabled is True
    assert (
        production_settings(
            monkeypatch, MAINTENANCE_ENABLED=True
        ).maintenance_enabled
        is True
    )


# ---------------------------------------------------------------------
# Nenhum floor real configurado em arquivos versionados
# ---------------------------------------------------------------------

_CONCRETE_ISO_DATETIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
)


def test_no_versioned_compose_assigns_a_floor_value() -> None:
    for path in (DEV_COMPOSE, PROD_COMPOSE):
        for line in path.read_text(encoding="utf-8").splitlines():
            if FLOOR_NAME in line and not line.lstrip().startswith("#"):
                assert line.strip() == f"{FLOOR_NAME}:" and path == (
                    DEV_COMPOSE
                ), f"{path.name}: {line!r}"


# ---------------------------------------------------------------------
# Documento operacional
# ---------------------------------------------------------------------


def test_procedure_document_is_complete_and_free_of_real_values() -> None:
    text = PROCEDURE_DOC.read_text(encoding="utf-8")

    for required in (
        "NÃO autoriza ativação",
        "ATIVAÇÃO BLOQUEADA até D-2",
        "evidence_floor_preflight.py capture",
        "evidence_floor_preflight.py check",
        "sem variável → `false`",
        "MAINTENANCE_ENABLED=true",
        "Correlação temporal ≠ causalidade",
        "NÃO VERIFICADO",
        "extras != 0",
        "image ID",
        "nunca** commitado",
        "H = 30 dias",
        "UNVERIFIED_*",
        "placeholder",
    ):
        assert required in text, f"trecho obrigatorio ausente: {required}"

    # sem T0 real / datetime concreto, sem credencial, sem dado de cliente
    assert _CONCRETE_ISO_DATETIME.search(text) is None
    assert not re.search(r"://[^/\s:]+:[^@\s]+@", text)
    assert not re.search(r"(?i)api_key\s*=\s*\S", text)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
