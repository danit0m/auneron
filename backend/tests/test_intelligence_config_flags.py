"""INTELLIGENCE-IMPL-0 -- feature flags, real-data refusal, lab values."""

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.intelligence.gateway import contracts
from app.intelligence.gateway.contracts import REAL_DATA_ALLOWED
from app.intelligence.gateway.contracts import SUPPORTED_MODES


pytestmark = pytest.mark.security

BACKEND = Path(__file__).resolve().parents[1]
TEST_URL = (
    "postgresql+psycopg://auneron:test_password@localhost:5432/auneron_test"
)
PROD_URL = "postgresql+psycopg://auneron:strong-password@postgres:5432/auneron"
PROD_API_KEY = "Q7d9R2m8L4x1P6v3N5k0T8z2B9c4W7y1"
PROD_PROXIES = "172.28.1.10/32,172.28.0.10/32"


def make_settings(**overrides) -> Settings:
    values = {"APP_ENV": "test", "DATABASE_URL": TEST_URL}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def production_settings(monkeypatch, **overrides) -> Settings:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", PROD_PROXIES)
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": PROD_URL,
        "API_KEY": PROD_API_KEY,
        "CORS_ORIGINS": "",
        "DEBUG": False,
        "DATABASE_ECHO": False,
        "EXPECTED_DATABASE_NAME": "auneron",
        "EXPECTED_DATABASE_HOST": "postgres",
        "FORWARDED_ALLOW_IPS": PROD_PROXIES,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


# ----------------------------------------------------------------------
# INTELLIGENCE_MODE
# ----------------------------------------------------------------------


def test_default_mode_is_off() -> None:
    assert make_settings().intelligence_mode == "off"
    assert Settings(
        _env_file=None, APP_ENV="development", DATABASE_URL=TEST_URL.replace(
            "auneron_test", "auneron"
        )
    ).intelligence_mode == "off"


@pytest.mark.parametrize("value", ["off", "OFF", " off "])
def test_off_spellings(value: str) -> None:
    assert make_settings(INTELLIGENCE_MODE=value).intelligence_mode == "off"


@pytest.mark.parametrize("value", ["shadow", "Shadow", " SHADOW "])
def test_shadow_is_accepted_outside_production(value: str) -> None:
    assert make_settings(INTELLIGENCE_MODE=value).intelligence_mode == "shadow"
    assert (
        Settings(
            _env_file=None,
            APP_ENV="development",
            DATABASE_URL=TEST_URL.replace("auneron_test", "auneron"),
            INTELLIGENCE_MODE=value,
        ).intelligence_mode
        == "shadow"
    )


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_mode_degrades_to_off_never_to_something_wider(
    value: str,
) -> None:
    assert make_settings(INTELLIGENCE_MODE=value).intelligence_mode == "off"


@pytest.mark.parametrize("value", ["governed", "bounded", "GOVERNED", " Bounded "])
def test_future_modes_are_refused_at_startup(value: str) -> None:
    with pytest.raises(ValidationError, match="governed/bounded"):
        make_settings(INTELLIGENCE_MODE=value)


@pytest.mark.parametrize("value", ["on", "true", "1", "live", "shadow2", "off,shadow"])
def test_unknown_modes_are_refused(value: str) -> None:
    with pytest.raises(ValidationError, match="INTELLIGENCE_MODE"):
        make_settings(INTELLIGENCE_MODE=value)


def test_production_requires_mode_off(monkeypatch) -> None:
    assert production_settings(monkeypatch).intelligence_mode == "off"
    assert (
        production_settings(
            monkeypatch, INTELLIGENCE_MODE="off"
        ).intelligence_mode
        == "off"
    )

    with pytest.raises(ValidationError, match="INTELLIGENCE_MODE deve ser off"):
        production_settings(monkeypatch, INTELLIGENCE_MODE="shadow")

    for value in ("governed", "bounded"):
        with pytest.raises(ValidationError):
            production_settings(monkeypatch, INTELLIGENCE_MODE=value)


def test_mode_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("INTELLIGENCE_MODE", "shadow")
    assert make_settings().intelligence_mode == "shadow"

    monkeypatch.setenv("INTELLIGENCE_MODE", "bounded")
    with pytest.raises(ValidationError):
        make_settings()


# ----------------------------------------------------------------------
# numeric limits
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, low, high",
    [
        ("INTELLIGENCE_MAX_ATTEMPTS", 1, 3),
        ("INTELLIGENCE_CALL_TIMEOUT_SECONDS", 1, 120),
        ("INTELLIGENCE_MAX_CONCURRENT_CALLS", 1, 8),
        ("INTELLIGENCE_RESERVATION_MARGIN_BPS", 0, 10000),
        ("INTELLIGENCE_UNKNOWN_FRACTION_BPS", 1, 10000),
        ("INTELLIGENCE_RECOVERY_GRACE_SECONDS", 0, 3600),
        ("INTELLIGENCE_BREAKER_FAILURE_THRESHOLD", 1, 20),
        ("INTELLIGENCE_BREAKER_COOLDOWN_SECONDS", 1, 3600),
    ],
)
def test_numeric_limits_are_bounded(name, low, high) -> None:
    make_settings(**{name: low})
    make_settings(**{name: high})

    for bad in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            make_settings(**{name: bad})


