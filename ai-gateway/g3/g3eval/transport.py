"""
Transport interface of the G3 evaluator.

The evaluator only knows the `Transport` protocol. Tests use FakeTransport
(fake.py). `RealOllamaTransport` exists ONLY as a switched-OFF interface:
it refuses to be built unless ALL THREE locks are open:

  1. REAL_TRANSPORT_BUILD_ENABLED is True in this file (a reviewed code
     change; it ships as False);
  2. the caller passes enabled=True explicitly (CLI: --real);
  3. an authorization file exists, parses, is not expired, names the arm,
     the dataset sha256 and a call ceiling, and was issued by a person.

Nothing in this delivery builds or calls it. Loopback only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Protocol

REAL_TRANSPORT_BUILD_ENABLED = False
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_PORT = 11434


class TransportError(Exception):
    """Base class; the evaluator records only the class name."""


class TransportDisabled(TransportError):
    pass


class TransportNotSent(TransportError):
    pass


class TransportTimeout(TransportError):
    pass


class TransportHTTPError(TransportError):
    pass


class TransportInvalidResponse(TransportError):
    pass


@dataclass(frozen=True)
class GenRequest:
    model_id: str
    instructions: str
    input_text: str
    output_schema: dict[str, Any]
    options: dict[str, Any]
    think: bool | None  # None: the key is NOT sent (non-thinking model)
    timeout_s: float


@dataclass(frozen=True)
class GenResponse:
    done_reason: str | None  # "stop" or "length"
    content: str | None  # in memory only; never persisted
    thinking_length: int | None
    output_tokens: int | None
    prompt_tokens: int | None
    load_duration_ms: int | None
    total_duration_ms: int | None
    model_digest: str | None
    runtime_version: str | None


class Transport(Protocol):
    def identity(self) -> dict[str, Any]:
        """{"version": str, "digests": {model_id: sha256 hex}}"""

    def generate(self, request: GenRequest) -> GenResponse:
        ...


def build_chat_body(request: GenRequest) -> dict[str, Any]:
    """Pure: the /api/chat body (same shape the shadow provider sends)."""
    body: dict[str, Any] = {
        "model": request.model_id,
        "messages": [
            {"role": "system", "content": request.instructions},
            {"role": "user", "content": request.input_text},
        ],
        "stream": False,
        "format": request.output_schema,
        "options": dict(request.options),
    }

    if request.think is not None:
        body["think"] = request.think

    return body


def _ns_to_ms(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value // 1_000_000

    return None


def parse_chat_response(
    raw: bytes, *, digest: str | None, version: str | None
) -> GenResponse:
    """Pure: Ollama /api/chat (stream=false) -> GenResponse."""
    try:
        doc = json.loads(raw.decode("utf-8"))
        message = doc["message"]
        assert isinstance(doc, dict) and isinstance(message, dict)
    except Exception:
        raise TransportInvalidResponse("unreadable") from None

    content = message.get("content")
    thinking = message.get("thinking")

    def count(key: str) -> int | None:
        value = doc.get(key)
        ok = isinstance(value, int) and not isinstance(value, bool)
        return value if ok and value >= 0 else None

    return GenResponse(
        done_reason=doc.get("done_reason"),
        content=content if isinstance(content, str) else None,
        thinking_length=len(thinking) if isinstance(thinking, str) else None,
        output_tokens=count("eval_count"),
        prompt_tokens=count("prompt_eval_count"),
        load_duration_ms=_ns_to_ms(doc.get("load_duration")),
        total_duration_ms=_ns_to_ms(doc.get("total_duration")),
        model_digest=digest,
        runtime_version=version,
    )


# ----------------------------------------------------------------------
# authorization file (lock 3)
# ----------------------------------------------------------------------

_REQUIRED_AUTH_KEYS = (
    "authorized_by", "authorized_at", "expires_at", "arms",
    "dataset_sha256", "max_calls",
)


def load_authorization(
    path: Path,
    *,
    now: datetime,
    arm_id: str,
    dataset_sha256: str,
) -> dict[str, Any]:
    """Returns the authorization or raises TransportDisabled."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        assert isinstance(doc, dict)
    except Exception:
        raise TransportDisabled("authorization_unreadable") from None

    for key in _REQUIRED_AUTH_KEYS:
        if key not in doc:
            raise TransportDisabled(f"authorization_missing:{key}")

    who = doc["authorized_by"]

    if not isinstance(who, str) or not who.strip():
        raise TransportDisabled("authorization_without_author")

    try:
        expires = datetime.fromisoformat(doc["expires_at"])
        issued = datetime.fromisoformat(doc["authorized_at"])
    except Exception:
        raise TransportDisabled("authorization_bad_dates") from None

    naive_now = now.tzinfo is None

    if (expires.tzinfo is None) != naive_now or (
        issued.tzinfo is None
    ) != naive_now:
        raise TransportDisabled("authorization_timezone_mismatch")

    if not issued <= now < expires:
        raise TransportDisabled("authorization_not_in_force")

    if arm_id not in doc["arms"]:
        raise TransportDisabled("authorization_arm_not_listed")

    if str(doc["dataset_sha256"]).lower() != dataset_sha256.lower():
        raise TransportDisabled("authorization_dataset_mismatch")

    ceiling = doc["max_calls"]

    if (
        isinstance(ceiling, bool)
        or not isinstance(ceiling, int)
        or ceiling < 1
    ):
        raise TransportDisabled("authorization_bad_ceiling")

    return doc


