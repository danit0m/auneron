"""Area 3: PURE transport logic for the overdue evaluation. Nothing here touches a network.

Defined and exercised ONLY under ``tests/``. It builds the local request, hands it to an
INJECTED ``post(url, body, timeout_s)`` (and ``get(url, timeout_s)``) and turns whatever
comes back into a ``ModelReply`` for the runner. There is no socket, no HTTP client and no
Ollama client in this module: the real I/O lives in ``intelligence_overdue_eval_http.py``
(loopback only) and the scripted fakes live in the tests.

* ``/api/generate`` is requested with ``stream`` ON. The body is newline-delimited JSON; the
  parser here is pure (bytes in, ``ModelReply`` out) and works without a socket.
* The timeout is imposed ON THE CALLER: ``timeout_s`` is passed to ``post``, which must
  enforce it (and raise ``TimeoutError``). The runner measures time and classifies a late
  answer as a timeout in any case.
* Nothing is repaired or retried, and NO failure path discards content: a bad body becomes an
  ``error`` reply that keeps the text (``errors="replace"`` for bytes that are not UTF-8,
  cut at ``MAX_BODY_BYTES``) and the original size in ``reported_timings["body_bytes"]``.
  When the envelope is readable, the model's ``response`` text is what is kept, so the
  runner can still analyse it for safety. An oversized or cut-off envelope is kept as text
  and can only be scanned for signals, not parsed (a declared limit).
* Durations are kept in NANOSECONDS, as the runtime reports them.
* The shapes of the real API are ASSUMPTIONS declared in the protocol; the short proof
  against the real runtime has not been done.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Final

import intelligence_abstention_contract as ac
import intelligence_overdue_eval_runner as rn

Post = Callable[..., tuple]  # (url, body, timeout_s) -> (status, bytes) or (status, bytes, metrics)
Get = Callable[[str, int], tuple[int, bytes]]

GENERATE_PATH: Final[str] = "/api/generate"
SHOW_PATH: Final[str] = "/api/show"
TAGS_PATH: Final[str] = "/api/tags"
PS_PATH: Final[str] = "/api/ps"
VERSION_PATH: Final[str] = "/api/version"
CANDIDATE_MODEL: Final[str] = rn.CANDIDATE_MODEL_NAME  # candidate, not approved
# Effective parameters, fixed and hashed with this module (a proposal for the director).
EFFECTIVE_OPTIONS: Final[dict] = {"top_k": 1, "top_p": 1.0, "repeat_penalty": 1.0, "num_ctx": 8192}
THINK: Final[bool] = False
STREAM: Final[bool] = True
KEEP_ALIVE: Final[str] = "30m"
MAX_BODY_BYTES: Final[int] = 4_000_000
TIMING_KEYS: Final[tuple[str, ...]] = (
    "total_duration", "load_duration", "prompt_eval_duration", "eval_duration")


def build_body(request: rn.ModelRequest, model: str) -> dict:
    """The local request. Same options for every call, only the seed and limits come from the request."""
    body: dict = {
        "model": model,
        "prompt": request.prompt,
        "stream": STREAM,
        "think": THINK,
        "keep_alive": KEEP_ALIVE,
        "options": {
            **EFFECTIVE_OPTIONS,
            "temperature": request.options["temperature"],
            "seed": request.options["seed"],
            "num_predict": request.options["num_predict"],
        },
    }
    # `format` on BOTH stages. The candidate model is a reasoning-only build for which
    # `think: false` does not switch reasoning off (protocol, section 18); it is the grammar
    # of `format` that makes the text start with `{` and be one JSON object.
    if request.stage == rn.STAGE_ABSTENTION:
        body["format"] = ac.ABSTENTION_SCHEMA
    elif request.stage == rn.STAGE_PLAN:
        body["format"] = rn.plan_format_schema()
    return body


def encode(body: Mapping) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class Decoded:
    """What came back. ``objects`` are the parsed JSON lines, ``text`` whatever could be kept."""

    def __init__(self, objects, text, failure, size):
        self.objects, self.text, self.failure, self.size = objects, text, failure, size

    @property
    def body(self):
        return self.objects[-1] if self.objects else None


def _kept(data: object) -> tuple[str | None, int | None]:
    """The content to preserve, whatever happens next: replace-decoded and cut at the limit."""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[:MAX_BODY_BYTES]).decode("utf-8", errors="replace"), len(data)
    if isinstance(data, str):
        return data[:MAX_BODY_BYTES], len(data.encode("utf-8", errors="replace"))
    return None, None


def _joined(objects: list, key: str = "response") -> str | None:
    """Concatenation of the text pieces of the parsed lines (None when no line carries one)."""
    pieces = [o[key] for o in objects if type(o) is dict and type(o.get(key)) is str]
    return "".join(pieces) if pieces else None


def partial_text(data: object) -> str | None:
    """The model text in bytes that may be incomplete or cut: pieces of every complete line.

    Lenient on purpose (used for cut, late and orphaned evidence): lines that do not parse are
    skipped; with no readable line the replace-decoded raw text is returned.
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    text = bytes(data[:MAX_BODY_BYTES]).decode("utf-8", errors="replace")
    objects = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if type(item) is dict:
            objects.append(item)
    joined = _joined(objects)
    if joined is not None:
        return joined[:MAX_BODY_BYTES]
    return text or None


