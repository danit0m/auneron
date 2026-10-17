import json
import logging
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.intelligence.gateway.adapters.base import ProviderAdapter
from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.circuit_breaker import CircuitBreaker
from app.intelligence.gateway.concurrency import ConcurrencyLimiter
from app.intelligence.gateway.contracts import REAL_DATA_ALLOWED
from app.intelligence.gateway.contracts import SUPPORTED_MODES
from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.gateway.contracts import ModelCallRequest
from app.intelligence.gateway.contracts import ModelCallResult
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.errors import ProviderError
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderNotSentError
from app.intelligence.gateway.pricing import PRICE_TABLE
from app.intelligence.gateway.pricing import cost_micro
from app.intelligence.gateway.pricing import input_token_upper_bound
from app.intelligence.gateway.pricing import worst_case_reservation
from app.intelligence.gateway.profiles import FAKE_PROVIDER_KEY
from app.intelligence.gateway.profiles import ModelProfile
from app.intelligence.gateway.profiles import ProfileRegistry
from app.intelligence.gateway.profiles import (
    assert_fake_provider_allowed,
)
from app.intelligence.gateway.profiles import is_production_environment
from app.intelligence.gateway.redaction import log_event
from app.intelligence.gateway.redaction import request_digest
from app.intelligence.gateway.redaction import safe_error_class
from app.intelligence.gateway.redaction import sha256_hex


# Lab limits (docs/intelligence/GATEWAY_CONTRACT.md, "Lab values").
MAX_REQUEST_BYTES = 32_768
MAX_RESPONSE_BYTES = 65_536
MAX_CALL_KEY_LENGTH = 120


@dataclass(frozen=True)
class GatewayLimits:
    max_attempts: int
    call_timeout_seconds: int
    reservation_margin_bps: int

    @classmethod
    def from_settings(cls, settings: Any) -> "GatewayLimits":
        return cls(
            max_attempts=settings.intelligence_max_attempts,
            call_timeout_seconds=(
                settings.intelligence_call_timeout_seconds
            ),
            reservation_margin_bps=(
                settings.intelligence_reservation_margin_bps
            ),
        )


class StrictJsonError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    seen: dict[str, Any] = {}

    for key, value in pairs:
        if key in seen:
            raise StrictJsonError("duplicate key")
        seen[key] = value

    return seen


def _reject_constant(_: str) -> Any:
    raise StrictJsonError("non-finite number")


def strict_json_loads(text: str) -> Any:
    """json.loads that refuses duplicate keys, NaN and Infinity."""
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )


def _default_mode() -> str:
    from app.core.config import get_settings

    return get_settings().intelligence_mode


