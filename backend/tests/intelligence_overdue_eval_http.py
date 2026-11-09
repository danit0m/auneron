"""Area 3: the REAL transport of the overdue evaluation. Loopback HTTP only, standard library only.

Defined and exercised ONLY under ``tests/``. It is the one module of the evaluation that opens a
socket. It is never imported by ``app/`` and is never run against Ollama by the tests: they use a
scripted server on ``127.0.0.1`` and a temporary port.

* Loopback only: ``127.0.0.1``, ``::1`` and ``localhost`` are accepted, nothing else, and the
  connected peer address is checked again. No proxy (the environment is never read), no redirect
  followed (3xx is an error for the runner), no repeated call.
* ``http.client`` opens the connection and sends the request. The RESPONSE is read from the raw
  socket in slices of about one second and parsed by ``ResponseParser`` below, because
  ``http.client``'s buffered reader cannot be read in slices: after one socket timeout it refuses
  every later read. Slices make Ctrl+C work on Windows and let the deadline be checked even when
  the server keeps sending small blocks.
* The deadline is ABSOLUTE and monotonic, computed when the call begins: the ceiling of the
  request plus a tolerance (stage 1: 150 + 40 s, stage 2: 400 + 100 s). Arrival of blocks never
  restarts it. Between the ceiling and the deadline the answer is still accepted; the runner
  classifies it ``timeout`` with no credit. Past the deadline the connection is cut and
  ``TimeoutError`` carries the partial output.
* On disk, next to the results, ``wire/``: ``<case>.r<rep>.<stage>.request.json`` (the exact
  body, exclusive create, written BEFORE sending) and ``<...>.response.bin`` (the raw bytes
  received, appended and flushed block by block BEFORE any interpretation). If the request file
  already exists and the runner has no record of the call, the request is NOT sent again: the
  call is returned as ``interrupted`` / ``orphaned_call`` with whatever the response file holds.
* Cutting the connection does not prove that the runtime stopped generating. That is what the
  short proof against the real runtime has to show (protocol section 16).
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import intelligence_overdue_eval_runner as rn
import intelligence_overdue_eval_transport as tr

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})
# Tolerance added to the ceiling of each stage (seconds). The deadline is ceiling + tolerance.
TOLERANCE_S: Final[dict] = {rn.STAGE_ABSTENTION: 40, rn.STAGE_PLAN: 100}
SLICE_S: Final[float] = 1.0
DRAIN_S: Final[float] = 0.05  # after the body ended: how long to wait for bytes that still arrive
METADATA_TIMEOUT_S: Final[int] = 30
RECV_BYTES: Final[int] = 65536
MAX_HEAD_BYTES: Final[int] = 65536
WIRE_DIR_NAME: Final[str] = "wire"


class NotLoopback(ValueError):
    """The host is not a loopback address: refused before any connection."""


def check_loopback_host(host: object) -> str:
    if type(host) is not str or host not in LOOPBACK_HOSTS:
        raise NotLoopback("host_not_loopback")
    return host


def _peer_is_loopback(sock: socket.socket) -> bool:
    try:
        address = ipaddress.ip_address(sock.getpeername()[0].split("%")[0])
    except (OSError, ValueError):
        return False
    return (getattr(address, "ipv4_mapped", None) or address).is_loopback


def resolve_loopback_all(host: str, port: int) -> list[str]:
    """Resolve BEFORE connecting; refuse if ANY resolved address is not a loopback address.

    Returns every validated literal IP, in the order the system gave them and without repeats
    (the caller connects to literals, never to the name, so a later resolution cannot differ).
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise NotLoopback("resolution_failed") from None
    addresses = []
    for info in infos:
        text = str(info[4][0]).split("%")[0]
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            raise NotLoopback("resolved_address_invalid") from None
        if not (getattr(address, "ipv4_mapped", None) or address).is_loopback:
            raise NotLoopback("resolved_address_not_loopback")
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise NotLoopback("resolution_empty")
    return addresses


def resolve_loopback(host: str, port: int) -> str:
    """The first validated address (see ``resolve_loopback_all``)."""
    return resolve_loopback_all(host, port)[0]


def split_base_url(base_url: str) -> tuple[str, int]:
    parts = urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname is None:
        raise NotLoopback("url_not_http")
    return check_loopback_host(parts.hostname), parts.port or 80


# --- response parsing (pure) -------------------------------------------------------------