def _decode(status: object, data: object) -> Decoded:
    text, size = _kept(data)
    if text == "":
        text = None  # an empty body has no content to keep
    if type(status) is not int or not isinstance(data, (bytes, bytearray)):
        return Decoded([], text, "capture_failed", size)
    if status != 200:
        return Decoded([], text, f"http_{status}", size)
    if not data:
        return Decoded([], None, "capture_failed", size)
    if len(data) > MAX_BODY_BYTES:
        return Decoded([], text, "body_too_large", size)
    try:
        strict = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        # a cut in the middle of a multibyte character is a truncated body; any other
        # byte that is not UTF-8 is a capture failure
        cut = exc.reason == "unexpected end of data" and exc.end == len(data)
        # a stream cut inside a character: keep the model text of the complete lines
        return Decoded([], partial_text(data) if cut else text,
                       "body_truncated" if cut else "capture_failed", size)
    lines = [line for line in strict.split("\n") if line.strip()]
    if not lines:
        return Decoded([], text, "capture_failed", size)
    objects: list = []
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            last = index == len(lines) - 1
            failure = "body_truncated" if last and not line.rstrip().endswith("}") else "invalid_json"
            return Decoded(objects, _joined(objects) or strict, failure, size)
        if type(item) is not dict:
            return Decoded(objects, _joined(objects) or strict, "invalid_json", size)
        objects.append(item)
    return Decoded(objects, strict, None, size)


def _sizes(size: int | None) -> dict:
    return {} if size is None else {"body_bytes": size}


def _metric(metrics: object, key: str) -> float | None:
    if isinstance(metrics, Mapping):
        value = metrics.get(key)
        if type(value) in (int, float) and value >= 0 and value == value and value != float("inf"):
            return float(value)
    return None


def parse_reply(status: object, data: object, metrics: Mapping | None = None,
                model: str = CANDIDATE_MODEL) -> rn.ModelReply:
    """Pure: the whole response body (newline-delimited JSON, or one object) to a reply.

    Bytes that came AFTER the end of the body (``metrics["trailing"]``) make the reply an error
    (``trailing_bytes``, never credited) and are appended to ``raw_text`` for the safety analysis.
    """
    reply = _parse_body(status, data, metrics, model)
    trailing = metrics.get("trailing") if isinstance(metrics, Mapping) else None
    if type(trailing) is str and trailing:
        kept = reply.raw_text or ""
        text = (kept + "\n" if kept else "") + trailing[:MAX_BODY_BYTES]
        reply = dataclasses.replace(
            reply, status="error", raw_text=text[:MAX_BODY_BYTES * 2],
            error=reply.error if reply.status == "error" and reply.error else "trailing_bytes")
    return reply