def test_documented_lab_values_are_the_actual_defaults() -> None:
    settings = make_settings()
    expected = {
        "intelligence_max_attempts": 2,
        "intelligence_call_timeout_seconds": 30,
        "intelligence_max_concurrent_calls": 2,
        "intelligence_reservation_margin_bps": 2000,
        "intelligence_unknown_fraction_bps": 2500,
        "intelligence_recovery_grace_seconds": 30,
        "intelligence_breaker_failure_threshold": 3,
        "intelligence_breaker_cooldown_seconds": 30,
    }

    for field, value in expected.items():
        assert getattr(settings, field) == value

    document = (
        BACKEND / "docs" / "intelligence" / "GATEWAY_CONTRACT.md"
    ).read_text(encoding="utf-8")

    for field, value in expected.items():
        row = re.search(
            rf"\| `{field.upper()}` \| {value}[ |]", document
        )
        assert row, f"{field}={value} missing from the documented lab values"

    example = (BACKEND / ".env.example").read_text(encoding="utf-8")

    for field, value in expected.items():
        assert f"{field.upper()}={value}\n" in example.replace("\r\n", "\n")

    assert "INTELLIGENCE_MODE=off\n" in example.replace("\r\n", "\n")


# ----------------------------------------------------------------------
# REAL_DATA_ALLOWED is code, not configuration
# ----------------------------------------------------------------------


def test_real_data_allowed_is_a_false_constant() -> None:
    assert REAL_DATA_ALLOWED is False

    tree = ast.parse(Path(contracts.__file__).read_text(encoding="utf-8"))
    assigns = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "REAL_DATA_ALLOWED"
    ]
    assert len(assigns) == 1
    assert isinstance(assigns[0].value, ast.Constant)
    assert assigns[0].value.value is False  # a literal, nothing computed


def test_no_setting_or_environment_can_enable_real_data(monkeypatch) -> None:
    for name in (
        "REAL_DATA_ALLOWED",
        "INTELLIGENCE_REAL_DATA_ALLOWED",
        "INTELLIGENCE_ALLOW_REAL_DATA",
        "ALLOW_REAL_DATA",
    ):
        monkeypatch.setenv(name, "true")

    settings = make_settings()
    fields = {name.lower() for name in Settings.model_fields}

    assert not [f for f in fields if "real_data" in f or "allow_real" in f]
    assert re.search(r"real_data", " ".join(sorted(fields))) is None
    assert contracts.REAL_DATA_ALLOWED is False
    assert settings.intelligence_mode == "off"


def test_gateway_refuses_real_data_even_if_the_constant_is_tampered(
    monkeypatch,
) -> None:
    """
    The classification gate must not depend on the constant alone: with
    the constant flipped in memory, REAL still never passes because only
    the SYNTHETIC enum member is accepted and the database CHECK rejects
    anything else.
    """
    from app.database.database import SessionLocal
    from app.intelligence.gateway import gateway as gateway_module
    from app.intelligence.gateway.adapters.fake import FakeProvider
    from app.intelligence.gateway.budget import BudgetLedger
    from app.intelligence.gateway.contracts import DataClassification
    from app.intelligence.gateway.contracts import ModelCallRequest
    from app.intelligence.gateway.errors import GatewayError
    from app.intelligence.gateway.gateway import Gateway
    from app.intelligence.gateway.gateway import GatewayLimits
    from app.intelligence.gateway.profiles import build_lab_registry

    monkeypatch.setattr(gateway_module, "REAL_DATA_ALLOWED", True)
    gateway = Gateway(
        registry=build_lab_registry(),
        adapters={"fake": FakeProvider()},
        ledger=BudgetLedger(
            SessionLocal, unknown_fraction_bps=2500, recovery_grace_seconds=1
        ),
        limits=GatewayLimits(1, 5, 0),
        mode_provider=lambda: "shadow",
    )
    request = ModelCallRequest(
        call_key="real-1",
        purpose="lab",
        profile="lab-fast",
        instructions="i",
        input_text="t",
        output_schema={"type": "object"},
        max_output_tokens=8,
        data_classification=DataClassification.REAL,
        deployment_budget_id=1,
    )

    with pytest.raises(GatewayError) as caught:
        gateway.call(request)

    assert caught.value.code.value == "real_data_refused"

    synthetic = ModelCallRequest(
        call_key="real-2",
        purpose="lab",
        profile="lab-fast",
        instructions="i",
        input_text="t",
        output_schema={"type": "object"},
        max_output_tokens=8,
        deployment_budget_id=1,
    )

    with pytest.raises(GatewayError) as caught:
        gateway.call(synthetic)  # a flipped constant fails closed

    assert caught.value.code.value == "real_data_refused"


def test_supported_modes_are_exactly_off_and_shadow() -> None:
    assert SUPPORTED_MODES == frozenset({"off", "shadow"})


# ----------------------------------------------------------------------
# no credential names for LLM providers anywhere in config surfaces
# ----------------------------------------------------------------------


def test_no_llm_credential_settings_or_examples_exist() -> None:
    names = {name.lower() for name in Settings.model_fields}
    forbidden = ("openai", "anthropic", "llm_api", "claude_api", "gemini")

    assert not [n for n in names if any(f in n for f in forbidden)]

    example = (BACKEND / ".env.example").read_text(encoding="utf-8").lower()

    for token in forbidden:
        assert token not in example
