"""INTELLIGENCE-IMPL-0 -- FakeProvider behavior and production ban.

The ban is verified on EVERY path that can introduce a FakeProvider,
not just profile configuration, and each layer is also shown to be
sensitive (the test fails if the guard is neutralised).
"""

import socket

import pytest

from app.core.config import get_settings
from app.database.database import SessionLocal
from app.intelligence.gateway import adapters as adapters_module
from app.intelligence.gateway import profiles as profiles_module
from app.intelligence.gateway.adapters import create_adapter
from app.intelligence.gateway.adapters import fake as fake_module
from app.intelligence.gateway.adapters.base import ProviderAdapter
from app.intelligence.gateway.adapters.fake import FakeProvider
from app.intelligence.gateway.adapters.fake import http_error
from app.intelligence.gateway.adapters.fake import not_sent
from app.intelligence.gateway.adapters.fake import ok
from app.intelligence.gateway.adapters.fake import raw
from app.intelligence.gateway.adapters.fake import refusal
from app.intelligence.gateway.adapters.fake import timeout
from app.intelligence.gateway.adapters.fake import transport_error
from app.intelligence.gateway.adapters.fake import truncated
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderNotSentError
from app.intelligence.gateway.errors import ProviderTimeoutError
from app.intelligence.gateway.errors import ProviderTransportError
from app.intelligence.gateway.gateway import Gateway
from app.intelligence.gateway.gateway import GatewayLimits
from app.intelligence.gateway.profiles import LAB_PROFILES
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import build_lab_registry
from app.intelligence.gateway.profiles import is_production_environment


pytestmark = pytest.mark.security

REQUEST = ProviderRequest(
    model_id="fake-small-v0",
    instructions="i",
    input_text="t",
    output_schema={"type": "object"},
    max_output_tokens=8,
    timeout_seconds=5,
)


def make_gateway(adapter: ProviderAdapter, mode: str = "shadow") -> Gateway:
    return Gateway(
        registry=build_lab_registry(),
        adapters={adapter.provider_kind: adapter},
        ledger=BudgetLedger(
            SessionLocal,
            unknown_fraction_bps=2500,
            recovery_grace_seconds=30,
        ),
        limits=GatewayLimits(2, 5, 2000),
        mode_provider=lambda: mode,
    )


