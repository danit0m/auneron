import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.pricing import PRICE_TABLE


# Providers the gateway knows how to build in this phase. Real
# providers are NOT here: adding one needs a new gate, a new adapter
# module, a new DB CHECK migration and PO approval.
KNOWN_PROVIDERS: Final[frozenset[str]] = frozenset({"fake"})
FAKE_PROVIDER_KEY: Final[str] = "fake"


def is_production_environment() -> bool:
    """
    Single source of truth for "are we in production". Deliberately
    checks BOTH the parsed Settings and the raw APP_ENV variable, so a
    code path that bypasses one of them is still caught. Either one
    saying production is enough (fail-closed).
    """
    from app.core.config import get_settings

    if get_settings().environment == "production":
        return True

    return os.environ.get("APP_ENV", "").strip().lower() == "production"


def assert_fake_provider_allowed() -> None:
    """
    Central choke point for FakeProvider. Called by every layer that
    can introduce one: ProfileRegistry validation, the adapter
    factory, FakeProvider.__init__, Gateway.__init__ and every
    Gateway.call. Raises FAKE_PROVIDER_FORBIDDEN in production.
    """
    if is_production_environment():
        raise GatewayError(
            GatewayErrorCode.FAKE_PROVIDER_FORBIDDEN,
            "FakeProvider is forbidden in production.",
        )


@dataclass(frozen=True)
class ModelProfile:
    """Logical model profile: callers never name a provider model."""

    name: str
    provider: str
    model_id: str
    price_key: str
    max_output_tokens_cap: int
    fallbacks: tuple[str, ...] = ()


class ProfileRegistry:
    def __init__(self, profiles: Iterable[ModelProfile]) -> None:
        self._profiles: dict[str, ModelProfile] = {}

        for profile in profiles:
            self._validate(profile)

            if profile.name in self._profiles:
                raise GatewayError(
                    GatewayErrorCode.INVALID_REQUEST,
                    "Duplicate profile name.",
                )

            self._profiles[profile.name] = profile

        for profile in self._profiles.values():
            for fallback in profile.fallbacks:
                if fallback not in self._profiles:
                    raise GatewayError(
                        GatewayErrorCode.UNKNOWN_PROFILE,
                        "Fallback references an unknown profile.",
                    )

    @staticmethod
    def _validate(profile: ModelProfile) -> None:
        if profile.provider not in KNOWN_PROVIDERS:
            raise GatewayError(
                GatewayErrorCode.UNKNOWN_PROVIDER,
                "Provider is not enabled in this phase.",
            )

        if profile.provider == FAKE_PROVIDER_KEY:
            assert_fake_provider_allowed()

        if profile.price_key not in PRICE_TABLE:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Profile has no pinned price entry.",
            )

        if profile.max_output_tokens_cap < 1:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Profile output cap must be positive.",
            )

    def get(self, name: str) -> ModelProfile:
        try:
            return self._profiles[name]
        except KeyError:
            raise GatewayError(
                GatewayErrorCode.UNKNOWN_PROFILE,
                "Unknown model profile.",
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


LAB_PROFILES: Final[tuple[ModelProfile, ...]] = (
    ModelProfile(
        name="lab-fast",
        provider="fake",
        model_id="fake-small-v0",
        price_key="fake-small",
        max_output_tokens_cap=512,
        fallbacks=("lab-strong",),
    ),
    ModelProfile(
        name="lab-strong",
        provider="fake",
        model_id="fake-large-v0",
        price_key="fake-large",
        max_output_tokens_cap=1024,
    ),
)


def build_lab_registry() -> ProfileRegistry:
    """Lab registry (FakeProvider only). Refused in production."""
    return ProfileRegistry(LAB_PROFILES)
