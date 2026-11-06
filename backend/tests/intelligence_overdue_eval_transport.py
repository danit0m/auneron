"""Area 3: SIMULATED transport for the overdue evaluation. Nothing here touches a network.

Defined and exercised ONLY under ``tests/``. It builds the local request, hands it to an
INJECTED ``post(url, body, timeout_s)`` and turns whatever comes back into a
``ModelReply`` for the runner. There is no socket, no HTTP client and no Ollama client in
this module: ``post`` is supplied by the caller, and the only implementations that exist
are scripted fakes in the tests.

* The timeout is imposed ON THE CALLER: ``timeout_s`` is passed to ``post``, which must
  enforce it (and raise ``TimeoutError``). The runner measures time and classifies a late
  answer as a timeout in any case.
* The request and response SHAPES below are a simulation. The real transport, with a real
  network call, still needs the director's approval and must be checked against the real
  API before use.
* Nothing is repaired or retried, and NO failure path discards content: a bad body becomes an
  ``error`` reply that keeps the text (``errors="replace"`` for bytes that are not UTF-8,
  cut at ``MAX_BODY_BYTES``) and the original size in ``reported_timings["body_bytes"]``.
  When the envelope is readable, the model's ``response`` text is what is kept, so the
  runner can still analyse it for safety. An oversized or cut-off envelope is kept as text
  and can only be scanned for signals, not parsed (a declared limit).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Final

import intelligence_abstention_contract as ac
import intelligence_overdue_eval_runner as rn

Post = Callable[[str, bytes, int], tuple[int, bytes]]

GENERATE_PATH: Final[str] = "/api/generate"
SHOW_PATH: Final[str] = "/api/show"
CANDIDATE_MODEL: Final[str] = rn.CANDIDATE_MODEL_NAME  # candidate, not approved
# Effective parameters, fixed and hashed with this module (a proposal for the director).
EFFECTIVE_OPTIONS: Final[dict] = {"top_k": 1, "top_p": 1.0, "repeat_penalty": 1.0, "num_ctx": 8192}
THINK: Final[bool] = False
KEEP_ALIVE: Final[str] = "30m"
MAX_BODY_BYTES: Final[int] = 4_000_000
TIMING_KEYS: Final[tuple[str, ...]] = (
    "total_duration", "load_duration", "prompt_eval_duration", "eval_duration")


def build_body(request: rn.ModelRequest, model: str) -> dict:
    """The local request. Same options for every call, only the seed and limits come from the request."""
    body: dict = {
        "model": model,
        "prompt": request.prompt,
        "stream": False,
        "think": THINK,
        "keep_alive": KEEP_ALIVE,
        "options": {
            **EFFECTIVE_OPTIONS,
            "temperature": request.options["temperature"],
            "seed": request.options["seed"],
            "num_predict": request.options["num_predict"],
        },
    }
    if request.stage == rn.STAGE_ABSTENTION:
        body["format"] = ac.ABSTENTION_SCHEMA
    return body


def encode(body: Mapping) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class Decoded:
    """What came back. ``text`` is whatever could be kept, ``size`` the original byte count."""

    def __init__(self, body, text, failure, size):
        self.body, self.text, self.failure, self.size = body, text, failure, size


def _kept(data: object) -> tuple[str | None, int | None]:
    """The content to preserve, whatever happens next: replace-decoded and cut at the limit."""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[:MAX_BODY_BYTES]).decode("utf-8", errors="replace"), len(data)
    if isinstance(data, str):
        return data[:MAX_BODY_BYTES], len(data.encode("utf-8", errors="replace"))
    return None, None


def _decode(status: object, data: object) -> Decoded:
    text, size = _kept(data)
    if text == "":
        text = None  # an empty body has no content to keep
    if type(status) is not int or not isinstance(data, (bytes, bytearray)):
        return Decoded(None, text, "capture_failed", size)
    if status != 200:
        return Decoded(None, text, f"http_{status}", size)
    if not data:
        return Decoded(None, None, "capture_failed", size)
    if len(data) > MAX_BODY_BYTES:
        return Decoded(None, text, "body_too_large", size)
    try:
        strict = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        # a cut in the middle of a multibyte character is a truncated body; any other
        # byte that is not UTF-8 is a capture failure
        cut = exc.reason == "unexpected end of data" and exc.end == len(data)
        return Decoded(None, text, "body_truncated" if cut else "capture_failed", size)
    try:
        body = json.loads(strict)
    except (ValueError, RecursionError):
        return Decoded(None, strict, "body_truncated" if not strict.rstrip().endswith("}")
                       else "invalid_json", size)
    if type(body) is not dict:
        return Decoded(None, strict, "invalid_json", size)
    return Decoded(body, strict, None, size)


def _sizes(size: int | None) -> dict:
    return {} if size is None else {"body_bytes": size}


def parse_reply(status: object, data: object) -> rn.ModelReply:
    decoded = _decode(status, data)
    body, text, failure = decoded.body, decoded.text, decoded.failure
    sizes = _sizes(decoded.size)
    if failure is not None:
        return rn.ModelReply("error", raw_text=text, error=failure, reported_timings=sizes)
    # a readable envelope: keep the model's own text (what the safety analysis needs)
    kept = body["response"] if type(body.get("response")) is str else text
    if "error" in body:
        return rn.ModelReply("error", raw_text=kept, error="server_error", reported_timings=sizes)
    if body.get("done") is not True:
        return rn.ModelReply("error", raw_text=kept, error="body_truncated", reported_timings=sizes)
    if type(body.get("response")) is not str:
        return rn.ModelReply("error", raw_text=text, error="capture_failed", reported_timings=sizes)
    digest = body.get("digest")
    timings = dict(sizes)
    for key in TIMING_KEYS:
        value = _count(body.get(key))
        if value is not None:
            timings[key.replace("_duration", "_ms")] = value // 1_000_000
    return rn.ModelReply(
        "ok", raw_text=body["response"],
        input_tokens=_count(body.get("prompt_eval_count")),
        output_tokens=_count(body.get("eval_count")),
        model_digest=digest if type(digest) is str else None,
        reported_timings=timings,
        error="done_reason_length" if body.get("done_reason") == "length" else None,
    )


def _partial(exc: BaseException) -> str | None:
    """Output captured before the caller's failure, when it attached any."""
    partial = getattr(exc, "partial_output", None)
    return partial[:MAX_BODY_BYTES] if type(partial) is str else None


