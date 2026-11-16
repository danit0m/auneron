"""
Local Ollama adapter (INTELLIGENCE S1) -- LOOPBACK ONLY.

This is the single module of ``app/intelligence`` allowed to open a
socket, by an explicit, nominal exception in
``tests/test_intelligence_boundaries.py`` (``NETWORK_EXCEPTION``). The
exception is limited to the stdlib ``http.client`` and to loopback
peers: the host is validated at construction (127.0.0.1, ::1,
localhost), the connected peer address is validated again after
connect, no proxy is used (``http.client`` never reads the
environment), no redirect is followed, no credential exists.

Endpoint choice: ``/api/chat`` (not ``/api/generate`` as the offline
evaluation runner uses). The gateway contract carries ``instructions``
and ``input_text`` separately, which map 1:1 to the system and user
messages; ``format`` receives the JSON Schema (structured output),
``think`` is False and the sampling options are the runner's
deterministic ones (temperature 0, top_k 1, top_p 1, seed 0). The call
is NON-streaming: the gateway needs one complete document, and the
deadline is absolute and monotonic.

Model identification, BEFORE the prompt is sent: ``/api/version`` and
``/api/tags`` (digest of the requested model). A digest different from
the approved one (or an unknown model) raises
``ProviderModelNotApprovedError`` (never_sent: the prompt was not
transmitted). After the call the ``model`` reported by the runtime must
equal the requested one, else ``ProviderModelMismatchError`` (output
dropped, call held as unknown).
"""

import http.client
import ipaddress
import json
import re
import time
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import Final

from app.intelligence.gateway.adapters.base import ProviderAdapter
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderResponse
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderInvalidResponseError
from app.intelligence.gateway.errors import ProviderModelMismatchError
from app.intelligence.gateway.errors import ProviderModelNotApprovedError
from app.intelligence.gateway.errors import ProviderNotSentError
from app.intelligence.gateway.errors import ProviderTimeoutError
from app.intelligence.gateway.errors import ProviderTransportError
from app.intelligence.gateway.pricing import input_token_upper_bound


LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "::1", "localhost"}
)
DEFAULT_PORT: Final[int] = 11434
CHAT_PATH: Final[str] = "/api/chat"
TAGS_PATH: Final[str] = "/api/tags"
VERSION_PATH: Final[str] = "/api/version"
MAX_BODY_BYTES: Final[int] = 4_000_000
IDENTIFICATION_TIMEOUT_CAP_S: Final[float] = 10.0
_READ_SLICE_BYTES: Final[int] = 65_536
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{64}")

# (method, path, body or None, timeout_s) -> (http_status, body_bytes)
# Raises TimeoutError on deadline, OSError on connection failure.
Transport = Callable[[str, str, bytes | None, float], tuple[int, bytes]]


class NonLoopbackHostError(ValueError):
    """The configured host is not a loopback address."""


def assert_loopback_host(host: str) -> str:
    if (
        not isinstance(host, str)
        or host.strip().lower() not in LOOPBACK_HOSTS
    ):
        raise NonLoopbackHostError(
            "Ollama adapter accepts only loopback hosts."
        )

    return host.strip().lower()


def _peer_is_loopback(connection: http.client.HTTPConnection) -> bool:
    sock = connection.sock

    if sock is None:
        return False

    try:
        address = sock.getpeername()[0]
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except (OSError, ValueError, IndexError):
        return False


