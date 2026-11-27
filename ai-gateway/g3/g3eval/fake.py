"""Offline fake transport (tests and dry runs). Never talks to anything."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from g3eval import verify_v3
from g3eval.transport import GenRequest
from g3eval.transport import GenResponse
from g3eval.transport import TransportNotSent
from g3eval.transport import TransportTimeout

Behaviour = Callable[[int, GenRequest, str], dict[str, Any]]


def oracle_decision(input_text: str) -> str:
    """What a perfect model answers, from the input text alone."""
    return verify_v3.truth_from_payload(json.loads(input_text))[0]


class FakeTransport:
    """
    Scripted stand-in for the runtime. `behaviour(call_index, request,
    oracle)` returns a dict with any of: decision, reason, done_reason,
    content, raise_ ("timeout"|"not_sent"), tokens, thinking_length,
    load_ms, total_ms, latency_s (advances the injected fake clock).
    """

    def __init__(
        self,
        *,
        digests: dict[str, str],
        version: str = "0.40.16",
        behaviour: Behaviour | None = None,
        advance: Callable[[float], None] | None = None,
    ) -> None:
        self.digests = digests
        self.version = version
        self.behaviour = behaviour or (lambda i, r, o: {})
        self.advance = advance or (lambda s: None)
        self.requests: list[GenRequest] = []
        self.identity_calls = 0

    def identity(self) -> dict[str, Any]:
        self.identity_calls += 1
        return {"version": self.version, "digests": dict(self.digests)}

    def generate(self, request: GenRequest) -> GenResponse:
        index = len(self.requests)
        self.requests.append(request)
        oracle = oracle_decision(request.input_text)
        plan = self.behaviour(index, request, oracle)
        self.advance(float(plan.get("latency_s", 2.0)))
        raised = plan.get("raise_")

        if raised == "timeout":
            raise TransportTimeout("timeout")

        if raised == "not_sent":
            raise TransportNotSent("not_sent")

        content = plan.get("content")

        if content is None and plan.get("done_reason", "stop") != "length":
            content = json.dumps(
                {
                    "decision": plan.get("decision", oracle),
                    "reason": plan.get("reason", "scripted"),
                }
            )

        return GenResponse(
            done_reason=plan.get("done_reason", "stop"),
            content=content,
            thinking_length=plan.get(
                "thinking_length", 120 if request.think else None
            ),
            output_tokens=plan.get("tokens", 40),
            prompt_tokens=plan.get("prompt_tokens", 700),
            load_duration_ms=plan.get("load_ms", 20),
            total_duration_ms=plan.get("total_ms", 1500),
            model_digest=self.digests.get(request.model_id),
            runtime_version=self.version,
        )