class Gateway:
    """
    Structured-output model gateway (foundation, INTELLIGENCE-IMPL-0).

    * No network code: adapters are injected; only the FakeProvider
      exists, and it is refused in production by four layers.
    * Fail-closed: mode `off` (default) refuses every call; only
      synthetic data is accepted (REAL_DATA_ALLOWED is a constant).
    * Every attempt is: reserve (T1) -> durable in_flight mark (T2)
      -> provider call (NO database transaction open) -> settle (T3)
      or unknown (T4) or release (T5).
    * Retries and fallbacks take a NEW reservation per attempt, and
      only after availability failures; never after a refusal,
      truncation, invalid schema or budget rejection.
    """

    def __init__(
        self,
        *,
        registry: ProfileRegistry,
        adapters: Mapping[str, ProviderAdapter],
        ledger: BudgetLedger,
        limits: GatewayLimits,
        mode_provider: Callable[[], str] = _default_mode,
        breaker: CircuitBreaker | None = None,
        limiter: ConcurrencyLimiter | None = None,
        max_concurrent_calls: int = 2,
    ) -> None:
        for provider_key, adapter in adapters.items():
            if provider_key != adapter.provider_kind:
                raise GatewayError(
                    GatewayErrorCode.UNKNOWN_PROVIDER,
                    "Adapter registered under a different provider.",
                )

            self._guard_adapter(adapter)

        self._registry = registry
        self._adapters = dict(adapters)
        self._ledger = ledger
        self._limits = limits
        self._mode_provider = mode_provider
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=3, cooldown_seconds=30
        )
        self._limiter = limiter or ConcurrencyLimiter(
            max_concurrent_calls
        )

    @staticmethod
    def _guard_adapter(adapter: ProviderAdapter) -> None:
        """Layer 4 of 4: any adapter that is (or claims to be) fake."""
        from app.intelligence.gateway.adapters.fake import FakeProvider

        if (
            adapter.is_fake
            or adapter.provider_kind == FAKE_PROVIDER_KEY
            or isinstance(adapter, FakeProvider)
        ):
            assert_fake_provider_allowed()

    # ------------------------------------------------------------------
    # pre-flight (raises; the ledger is untouched)
    # ------------------------------------------------------------------

    def _preflight(self, request: ModelCallRequest) -> ModelProfile:
        mode = self._mode_provider()

        if mode not in SUPPORTED_MODES:
            raise GatewayError(
                GatewayErrorCode.MODE_NOT_SUPPORTED,
                "Intelligence mode is not supported in this phase.",
            )

        if mode == "off":
            raise GatewayError(
                GatewayErrorCode.MODE_DISABLED,
                "Intelligence gateway is disabled.",
            )

        if mode == "shadow" and is_production_environment():
            raise GatewayError(
                GatewayErrorCode.MODE_NOT_SUPPORTED,
                "Shadow mode is not allowed in production.",
            )

        # Two independent conditions, both fail-closed: only the
        # SYNTHETIC enum member is ever accepted, and a constant that
        # is no longer exactly False (tampering) refuses everything.
        if (
            REAL_DATA_ALLOWED is not False
            or request.data_classification
            is not DataClassification.SYNTHETIC
        ):
            raise GatewayError(
                GatewayErrorCode.REAL_DATA_REFUSED,
                "Only synthetic data is accepted.",
            )

        if (
            not request.call_key.strip()
            or len(request.call_key) > MAX_CALL_KEY_LENGTH
            or not request.purpose.strip()
            or request.max_output_tokens < 1
            or not request.budget_ids
        ):
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Malformed request.",
            )

        if (
            input_token_upper_bound(
                request.instructions, request.input_text
            )
            > MAX_REQUEST_BYTES
        ):
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Request too large.",
            )

        try:
            Draft202012Validator.check_schema(
                dict(request.output_schema)
            )
        except SchemaError:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Invalid output schema.",
            ) from None

        profile = self._registry.get(request.profile)

        if request.max_output_tokens > profile.max_output_tokens_cap:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "max_output_tokens exceeds the profile cap.",
            )

        return profile

    def _chain(self, profile: ModelProfile) -> list[ModelProfile]:
        chain = [profile]

        for name in profile.fallbacks:
            candidate = self._registry.get(name)

            if candidate not in chain:
                chain.append(candidate)

        return chain

    def _adapter_for(self, profile: ModelProfile) -> ProviderAdapter:
        adapter = self._adapters.get(profile.provider)

        if adapter is None:
            raise GatewayError(
                GatewayErrorCode.UNKNOWN_PROVIDER,
                "No adapter for provider.",
            )

        # Runtime re-check (layer 4): production can be entered after
        # construction in tests, and an adapter object could have been
        # swapped in; the check is cheap and unconditional.
        self._guard_adapter(adapter)
        return adapter

    # ------------------------------------------------------------------
    # call
    # ------------------------------------------------------------------

    def call(self, request: ModelCallRequest) -> ModelCallResult:
        profile = self._preflight(request)
        chain = self._chain(profile)
        index = 0
        call_ids: list[int] = []
        profiles_used: list[str] = []
        parent_call_id: int | None = None
        total_micro = 0
        total_tokens = 0
        attempt = 0
        last_error = GatewayErrorCode.PROVIDER_FAILURE.value
        final_state: str | None = None

        def result(
            ok: bool,
            error: str | None,
            parsed: Any = None,
        ) -> ModelCallResult:
            return ModelCallResult(
                ok=ok,
                parsed=parsed,
                error_code=error,
                call_ids=tuple(call_ids),
                final_state=final_state,
                attempts=attempt,
                actual_micro=total_micro,
                actual_tokens=total_tokens,
                profiles_used=tuple(profiles_used),
            )

        while attempt < self._limits.max_attempts:
            attempt += 1
            current = chain[index]
            adapter = self._adapter_for(current)

            if not self._breaker.allow(current.name):
                last_error = GatewayErrorCode.CIRCUIT_OPEN.value
                log_event(
                    logging.WARNING,
                    "intelligence_circuit_open",
                    model_profile=current.name,
                    attempt=attempt,
                )

                if index + 1 < len(chain):
                    # Skipping an open profile consumes no attempt.
                    index += 1
                    attempt -= 1
                    continue

                return result(False, last_error)

            try:
                with self._limiter.slot():
                    outcome = self._attempt(
                        request=request,
                        profile=current,
                        adapter=adapter,
                        attempt=attempt,
                        parent_call_id=parent_call_id,
                    )
            except GatewayError as error:
                # Raised before/while reserving: nothing was sent.
                self._breaker.cancel_probe(current.name)
                log_event(
                    logging.WARNING,
                    "intelligence_attempt_rejected",
                    model_profile=current.name,
                    attempt=attempt,
                    error_code=error.code.value,
                )
                return result(False, error.code.value)

            if outcome.call_id is not None:
                call_ids.append(outcome.call_id)
                profiles_used.append(current.name)

                if parent_call_id is None:
                    parent_call_id = outcome.call_id

            final_state = outcome.final_state
            total_micro += outcome.actual_micro
            total_tokens += outcome.actual_tokens

            if outcome.finished:
                return result(
                    outcome.ok, outcome.error_code, outcome.parsed
                )

            # Transient availability failure: retry, falling back to
            # the next profile when one remains.
            last_error = outcome.error_code or last_error

            if index + 1 < len(chain):
                index += 1

        return result(False, last_error)

    # ------------------------------------------------------------------
    # one attempt
    # ------------------------------------------------------------------

    @dataclass(frozen=True)
    class _Outcome:
        finished: bool
        ok: bool = False
        error_code: str | None = None
        parsed: Any = None
        call_id: int | None = None
        final_state: str | None = None
        actual_micro: int = 0
        actual_tokens: int = 0

    def _attempt(
        self,
        *,
        request: ModelCallRequest,
        profile: ModelProfile,
        adapter: ProviderAdapter,
        attempt: int,
        parent_call_id: int | None,
    ) -> "_Outcome":
        outcome = Gateway._Outcome
        price = PRICE_TABLE[profile.price_key]
        timeout = self._limits.call_timeout_seconds
        reservation = worst_case_reservation(
            input_upper_bound_tokens=input_token_upper_bound(
                request.instructions,
                request.input_text,
                json.dumps(
                    dict(request.output_schema), sort_keys=True
                ),
            ),
            max_output_tokens=request.max_output_tokens,
            price=price,
            margin_bps=self._limits.reservation_margin_bps,
        )

        # T1 -- may raise GatewayError(BUDGET_*), handled by call().
        call_id = self._ledger.reserve(
            call_key=f"{request.call_key}#a{attempt}",
            purpose=request.purpose,
            provider=profile.provider,
            model_profile=profile.name,
            model_id=profile.model_id,
            reservation=reservation,
            deadline_seconds=timeout,
            user_budget_id=request.user_budget_id,
            objective_budget_id=request.objective_budget_id,
            deployment_budget_id=request.deployment_budget_id,
            attempt=attempt,
            parent_call_id=parent_call_id,
            objective_id=request.objective_id,
            plan_id=request.plan_id,
            prompt_template_id=request.prompt_template_id,
            output_schema_id=request.output_schema_id,
            data_classification=request.data_classification,
        )
        log_event(
            logging.INFO,
            "intelligence_reserved",
            call_id=call_id,
            provider=profile.provider,
            model_profile=profile.name,
            purpose=request.purpose,
            attempt=attempt,
            reserved_micro=reservation.micro,
            reserved_tokens=reservation.tokens,
        )

        provider_request = ProviderRequest(
            model_id=profile.model_id,
            instructions=request.instructions,
            input_text=request.input_text,
            output_schema=request.output_schema,
            max_output_tokens=request.max_output_tokens,
            timeout_seconds=timeout,
        )

        # T2 -- write-ahead mark, durable BEFORE any network write.
        try:
            self._ledger.mark_in_flight(
                call_id,
                timeout_seconds=timeout,
                request_digest=request_digest(
                    model_id=profile.model_id,
                    instructions=request.instructions,
                    input_text=request.input_text,
                    output_schema=dict(request.output_schema),
                    max_output_tokens=request.max_output_tokens,
                ),
            )
        except GatewayError as error:
            # State changed under us (e.g. recovery released it): the
            # request was never handed to the adapter.
            return outcome(
                finished=True,
                error_code=error.code.value,
                call_id=call_id,
                final_state=self._state_of(call_id),
            )
        except Exception as error:
            # Mark outcome is ambiguous (e.g. commit acknowledgement
            # lost). We never invoked the adapter, so this process
            # can attest "not sent" itself.
            self._release_unsent(call_id, safe_error_class(error))
            return outcome(
                finished=True,
                error_code=GatewayErrorCode.PROVIDER_FAILURE.value,
                call_id=call_id,
                final_state=self._state_of(call_id),
            )

        started = time.monotonic()

        # Provider call: NO database transaction is open here.
        try:
            response = adapter.invoke(provider_request)
        except ProviderNotSentError as error:
            self._ledger.release_never_sent(
                call_id,
                evidence_ref=error.evidence,
                error_class="ProviderNotSentError",
            )
            self._breaker.record_failure(profile.name)
            return outcome(
                finished=False,
                error_code=GatewayErrorCode.PROVIDER_FAILURE.value,
                call_id=call_id,
                final_state="released",
            )
        except ProviderError as error:
            label = (
                f"http_{error.status_code}"
                if isinstance(error, ProviderHTTPError)
                else safe_error_class(error)
            )
            # T4: 400/429/5xx/timeouts are never presumed free.
            self._ledger.mark_unknown(call_id, error_class=label)
            self._breaker_failure(profile, error)
            log_event(
                logging.WARNING,
                "intelligence_unknown",
                call_id=call_id,
                model_profile=profile.name,
                error_class=label,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return outcome(
                finished=not error.transient,
                error_code=GatewayErrorCode.PROVIDER_FAILURE.value,
                call_id=call_id,
                final_state="unknown",
            )
        except Exception as error:
            # Unexpected adapter failure after the send mark: the
            # provider may have processed it => unknown (T4).
            self._ledger.mark_unknown(
                call_id, error_class=safe_error_class(error)
            )
            self._breaker.record_failure(profile.name)
            return outcome(
                finished=True,
                error_code=GatewayErrorCode.PROVIDER_FAILURE.value,
                call_id=call_id,
                final_state="unknown",
            )

        return self._handle_response(
            request=request,
            profile=profile,
            call_id=call_id,
            response=response,
            price=price,
        )

    def _breaker_failure(
        self,
        profile: ModelProfile,
        error: ProviderError,
    ) -> None:
        # Only availability failures count toward the breaker.
        if error.transient:
            self._breaker.record_failure(profile.name)

    def _state_of(self, call_id: int) -> str | None:
        try:
            return self._ledger.get_call(call_id).state
        except Exception:
            return None

    def _release_unsent(self, call_id: int, error_class: str) -> None:
        try:
            self._ledger.release_never_sent(
                call_id,
                evidence_ref="gateway:aborted_before_send",
                error_class=error_class,
            )
        except Exception:
            # Leave it for recover(): in_flight => unknown (conservative).
            log_event(
                logging.ERROR,
                "intelligence_release_unsent_failed",
                call_id=call_id,
                error_class=error_class,
            )

    def _handle_response(
        self,
        *,
        request: ModelCallRequest,
        profile: ModelProfile,
        call_id: int,
        response: ProviderResponse,
        price: Any,
    ) -> "_Outcome":
        outcome = Gateway._Outcome
        actual_micro = cost_micro(
            response.input_tokens, response.output_tokens, price
        )
        actual_tokens = response.input_tokens + response.output_tokens
        response_digest = (
            sha256_hex(response.output_text)
            if response.output_text is not None
            else None
        )

        # T3 -- refusals and truncations are charged too.
        try:
            self._ledger.settle(
                call_id,
                actual_micro=actual_micro,
                actual_tokens=actual_tokens,
                response_digest=response_digest,
            )
            final_state = "settled"
        except GatewayError as error:
            if error.code is not GatewayErrorCode.INVALID_TRANSITION:
                raise

            # Recovery already moved it to unknown while the answer
            # was in flight. The response itself is the evidence.
            self._ledger.reconcile_unknown_settled(
                call_id,
                actual_micro=actual_micro,
                actual_tokens=actual_tokens,
                evidence_ref=(
                    f"late_response:{response.request_id or 'n/a'}"
                ),
                reconciled_by_reference="system:gateway",
            )
            final_state = "settled"

        self._breaker.record_success(profile.name)

        def finish(
            ok: bool,
            error: GatewayErrorCode | None = None,
            parsed: Any = None,
        ) -> "_Outcome":
            return outcome(
                finished=True,
                ok=ok,
                error_code=error.value if error else None,
                parsed=parsed,
                call_id=call_id,
                final_state=final_state,
                actual_micro=actual_micro,
                actual_tokens=actual_tokens,
            )

        if response.status is ProviderStatus.REFUSAL:
            return finish(False, GatewayErrorCode.REFUSAL)

        if response.status is ProviderStatus.TRUNCATED:
            return finish(False, GatewayErrorCode.TRUNCATED)

        text = response.output_text

        if (
            text is None
            or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES
        ):
            return finish(
                False, GatewayErrorCode.INVALID_SCHEMA_RESPONSE
            )

        try:
            parsed = strict_json_loads(text)
            Draft202012Validator(
                dict(request.output_schema)
            ).validate(parsed)
        except Exception:
            return finish(
                False, GatewayErrorCode.INVALID_SCHEMA_RESPONSE
            )

        return finish(True, None, parsed)