def _parse_body(status: object, data: object, metrics: Mapping | None, model: str) -> rn.ModelReply:
    decoded = _decode(status, data)
    total = metrics.get("body_total") if isinstance(metrics, Mapping) else None
    if type(total) is int and decoded.size is not None and total > decoded.size:
        decoded.size = total  # the client stopped keeping bytes at the limit: the size it SAW
    text, failure = decoded.text, decoded.failure
    sizes = _sizes(decoded.size)
    first_chunk, first_token = _metric(metrics, "first_chunk_s"), _metric(metrics, "first_token_s")
    if failure is not None:
        return rn.ModelReply("error", raw_text=text, error=failure, reported_timings=sizes,
                             first_chunk_s=first_chunk, first_token_s=first_token)
    objects, body = decoded.objects, decoded.body
    # a readable envelope: keep the model's own text (what the safety analysis needs)
    joined = _joined(objects)
    kept = joined if joined is not None else text
    if isinstance(metrics, Mapping) and metrics.get("truncated") is True:
        # the connection ended before the declared end of the body: never trusted as complete
        return rn.ModelReply("error", raw_text=kept, error="body_truncated", reported_timings=sizes,
                             first_chunk_s=first_chunk, first_token_s=first_token)
    if any("error" in item for item in objects):
        return rn.ModelReply("error", raw_text=kept, error="server_error", reported_timings=sizes,
                             first_chunk_s=first_chunk, first_token_s=first_token)
    if body.get("done") is not True:
        return rn.ModelReply("error", raw_text=kept, error="body_truncated", reported_timings=sizes,
                             first_chunk_s=first_chunk, first_token_s=first_token)
    if joined is None:
        return rn.ModelReply("error", raw_text=text, error="capture_failed", reported_timings=sizes,
                             first_chunk_s=first_chunk, first_token_s=first_token)
    digest = body.get("digest")
    timings = dict(sizes)
    for key in TIMING_KEYS:
        value = _count(body.get(key))
        if value is not None:
            timings[key] = value  # nanoseconds, as informed
    names = [o["model"] for o in objects if type(o.get("model")) is str]
    envelope_name = next((n for n in names if n != model), names[-1] if names else None)
    thinking = _joined(objects, "thinking")
    done_reason = body.get("done_reason")
    return rn.ModelReply(
        "ok", raw_text=joined,
        input_tokens=_count(body.get("prompt_eval_count")),
        output_tokens=_count(body.get("eval_count")),
        model_digest=digest if type(digest) is str else None,
        reported_timings=timings,
        error="done_reason_length" if done_reason == "length" else None,
        model_name=envelope_name,
        done_reason=done_reason if type(done_reason) is str else None,
        thinking=thinking,
        first_chunk_s=first_chunk, first_token_s=first_token,
    )


class StreamTracker:
    """First block and first generated-content block of a stream, by the client's own clock.

    ``feed`` takes the body bytes that just arrived and the monotonic offset (seconds since the
    call started) at which they arrived. ``first_chunk_s`` is the first non-empty block;
    ``first_token_s`` the first block that completes a JSON line whose ``response`` is a
    non-empty text. Both stay ``None`` when they never happen. Pure: no clock of its own.
    """

    def __init__(self) -> None:
        self.first_chunk_s: float | None = None
        self.first_token_s: float | None = None
        self._pending = b""

    def feed(self, data: bytes, offset_s: float) -> None:
        if not data:
            return
        if self.first_chunk_s is None:
            self.first_chunk_s = offset_s
        if self.first_token_s is not None:
            return
        self._pending += data
        if len(self._pending) > MAX_BODY_BYTES:
            self._pending = b""  # a line longer than the body limit: bounded memory, no token seen
            return
        *complete, self._pending = self._pending.split(b"\n")
        for line in complete:
            try:
                item = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if type(item) is dict and type(item.get("response")) is str and item["response"] != "":
                self.first_token_s = offset_s
                return

    def metrics(self) -> dict:
        return {"first_chunk_s": self.first_chunk_s, "first_token_s": self.first_token_s}


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
            result = post(url, body, request.timeout_s)
        except TimeoutError as exc:
            metrics = getattr(exc, "metrics", None)
            return rn.ModelReply("timeout", raw_text=_partial(exc), error="timeout",
                                 first_chunk_s=_metric(metrics, "first_chunk_s"),
                                 first_token_s=_metric(metrics, "first_token_s"))
        except Exception as exc:  # the caller's transport failed: recorded, not retried
            metrics = getattr(exc, "metrics", None)
            return rn.ModelReply("error", raw_text=_partial(exc), error=type(exc).__name__,
                                 first_chunk_s=_metric(metrics, "first_chunk_s"),
                                 first_token_s=_metric(metrics, "first_token_s"))
        status, data = result[0], result[1]
        return parse_reply(status, data, result[2] if len(result) > 2 else None, model)

    return ask_model


