"""
Regressao do compose de producao: todo servico que roda a imagem do backend
precisa iniciar com o ambiente que o proprio compose lhe entrega.

Defeito coberto: `migration` nao definia FORWARDED_ALLOW_IPS; em production
os Settings recusavam iniciar, a migracao falhava e o `backend` (que depende
dela) nao subia. O ambiente vem do arquivo versionado, nao de uma copia.
Sem Docker e sem banco.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


PROD_COMPOSE = (
    Path(__file__).resolve().parents[1] / "docker-compose.prod.yml"
)

# Valores que o operador fornece no `.env` (obrigatorios, sem default).
OPERATOR_VALUES = {
    "CUSTOMER_DOMAIN": "cliente.com.br",
    "EXPECTED_DATABASE_NAME": "auneron",
    "EXPECTED_DATABASE_HOST": "postgres",
}

# Valores que chegam por Docker secrets.
SECRET_VALUES = {
    "DATABASE_URL": (
        "postgresql+psycopg://auneron:strong-password@postgres:5432/auneron"
    ),
    "API_KEY": "Q7d9R2m8L4x1P6v3N5k0T8z2B9c4W7y1",
}

_INTERPOLATION = re.compile(
    r"\$\{(?P<name>[A-Z_]+)(?::(?P<op>[-?+])(?P<arg>[^}]*))?\}"
)


def _interpolate(value: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        current = variables.get(match["name"], "")
        operator, argument = match["op"], match["arg"] or ""

        if operator == "-":
            return current or argument
        if operator == "+":
            return argument if current else ""
        if operator == "?":
            assert current, f"variavel obrigatoria: {match['name']}"
        return current

    return _INTERPOLATION.sub(replace, value)


def _service_environment(service: str) -> dict[str, str]:
    text = PROD_COMPOSE.read_text(encoding="utf-8")
    block = re.search(
        rf"^  {service}:\n(?P<body>(?:(?:    .*)?\n)+)", text, re.M
    )
    assert block is not None, f"servico ausente: {service}"
    section = re.search(
        r"^    environment:\n(?P<body>(?:      .*\n)+)", block["body"], re.M
    )
    assert section is not None, f"environment ausente: {service}"

    environment = {}
    for line in section["body"].splitlines():
        item = line.strip()
        if item.startswith("#"):
            continue
        key, separator, value = item.partition(": ")
        assert separator, f"linha inesperada: {line!r}"
        environment[key] = value
    return environment


def _load_settings(
    environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    variables: dict[str, str] | None = None,
) -> Settings:
    for name in Settings.model_fields.values():
        alias = name.validation_alias
        if isinstance(alias, str):
            monkeypatch.delenv(alias, raising=False)

    resolved = {
        key: _interpolate(value, {**OPERATOR_VALUES, **(variables or {})})
        for key, value in environment.items()
    }
    for key, value in {**resolved, **SECRET_VALUES}.items():
        monkeypatch.setenv(key, value)

    return Settings(_env_file=None, _secrets_dir=None)


@pytest.mark.parametrize("service", ["migration", "backend"])
def test_service_environment_starts_in_production(
    service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _load_settings(_service_environment(service), monkeypatch)

    assert settings.environment == "production"
    assert settings.policy_mark_overdue_trigger_enabled is False
    assert settings.overdue_shadow_enabled is False
    assert settings.intelligence_mode == "off"


@pytest.mark.parametrize("service", ["migration", "backend"])
def test_service_accepts_operator_forwarded_allow_ips(
    service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = "172.28.1.10/32,172.28.0.10/32,203.0.113.0/24"
    settings = _load_settings(
        _service_environment(service),
        monkeypatch,
        {"FORWARDED_ALLOW_IPS": value},
    )

    assert settings.forwarded_allow_ips == value


def test_migration_and_backend_share_forwarded_allow_ips() -> None:
    migration = _service_environment("migration")
    backend = _service_environment("backend")

    assert (
        migration["FORWARDED_ALLOW_IPS"] == backend["FORWARDED_ALLOW_IPS"]
    )


def test_missing_forwarded_allow_ips_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reproduz o defeito original: prova que os testes acima o detectam.
    environment = _service_environment("migration")
    environment.pop("FORWARDED_ALLOW_IPS", None)

    with pytest.raises(ValidationError, match="proxies empacotados"):
        _load_settings(environment, monkeypatch)
