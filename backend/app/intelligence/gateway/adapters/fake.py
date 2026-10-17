import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.intelligence.gateway.adapters.base import ProviderAdapter
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderNotSentError
from app.intelligence.gateway.errors import ProviderTimeoutError
from app.intelligence.gateway.errors import ProviderTransportError
from app.intelligence.gateway.profiles import (
    assert_fake_provider_allowed,
)


@dataclass(frozen=True)
class FakeStep:
    """One scripted provider behavior."""

    kind: str
    payload: Any = None
    input_tokens: int = 10
    output_tokens: int = 10
    status_code: int = 500
    raw_output: str | None = None


def ok(payload: Any, *, input_tokens: int = 10, output_tokens: int = 10):
    return FakeStep("ok", payload, input_tokens, output_tokens)


def raw(text: str, *, input_tokens: int = 10, output_tokens: int = 10):
    """Completed response with an arbitrary (possibly invalid) body."""
    return FakeStep(
        "ok",
        None,
        input_tokens,
        output_tokens,
        raw_output=text,
    )


def refusal(*, input_tokens: int = 10, output_tokens: int = 2):
    return FakeStep("refusal", None, input_tokens, output_tokens)


def truncated(*, input_tokens: int = 10, output_tokens: int = 50):
    return FakeStep("truncated", None, input_tokens, output_tokens)


def timeout():
    return FakeStep("timeout")


def transport_error():
    return FakeStep("transport")


def http_error(status_code: int):
    return FakeStep("http", status_code=status_code)


def not_sent():
    return FakeStep("not_sent")


class FakeProvider(ProviderAdapter):
    """
    Deterministic in-process provider for tests and the lab. Opens no
    socket, reads no credential. Forbidden in production: construction
    re-checks the central guard (layer 3 of 4).
    """

    provider_kind = "fake"

    def __init__(
        self,
        script: list[FakeStep] | None = None,
        *,
        on_invoke: Callable[[ProviderRequest], None] | None = None,
    ) -> None:
        assert_fake_provider_allowed()
        self._script: deque[FakeStep] = deque(script or [])
        self._on_invoke = on_invoke
        self.calls: list[ProviderRequest] = []

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        assert_fake_provider_allowed()
        self.calls.append(request)

        if self._on_invoke is not None:
            self._on_invoke(request)

        step = (
            self._script.popleft()
            if self._script
            else ok({"ok": True})
        )

        if step.kind == "not_sent":
            raise ProviderNotSentError()
        if step.kind == "timeout":
            raise ProviderTimeoutError("timeout")
        if step.kind == "transport":
            raise ProviderTransportError("transport")
        if step.kind == "http":
            raise ProviderHTTPError(step.status_code)
        if step.kind == "refusal":
            return ProviderResponse(
                status=ProviderStatus.REFUSAL,
                output_text=None,
                input_tokens=step.input_tokens,
                output_tokens=step.output_tokens,
                request_id="fake-req",
            )
        if step.kind == "truncated":
            return ProviderResponse(
                status=ProviderStatus.TRUNCATED,
                output_text='{"partial":',
                input_tokens=step.input_tokens,
                output_tokens=step.output_tokens,
                request_id="fake-req",
            )

        text = (
            step.raw_output
            if step.raw_output is not None
            else json.dumps(step.payload)
        )
        return ProviderResponse(
            status=ProviderStatus.COMPLETED,
            output_text=text,
            input_tokens=step.input_tokens,
            output_tokens=step.output_tokens,
            request_id="fake-req",
        )