# --- metadata calls: model_info and the runtime identity probe --------------------------

def _get_object(get: Get, url: str, timeout_s: int, label: str) -> dict:
    try:
        status, data = get(url, timeout_s)
    except TimeoutError:
        raise RuntimeError(f"{label}_timeout") from None
    decoded = _decode(status, data)
    if decoded.failure is not None:
        raise RuntimeError(f"{label}_{decoded.failure}")
    if len(decoded.objects) != 1:
        raise RuntimeError(f"{label}_invalid_json")
    return decoded.objects[0]


def catalog_digest(tags: Mapping, model: str) -> str | None:
    """Digest of the entry whose name is EXACTLY ``model`` (``qwen3:4b`` is not ``qwen3:4b-...``)."""
    models = tags.get("models")
    if type(models) is not list:
        raise RuntimeError("tags_invalid")
    matches = [m for m in models if type(m) is dict and m.get("name") == model]
    if not matches:
        return None
    digest = matches[0].get("digest")
    return digest if type(digest) is str and digest else None


def loaded_models(ps: Mapping) -> list[dict]:
    """The models the runtime reports as loaded, with the memory it reports for each."""
    models = ps.get("models")
    if type(models) is not list:
        raise RuntimeError("ps_invalid")
    out = []
    for item in models:
        if type(item) is not dict:
            raise RuntimeError("ps_invalid")
        name = item.get("name") if type(item.get("name")) is str else item.get("model")
        digest = item.get("digest")
        out.append({
            "name": name if type(name) is str else None,
            "digest": digest if type(digest) is str and digest else None,
            "size": _count(item.get("size")),
            "size_vram": _count(item.get("size_vram")),
        })
    return out


def make_model_info(get: Get, *, base_url: str, model: str = CANDIDATE_MODEL,
                    timeout_s: int = 30) -> rn.ModelInfo:
    """``model_info`` for the runner: the catalog digest of the model that will answer."""
    url = base_url.rstrip("/") + TAGS_PATH

    def model_info() -> Mapping[str, object]:
        try:
            digest = catalog_digest(_get_object(get, url, timeout_s, "model_info"), model)
        except RuntimeError as exc:
            if str(exc) == "tags_invalid":
                raise RuntimeError("model_info_invalid_json") from None
            raise
        if digest is None:
            raise RuntimeError("model_info_digest_missing")
        return {"digest": digest}

    return model_info


def _sha(value: object) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if type(value) is str and value else None


def make_runtime_probe(get: Get, post: Post, *, base_url: str, model: str = CANDIDATE_MODEL,
                       timeout_s: int = 30) -> rn.RuntimeProbe:
    """The identity probe injected in the runner. Reads only metadata; never runs inference.

    ``preflight``: Ollama version, hashes of the template and parameters of ``/api/show``,
    the catalog digest and what is loaded. ``before``: catalog digest and what is loaded.
    ``after``: what is loaded. Any failure raises ``RuntimeError`` (the runner decides).
    """
    base = base_url.rstrip("/")

    def tags() -> str | None:
        return catalog_digest(_get_object(get, base + TAGS_PATH, timeout_s, "probe_tags"), model)

    def ps() -> list[dict]:
        return loaded_models(_get_object(get, base + PS_PATH, timeout_s, "probe_ps"))

    def probe(phase: str) -> Mapping[str, object]:
        if phase == rn.PROBE_AFTER:
            return {"loaded": ps()}
        result: dict = {"catalog_digest": tags(), "loaded": ps()}
        if phase == rn.PROBE_PREFLIGHT:
            version = _get_object(get, base + VERSION_PATH, timeout_s, "probe_version").get("version")
            try:
                status, data = post(base + SHOW_PATH, encode({"model": model}), timeout_s)[:2]
            except TimeoutError:
                raise RuntimeError("probe_show_timeout") from None
            decoded = _decode(status, data)
            if decoded.failure is not None or len(decoded.objects) != 1:
                raise RuntimeError(f"probe_show_{decoded.failure or 'invalid_json'}")
            show = decoded.objects[0]
            result.update(version=version if type(version) is str and version else None,
                          template_sha256=_sha(show.get("template")),
                          parameters_sha256=_sha(show.get("parameters")))
        return result

    return probe
