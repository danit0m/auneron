from enum import Enum


class GatewayErrorCode(str, Enum):
    MODE_DISABLED = "mode_disabled"
    MODE_NOT_SUPPORTED = "mode_not_supported"
    REAL_DATA_REFUSED = "real_data_refused"
    FAKE_PROVIDER_FORBIDDEN = "fake_provider_forbidden"
    UNKNOWN_PROFILE = "unknown_profile"
    UNKNOWN_PROVIDER = "unknown_provider"
    INVALID_REQUEST = "invalid_request"
    BUDGET_NOT_FOUND = "budget_not_found"
    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_NOT_ACTIVE = "budget_not_active"
    UNKNOWN_FRACTION_BREAKER = "unknown_fraction_breaker"
    CIRCUIT_OPEN = "circuit_open"
    CONCURRENCY_LIMIT = "concurrency_limit"
    DUPLICATE_CALL_KEY = "duplicate_call_key"
    INVALID_TRANSITION = "invalid_transition"
    EVIDENCE_REQUIRED = "evidence_required"
    CALL_NOT_FOUND = "call_not_found"
    REFUSAL = "refusal"
    TRUNCATED = "truncated"
    INVALID_SCHEMA_RESPONSE = "invalid_schema_response"
    PROVIDER_FAILURE = "provider_failure"


class GatewayError(Exception):
    """
    Typed gateway failure. str(error) carries only the stable code and
    a fixed, content-free message -- never prompt, completion, provider
    message or credential text.
    """

    def __init__(
        self,
        code: GatewayErrorCode,
        message: str = "",
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.safe_message = message or code.value


# ---------------------------------------------------------------------
# Provider-side failures (raised by adapters). The class alone decides
# the ledger transition; message text is never persisted or logged.
# ---------------------------------------------------------------------


class ProviderError(Exception):
    """Base for adapter failures. transient => retry/fallback allowed."""

    transient = False
    # True only when the adapter can PROVE nothing reached the provider
    # (e.g. connection refused before any byte was written).
    proven_not_sent = False


class ProviderNotSentError(ProviderError):
    """Adapter-attested: the request was never transmitted (T5)."""

    transient = True
    proven_not_sent = True

    def __init__(self, evidence: str = "adapter_attested:not_sent"):
        super().__init__("not_sent")
        self.evidence = evidence


class ProviderTimeoutError(ProviderError):
    """Request may have been sent and processed: charge unknown (T4)."""

    transient = True


class ProviderTransportError(ProviderError):
    """Connection failure after the request may have been sent (T4)."""

    transient = True


class ProviderHTTPError(ProviderError):
    """
    Provider HTTP error. 400/429/5xx are never presumed free (T4):
    the ledger holds them as unknown until evidence says otherwise.
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"http_{status_code}")
        self.status_code = status_code
        self.transient = status_code == 429 or status_code >= 500


class SimulatedCrash(BaseException):
    """
    Test-only process-death marker. Derives from BaseException so no
    gateway `except Exception` can swallow it; it is defined here only
    so tests and the FakeProvider share one type.
    """
