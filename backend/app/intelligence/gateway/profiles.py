import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.intelligence.gateway.contracts import DETERMINISTIC_SAMPLING
from app.intelligence.gateway.contracts import SamplingOptions
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.pricing import PRICE_TABLE


# Providers the gateway knows how to build in this phase. Paid/remote
# providers are NOT here: adding one needs a new gate, a new adapter
# module, a new DB CHECK migration and PO approval. "ollama" is the
# loopback-only local runtime (S1); like every profile it is usable
# only in shadow mode with SYNTHETIC data (gateway preflight).
KNOWN_PROVIDERS: Final[frozenset[str]] = frozenset({"fake", "ollama"})
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
    # Reasoning mode and sampling are properties of the profile, not of
    # the adapter. Defaults keep every existing profile unchanged.
    think: bool = False
    sampling: SamplingOptions = DETERMINISTIC_SAMPLING


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


LOCAL_OLLAMA_PROVIDER_KEY: Final[str] = "ollama"
LOCAL_QWEN3_4B_PROFILE_NAME: Final[str] = "local-qwen3-4b"


# Qwen3-4B-Thinking-2507 (the installed qwen3:4b) always reasons. With
# think=True the runtime separates the reasoning from the answer. The
# budget must hold reasoning + the JSON answer: 4096 tokens (see
# docs/operations/S5_SHADOW_RUNBOOK.md). The cap equals that budget.
LOCAL_QWEN3_4B_THINK_OUTPUT_TOKENS: Final[int] = 4096

# Sampling = the model card's recommendation for thinking mode
# (temperature 0.6, top_p 0.95, top_k 20) with a FIXED seed. Greedy
# decoding (temperature 0) is documented by the vendor to cause endless
# repetition in reasoning, which would burn the whole budget and end as
# `length`. The seed is fixed for repeatability on the same runtime
# build; bit-identical output across builds/hardware is NOT guaranteed.
QWEN3_THINKING_SAMPLING: Final[SamplingOptions] = SamplingOptions(
    temperature=0.6, top_k=20, top_p=0.95, seed=0
)


def build_local_ollama_profile(
    *,
    name: str = LOCAL_QWEN3_4B_PROFILE_NAME,
    model_id: str = "qwen3:4b",
    max_output_tokens_cap: int = LOCAL_QWEN3_4B_THINK_OUTPUT_TOKENS,
    think: bool = True,
    sampling: SamplingOptions = QWEN3_THINKING_SAMPLING,
) -> ModelProfile:
    """Configurable local-model profile (zero price, no fallback)."""
    return ModelProfile(
        name=name,
        provider=LOCAL_OLLAMA_PROVIDER_KEY,
        model_id=model_id,
        price_key="local-zero",
        max_output_tokens_cap=max_output_tokens_cap,
        think=think,
        sampling=sampling,
    )


def build_lab_registry() -> ProfileRegistry:
    """Lab registry (FakeProvider only). Refused in production."""
    return ProfileRegistry(LAB_PROFILES)