class RealOllamaTransport:
    """OFF by default. See the module docstring for the three locks."""

    def __init__(
        self,
        *,
        enabled: bool,
        authorization_path: Path | None,
        arm_id: str,
        dataset_sha256: str,
        now: Callable[[], datetime],
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        http: Callable[[str, str, bytes | None, float], tuple[int, bytes]]
        | None = None,
    ) -> None:
        if not REAL_TRANSPORT_BUILD_ENABLED:
            raise TransportDisabled("build_lock_closed")

        if enabled is not True:
            raise TransportDisabled("flag_not_set")

        if authorization_path is None or not Path(authorization_path).is_file():
            raise TransportDisabled("authorization_file_absent")

        if host not in LOOPBACK_HOSTS or not re.fullmatch(
            r"[A-Za-z0-9.:\[\]-]+", host
        ):
            raise TransportDisabled("host_not_loopback")

        if http is None:
            raise TransportDisabled("no_http_client_injected")

        self._auth = load_authorization(
            Path(authorization_path),
            now=now(),
            arm_id=arm_id,
            dataset_sha256=dataset_sha256,
        )
        self._calls = 0
        self._ceiling = int(self._auth["max_calls"])
        self._http = http
        self._version: str | None = None
        self._digests: dict[str, str] = {}

    def identity(self) -> dict[str, Any]:
        try:
            status, raw = self._http("GET", "/api/version", None, 10.0)
            version = json.loads(raw.decode("utf-8")).get("version")
            status2, raw2 = self._http("GET", "/api/tags", None, 10.0)
            models = json.loads(raw2.decode("utf-8")).get("models") or []
        except Exception:
            raise TransportInvalidResponse("identity_unreadable") from None

        if status != 200 or status2 != 200:
            raise TransportHTTPError("identity_http")

        digests: dict[str, str] = {}

        for entry in models:
            name = entry.get("name") or entry.get("model")
            raw_digest = str(entry.get("digest", "")).lower()
            digests[str(name)] = raw_digest.removeprefix("sha256:")

        self._version = version
        self._digests = digests
        return {"version": version, "digests": digests}

    def generate(self, request: GenRequest) -> GenResponse:
        if self._calls >= self._ceiling:
            raise TransportDisabled("authorization_call_ceiling")

        self._calls += 1
        body = json.dumps(build_chat_body(request)).encode("utf-8")
        status, raw = self._http(
            "POST", "/api/chat", body, request.timeout_s
        )

        if status != 200:
            raise TransportHTTPError(f"http_{status}")

        return parse_chat_response(
            raw,
            digest=self._digests.get(request.model_id),
            version=self._version,
        )