def go_production(monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    if how in ("env", "both"):
        monkeypatch.setenv("APP_ENV", "production")
    if how in ("settings", "both"):
        monkeypatch.setattr(get_settings(), "environment", "production")


PRODUCTION_PATHS = ["env", "settings", "both"]


# ----------------------------------------------------------------------
# deterministic behavior
# ----------------------------------------------------------------------


def test_fake_provider_replays_its_script_in_order() -> None:
    provider = FakeProvider(
        [
            ok({"a": 1}, input_tokens=3, output_tokens=4),
            refusal(),
            truncated(),
            raw("free text"),
        ]
    )

    first = provider.invoke(REQUEST)
    assert first.status is ProviderStatus.COMPLETED
    assert first.output_text == '{"a": 1}'
    assert (first.input_tokens, first.output_tokens) == (3, 4)

    assert provider.invoke(REQUEST).status is ProviderStatus.REFUSAL
    assert provider.invoke(REQUEST).status is ProviderStatus.TRUNCATED
    assert provider.invoke(REQUEST).output_text == "free text"
    assert len(provider.calls) == 4


def test_fake_provider_default_answer_is_deterministic() -> None:
    provider = FakeProvider()

    assert provider.invoke(REQUEST).output_text == '{"ok": true}'
    assert provider.invoke(REQUEST).output_text == '{"ok": true}'


@pytest.mark.parametrize(
    "step, error",
    [
        (timeout(), ProviderTimeoutError),
        (transport_error(), ProviderTransportError),
        (http_error(429), ProviderHTTPError),
        (not_sent(), ProviderNotSentError),
    ],
)
def test_fake_provider_raises_typed_failures(step, error) -> None:
    with pytest.raises(error):
        FakeProvider([step]).invoke(REQUEST)


def test_http_error_transience_matches_retry_policy() -> None:
    assert ProviderHTTPError(429).transient
    assert ProviderHTTPError(500).transient
    assert ProviderHTTPError(503).transient
    assert not ProviderHTTPError(400).transient
    assert not ProviderHTTPError(404).transient
    assert ProviderNotSentError().proven_not_sent
    assert not ProviderTimeoutError("x").proven_not_sent
    assert not ProviderHTTPError(500).proven_not_sent


def test_fake_provider_opens_no_socket(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    provider = FakeProvider([ok({"a": 1})])

    assert provider.invoke(REQUEST).status is ProviderStatus.COMPLETED


def test_fake_provider_reads_no_credential_environment(monkeypatch) -> None:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert FakeProvider().invoke(REQUEST).status is (
        ProviderStatus.COMPLETED
    )


# ----------------------------------------------------------------------
# production ban -- every path
# ----------------------------------------------------------------------


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_environment_detection_is_fail_closed(monkeypatch, how) -> None:
    assert not is_production_environment()
    go_production(monkeypatch, how)
    assert is_production_environment()


@pytest.mark.parametrize("value", ["production", "PRODUCTION", " Production "])
def test_app_env_spelling_variants_are_still_production(
    monkeypatch, value
) -> None:
    monkeypatch.setenv("APP_ENV", value)

    assert is_production_environment()


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_layer1_profile_registry_refuses_fake_profiles(
    monkeypatch, how
) -> None:
    go_production(monkeypatch, how)

    with pytest.raises(GatewayError) as caught:
        ProfileRegistry(LAB_PROFILES)
    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN

    with pytest.raises(GatewayError) as caught:
        build_lab_registry()
    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_layer2_adapter_factory_refuses_fake(monkeypatch, how) -> None:
    go_production(monkeypatch, how)

    with pytest.raises(GatewayError) as caught:
        create_adapter("fake")

    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN


def test_adapter_factory_knows_only_fake_in_this_phase() -> None:
    assert isinstance(create_adapter("fake"), FakeProvider)

    for provider in ("openai", "anthropic", "ollama", "", "FAKE"):
        with pytest.raises(GatewayError) as caught:
            create_adapter(provider)
        assert caught.value.code is GatewayErrorCode.UNKNOWN_PROVIDER


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_layer3_direct_construction_refuses_fake(monkeypatch, how) -> None:
    go_production(monkeypatch, how)

    with pytest.raises(GatewayError) as caught:
        FakeProvider()

    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_layer3_invoke_rechecks_for_instances_built_earlier(
    monkeypatch, how
) -> None:
    provider = FakeProvider([ok({"a": 1})])  # built in the test env
    go_production(monkeypatch, how)

    with pytest.raises(GatewayError) as caught:
        provider.invoke(REQUEST)

    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN
    assert provider.calls == []


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_layer4_gateway_construction_refuses_prebuilt_fake(
    monkeypatch, how
) -> None:
    provider = FakeProvider()
    go_production(monkeypatch, how)

    with pytest.raises(GatewayError) as caught:
        Gateway(
            registry=None,
            adapters={"fake": provider},
            ledger=None,
            limits=GatewayLimits(1, 1, 0),
        )

    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_layer4_gateway_call_never_reaches_a_fake_in_production(
    monkeypatch, how
) -> None:
    provider = FakeProvider([ok({"ok": True})])
    gateway = make_gateway(provider)
    go_production(monkeypatch, how)

    request = ModelCallRequest(
        call_key="prod-1",
        purpose="lab",
        profile="lab-fast",
        instructions="i",
        input_text="t",
        output_schema={"type": "object"},
        max_output_tokens=8,
        deployment_budget_id=1,
    )

    with pytest.raises(GatewayError) as caught:
        gateway.call(request)

    assert caught.value.code in (
        GatewayErrorCode.MODE_NOT_SUPPORTED,
        GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN,
    )
    assert provider.calls == []

    # the runtime adapter guard alone also refuses (independent of
    # the production shadow ban in the mode check)
    with pytest.raises(GatewayError) as caught:
        gateway._adapter_for(gateway._registry.get("lab-fast"))
    assert caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN


@pytest.mark.parametrize("how", PRODUCTION_PATHS)
def test_alternative_paths_subclass_and_flag_spoofing_are_caught(
    monkeypatch, how
) -> None:
    class SkipsInit(FakeProvider):
        def __init__(self) -> None:  # deliberately bypasses the guard
            self._script = []
            self.calls = []
            self._on_invoke = None

    class FlagOnly(ProviderAdapter):
        provider_kind = "fake"

        def invoke(self, request):  # pragma: no cover - never reached
            raise AssertionError

    class SelfDeclaredFake(ProviderAdapter):
        provider_kind = "mimic"

        @property
        def is_fake(self) -> bool:
            return True

        def invoke(self, request):  # pragma: no cover - never reached
            raise AssertionError

    go_production(monkeypatch, how)
    limits = GatewayLimits(1, 1, 0)

    for key, adapter in (
        ("fake", SkipsInit()),
        ("fake", FlagOnly()),
        ("mimic", SelfDeclaredFake()),
    ):
        with pytest.raises(GatewayError) as caught:
            Gateway(
                registry=None,
                adapters={key: adapter},
                ledger=None,
                limits=limits,
            )
        assert (
            caught.value.code is GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN
        )


def test_adapter_registered_under_the_wrong_provider_key_is_refused() -> None:
    with pytest.raises(GatewayError) as caught:
        Gateway(
            registry=None,
            adapters={"openai": FakeProvider()},
            ledger=None,
            limits=GatewayLimits(1, 1, 0),
        )

    assert caught.value.code is GatewayErrorCode.UNKNOWN_PROVIDER


# ----------------------------------------------------------------------
# synthetic negative tests: neutralise a layer, prove the others hold
# and that the test above would notice
# ----------------------------------------------------------------------


def test_sensitivity_removing_the_central_guard_is_detected(
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(
        profiles_module, "is_production_environment", lambda: False
    )

    # With the detector neutered the ban disappears: this is exactly
    # what the tests above would catch as a regression.
    assert isinstance(create_adapter("fake"), FakeProvider)


def test_defence_in_depth_each_layer_survives_the_others_failing(
    monkeypatch,
) -> None:
    provider = FakeProvider()
    monkeypatch.setenv("APP_ENV", "production")

    # Layer 3 neutralised in its own module: construction passes ...
    monkeypatch.setattr(
        fake_module, "assert_fake_provider_allowed", lambda: None
    )
    assert isinstance(FakeProvider(), FakeProvider)

    # ... but the factory (layer 2) still refuses ...
    with pytest.raises(GatewayError):
        create_adapter("fake")

    # ... and so does the gateway (layer 4) for that instance.
    with pytest.raises(GatewayError):
        Gateway(
            registry=None,
            adapters={"fake": provider},
            ledger=None,
            limits=GatewayLimits(1, 1, 0),
        )

    # Neutralise layer 2 too: registry (layer 1) and gateway hold.
    monkeypatch.setattr(
        adapters_module, "assert_fake_provider_allowed", lambda: None
    )
    assert isinstance(create_adapter("fake"), FakeProvider)

    with pytest.raises(GatewayError):
        build_lab_registry()

    with pytest.raises(GatewayError):
        Gateway(
            registry=None,
            adapters={"fake": provider},
            ledger=None,
            limits=GatewayLimits(1, 1, 0),
        )


def test_provider_response_dataclass_carries_no_content_in_repr_of_errors() -> None:
    error = ProviderHTTPError(500)

    assert str(error) == "http_500"
    assert ProviderResponse(
        status=ProviderStatus.COMPLETED,
        output_text=None,
        input_tokens=1,
        output_tokens=1,
    ).request_id is None