class LoopbackHttpTransport:
    """
    Default stdlib transport. Absolute monotonic deadline per request
    (a trickling server cannot extend it), response size capped.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self._host = assert_loopback_host(host)

        if not isinstance(port, int) or not 0 < port < 65536:
            raise ValueError("Invalid port.")

        self._port = port

    def __call__(
        self,
        method: str,
        path: str,
        body: bytes | None,
        timeout_s: float,
    ) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout_s
        connection = http.client.HTTPConnection(
            self._host, self._port, timeout=max(timeout_s, 0.001)
        )

        try:
            connection.connect()

            if not _peer_is_loopback(connection):
                raise ProviderNotSentError(
                    "adapter_attested:peer_not_loopback"
                )

            headers = (
                {"Content-Type": "application/json"}
                if body is not None
                else {}
            )
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            chunks: list[bytes] = []
            size = 0

            while True:
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    raise TimeoutError("deadline")

                if connection.sock is not None:
                    connection.sock.settimeout(remaining)

                chunk = response.read1(_READ_SLICE_BYTES)

                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_BODY_BYTES:
                    raise ProviderInvalidResponseError("body_too_large")

                chunks.append(chunk)

            return response.status, b"".join(chunks)
        finally:
            connection.close()


def _normalize_digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip().lower()

    if text.startswith("sha256:"):
        text = text[len("sha256:"):]

    return text or None


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


class OllamaProvider(ProviderAdapter):
    """Loopback-only adapter for a local Ollama runtime."""

    provider_kind = "ollama"

    def __init__(
        self,
        *,
        approved_digests: Mapping[str, str],
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        transport: Transport | None = None,
    ) -> None:
        # Host is validated even when a transport is injected, so a
        # non-loopback target can never be configured.
        self._host = assert_loopback_host(host)
        self._approved: dict[str, str] = {}

        for model_id, digest in approved_digests.items():
            normalized = _normalize_digest(digest)

            if not model_id or normalized is None:
                raise ValueError("Approved digest must be non-empty.")

            # S2: the ledger stores exactly 64 lowercase hex (CHECK).
            if _DIGEST_RE.fullmatch(normalized) is None:
                raise ValueError(
                    "Approved digest must be 64 lowercase hex "
                    "characters."
                )

            self._approved[model_id] = normalized

        if not self._approved:
            raise ValueError("At least one approved digest is required.")

        self._transport: Transport = (
            transport
            if transport is not None
            else LoopbackHttpTransport(self._host, port)
        )

    # ------------------------------------------------------------------

    def _get_json(
        self, path: str, deadline: float, reason: str
    ) -> dict[str, Any]:
        """Identification read. Any failure => prompt never sent."""
        remaining = min(
            deadline - time.monotonic(), IDENTIFICATION_TIMEOUT_CAP_S
        )

        if remaining <= 0:
            raise ProviderModelNotApprovedError(f"{reason}_timeout")

        try:
            status, body = self._transport("GET", path, None, remaining)
            document = json.loads(body.decode("utf-8"))
        except ProviderNotSentError:
            raise
        except Exception:
            raise ProviderModelNotApprovedError(
                f"{reason}_unreadable"
            ) from None

        if status != 200 or not isinstance(document, dict):
            raise ProviderModelNotApprovedError(f"{reason}_unreadable")

        return document

    def _identify(
        self, model_id: str, deadline: float
    ) -> tuple[str, str]:
        approved = self._approved.get(model_id)

        if approved is None:
            raise ProviderModelNotApprovedError("no_approved_digest")

        version_doc = self._get_json(VERSION_PATH, deadline, "version")
        version = version_doc.get("version")

        if not isinstance(version, str) or not version:
            raise ProviderModelNotApprovedError("version_unreadable")

        tags_doc = self._get_json(TAGS_PATH, deadline, "tags")
        models = tags_doc.get("models")
        digest: str | None = None

        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict) and model_id in (
                    entry.get("name"),
                    entry.get("model"),
                ):
                    digest = _normalize_digest(entry.get("digest"))
                    break

        if digest is None:
            raise ProviderModelNotApprovedError("model_not_installed")

        if digest != approved:
            raise ProviderModelNotApprovedError("digest_mismatch")

        return digest, version

    @staticmethod
    def _chat_body(request: ProviderRequest) -> bytes:
        body = {
            "model": request.model_id,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.input_text},
            ],
            "stream": False,
            "think": False,
            "format": dict(request.output_schema),
            "options": {
                "temperature": 0,
                "top_k": 1,
                "top_p": 1.0,
                "seed": 0,
                "num_ctx": 8192,
                "num_predict": request.max_output_tokens,
            },
        }

        return json.dumps(
            body, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        started = time.monotonic()
        deadline = started + request.timeout_seconds

        digest, version = self._identify(request.model_id, deadline)
        payload = self._chat_body(request)
        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise ProviderNotSentError("adapter_attested:deadline_before_send")

        try:
            status, body = self._transport(
                "POST", CHAT_PATH, payload, remaining
            )
        except ProviderNotSentError:
            raise
        except ConnectionRefusedError:
            raise ProviderNotSentError(
                "adapter_attested:connection_refused"
            ) from None
        except TimeoutError:
            raise ProviderTimeoutError("timeout") from None
        except ProviderInvalidResponseError:
            raise
        except OSError:
            raise ProviderTransportError("transport") from None
        except http.client.HTTPException:
            raise ProviderTransportError("transport") from None

        if status != 200:
            raise ProviderHTTPError(status)

        latency_ms = int((time.monotonic() - started) * 1000)

        try:
            document = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ProviderInvalidResponseError("envelope_not_json") from None

        if (
            not isinstance(document, dict)
            or "error" in document
            or document.get("done") is not True
        ):
            raise ProviderInvalidResponseError("envelope_invalid")

        message = document.get("message")
        content = (
            message.get("content") if isinstance(message, dict) else None
        )
        reported = document.get("model")

        if not isinstance(content, str) or not isinstance(reported, str):
            raise ProviderInvalidResponseError("envelope_invalid")

        if reported != request.model_id:
            raise ProviderModelMismatchError("model_mismatch")

        input_tokens = _count(document.get("prompt_eval_count"))
        output_tokens = _count(document.get("eval_count"))

        # Missing counters => conservative bounds, never zero.
        if input_tokens is None:
            input_tokens = input_token_upper_bound(
                request.instructions, request.input_text
            )

        if output_tokens is None:
            output_tokens = request.max_output_tokens

        truncated = document.get("done_reason") == "length"

        return ProviderResponse(
            status=(
                ProviderStatus.TRUNCATED
                if truncated
                else ProviderStatus.COMPLETED
            ),
            output_text=content,
            input_tokens=max(input_tokens, 1),
            output_tokens=max(output_tokens, 1),
            request_id=None,
            reported_model=reported,
            model_digest=digest,
            runtime_version=version,
            latency_ms=latency_ms,
        )
