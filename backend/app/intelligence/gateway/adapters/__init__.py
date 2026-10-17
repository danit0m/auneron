from app.intelligence.gateway.adapters.base import ProviderAdapter
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.profiles import (
    assert_fake_provider_allowed,
)


def create_adapter(provider: str) -> ProviderAdapter:
    """
    Adapter factory (layer 2 of 4 for the FakeProvider production
    guard). Only "fake" exists in this phase.
    """
    if provider == "fake":
        assert_fake_provider_allowed()

        from app.intelligence.gateway.adapters.fake import FakeProvider

        return FakeProvider()

    raise GatewayError(
        GatewayErrorCode.UNKNOWN_PROVIDER,
        "Provider is not enabled in this phase.",
    )