class ResponseParser:
    """Incremental HTTP/1.x response parser: status line, headers and the body.

    Handles ``Transfer-Encoding: chunked``, ``Content-Length`` and close-delimited bodies. Pure:
    ``feed`` takes bytes and returns the NEW body bytes. ``eof`` says the connection was closed.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.finished = False
        self.truncated = False
        self.malformed: str | None = None
        self._mode: str | None = None
        self._state = "head"
        self._remaining = 0
        self.trailing = bytearray()  # bytes after the end of the body (capped), kept as evidence

    @property
    def head_done(self) -> bool:
        return self._state != "head"

    def _keep_trailing(self, data: bytes) -> None:
        room = tr.MAX_BODY_BYTES - len(self.trailing)
        if room > 0:
            self.trailing += data[:room]

    def feed(self, data: bytes) -> bytes:
        """New body bytes. What comes after the end of the body goes to ``trailing``."""
        if self.malformed:
            return b""
        if self.finished:
            self._keep_trailing(data)
            return b""
        out = self._feed(data)
        if self.finished and self._buf:
            self._keep_trailing(bytes(self._buf))
            self._buf.clear()
        return out

    def _feed(self, data: bytes) -> bytes:
        out = bytearray()
        self._buf += data
        while self._state == "head":
            end = self._buf.find(b"\r\n\r\n")
            if end < 0:
                if len(self._buf) > MAX_HEAD_BYTES:
                    self.malformed = "head_too_large"
                return b""
            head = bytes(self._buf[:end]).decode("latin-1")
            del self._buf[: end + 4]
            lines = head.split("\r\n")
            parts = lines[0].split(" ", 2)
            if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
                self.malformed = "bad_status_line"
                return b""
            status = int(parts[1])
            if 100 <= status < 200:
                continue  # an interim response: the real head follows
            self.status = status
            self.headers = {}
            for line in lines[1:]:
                name, sep, value = line.partition(":")
                if sep:
                    self.headers[name.strip().lower()] = value.strip()
            if status in (204, 304):
                self.finished = True
                return b""
            if "chunked" in self.headers.get("transfer-encoding", "").lower():
                self._mode, self._state = "chunked", "size"
            elif "content-length" in self.headers:
                if re.fullmatch(r"[0-9]+", self.headers["content-length"]) is None:  # not "1_0", "+5", "-1"
                    self.malformed = "bad_content_length"
                    return b""
                self._remaining = int(self.headers["content-length"])
                self._mode, self._state = "length", "data"
                if self._remaining == 0:
                    self.finished = True
                    return b""
            else:
                self._mode, self._state = "close", "data"
        if self._mode == "close":
            out += self._buf
            self._buf.clear()
        elif self._mode == "length":
            take = min(self._remaining, len(self._buf))
            out += self._buf[:take]
            del self._buf[:take]
            self._remaining -= take
            if self._remaining == 0:
                self.finished = True
        else:
            self._feed_chunked(out)
        return bytes(out)

    def _feed_chunked(self, out: bytearray) -> None:
        while not self.finished and not self.malformed:
            if self._state == "size":
                end = self._buf.find(b"\r\n")
                if end < 0:
                    if len(self._buf) > MAX_HEAD_BYTES:  # a size line that never ends: bounded memory
                        self.malformed = "chunk_line_too_long"
                    return
                token = bytes(self._buf[:end]).split(b";", 1)[0].strip()
                if re.fullmatch(rb"[0-9A-Fa-f]+", token) is None:  # not "-1", "0x10", "1_0", "+5"
                    self.malformed = "bad_chunk_size"
                    return
                size = int(token, 16)
                del self._buf[: end + 2]
                if size == 0:
                    self._state = "trailer"
                else:
                    self._remaining, self._state = size, "data"
            elif self._state == "data":
                if not self._buf:
                    return
                take = min(self._remaining, len(self._buf))
                out += self._buf[:take]
                del self._buf[:take]
                self._remaining -= take
                if self._remaining == 0:
                    self._state = "data_end"
            elif self._state == "data_end":
                if len(self._buf) < 2:
                    return
                if bytes(self._buf[:2]) != b"\r\n":
                    self.malformed = "bad_chunk_end"
                    return
                del self._buf[:2]
                self._state = "size"
            else:  # trailer: optional header lines, then an empty line
                end = self._buf.find(b"\r\n")
                if end < 0:
                    if len(self._buf) > MAX_HEAD_BYTES:
                        self.malformed = "chunk_line_too_long"
                    return
                empty = end == 0
                del self._buf[: end + 2]
                if empty:
                    self.finished = True

    def eof(self) -> None:
        """The peer closed the connection."""
        if self.finished or self.malformed:
            return
        if self._mode == "close":
            self.finished = True
        else:
            self.truncated = True  # closed before the declared end of the body


def parse_wire_full(raw: bytes) -> tuple[int | None, bytes, bool, bytes]:
    """(status, body, complete, trailing bytes after the body) from a saved ``response.bin``."""
    parser = ResponseParser()
    body = parser.feed(raw)
    parser.eof()
    complete = parser.finished and not parser.truncated and not parser.malformed
    return parser.status, body, complete, bytes(parser.trailing)


def parse_wire(raw: bytes) -> tuple[int | None, bytes, bool]:
    """(status, body, complete) from the raw bytes saved in a ``response.bin``."""
    status, body, complete, _ = parse_wire_full(raw)
    return status, body, complete


# --- capture on disk ---------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wire_key(request: rn.ModelRequest) -> str:
    return f"{request.scenario_id}.r{request.rep}.{request.stage}"


class WireCapture:
    """``request.json`` (exclusive create) and ``response.bin`` (exclusive create, appended)."""

    def __init__(self, wire_dir: Path, key: str) -> None:
        self.request_path = Path(wire_dir) / f"{key}.request.json"
        self.response_path = Path(wire_dir) / f"{key}.response.bin"
        self._response = None
        self._hash = hashlib.sha256()
        self.received = 0
        self.request_sha256: str | None = None
        self.failure: str | None = None  # a disk failure while capturing: recorded, never fatal
        self.connected_ip: str = ""  # the validated address that accepted the connection
        self.connect_attempts = 0

    def write_request(self, body: bytes) -> None:
        """Raises ``FileExistsError`` when a request for this key was already written."""
        with open(self.request_path, "xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        self.request_sha256 = sha256_bytes(body)

    def record(self, data: bytes) -> None:
        """Raw bytes, as they arrive and before they are interpreted.

        A disk failure (``OSError``) does not stop the read: the failure is recorded, the hash
        and the byte count still follow what arrived, and the caller keeps parsing.
        """
        self._hash.update(data)
        self.received += len(data)
        if self.failure is not None:
            return
        try:
            if self._response is None:
                self._response = open(self.response_path, "xb")
            self._response.write(data)
            self._response.flush()
        except OSError as exc:
            self.failure = type(exc).__name__

    def close(self) -> None:
        """Flush, ``fsync`` and close. Never raises: it must not mask the original exception."""
        handle, self._response = self._response, None
        if handle is None:
            return
        try:
            handle.flush()
            os.fsync(handle.fileno())
        except OSError as exc:
            self.failure = self.failure or type(exc).__name__
        try:
            handle.close()
        except OSError as exc:
            self.failure = self.failure or type(exc).__name__

    def fail_request(self, exc: BaseException) -> None:
        """The request file could not be written whole: nothing is sent."""
        self.failure = type(exc).__name__

    def wire(self) -> dict:
        return {"request_sha256": self.request_sha256 or "",
                "response_sha256": self._hash.hexdigest() if self.received else "",
                "response_bytes": self.received,
                "capture_failed": 1 if self.failure else 0,
                "connected_ip": self.connected_ip,
                "connect_attempts": self.connect_attempts}


def existing_request_hash(wire_dir: Path, key: str) -> str | None:
    path = Path(wire_dir) / f"{key}.request.json"
    return sha256_bytes(path.read_bytes()) if path.exists() else None


# --- the client --------------------------------------------------------------------------

class LoopbackClient:
    """The real ``ask_model``, ``model_info`` and runtime probe over a loopback connection."""

    def __init__(self, base_url: str, *, wire_dir: Path | None = None,
                 tolerance_s: Mapping[str, float] | None = None, slice_s: float = SLICE_S,
                 clock: Callable[[], float] = time.monotonic, model: str = tr.CANDIDATE_MODEL) -> None:
        self.host, self.port = split_base_url(base_url)
        self.base_url = f"http://{'[' + self.host + ']' if ':' in self.host else self.host}:{self.port}"
        self.wire_dir = Path(wire_dir) if wire_dir is not None else None
        self.tolerance_s = dict(TOLERANCE_S if tolerance_s is None else tolerance_s)
        self.slice_s = slice_s
        self.clock = clock
        self.model = model

    # -- one exchange, absolute deadline -------------------------------------------------
    def exchange(self, method: str, path: str, body: bytes | None, deadline_s: float, *,
                 capture: WireCapture | None = None) -> tuple[int | None, bytes, dict]:
        """Returns (status, body bytes, metrics). Raises with ``partial_output`` and ``metrics``."""
        start = self.clock()
        deadline = start + deadline_s
        parser = ResponseParser()
        tracker = tr.StreamTracker()
        collected = bytearray()  # the HTTP body, capped at MAX_BODY_BYTES + 1 (enough to see an excess)
        body_total = 0  # every body byte seen, even those not kept
        raw_all = bytearray()  # everything received, capped, for bytes that are not a body
        addresses = resolve_loopback_all(self.host, self.port)  # before ANY connection

        def raw_text() -> str | None:
            return bytes(raw_all).decode("utf-8", errors="replace") or None

        def fail(exc: BaseException, *, raw: bool = False) -> BaseException:
            text = raw_text() if raw else tr.partial_text(bytes(collected))
            if text is None and parser.status is None:
                text = raw_text()  # no valid head: whatever came is not an HTTP body
            exc.partial_output = text  # type: ignore[attr-defined]
            exc.metrics = tracker.metrics()  # type: ignore[attr-defined]
            return exc

        # Each validated address in order, inside what is left of the absolute deadline: a name that
        # resolves to ::1 first must not defeat a runtime that listens only on 127.0.0.1.
        connection = None
        last_error: BaseException | None = None
        for address in addresses:
            remaining = deadline - self.clock()
            if remaining <= 0:
                last_error = last_error or TimeoutError("deadline")
                break
            if capture is not None:
                capture.connect_attempts += 1
            candidate = http.client.HTTPConnection(
                "[" + address + "]" if ":" in address else address, self.port,
                timeout=max(0.001, remaining))  # connect: what is left of the deadline
            try:
                candidate.connect()
            except OSError as exc:
                candidate.close()
                last_error = exc
                continue
            connection = candidate
            if capture is not None:
                capture.connected_ip = address
            break
        if connection is None:
            raised = fail(last_error or ConnectionError("no_address"))
            if capture is not None:
                capture.close()
                raised.wire = capture.wire()  # type: ignore[attr-defined]
            raise raised

        raised: BaseException | None = None
        try:
            try:
                if not _peer_is_loopback(connection.sock):
                    raise NotLoopback("peer_not_loopback")
                connection.sock.settimeout(max(0.001, deadline - self.clock()))  # send: same rule
                headers = {"Connection": "close", "Accept": "application/json"}
                if body is not None:
                    headers["Content-Type"] = "application/json"
                connection.request(method, path, body=body, headers=headers)
                sock = connection.sock
                while not parser.finished and not parser.malformed:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise TimeoutError("deadline")
                    sock.settimeout(max(0.001, min(self.slice_s, remaining)))
                    try:
                        data = sock.recv(RECV_BYTES)
                    except TimeoutError:
                        continue  # one slice without data: look at the deadline again
                    arrived = self.clock() - start
                    if not data:
                        parser.eof()
                        break
                    room = tr.MAX_BODY_BYTES - len(raw_all)
                    if room > 0:
                        raw_all += data[:room]
                    if capture is not None:
                        capture.record(data)  # on disk before it is interpreted; never raises
                    new_body = parser.feed(data)
                    body_total += len(new_body)
                    collected += new_body[:max(0, tr.MAX_BODY_BYTES + 1 - len(collected))]
                    tracker.feed(new_body, arrived)
                    if body_total > tr.MAX_BODY_BYTES:
                        break  # more than the limit: the reply will be `body_too_large`; stop reading
                # The body ended: a server that closes (Connection: close) answers at once; anything
                # that still arrives is bytes AFTER the body, kept as evidence and not trusted.
                if parser.finished and body_total <= tr.MAX_BODY_BYTES:
                    # One window for the whole wait: the smaller of DRAIN_S and what is left of the
                    # absolute deadline. A server that keeps emitting cannot stretch it.
                    drain_end = min(self.clock() + DRAIN_S, deadline)
                    try:
                        while len(parser.trailing) < tr.MAX_BODY_BYTES:
                            left = drain_end - self.clock()
                            if left <= 0:
                                break
                            sock.settimeout(max(0.001, left))
                            data = sock.recv(RECV_BYTES)
                            if not data:
                                break
                            room = tr.MAX_BODY_BYTES - len(raw_all)
                            if room > 0:
                                raw_all += data[:room]
                            if capture is not None:
                                capture.record(data)
                            parser.feed(data)
                    except OSError:
                        pass  # a timeout or a reset after a complete body: nothing more to read
            except (Exception, KeyboardInterrupt) as exc:
                raised = fail(exc)
                raise raised from None
        finally:
            connection.close()
            if capture is not None:
                capture.close()  # never raises, so it cannot mask the exception above
                if raised is not None:
                    raised.wire = capture.wire()  # type: ignore[attr-defined]  # also on Ctrl+C
        if parser.malformed:
            raise fail(ConnectionError(f"malformed_response_{parser.malformed}"), raw=True)
        if parser.status is None:
            raise fail(ConnectionError("no_response"), raw=True)
        # a body that ended before its declared end is flagged, not trusted as complete
        return parser.status, bytes(collected), {
            **tracker.metrics(), "truncated": bool(parser.truncated), "body_total": body_total,
            "trailing": bytes(parser.trailing).decode("utf-8", errors="replace")}

    # -- plain calls (metadata): GET/POST with an absolute timeout -------------------------
    def _path_of(self, url: str) -> str:
        parts = urlsplit(url)
        if check_loopback_host(parts.hostname) != self.host or (parts.port or 80) != self.port:
            raise NotLoopback("url_not_this_server")
        return parts.path or "/"

    def get(self, url: str, timeout_s: int = METADATA_TIMEOUT_S) -> tuple[int, bytes]:
        status, body, _ = self.exchange("GET", self._path_of(url), None, timeout_s)
        return status if status is not None else 0, body

    def post(self, url: str, body: bytes, timeout_s: int = METADATA_TIMEOUT_S) -> tuple[int, bytes]:
        status, data, _ = self.exchange("POST", self._path_of(url), body, timeout_s)
        return status if status is not None else 0, data

    # -- the pieces the runner needs ---------------------------------------------------------
    def model_info(self) -> rn.ModelInfo:
        return tr.make_model_info(self.get, base_url=self.base_url, model=self.model)

    def runtime_probe(self) -> rn.RuntimeProbe:
        return tr.make_runtime_probe(self.get, self.post, base_url=self.base_url, model=self.model)

    def unload_model(self) -> int:
        """``keep_alive: 0`` with no prompt: asks the runtime to unload. Returns the HTTP status."""
        body = tr.encode({"model": self.model, "keep_alive": 0})
        return self.post(self.base_url + tr.GENERATE_PATH, body)[0]

    def ask_model(self, request: rn.ModelRequest) -> rn.ModelReply:
        if self.wire_dir is None:
            raise RuntimeError("wire_dir_required")
        key = wire_key(request)
        body = tr.encode(tr.build_body(request, self.model))
        capture = WireCapture(self.wire_dir, key)
        try:
            self.wire_dir.mkdir(exist_ok=True)
            capture.write_request(body)
        except FileExistsError:
            return self._orphan(key)  # sent before and never recorded: NOT sent again
        except OSError as exc:
            # The request could not be written whole: NOTHING is sent. A recorded failure, with no
            # credit; the runner stops the round (`capture_failed`, resumable).
            capture.fail_request(exc)
            return rn.ModelReply("error", error="request_capture_failed", wire=capture.wire())
        deadline_s = request.timeout_s + self.tolerance_s[request.stage]

        def post(url: str, sent: bytes, timeout_s: int) -> tuple:
            if sent != body:
                raise RuntimeError("request_body_changed")
            return self._exchange_generate(deadline_s, body, capture)

        reply = tr.make_ask_model(post, base_url=self.base_url, model=self.model)(request)
        return dataclasses.replace(reply, wire=capture.wire())

    def _exchange_generate(self, deadline_s: float, body: bytes, capture: WireCapture) -> tuple:
        status, data, metrics = self.exchange("POST", tr.GENERATE_PATH, body, deadline_s,
                                              capture=capture)
        return status if status is not None else 0, data, metrics

    def _orphan(self, key: str) -> rn.ModelReply:
        request_hash = existing_request_hash(self.wire_dir, key)
        response_path = self.wire_dir / f"{key}.response.bin"
        raw = response_path.read_bytes() if response_path.exists() else b""
        _, body, _, trailing = parse_wire_full(raw)
        # no readable body (not HTTP at all, or only a head): the raw bytes go to the safety analysis
        text = tr.partial_text(body) if body else (
            raw[:tr.MAX_BODY_BYTES].decode("utf-8", errors="replace") or None)
        if trailing:  # bytes after the end of the body are evidence too
            text = (text + "\n" if text else "") + trailing.decode("utf-8", errors="replace")
        return rn.ModelReply(
            "interrupted", raw_text=text, error=rn.ORPHANED_CALL,
            wire={"request_sha256": request_hash or "",
                  "response_sha256": sha256_bytes(raw) if raw else "",
                  "response_bytes": len(raw)})