def make_ask_model(post: Post, *, base_url: str, model: str = CANDIDATE_MODEL) -> rn.AskModel:
    """``ask_model`` for the runner. The timeout of the request is passed to ``post``."""
    url = base_url.rstrip("/") + GENERATE_PATH

    def ask_model(request: rn.ModelRequest) -> rn.ModelReply:
        body = encode(build_body(request, model))
        try:
            status, data = post(url, body, request.timeout_s)
        except TimeoutError as exc:
            return rn.ModelReply("timeout", raw_text=_partial(exc), error="timeout")
        except Exception as exc:  # the caller's transport failed: recorded, not retried
            return rn.ModelReply("error", raw_text=_partial(exc), error=type(exc).__name__)
        return parse_reply(status, data)

    return ask_model


def make_model_info(post: Post, *, base_url: str, model: str = CANDIDATE_MODEL,
                    timeout_s: int = 30) -> rn.ModelInfo:
    """``model_info`` for the runner: the digest of the model that will answer."""
    url = base_url.rstrip("/") + SHOW_PATH

    def model_info() -> Mapping[str, object]:
        try:
            status, data = post(url, encode({"model": model}), timeout_s)
        except TimeoutError:
            raise RuntimeError("model_info_timeout") from None
        decoded = _decode(status, data)
        if decoded.failure is not None:
            raise RuntimeError(f"model_info_{decoded.failure}")
        digest = decoded.body.get("digest")
        if type(digest) is not str or not digest:
            raise RuntimeError("model_info_digest_missing")
        return {"digest": digest}

    return model_info
