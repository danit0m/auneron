"""The REAL transport of the overdue evaluation, against a SCRIPTED server on 127.0.0.1.

Everything here talks to a server started by these tests on a temporary loopback port. No
Ollama, no model, no external network, no inference: the server answers with the 12 synthetic
scenarios of the repository (the independent set is NOT read). Nothing here is evidence about the
real runtime; the shapes of its API are assumptions declared in the protocol.

Time: the runner clock is SCALED (`FACTOR` virtual seconds per real second) and the client asks for
`ceiling / FACTOR` real seconds, so a 150 s ceiling is 1.5 s of real time. The tolerance is real
seconds too (`TOL`).
"""

import ast
import collections
import dataclasses
import hashlib
import http.client
import json
import os
import re
import select
import socket
import struct
import _thread
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import intelligence_overdue_eval_http as hl
import intelligence_overdue_eval_runner as rn
import intelligence_overdue_eval_transport as tr
from test_intelligence_overdue_eval_runner import DIGEST  # noqa: F401
from test_intelligence_overdue_eval_runner import OTHER_DIGEST
from test_intelligence_overdue_eval_runner import Clock  # noqa: F401
from test_intelligence_overdue_eval_runner import calls_of
from test_intelligence_overdue_eval_runner import dataset  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import find
from test_intelligence_overdue_eval_runner import forbidden_step_plan
from test_intelligence_overdue_eval_runner import CTA_OF
from test_intelligence_overdue_eval_runner import make_manifest
from test_intelligence_overdue_eval_runner import mutating
from test_intelligence_overdue_eval_runner import scen
from test_intelligence_overdue_eval_runner import perfect
from test_intelligence_overdue_eval_runner import real_120  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import records_of
from test_intelligence_overdue_eval_runner import report_of
from test_intelligence_overdue_eval_runner import resp
from test_intelligence_overdue_eval_runner import run
from test_intelligence_overdue_eval_runner import twelve_case_protocol  # noqa: F401  (autouse)
from test_intelligence_overdue_eval_runner import validator  # noqa: F401  (fixture)

NAME = "qwen3:4b"
LOOKALIKE = "qwen3:4b-instruct-2507-q4_K_M"
FACTOR = 100.0  # virtual seconds per real second in the tests about TIME (late, cut, stall)
SLOW = 20.0  # in every other test: ceilings of 7.5 s and 20 s, so a stall of the machine cannot matter
TOL = {"abstention": 2.5, "plan": 3.0}  # real seconds added to the ceiling
RUNTIME_SERVER = {"ollama_version": "0.40.10",
                  "template_sha256": hashlib.sha256(b"T").hexdigest(),
                  "parameters_sha256": hashlib.sha256(b"P").hexdigest()}
STAGE_OF_PROMPT = {True: rn.STAGE_ABSTENTION, False: rn.STAGE_PLAN}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def piece(text, **over):
    return {"model": NAME, "response": text, "done": False, **over}


# --- the scripted server ------------------------------------------------------------------

class Disconnected(Exception):
    pass


class Script:
    """Everything the tests can steer or inspect. Counts requests per call key."""

    def __init__(self, dataset_):
        self.answer = perfect(dataset_)
        self.lock = threading.Lock()
        self.counts = collections.Counter()  # (case, rep, stage) -> generate requests
        self.bodies, self.headers, self.sent = {}, {}, {}
        self.requests = []  # (method, path) of everything, in order
        self.behaviors, self.hooks = {}, {}
        self.cut = set()  # keys whose client was gone when the server looked
        self.tags = None  # None: the default list; otherwise a list of (name, digest)
        self.tags_status = self.ps_status = 200
        self.ps_models = None
        self.version = "0.40.10"
        self.show = {"template": "T", "parameters": "P", "details": {}}
        self.name = NAME
        self.final = {"done_reason": "stop", "prompt_eval_count": 120, "eval_count": 60,
                      "total_duration": 3_000_000_000, "load_duration": 1_000_000_000,
                      "prompt_eval_duration": 500_000_000, "eval_duration": 1_500_000_000}
        self.unloads = 0
        self.url = ""

    def generates(self):
        return sum(self.counts.values())


def request_key(data):
    prompt = data["prompt"]
    scenario_id = json.loads(prompt.split("Facts:\n", 1)[1])["scenario_id"]
    # the stage is read from the first words of the template (final prompts: "You check whether ...")
    stage = rn.STAGE_ABSTENTION if prompt.startswith("You check whether") else rn.STAGE_PLAN
    return scenario_id, data["options"]["seed"] - 1000, stage


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    key = None

    def log_message(self, *args):
        pass

    @property
    def script(self):
        return self.server.script

    # -- helpers for the behaviours ------------------------------------------------------
    def json_response(self, status, obj, extra_headers=()):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def raw_response(self, status, data, extra_headers=()):
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def begin(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def chunk(self, data):
        self.script.sent.setdefault(self.key, bytearray()).extend(data)
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")

    def line(self, obj):
        self.chunk(json.dumps(obj).encode() + b"\n")

    def finish_stream(self):
        self.wfile.write(b"0\r\n\r\n")

    def final_line(self):
        body = {"model": self.script.name, "created_at": "2026-11-06T00:00:00Z", "response": "",
                "done": True, **self.script.final}
        return {k: v for k, v in body.items() if v is not None}

    def peer_closed(self):
        readable, _, _ = select.select([self.connection], [], [], 0)
        if not readable:
            return False
        try:
            gone = self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            gone = True
        if gone and self.key is not None:
            self.script.cut.add(self.key)  # the client is gone: what the cut tests assert
        return gone

    def wait_closed(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.peer_closed():
                return True
            time.sleep(0.01)
        return False

    def reset(self):
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.connection.close()

    # -- routes ----------------------------------------------------------------------------
    def do_GET(self):
        script = self.script
        script.requests.append(("GET", self.path))
        if self.path == "/api/tags":
            if script.tags_status != 200:
                return self.raw_response(script.tags_status, b"")
            models = script.tags if script.tags is not None else [(LOOKALIKE, OTHER_DIGEST), (NAME, DIGEST)]
            return self.json_response(200, {"models": [{"name": n, "model": n, "digest": d}
                                                       for n, d in models]})
        if self.path == "/api/ps":
            if script.ps_status != 200:
                return self.raw_response(script.ps_status, b"")
            models = script.ps_models if script.ps_models is not None else [
                {"name": NAME, "model": NAME, "digest": DIGEST, "size": 4_000_000_000,
                 "size_vram": 3_000_000_000}]
            return self.json_response(200, {"models": models})
        if self.path == "/api/version":
            return self.json_response(200, {"version": script.version})
        self.raw_response(404, b"")

    def do_POST(self):
        script = self.script
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        script.requests.append(("POST", self.path))
        if self.path == "/api/show":
            return self.json_response(200, script.show)
        if self.path != "/api/generate":
            return self.raw_response(404, b"")
        data = json.loads(body)
        if "prompt" not in data:
            script.unloads += 1
            script.last_unload = data
            return self.json_response(200, {"model": data.get("model"), "done": True,
                                            "done_reason": "unload", "response": ""})
        self.key = key = request_key(data)
        with script.lock:
            script.counts[key] += 1
            script.bodies[key] = body
            script.headers[key] = {k.lower(): v for k, v in self.headers.items()}
        hook = script.hooks.get(key)
        if hook:
            hook()
        try:
            script.behaviors.get(key, default_stream)(self, key, data)
        except (Disconnected, OSError):
            script.cut.add(key)


def default_stream(handler, key, data):
    script = handler.script
    request = rn.ModelRequest(key[2], key[0], key[1], data["prompt"], {}, 0)
    text = script.answer(request).raw_text
    handler.begin()
    handler.line(piece(""))  # a block with no generated content
    for index in range(0, len(text), 40):
        handler.line(piece(text[index:index + 40]))
    handler.line(handler.final_line())
    handler.finish_stream()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


@pytest.fixture
def server(dataset):
    script = Script(dataset)
    httpd = Server(("127.0.0.1", 0), Handler)
    httpd.script = script
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    script.port = httpd.server_address[1]
    script.url = f"http://127.0.0.1:{script.port}"
    yield script
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def make_client(script, wire_dir, **kw):
    return hl.LoopbackClient(script.url, wire_dir=wire_dir, tolerance_s=kw.pop("tolerance_s", TOL),
                             slice_s=kw.pop("slice_s", 0.02), **kw)


def req(case="c001", stage=rn.STAGE_ABSTENTION, rep=0, timeout_s=1.0, dataset_=None):
    """A request whose prompt really carries the scenario (the server reads the key from it)."""
    scenario = next(s for s in dataset_.scenarios if s.scenario_id == case)
    return rn.ModelRequest(stage, case, rep, rn.render_prompt(stage, scenario), {
        "temperature": 0.0, "seed": 1000 + rep,
        "num_predict": rn.ABSTENTION_NUM_PREDICT if stage == rn.STAGE_ABSTENTION else rn.PLAN_NUM_PREDICT},
        timeout_s)


def ask(server, tmp_path, dataset_, case="c001", stage=rn.STAGE_ABSTENTION, timeout_s=1.0, **kw):
    client = make_client(server, tmp_path / "wire", **kw)
    return client.ask_model(req(case, stage, timeout_s=timeout_s, dataset_=dataset_))


def answer_text(server, case, stage=rn.STAGE_ABSTENTION):
    return server.answer(rn.ModelRequest(stage, case, 0, "", {}, 0)).raw_text


def measure_exchange(monkeypatch):
    """Spans (seconds) of every network exchange, including the ones that end in an exception.

    The absolute deadline is a property of the EXCHANGE. The time of a call also holds the
    creation and `fsync` of `request.json` (measured up to ~0.5 s on this disk, outside the
    deadline), so these tests assert on the exchange and not on the wall time of `ask_model`.
    """
    spans = []
    real = hl.LoopbackClient.exchange

    def timed(self, *args, **kwargs):
        began = time.monotonic()
        try:
            return real(self, *args, **kwargs)
        finally:
            spans.append(time.monotonic() - began)

    monkeypatch.setattr(hl.LoopbackClient, "exchange", timed)
    return spans


def assert_cut(server, key, seconds=2.0):
    """The server noticed that the client was gone (the connection was really cut)."""
    end = time.monotonic() + seconds
    while key not in server.cut and time.monotonic() < end:
        time.sleep(0.02)
    assert key in server.cut, key


def assert_one_request_each(server):
    assert server.counts and max(server.counts.values()) == 1, dict(server.counts)


# --- the round through the real transport --------------------------------------------------------

class Scaled:
    """A monotonic clock that runs FACTOR times faster than real time."""

    def __init__(self, factor=SLOW):
        self.t0 = time.monotonic()
        self.factor = factor

    def __call__(self):
        return 1000.0 + (time.monotonic() - self.t0) * self.factor


def scaled_ask(client, factor):
    def ask_model(request):
        return client.ask_model(dataclasses.replace(request, timeout_s=request.timeout_s / factor))
    return ask_model


def net_manifest(dataset_, k=1):
    return make_manifest(dataset_, k=k, params={"runtime": dict(RUNTIME_SERVER)})


def net_run(script, tmp_path, validator_, dataset_, *, resume=False, clock=None, should_stop=None,
            **client_kw):
    clock = clock or Scaled()
    client = make_client(script, tmp_path / "wire", **client_kw)
    outcome, _ = run(tmp_path, validator_, dataset_, clock=clock, fake=scaled_ask(client, clock.factor),
                     manifest=net_manifest(dataset_), info=client.model_info(), resume=resume,
                     runtime_probe=client.runtime_probe(), should_stop=should_stop)
    return outcome


def net_report(tmp_path, dataset_):
    return report_of(tmp_path, dataset_, net_manifest(dataset_))


# =============================================================================
# the pure parts: no socket
# =============================================================================

def feed_all(data, step):
    parser = hl.ResponseParser()
    body = b"".join(parser.feed(data[i:i + step]) for i in range(0, len(data), step))
    return parser, body


CHUNKED = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: x\r\n\r\n"
           b"5\r\nhello\r\n6;ext=1\r\n world\r\n0\r\n\r\n")


@pytest.mark.parametrize("step", [1, 2, 3, 7, 1000])
def test_the_parser_reads_a_chunked_body_however_the_bytes_are_split(step) -> None:
    parser, body = feed_all(CHUNKED, step)

    assert body == b"hello world" and parser.status == 200 and parser.finished
    assert parser.headers["transfer-encoding"] == "chunked"
    parser.eof()
    assert parser.truncated is False


@pytest.mark.parametrize("step", [1, 5, 1000])
def test_the_parser_reads_content_length_and_close_delimited_bodies(step) -> None:
    sized, body = feed_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 4\r\n\r\nabcdXXXX", step)
    assert body == b"abcd" and sized.status == 404 and sized.finished

    closed, body = feed_all(b"HTTP/1.0 200 OK\r\n\r\nuntil close", step)
    assert body == b"until close" and not closed.finished
    closed.eof()
    assert closed.finished and closed.truncated is False


def test_the_parser_notices_a_body_that_ends_before_its_declared_end() -> None:
    for raw in (b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc",
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhel"):
        parser, body = feed_all(raw, 3)
        assert not parser.finished
        parser.eof()
        assert parser.truncated is True and body in (b"abc", b"hel")


@pytest.mark.parametrize("raw, reason", [
    (b"garbage\r\n\r\n", "bad_status_line"),
    (b"HTTP/1.1 abc\r\n\r\n", "bad_status_line"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: x\r\n\r\n", "bad_content_length"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n", "bad_chunk_size"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nabX", "bad_chunk_end"),
])
def test_the_parser_reports_a_malformed_response(raw, reason) -> None:
    parser = hl.ResponseParser()
    parser.feed(raw)

    assert parser.malformed == reason


def test_the_parser_skips_an_interim_response_and_knows_an_empty_one() -> None:
    parser, body = feed_all(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", 4)
    assert (parser.status, body) == (200, b"ok")
    empty, _ = feed_all(b"HTTP/1.1 204 No Content\r\n\r\n", 5)
    assert empty.status == 204 and empty.finished


def test_a_saved_wire_file_is_read_back_by_the_same_parser() -> None:
    assert hl.parse_wire(CHUNKED) == (200, b"hello world", True)
    status, body, complete = hl.parse_wire(CHUNKED[:-9])
    assert (status, body, complete) == (200, b"hello wor", False)
    assert hl.parse_wire(b"") == (None, b"", False)


def test_only_loopback_hosts_are_accepted() -> None:
    for host in ("127.0.0.1", "::1", "localhost"):
        assert hl.check_loopback_host(host) == host
    for host in ("0.0.0.0", "192.168.0.10", "10.0.0.1", "example.com", "127.0.0.2", "", None, "LOCALHOST",
                 "127.0.0.1.evil.com"):
        with pytest.raises(hl.NotLoopback):
            hl.check_loopback_host(host)
    assert hl.split_base_url("http://127.0.0.1:11434") == ("127.0.0.1", 11434)
    assert hl.split_base_url("http://[::1]:5") == ("::1", 5)
    for url in ("https://127.0.0.1:1", "ftp://127.0.0.1", "http://8.8.8.8:1", "http://user@evil.com:1",
                "127.0.0.1:11434"):
        with pytest.raises(hl.NotLoopback):
            hl.split_base_url(url)


class FakeSocket:
    def __init__(self, peer):
        self.peer = peer

    def getpeername(self):
        if isinstance(self.peer, Exception):
            raise self.peer
        return self.peer


@pytest.mark.parametrize("peer, expected", [
    (("127.0.0.1", 1), True), (("::1", 1, 0, 0), True), (("::ffff:127.0.0.1", 1, 0, 0), True),
    (("8.8.8.8", 1), False), (("::ffff:8.8.8.8", 1, 0, 0), False), (("192.168.1.5", 1), False),
    (("not-an-ip", 1), False), (OSError("gone"), False),
])
def test_the_connected_peer_must_itself_be_a_loopback_address(peer, expected) -> None:
    assert hl._peer_is_loopback(FakeSocket(peer)) is expected


def test_a_client_for_a_host_outside_the_loopback_never_opens_a_socket(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    with pytest.raises(hl.NotLoopback):
        hl.LoopbackClient("http://192.0.2.7:11434")
    client = hl.LoopbackClient("http://127.0.0.1:9", wire_dir=None)
    for url in ("http://192.0.2.7:9/api/tags", "http://127.0.0.1:10/api/tags", "http://localhost:9/api/tags"):
        with pytest.raises(hl.NotLoopback):
            client.get(url)


def test_a_peer_that_turns_out_not_to_be_loopback_is_refused_after_connecting(
        server, tmp_path, dataset, monkeypatch) -> None:
    monkeypatch.setattr(hl, "_peer_is_loopback", lambda sock: False)
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "NotLoopback"
    assert server.generates() == 0  # refused after connecting and before sending anything


# =============================================================================
# the request and the capture on disk
# =============================================================================

def test_the_request_on_the_wire_is_exactly_the_encoded_body_and_is_saved_before_sending(
        server, tmp_path, dataset) -> None:
    request = req(dataset_=dataset)
    expected = tr.encode(tr.build_body(request, NAME))
    seen_before_response = {}

    def hook():
        seen_before_response["request_file"] = (tmp_path / "wire" / "c001.r0.abstention.request.json").read_bytes()

    server.hooks[("c001", 0, "abstention")] = hook
    reply = make_client(server, tmp_path / "wire").ask_model(request)
    key = ("c001", 0, "abstention")
    sent = json.loads(expected)

    assert server.bodies[key] == expected
    assert seen_before_response["request_file"] == expected  # on disk by the time the server saw it
    assert (tmp_path / "wire" / "c001.r0.abstention.request.json").read_bytes() == expected
    assert sent["stream"] is True and sent["think"] is False and sent["keep_alive"] == "30m"
    assert sent["options"] == {"top_k": 1, "top_p": 1.0, "repeat_penalty": 1.0, "num_ctx": 8192,
                               "temperature": 0.0, "seed": 1000, "num_predict": 160}
    assert sent["format"] == tr.ac.ABSTENTION_SCHEMA and sent["model"] == NAME
    assert reply.wire["request_sha256"] == sha(expected)
    headers = server.headers[key]
    assert headers["connection"] == "close" and headers["host"] == f"127.0.0.1:{server.port}"
    assert int(headers["content-length"]) == len(expected)
    assert not [h for h in headers if "proxy" in h]
    assert server.requests == [("POST", "/api/generate")]


def test_a_full_stream_is_read_saved_byte_for_byte_and_hashed(server, tmp_path, dataset) -> None:
    reply = ask(server, tmp_path, dataset, "c003")
    key = ("c003", 0, "abstention")
    saved = (tmp_path / "wire" / "c003.r0.abstention.response.bin").read_bytes()
    status, body, complete = hl.parse_wire(saved)

    assert reply.status == "ok" and reply.error is None
    assert reply.raw_text == answer_text(server, "c003")
    assert (reply.input_tokens, reply.output_tokens) == (120, 60)
    assert reply.reported_timings["total_duration"] == 3_000_000_000
    assert reply.reported_timings["eval_duration"] == 1_500_000_000
    assert reply.model_name == NAME and reply.done_reason == "stop" and reply.model_digest is None
    assert saved.startswith(b"HTTP/1.1 200") and status == 200 and complete
    assert body == bytes(server.sent[key])  # what the server wrote is exactly what was saved
    assert reply.wire == {"request_sha256": sha((tmp_path / "wire" / "c003.r0.abstention.request.json").read_bytes()),
                          "response_sha256": sha(saved), "response_bytes": len(saved),
                          "capture_failed": 0, "connected_ip": "127.0.0.1", "connect_attempts": 1}
    assert sorted(p.name for p in (tmp_path / "wire").iterdir()) == [
        "c003.r0.abstention.request.json", "c003.r0.abstention.response.bin"]


def test_the_bytes_are_on_disk_while_the_stream_is_still_running(server, tmp_path, dataset) -> None:
    observed = {}

    def slow(handler, key, data):
        handler.begin()
        handler.line(piece("first part "))
        time.sleep(0.3)
        observed["size_mid_stream"] = (tmp_path / "wire" / "c001.r0.abstention.response.bin").stat().st_size
        handler.line(piece("second"))
        handler.line(handler.final_line())
        handler.finish_stream()

    server.behaviors[("c001", 0, "abstention")] = slow
    ask(server, tmp_path, dataset)

    assert observed["size_mid_stream"] > 0  # flushed block by block, before any interpretation


def test_a_capture_is_never_overwritten(tmp_path) -> None:
    capture = hl.WireCapture(tmp_path, "k")
    capture.write_request(b"one")
    with pytest.raises(FileExistsError):
        hl.WireCapture(tmp_path, "k").write_request(b"two")
    capture.record(b"abc")
    capture.close()
    again = hl.WireCapture(tmp_path, "k")
    again.record(b"zzz")  # exclusive create fails: recorded as a capture failure, nothing overwritten
    assert again.failure == "FileExistsError" and again.wire()["capture_failed"] == 1
    assert (tmp_path / "k.request.json").read_bytes() == b"one"
    assert (tmp_path / "k.response.bin").read_bytes() == b"abc"


# =============================================================================
# metrics
# =============================================================================

def test_first_chunk_and_first_token_are_different_when_a_block_has_no_content(
        server, tmp_path, dataset) -> None:
    def slow_first_token(handler, key, data):
        handler.begin()
        handler.line(piece(""))  # a block with nothing generated yet
        time.sleep(0.3)
        handler.line(piece("{}"))
        handler.line(handler.final_line())
        handler.finish_stream()

    server.behaviors[("c001", 0, "abstention")] = slow_first_token
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "ok"
    assert 0 <= reply.first_chunk_s < 0.25
    assert reply.first_token_s - reply.first_chunk_s >= 0.25
    assert reply.first_chunk_s != reply.first_token_s


def test_the_metrics_are_null_when_they_never_happen(server, tmp_path, dataset) -> None:
    def no_content(handler, key, data):
        handler.begin()
        handler.line(piece(""))
        handler.line(handler.final_line())
        handler.finish_stream()

    server.behaviors[("c001", 0, "abstention")] = no_content
    reply = ask(server, tmp_path, dataset)
    assert reply.first_chunk_s is not None and reply.first_token_s is None

    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    refused = hl.LoopbackClient(f"http://127.0.0.1:{port}", wire_dir=tmp_path / "w2", slice_s=0.02,
                                tolerance_s=TOL).ask_model(req(timeout_s=8.0, dataset_=dataset))
    assert refused.status == "error" and refused.error == "ConnectionRefusedError"
    assert refused.first_chunk_s is None and refused.first_token_s is None


# =============================================================================
# time: absolute deadline, tolerance, late answers
# =============================================================================

def test_a_delay_before_the_first_block_is_measured_and_still_answered(server, tmp_path, dataset) -> None:
    def delayed(handler, key, data):
        time.sleep(0.4)
        default_stream(handler, key, data)

    server.behaviors[("c001", 0, "abstention")] = delayed
    reply = ask(server, tmp_path, dataset, timeout_s=1.0)

    assert reply.status == "ok" and reply.first_chunk_s >= 0.3  # the server slept 0.4 s


def test_a_stall_in_the_middle_is_cut_at_the_deadline_with_the_partial_output(
        server, tmp_path, dataset, monkeypatch) -> None:
    gone = {}
    spans = measure_exchange(monkeypatch)

    def stall(handler, key, data):
        handler.begin()
        handler.line(piece("par"))
        handler.line(piece("tial"))
        gone["closed"] = handler.wait_closed(4.0)

    server.behaviors[("c001", 0, "abstention")] = stall
    reply = ask(server, tmp_path, dataset, timeout_s=0.3, tolerance_s={"abstention": 0.2, "plan": 0.2})
    took = spans[0]

    assert reply.status == "timeout" and reply.error == "timeout" and reply.raw_text == "partial"
    assert 0.45 <= took < 1.5  # ceiling 0.3 + tolerance 0.2
    assert reply.first_chunk_s is not None
    time.sleep(0.2)
    assert gone.get("closed") is True  # the connection was really cut
    assert_cut(server, ("c001", 0, "abstention"))
    assert (tmp_path / "wire" / "c001.r0.abstention.response.bin").stat().st_size > 0


def test_an_answer_between_the_ceiling_and_the_deadline_is_accepted_and_reaches_the_runner(
        server, tmp_path, dataset) -> None:
    def late(handler, key, data):
        time.sleep(0.5)
        default_stream(handler, key, data)

    server.behaviors[("c001", 0, "abstention")] = late
    reply = ask(server, tmp_path, dataset, timeout_s=0.3, tolerance_s={"abstention": 0.6, "plan": 0.6})

    assert reply.status == "ok" and reply.first_chunk_s > 0.3  # past the ceiling, inside the tolerance


def test_an_answer_after_the_deadline_is_cut_even_if_the_server_would_finish(
        server, tmp_path, dataset, monkeypatch) -> None:
    seen = {}
    spans = measure_exchange(monkeypatch)

    def too_late(handler, key, data):
        time.sleep(1.2)
        seen["peer_gone"] = handler.peer_closed()
        default_stream(handler, key, data)

    server.behaviors[("c001", 0, "abstention")] = too_late
    reply = ask(server, tmp_path, dataset, timeout_s=0.3, tolerance_s={"abstention": 0.2, "plan": 0.2})

    assert reply.status == "timeout" and reply.raw_text is None
    assert spans[0] < 1.1  # did not wait for the server
    time.sleep(1.3)
    assert seen["peer_gone"] is True
    assert_cut(server, ("c001", 0, "abstention"))


def test_a_stream_of_small_blocks_that_never_stops_cannot_extend_the_absolute_deadline(
        server, tmp_path, dataset, monkeypatch) -> None:
    seen = {"sent": 0}
    spans = measure_exchange(monkeypatch)

    def drip(handler, key, data):
        handler.begin()
        end = time.monotonic() + 4.0
        while time.monotonic() < end:
            handler.line(piece("x"))
            seen["sent"] += 1
            time.sleep(0.02)
            if handler.peer_closed():
                seen["closed"] = True
                return
        handler.line(handler.final_line())  # a client that restarts its clock would end here
        handler.finish_stream()

    server.behaviors[("c001", 0, "abstention")] = drip
    reply = ask(server, tmp_path, dataset, timeout_s=0.3, tolerance_s={"abstention": 0.3, "plan": 0.3})
    took = spans[0]

    assert reply.status == "timeout"
    assert 0.55 <= took < 1.5  # 0.3 + 0.3, not 4 s
    assert reply.raw_text and set(reply.raw_text) == {"x"}  # what arrived is kept
    assert reply.first_chunk_s < 0.1 and reply.first_token_s < 0.1
    time.sleep(0.3)
    assert seen.get("closed") is True and seen["sent"] < 60
    assert_cut(server, ("c001", 0, "abstention"))


def test_the_slices_let_the_deadline_be_checked_even_with_no_data_at_all(
        server, tmp_path, dataset, monkeypatch) -> None:
    spans = measure_exchange(monkeypatch)
    def silent(handler, key, data):
        handler.wait_closed(3.0)

    server.behaviors[("c001", 0, "abstention")] = silent
    reply = ask(server, tmp_path, dataset, timeout_s=0.25, tolerance_s={"abstention": 0.15, "plan": 0.15},
                slice_s=1.0)  # a one second slice must not overshoot the deadline by a second

    assert reply.status == "timeout" and reply.raw_text is None
    assert spans[0] < 0.9


# =============================================================================
# failures: nothing is retried and the content is kept
# =============================================================================

def fixed(status, data, headers=()):
    def behaviour(handler, key, body):
        handler.raw_response(status, data, headers)
    return behaviour


@pytest.mark.parametrize("status, data, error", [
    (404, json.dumps({"error": "model not found"}).encode(), "http_404"),
    (500, b"internal oops", "http_500"),
    (503, b"", "http_503"),
])
def test_an_http_error_is_an_error_reply_that_keeps_the_body(server, tmp_path, dataset, status, data, error) -> None:
    server.behaviors[("c001", 0, "abstention")] = fixed(status, data)
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == error
    assert reply.raw_text == (data.decode() or None)
    assert server.counts[("c001", 0, "abstention")] == 1  # not retried


def test_an_error_in_the_middle_of_the_stream_keeps_what_came_before(server, tmp_path, dataset) -> None:
    def broken(handler, key, data):
        handler.begin()
        handler.line(piece("half a "))
        handler.line(piece("plan"))
        time.sleep(0.2)  # let the client read it: an RST discards what is still unread
        handler.reset()

    server.behaviors[("c001", 0, "abstention")] = broken
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error in {"ConnectionResetError", "ConnectionAbortedError"}
    assert reply.raw_text == "half a plan"
    assert server.counts[("c001", 0, "abstention")] == 1


def test_an_error_object_in_the_stream_is_a_server_error(server, tmp_path, dataset) -> None:
    def failing(handler, key, data):
        handler.begin()
        handler.line(piece("so far"))
        handler.line({"error": "llama runner process has terminated"})
        handler.finish_stream()

    server.behaviors[("c001", 0, "abstention")] = failing
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "server_error" and reply.raw_text == "so far"


def test_a_refused_connection_is_an_error_and_is_not_retried(tmp_path, dataset) -> None:
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    client = hl.LoopbackClient(f"http://127.0.0.1:{port}", wire_dir=tmp_path / "w", slice_s=0.02, tolerance_s=TOL)
    reply = client.ask_model(req(timeout_s=8.0, dataset_=dataset))  # Windows takes ~2 s to refuse

    assert reply.status == "error" and reply.error == "ConnectionRefusedError" and reply.raw_text is None
    assert (tmp_path / "w" / "c001.r0.abstention.request.json").exists()  # written before the attempt


def test_a_stream_that_ends_without_the_final_block_is_truncated_and_keeps_the_text(
        server, tmp_path, dataset) -> None:
    def no_final(handler, key, data):
        handler.begin()
        handler.line(piece("abc"))
        handler.line(piece("def"))
        handler.finish_stream()  # a clean end of the HTTP body, but no `done: true`

    def cut_short(handler, key, data):
        handler.begin()
        handler.line(piece("uvw"))  # the connection just closes: no terminal chunk either

    server.behaviors[("c001", 0, "abstention")] = no_final
    server.behaviors[("c002", 0, "abstention")] = cut_short
    first = ask(server, tmp_path, dataset, "c001")
    second = ask(server, tmp_path, dataset, "c002")

    assert (first.status, first.error, first.raw_text) == ("error", "body_truncated", "abcdef")
    assert (second.status, second.error, second.raw_text) == ("error", "body_truncated", "uvw")


def test_invalid_json_non_utf8_and_a_cut_multibyte_character_are_told_apart(server, tmp_path, dataset) -> None:
    def invalid(handler, key, data):
        handler.begin()
        handler.line(piece("ok so far"))
        handler.chunk(b"this is not json}\n")
        handler.line(handler.final_line())
        handler.finish_stream()

    def not_utf8(handler, key, data):
        handler.begin()
        handler.chunk(b'{"response": "\xff\xfe", "done": true}\n')
        handler.finish_stream()

    def multibyte(handler, key, data):
        handler.begin()
        handler.line(piece("café"))
        handler.chunk(('{"model": "%s", "response": "é' % NAME).encode()[:-1])  # cut inside the character
        handler.finish_stream()

    for case, behaviour in (("c001", invalid), ("c002", not_utf8), ("c003", multibyte)):
        server.behaviors[(case, 0, "abstention")] = behaviour
    one, two, three = (ask(server, tmp_path, dataset, c) for c in ("c001", "c002", "c003"))

    assert (one.error, one.raw_text) == ("invalid_json", "ok so far")
    assert two.error == "capture_failed" and "�" in two.raw_text
    assert three.error == "body_truncated" and three.raw_text.startswith("caf")


def test_a_body_over_the_size_limit_is_cut_but_kept(server, tmp_path, dataset, monkeypatch) -> None:
    monkeypatch.setattr(tr, "MAX_BODY_BYTES", 300)

    def big(handler, key, data):
        handler.begin()
        for _ in range(20):
            handler.line(piece("y" * 50))
        handler.line(handler.final_line())
        handler.finish_stream()

    server.behaviors[("c001", 0, "abstention")] = big
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "body_too_large"
    assert 0 < len(reply.raw_text) <= 300 and reply.reported_timings["body_bytes"] > 300


def test_a_length_cut_is_kept_with_its_note(server, tmp_path, dataset) -> None:
    server.final = {**server.final, "done_reason": "length"}
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "ok" and reply.error == "done_reason_length" and reply.done_reason == "length"


def test_a_malformed_http_response_is_an_error(server, tmp_path, dataset) -> None:
    def garbage(handler, key, data):
        handler.wfile.write(b"NOT HTTP AT ALL\r\n\r\n")
        handler.close_connection = True

    server.behaviors[("c001", 0, "abstention")] = garbage
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "ConnectionError"
    assert reply.raw_text == "NOT HTTP AT ALL\r\n\r\n"  # bytes outside any body still reach the analysis


# =============================================================================
# no redirect, no proxy
# =============================================================================

def test_a_redirect_is_an_error_and_is_not_followed(server, tmp_path, dataset) -> None:
    class Other(BaseHTTPRequestHandler):
        hits = 0

        def do_POST(self):
            Other.hits += 1
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args):
            pass

    other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
    thread = threading.Thread(target=other.serve_forever, daemon=True)
    thread.start()
    try:
        target = f"http://127.0.0.1:{other.server_address[1]}/api/generate"
        server.behaviors[("c001", 0, "abstention")] = fixed(307, b"", [("Location", target)])
        reply = ask(server, tmp_path, dataset)
    finally:
        other.shutdown()
        other.server_close()

    assert reply.status == "error" and reply.error == "http_307"
    assert Other.hits == 0 and server.counts[("c001", 0, "abstention")] == 1


def test_proxy_environment_variables_are_ignored(server, tmp_path, dataset, monkeypatch) -> None:
    hits = []

    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{proxy.server_address[1]}"
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, address)
    monkeypatch.setenv("NO_PROXY", "")
    try:
        reply = ask(server, tmp_path, dataset)
        info = make_client(server, tmp_path / "w2").model_info()()
    finally:
        proxy.shutdown()
        proxy.server_close()

    assert reply.status == "ok" and info == {"digest": DIGEST}
    assert hits == [] and server.counts[("c001", 0, "abstention")] == 1


# =============================================================================
# model identity against the scripted runtime
# =============================================================================

def test_the_catalog_match_is_by_exact_name_even_when_a_look_alike_comes_first(server, tmp_path) -> None:
    client = make_client(server, tmp_path / "w")

    assert client.model_info()() == {"digest": DIGEST}  # not OTHER_DIGEST of the look-alike
    server.tags = [(LOOKALIKE, OTHER_DIGEST)]
    with pytest.raises(RuntimeError, match="model_info_digest_missing"):
        client.model_info()()
    server.tags = []
    with pytest.raises(RuntimeError, match="model_info_digest_missing"):
        client.model_info()()
    server.tags_status = 500
    with pytest.raises(RuntimeError, match="model_info_http_500"):
        client.model_info()()


def test_the_probe_reads_the_scripted_runtime(server, tmp_path) -> None:
    probe = make_client(server, tmp_path / "w").runtime_probe()
    pre = probe("preflight")

    assert pre["version"] == "0.40.10" and pre["catalog_digest"] == DIGEST
    assert pre["template_sha256"] == RUNTIME_SERVER["template_sha256"]
    assert pre["parameters_sha256"] == RUNTIME_SERVER["parameters_sha256"]
    assert pre["loaded"][0]["size"] == 4_000_000_000 and pre["loaded"][0]["size_vram"] == 3_000_000_000
    server.ps_status = 500
    with pytest.raises(RuntimeError, match="probe_ps_http_500"):
        probe("after")
    assert ("POST", "/api/generate") not in server.requests  # metadata only, never inference


def test_a_whole_round_through_the_real_transport_confirms_identity_at_all_three_points(
        server, tmp_path, validator, dataset) -> None:
    outcome = net_run(server, tmp_path, validator, dataset)

    assert outcome.status == "completed"
    assert server.generates() == 15
    assert_one_request_each(server)
    for call in calls_of(tmp_path):
        assert call["identity_unconfirmed"] is False
        assert call["loaded_digest_after"] == DIGEST and call["reply_model_name"] == NAME
        assert call["wire"]["request_sha256"] and call["wire"]["response_bytes"] > 0
        assert call["first_chunk_s"] is not None and call["first_token_s"] is not None
        assert call["first_chunk_s"] <= call["first_token_s"]
        assert call["runtime_memory"] == {"size": 4_000_000_000, "size_vram": 3_000_000_000}
        assert call["reported_timings"]["eval_duration"] == 1_500_000_000
        assert call["elapsed_s"] < 100
    report = net_report(tmp_path, dataset)
    assert report["complete"] is True and rn.evaluate_criteria(report)["overall"] == "pass"
    stage1 = report["performance"]["stages"]["abstention"]
    assert stage1["generation_tokens_per_s"]["median"] == 40.0  # 60 tokens in 1.5 s of eval
    assert stage1["effective_tokens_per_s"]["median"] != 40.0  # the two rates are separate
    names = {p.name for p in (tmp_path / "wire").iterdir()}
    assert len(names) == 30 and "c001.r0.plan.request.json" in names and "c012.r0.abstention.response.bin" in names


def test_a_digest_that_changes_between_calls_stops_the_round_without_resumption(
        server, tmp_path, validator, dataset) -> None:
    server.hooks[("c003", 0, "abstention")] = lambda: setattr(server, "tags", [(NAME, OTHER_DIGEST)])
    outcome = net_run(server, tmp_path, validator, dataset)

    assert outcome.status == "stopped" and outcome.stop_reason == "model_mismatch"
    assert server.counts[("c004", 0, "abstention")] == 0 and server.generates() == 5
    with pytest.raises(rn.ResumeRefused):
        net_run(server, tmp_path, validator, dataset, resume=True)


def test_a_missing_model_stops_the_round_resumably_and_a_loaded_look_alike_makes_it_not_exclusive(
        server, tmp_path, validator, dataset) -> None:
    clock = Scaled()
    server.hooks[("c003", 0, "abstention")] = lambda: setattr(server, "tags", [(LOOKALIKE, OTHER_DIGEST)])
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock)
    assert outcome.stop_reason == "model_unconfirmed" and server.generates() == 5

    server.tags = None
    server.hooks.clear()
    server.ps_models = [{"name": NAME, "digest": DIGEST, "size": 1, "size_vram": 1},
                        {"name": LOOKALIKE, "digest": OTHER_DIGEST, "size": 1, "size_vram": 1}]
    with pytest.raises(rn.PreflightRefused) as info:
        net_run(server, tmp_path, validator, dataset, clock=clock, resume=True)
    assert "model_not_exclusive" in info.value.reasons and server.generates() == 5

    server.ps_models = None
    assert net_run(server, tmp_path, validator, dataset, clock=clock, resume=True).status == "completed"
    assert_one_request_each(server)


def test_a_different_model_name_in_the_envelope_stops_the_round_for_good(
        server, tmp_path, validator, dataset) -> None:
    def wrong_name(handler, key, data):
        handler.begin()
        handler.line(piece(answer_text(server, "c002")))
        handler.line({**handler.final_line(), "model": LOOKALIKE})
        handler.finish_stream()

    server.behaviors[("c002", 0, "abstention")] = wrong_name
    outcome = net_run(server, tmp_path, validator, dataset)

    assert outcome.stop_reason == "model_mismatch"
    assert find(tmp_path, "c002", 0, "abstention")["reply_model_name"] == LOOKALIKE
    with pytest.raises(rn.ResumeRefused):
        net_run(server, tmp_path, validator, dataset, resume=True)


def test_ps_unavailable_after_a_call_leaves_it_unconfirmed_without_credit_and_without_stage_two(
        server, tmp_path, validator, dataset) -> None:
    clock = Scaled()
    server.hooks[("c001", 0, "abstention")] = lambda: setattr(server, "ps_status", 500)
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock)
    call = find(tmp_path, "c001", 0, "abstention")

    assert outcome.stop_reason == "model_unconfirmed"
    assert call["call_status"] == "ok" and call["identity_unconfirmed"] is True
    assert call["result"] == "invalid_response" and call["model_decision"] is None

    server.ps_status = 200
    assert net_run(server, tmp_path, validator, dataset, clock=clock, resume=True).status == "completed"
    assert server.counts[("c001", 0, "plan")] == 0  # never opened
    assert_one_request_each(server)
    assert "identity_unconfirmed" in net_report(tmp_path, dataset)["incomplete_reasons"]


def test_another_model_loaded_stops_before_the_call(server, tmp_path, validator, dataset) -> None:
    other = {"name": "llama3:8b", "digest": OTHER_DIGEST, "size": 1, "size_vram": 1}

    def load_other():
        server.ps_models = [{"name": NAME, "digest": DIGEST, "size": 1, "size_vram": 1}, other]

    server.hooks[("c002", 0, "abstention")] = load_other
    outcome = net_run(server, tmp_path, validator, dataset)

    assert outcome.stop_reason == "model_not_exclusive" and server.counts[("c002", 0, "plan")] == 0


# =============================================================================
# the runner on top: late answers, partial and orphaned content, stop, Ctrl+C, resume
# =============================================================================

def late_stream(delay, text_fn):
    def behaviour(handler, key, data):
        time.sleep(delay)
        handler.begin()
        handler.line(piece(text_fn()))
        handler.line(handler.final_line())
        handler.finish_stream()
    return behaviour


def test_a_late_correct_answer_inside_the_tolerance_is_a_timeout_without_credit(
        server, tmp_path, validator, dataset) -> None:
    server.behaviors[("c003", 0, "abstention")] = late_stream(2.2, lambda: answer_text(server, "c003"))
    net_run(server, tmp_path, validator, dataset, clock=Scaled(FACTOR))
    call = find(tmp_path, "c003", 0, "abstention")

    assert call["call_status"] == "timeout" and call["reply_status"] == "ok"
    assert call["elapsed_s"] > call["timeout_s"] and call["raw_text"] == answer_text(server, "c003")
    assert call["result"] == "invalid_response" and call["late_content"] == "correct"
    report = net_report(tmp_path, dataset)
    not_due = report["abstention_by_code"]["not_due"]
    assert not_due["numerator"] == not_due["denominator"] - 1  # correct, late, no credit
    assert report["late_responses"]["stage1"]["correct"] == 1
    assert call["elapsed_s"] < (150 / FACTOR + TOL["abstention"]) * FACTOR  # inside the tolerance


def test_a_late_forbidden_capability_is_still_caught(server, tmp_path, validator, dataset) -> None:
    evil = json.dumps(forbidden_step_plan(dataset))
    server.behaviors[("c001", 0, "plan")] = late_stream(5.0, lambda: evil)
    outcome = net_run(server, tmp_path, validator, dataset, clock=Scaled(FACTOR))
    call = find(tmp_path, "c001", 0, "plan")

    assert call["call_status"] == "timeout" and call["forbidden_capability"] is True
    assert outcome.stop_reason == "forbidden_capability"


def test_a_partial_answer_cut_at_the_deadline_is_analysed_for_a_forbidden_capability(
        server, tmp_path, validator, dataset) -> None:
    evil = json.dumps(forbidden_step_plan(dataset))

    def sends_the_plan_then_stalls(handler, key, data):
        handler.begin()
        handler.line(piece(evil))  # a complete plan, but `done` never comes
        handler.wait_closed(14.0)  # longer than the deadline of stage 2 (4 s + 3 s)

    server.behaviors[("c001", 0, "plan")] = sends_the_plan_then_stalls
    outcome = net_run(server, tmp_path, validator, dataset, clock=Scaled(FACTOR))
    call = find(tmp_path, "c001", 0, "plan")

    assert call["call_status"] == "timeout" and call["raw_text"] == evil
    assert call["forbidden_capability"] is True and call["correct"] is False
    assert outcome.stop_reason == "forbidden_capability"
    assert_cut(server, ("c001", 0, "plan"))


class Crash(BaseException):
    pass


def crash_on_call_record(monkeypatch, nth):
    original = rn.ResultsLog.append
    state = {"calls": 0}

    def append(self, body):
        if body.get("kind") == "call":
            state["calls"] += 1
            if state["calls"] == nth:
                raise Crash()
        return original(self, body)

    monkeypatch.setattr(rn.ResultsLog, "append", append)
    return lambda: monkeypatch.setattr(rn.ResultsLog, "append", original)


def test_a_crash_after_sending_and_before_recording_is_never_resent(
        server, tmp_path, validator, dataset, monkeypatch) -> None:
    clock = Scaled()
    restore = crash_on_call_record(monkeypatch, 3)  # the third call: c002, stage 1
    with pytest.raises(Crash):
        net_run(server, tmp_path, validator, dataset, clock=clock)
    restore()
    key = ("c002", 0, "abstention")

    assert server.counts[key] == 1 and key not in {(c["scenario_id"], c["rep"], c["stage"]) for c in calls_of(tmp_path)}
    wire = tmp_path / "wire"
    assert (wire / "c002.r0.abstention.request.json").exists()
    saved_request = (wire / "c002.r0.abstention.request.json").read_bytes()

    outcome = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True)
    call = find(tmp_path, *key)

    assert outcome.status == "completed"
    assert server.counts[key] == 1  # the server counter proves it: sent once, never again
    assert_one_request_each(server)
    assert call["call_status"] == "interrupted" and call["error"] == "orphaned_call"
    assert call["raw_text"] == answer_text(server, "c002")  # the evidence found on disk
    assert call["result"] == "invalid_response" and call["model_decision"] is None
    assert server.counts[("c002", 0, "plan")] == 0  # no stage 2 after it
    assert call["elapsed_s"] is None and call["wire"]["response_bytes"] > 0
    assert (wire / "c002.r0.abstention.request.json").read_bytes() == saved_request  # untouched
    records = records_of(tmp_path)
    ordinary = sum(c["elapsed_s"] for c in calls_of(tmp_path) if c["elapsed_s"] is not None)
    assert rn.used_seconds(records) == pytest.approx(ordinary + 150)
    assert rn.interrupted_count(records) == 1
    assert net_report(tmp_path, dataset)["identity"]["orphaned_calls"] == 1


def test_an_orphaned_request_with_no_response_on_disk_is_still_not_resent(
        server, tmp_path, validator, dataset) -> None:
    (tmp_path / "wire").mkdir()
    (tmp_path / "wire" / "c005.r0.abstention.request.json").write_bytes(b"{}")  # sent, died before any answer
    outcome = net_run(server, tmp_path, validator, dataset)
    call = find(tmp_path, "c005", 0, "abstention")

    assert outcome.status == "completed" and server.counts[("c005", 0, "abstention")] == 0
    assert call["call_status"] == "interrupted" and call["error"] == "orphaned_call"
    assert call["raw_text"] is None and call["wire"]["response_bytes"] == 0
    assert call["wire"]["request_sha256"] == sha(b"{}")


def test_an_orphaned_forbidden_plan_stops_the_round(server, tmp_path, validator, dataset, monkeypatch) -> None:
    evil = json.dumps(forbidden_step_plan(dataset))
    server.behaviors[("c001", 0, "plan")] = late_stream(0, lambda: evil)
    clock = Scaled()
    restore = crash_on_call_record(monkeypatch, 2)  # c001 plan: sent, answered, never recorded
    with pytest.raises(Crash):
        net_run(server, tmp_path, validator, dataset, clock=clock)
    restore()
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True)
    call = find(tmp_path, "c001", 0, "plan")

    assert server.counts[("c001", 0, "plan")] == 1
    assert call["error"] == "orphaned_call" and call["forbidden_capability"] is True
    assert outcome.stop_reason == "forbidden_capability" and call["correct"] is False


def test_ctrl_c_keeps_the_partial_output_and_the_bytes_and_resume_does_not_repeat_it(
        server, tmp_path, validator, dataset) -> None:
    def stalls(handler, key, data):
        handler.begin()
        handler.line(piece('{"decision": "pro'))
        handler.wait_closed(5.0)

    server.behaviors[("c002", 0, "abstention")] = stalls
    server.hooks[("c002", 0, "abstention")] = lambda: threading.Timer(0.3, _thread.interrupt_main).start()
    clock = Scaled()
    with pytest.raises(KeyboardInterrupt):
        net_run(server, tmp_path, validator, dataset, clock=clock)
    call = find(tmp_path, "c002", 0, "abstention")

    assert call["call_status"] == "interrupted" and call["raw_text"] == '{"decision": "pro'
    assert_cut(server, ("c002", 0, "abstention"))
    assert call["result"] == "invalid_response"
    assert (tmp_path / "wire" / "c002.r0.abstention.response.bin").stat().st_size > 0

    server.behaviors.clear()
    assert net_run(server, tmp_path, validator, dataset, clock=clock, resume=True).status == "completed"
    assert server.counts[("c002", 0, "abstention")] == 1 and server.counts[("c002", 0, "plan")] == 0
    assert_one_request_each(server)


def test_the_stop_file_ends_the_round_after_the_call_in_progress_and_resume_goes_on(
        server, tmp_path, validator, dataset) -> None:
    stop = tmp_path / "STOP"
    clock = Scaled()
    server.hooks[("c002", 0, "abstention")] = lambda: stop.write_text("stop", encoding="utf-8")
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock, should_stop=stop.exists)

    assert outcome.stop_reason == "operator_stop"
    done = {(c["scenario_id"], c["stage"]) for c in calls_of(tmp_path)}
    assert ("c002", "abstention") in done and server.generates() == 3  # the call in progress finished
    assert rn.interrupted_count(records_of(tmp_path)) == 0

    stop.unlink()
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True, should_stop=stop.exists)
    assert outcome.status == "completed" and server.generates() == 15
    assert_one_request_each(server)


def test_the_whole_round_of_the_12_scenarios_with_a_stop_and_a_resume(
        server, tmp_path, validator, dataset) -> None:
    stop = tmp_path / "STOP"
    clock = Scaled()
    server.hooks[("c006", 0, "abstention")] = lambda: stop.write_text("", encoding="utf-8")
    first = net_run(server, tmp_path, validator, dataset, clock=clock, should_stop=stop.exists)
    stop.unlink()
    second = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True, should_stop=stop.exists)

    assert (first.stop_reason, second.status) == ("operator_stop", "completed")
    report = net_report(tmp_path, dataset)
    assert report["complete"] is True and rn.evaluate_criteria(report)["overall"] == "pass"
    assert server.generates() == 15 and report["calls"] == 15
    assert [r["event"] for r in records_of(tmp_path) if r["kind"] == "event"] == [
        "start", "stop", "resume", "finish"]
    assert_one_request_each(server)


@pytest.mark.parametrize("label", ["late", "error500", "reset", "timeout_cut"])
def test_stage_two_is_not_started_after_a_stage_one_that_did_not_conclude(
        server, tmp_path, validator, dataset, label) -> None:
    proceed = json.dumps(resp("c001", "proceed"))

    def reset_with_proceed(handler, key, data):
        handler.begin()
        handler.line(piece(proceed))
        time.sleep(0.2)
        handler.reset()

    def cut_with_proceed(handler, key, data):
        handler.begin()
        handler.line(piece(proceed))
        handler.wait_closed(9.0)  # longer than the deadline of stage 1 (1.5 s + 2.5 s)

    behaviours = {
        "late": late_stream(2.2, lambda: proceed),
        "error500": fixed(500, proceed.encode()),
        "reset": reset_with_proceed,
        "timeout_cut": cut_with_proceed,
    }
    server.behaviors[("c001", 0, "abstention")] = behaviours[label]
    clock = Scaled(FACTOR)
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock)
    call = find(tmp_path, "c001", 0, "abstention")

    assert outcome.status == "completed" and call["call_status"] != "ok"
    assert call["raw_text"] == proceed and call["model_decision"] is None
    assert server.counts[("c001", 0, "plan")] == 0
    again = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True)
    assert again.status == "completed" and server.counts[("c001", 0, "plan")] == 0
    assert_one_request_each(server)


def test_one_request_per_call_in_every_failure_path_and_resume(server, tmp_path, validator, dataset) -> None:
    clock = Scaled(FACTOR)
    server.behaviors[("c001", 0, "abstention")] = fixed(404, b'{"error": "model not found"}')
    server.behaviors[("c002", 0, "abstention")] = fixed(500, b"oops")
    server.behaviors[("c003", 0, "abstention")] = late_stream(2.2, lambda: answer_text(server, "c003"))
    server.behaviors[("c004", 0, "abstention")] = lambda h, k, d: (h.begin(), h.line(piece("x")), time.sleep(0.2), h.reset())
    server.behaviors[("c005", 0, "abstention")] = lambda h, k, d: (h.begin(), h.line(piece("y")), h.finish_stream())
    first = net_run(server, tmp_path, validator, dataset, clock=clock)
    assert first.stop_reason == "circuit_breaker", [(c["scenario_id"], c["call_status"], c.get("error"), c.get("internal_error"), c["raw_text"]) for c in calls_of(tmp_path)]  # five failures in a row
    before = dict(server.counts)
    assert all(v == 1 for v in before.values()) and len(before) == 5

    server.behaviors.clear()
    second = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True)
    assert second.status == "completed"
    assert all(server.counts[k] == 1 for k in before)  # the five that failed were not asked again
    assert_one_request_each(server)
    assert len(calls_of(tmp_path)) == server.generates() == 13  # 12 first stages + c011's plan


@pytest.mark.parametrize("label, final", [
    ("zero", {"eval_duration": 0}),
    ("absent", {"eval_duration": None}),
    ("count_absent", {"eval_count": None}),
    ("count_zero", {"eval_count": 0}),
])
def test_durations_absent_or_zero_leave_the_generation_speed_null_with_a_reason(
        server, tmp_path, validator, dataset, label, final) -> None:
    server.final = {**server.final, **final}
    net_run(server, tmp_path, validator, dataset)
    stage = net_report(tmp_path, dataset)["performance"]["stages"]["abstention"]

    assert stage["generation_tokens_per_s"]["median"] is None
    assert stage["generation_tokens_per_s"]["reason"] == "runtime_timing_missing"
    assert stage["generation_tokens_per_s"]["excluded"]["runtime_timing_missing"] == 12, (
        [(c["scenario_id"], c["stage"], c["call_status"], c.get("error"), c["elapsed_s"])
         for c in calls_of(tmp_path) if c["call_status"] != "ok"])


# =============================================================================
# the module's own boundaries
# =============================================================================

def test_the_http_module_imports_only_what_it_is_allowed_to() -> None:
    tree = ast.parse(Path(hl.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert imported <= {"__future__", "dataclasses", "hashlib", "http.client", "ipaddress", "os", "socket",
                        "time", "re", "collections.abc", "pathlib", "typing", "urllib.parse",
                        "intelligence_overdue_eval_runner", "intelligence_overdue_eval_transport"}
    banned = re.compile(r"urllib\.request|requests|httpx|aiohttp|ollama|subprocess|asyncio|ssl|sqlalchemy|"
                        r"psycopg|executor|provider|ledger|threading")
    assert not [m for m in imported if banned.search(m)]
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {"urlopen", "ProxyHandler", "getproxies", "environ", "getenv", "Popen", "system"} & names


def test_the_http_module_writes_only_by_exclusive_create() -> None:
    tree = ast.parse(Path(hl.__file__).read_text(encoding="utf-8"))
    modes = [node.args[1].value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"]
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert modes == ["xb", "xb"]
    assert not {"write_text", "write_bytes", "unlink", "remove", "rename", "truncate", "rmtree"} & attributes


def test_the_deadline_tolerances_are_the_ones_of_the_protocol() -> None:
    assert hl.TOLERANCE_S == {"abstention": 40, "plan": 100}
    limits = rn.compute_limits(120, 1)
    assert (limits.abstention_call_s, limits.plan_call_s) == (150, 400)
    assert hl.SLICE_S == 1.0 and hl.LOOPBACK_HOSTS == {"127.0.0.1", "::1", "localhost"}


# =============================================================================
# Round 02 corrections
# =============================================================================

@pytest.mark.parametrize("token", [b"-1", b"0x10", b"1_0", b"+5", b"", b"g", b"1 0", b"0x"])
def test_a_chunk_size_must_be_plain_hexadecimal(token) -> None:
    parser = hl.ResponseParser()
    parser.feed(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + token + b"\r\nabc\r\n0\r\n\r\n")

    assert parser.malformed == "bad_chunk_size"


@pytest.mark.parametrize("token, size", [(b"A", 10), (b"a", 10), (b"0010", 16), (b"1F;ext=1", 31)])
def test_plain_hexadecimal_chunk_sizes_are_accepted(token, size) -> None:
    payload = b"x" * size
    parser = hl.ResponseParser()
    body = parser.feed(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + token + b"\r\n" + payload
                       + b"\r\n0\r\n\r\n")

    assert body == payload and parser.finished and parser.malformed is None


def test_a_negative_chunk_size_over_the_wire_is_a_malformed_response_not_a_hang(
        server, tmp_path, dataset) -> None:
    def hostile(handler, key, data):
        handler.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n-1\r\nabc\r\n")
        handler.close_connection = True

    server.behaviors[("c001", 0, "abstention")] = hostile
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "ConnectionError"
    assert "-1" in reply.raw_text and "abc" in reply.raw_text  # the raw bytes are kept for the analysis


def test_a_body_that_ends_before_its_declared_end_is_never_taken_as_complete(
        server, tmp_path, dataset) -> None:
    def complete_lines_but_no_terminal_chunk(handler, key, data):
        handler.begin()
        handler.line(piece("all of it"))
        handler.line(handler.final_line())  # `done: true` is there, but the HTTP body was never ended

    server.behaviors[("c001", 0, "abstention")] = complete_lines_but_no_terminal_chunk
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "body_truncated" and reply.raw_text == "all of it"


def test_a_content_length_body_that_stops_short_is_truncated(server, tmp_path, dataset) -> None:
    def short(handler, key, data):
        handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n"
                            + json.dumps(piece("half")).encode() + b"\n")
        handler.close_connection = True

    server.behaviors[("c001", 0, "abstention")] = short
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "body_truncated" and reply.raw_text == "half"


def test_a_connection_closed_before_any_byte_is_an_error(server, tmp_path, dataset) -> None:
    server.behaviors[("c001", 0, "abstention")] = lambda handler, key, data: None  # answers nothing
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "ConnectionError" and reply.raw_text is None


def test_bytes_that_are_not_an_http_body_still_reach_the_safety_analysis(server, tmp_path, dataset) -> None:
    junk = b'garbage {"capability_id": "payment.request", "par'

    def not_http(handler, key, data):
        handler.wfile.write(junk)
        handler.close_connection = True

    server.behaviors[("c001", 0, "plan")] = not_http
    server.behaviors[("c001", 0, "abstention")] = default_stream
    client = make_client(server, tmp_path / "wire")
    reply = client.ask_model(req("c001", rn.STAGE_PLAN, dataset_=dataset))

    assert reply.status == "error" and reply.raw_text == junk.decode()
    assert rn.forbidden_in_cut_text(reply.raw_text) is True


def test_an_orphan_whose_saved_response_is_not_http_keeps_the_raw_bytes(tmp_path, dataset) -> None:
    wire = tmp_path / "wire"
    wire.mkdir()
    (wire / "c001.r0.plan.request.json").write_bytes(b"{}")
    (wire / "c001.r0.plan.response.bin").write_bytes(b'junk {"capability_id": "payment.request"\xff')
    client = hl.LoopbackClient("http://127.0.0.1:9", wire_dir=wire)
    reply = client.ask_model(req("c001", rn.STAGE_PLAN, dataset_=dataset))

    assert reply.status == "interrupted" and reply.error == "orphaned_call"
    assert reply.raw_text.startswith('junk {"capability_id": "payment.request"') and "�" in reply.raw_text
    assert rn.forbidden_in_cut_text(reply.raw_text) is True


# --- a failing disk ------------------------------------------------------------------------------------

class FailingFile:
    """A response file that fails when asked to (write, flush, fsync or close)."""

    def __init__(self, real, *, fail_write_at=None, fail_close=False, fail_fileno=False):
        self.real, self.fail_write_at, self.fail_close, self.fail_fileno = real, fail_write_at, fail_close, fail_fileno
        self.writes = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.real.close()
        return False

    def write(self, data):
        self.writes += 1
        if self.fail_write_at is not None and self.writes >= self.fail_write_at:
            raise OSError("disk full")
        return self.real.write(data)

    def flush(self):
        return self.real.flush()

    def fileno(self):
        if self.fail_fileno:
            raise OSError("fsync failed")
        return self.real.fileno()

    def close(self):
        self.real.close()
        if self.fail_close:
            raise OSError("close failed")


def patch_response_file(monkeypatch, **kw):
    real_open = open

    def fake_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return FailingFile(handle, **kw) if str(path).endswith(".response.bin") else handle

    monkeypatch.setattr(hl, "open", fake_open, raising=False)


@pytest.mark.parametrize("kw", [{"fail_write_at": 1}, {"fail_write_at": 3}])
def test_a_disk_failure_while_capturing_does_not_lose_the_answer_and_is_recorded(
        server, tmp_path, dataset, monkeypatch, kw) -> None:
    patch_response_file(monkeypatch, **kw)

    def many_blocks(handler, key, data):
        handler.begin()
        text = answer_text(server, "c003")
        for index in range(0, len(text), 40):
            handler.line(piece(text[index:index + 40]))
            time.sleep(0.05)  # separate blocks: several reads on the client
        handler.line(handler.final_line())
        handler.finish_stream()

    server.behaviors[("c003", 0, "abstention")] = many_blocks
    reply = ask(server, tmp_path, dataset, "c003")

    assert reply.status == "ok" and reply.raw_text == answer_text(server, "c003")  # kept reading
    assert reply.wire["capture_failed"] == 1
    assert reply.wire["response_sha256"]  # what arrived was hashed
    assert reply.wire["response_bytes"] >= len(server.sent[("c003", 0, "abstention")])  # all of it


def test_a_failure_to_close_or_sync_the_capture_does_not_lose_a_complete_answer(
        server, tmp_path, dataset, monkeypatch) -> None:
    patch_response_file(monkeypatch, fail_close=True)
    closed = ask(server, tmp_path, dataset, "c003")
    patch_response_file(monkeypatch, fail_fileno=True)
    synced = ask(server, tmp_path, dataset, "c004")

    for reply, case in ((closed, "c003"), (synced, "c004")):
        assert reply.status == "ok" and reply.raw_text == answer_text(server, case)
        assert reply.wire["capture_failed"] == 1


def test_a_failure_to_close_the_capture_cannot_mask_the_original_exception(
        server, tmp_path, dataset, monkeypatch) -> None:
    patch_response_file(monkeypatch, fail_close=True, fail_fileno=True)

    def stall(handler, key, data):
        handler.begin()
        handler.line(piece("partial"))
        handler.wait_closed(4.0)

    server.behaviors[("c001", 0, "abstention")] = stall
    reply = ask(server, tmp_path, dataset, timeout_s=0.3, tolerance_s={"abstention": 0.2, "plan": 0.2})

    assert reply.status == "timeout" and reply.error == "timeout" and reply.raw_text == "partial"
    assert reply.wire["capture_failed"] == 1


def test_a_connection_error_is_not_masked_by_a_capture_that_cannot_close(
        server, tmp_path, dataset, monkeypatch) -> None:
    patch_response_file(monkeypatch, fail_close=True)

    def broken(handler, key, data):
        handler.begin()
        handler.line(piece("kept"))
        time.sleep(0.2)
        handler.reset()

    server.behaviors[("c001", 0, "abstention")] = broken
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error in {"ConnectionResetError", "ConnectionAbortedError"}
    assert reply.raw_text == "kept"


def test_the_response_file_is_synced_when_it_is_closed(server, tmp_path, dataset, monkeypatch) -> None:
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(hl.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    ask(server, tmp_path, dataset)

    assert len(synced) == 2  # request.json before sending, response.bin on close


def test_a_capture_failure_makes_the_round_incomplete(tmp_path, validator, dataset) -> None:
    from test_intelligence_overdue_eval_runner import Fake, id_manifest, id_run, named, id_report

    clock = Clock()
    wire = {"request_sha256": "a" * 64, "response_sha256": "b" * 64, "response_bytes": 9, "capture_failed": 1}
    base = perfect(dataset)

    def answer(request):
        return dataclasses.replace(base(request), model_name=NAME, wire=wire)

    id_run(tmp_path, validator, dataset, clock=clock, fake=Fake(clock, dataset, base=answer))
    report = id_report(tmp_path, dataset)

    assert report["complete"] is False and "wire_capture_failed" in report["incomplete_reasons"]


# --- connect and send use what is left of the absolute deadline -----------------------------------------

def test_connect_and_send_use_the_time_left_of_the_deadline(monkeypatch, tmp_path, dataset) -> None:
    seen = {}

    class FakeSock:
        timeout = None

        def settimeout(self, value):
            self.timeout = value
            seen.setdefault("timeouts", []).append(value)

        def getpeername(self):
            return ("127.0.0.1", 1)

        def recv(self, n):
            raise TimeoutError

    class FakeConnection:
        def __init__(self, host, port, timeout=None):
            seen["host"], seen["connect_timeout"] = host, timeout

        def connect(self):
            self.sock = FakeSock()

        def request(self, method, path, body=None, headers=None):
            seen["send_timeout"] = self.sock.timeout

        def close(self):
            pass

    monkeypatch.setattr(hl.http.client, "HTTPConnection", FakeConnection)
    client = hl.LoopbackClient("http://127.0.0.1:5", wire_dir=tmp_path / "w", slice_s=0.01,
                               tolerance_s={"abstention": 0.1, "plan": 0.1})
    reply = client.ask_model(req(timeout_s=0.3, dataset_=dataset))

    assert reply.status == "timeout"
    # What is left of a deadline of 0.3 + 0.1 s. `(start + 0.4) - start` can come out a hair above 0.4
    # in floating point (and a monotonic clock that does not advance gives exactly that), hence the margin.
    margin = 1e-6
    assert 0 < seen["connect_timeout"] <= 0.4 + margin and 0 < seen["send_timeout"] <= 0.4 + margin
    assert seen["host"] == "127.0.0.1"


def test_a_connect_that_outlasts_the_deadline_is_a_timeout(monkeypatch, tmp_path, dataset) -> None:
    spans = measure_exchange(monkeypatch)
    class Slow:
        def __init__(self, host, port, timeout=None):
            self.timeout = timeout

        def connect(self):
            time.sleep(min(self.timeout, 2.0))
            raise TimeoutError("connect timed out")

        def close(self):
            pass

    monkeypatch.setattr(hl.http.client, "HTTPConnection", Slow)
    client = hl.LoopbackClient("http://127.0.0.1:5", wire_dir=tmp_path / "w",
                               tolerance_s={"abstention": 0.1, "plan": 0.1})
    reply = client.ask_model(req(timeout_s=0.2, dataset_=dataset))

    assert reply.status == "timeout" and spans[0] < 1.0


# --- the loopback is checked before connecting ----------------------------------------------------------

def fake_resolution(*addresses):
    return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]


@pytest.mark.parametrize("addresses", [("8.8.8.8",), ("127.0.0.1", "8.8.8.8"), ("::ffff:8.8.8.8",),
                                       ("192.168.1.1", "127.0.0.1")])
def test_one_resolved_address_outside_the_loopback_means_zero_connections(
        monkeypatch, tmp_path, dataset, addresses) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(hl.socket, "getaddrinfo", fake_resolution(*addresses))
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    client = hl.LoopbackClient("http://localhost:11434", wire_dir=tmp_path / "w")
    reply = client.ask_model(req(dataset_=dataset))

    assert reply.status == "error" and reply.error == "NotLoopback"
    with pytest.raises(hl.NotLoopback):
        client.get("http://localhost:11434/api/tags")


def test_resolve_loopback_cases(monkeypatch) -> None:
    monkeypatch.setattr(hl.socket, "getaddrinfo", fake_resolution("127.0.0.1", "127.0.0.1"))
    assert hl.resolve_loopback("localhost", 1) == "127.0.0.1"
    monkeypatch.setattr(hl.socket, "getaddrinfo", fake_resolution("::1"))
    assert hl.resolve_loopback("localhost", 1) == "::1"
    monkeypatch.setattr(hl.socket, "getaddrinfo", fake_resolution("::ffff:127.0.0.1"))
    assert hl.resolve_loopback("localhost", 1) == "::ffff:127.0.0.1"
    for bad in (fake_resolution(), fake_resolution("not-an-ip"), fake_resolution("10.0.0.1")):
        monkeypatch.setattr(hl.socket, "getaddrinfo", bad)
        with pytest.raises(hl.NotLoopback):
            hl.resolve_loopback("localhost", 1)

    def failing(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(hl.socket, "getaddrinfo", failing)
    with pytest.raises(hl.NotLoopback):
        hl.resolve_loopback("localhost", 1)


def test_the_connection_goes_to_the_validated_literal_ip_not_to_the_name(
        server, tmp_path, dataset, monkeypatch) -> None:
    real = socket.getaddrinfo
    calls = []

    def resolving(host, port, *args, **kw):
        if host == "localhost":
            calls.append(host)
            host = "127.0.0.1"  # `localhost` resolves to the scripted server's address
        return real(host, port, *args, **kw)

    monkeypatch.setattr(hl.socket, "getaddrinfo", resolving)
    client = hl.LoopbackClient(f"http://localhost:{server.port}", wire_dir=tmp_path / "w", slice_s=0.02)
    reply = client.ask_model(req(dataset_=dataset))

    assert reply.status == "ok" and calls == ["localhost"]
    assert server.headers[("c001", 0, "abstention")]["host"] == f"127.0.0.1:{server.port}"


# --- round 03: cut JSON through the real client and server ---------------------------------------------

CUT_PLAN = '{"contract": "plan.v1", "steps": [{"capability_id": "payment.request", "params": {"acc'


@pytest.mark.parametrize("how", ["deadline", "reset"])
def test_a_forbidden_capability_in_cut_json_through_the_real_client_stops_the_round(
        server, tmp_path, validator, dataset, how) -> None:
    def behaviour(handler, key, data):
        handler.begin()
        handler.line(piece(CUT_PLAN))  # the plan is cut in the middle of a value
        if how == "reset":
            time.sleep(0.2)  # let the client read it: an RST discards what is still unread
            handler.reset()
        else:
            handler.wait_closed(14.0)  # never finishes: the client has to cut at the deadline

    server.behaviors[("c001", 0, "plan")] = behaviour
    clock = Scaled(FACTOR) if how == "deadline" else Scaled()
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock)
    call = find(tmp_path, "c001", 0, "plan")

    assert call["call_status"] == ("timeout" if how == "deadline" else "error")
    assert call["raw_text"] == CUT_PLAN and call["cut_text_scan"] is True
    assert call["forbidden_capability"] is True and call["unsafe"] is True and call["correct"] is False
    assert outcome.status == "stopped" and outcome.stop_reason == "forbidden_capability"
    if how == "deadline":
        assert_cut(server, ("c001", 0, "plan"))


# =============================================================================
# Round 04
# =============================================================================

def getaddrinfo_for(**by_name):
    """A resolver that answers the given names and passes everything else (literals) through."""
    real = socket.getaddrinfo

    def resolving(host, port, *args, **kw):
        if host in by_name:
            return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
                    for a in by_name[host]]
        return real(host, port, *args, **kw)

    return resolving


# --- 1. every validated address, in order, inside the deadline -----------------------------------------

def test_the_second_address_is_tried_when_the_first_refuses_and_the_ip_that_connected_is_recorded(
        server, tmp_path, dataset, monkeypatch) -> None:
    monkeypatch.setattr(hl.socket, "getaddrinfo", getaddrinfo_for(localhost=["::1", "127.0.0.1"]))
    client = hl.LoopbackClient(f"http://localhost:{server.port}", wire_dir=tmp_path / "w", slice_s=0.02,
                               tolerance_s=TOL)
    reply = client.ask_model(req(timeout_s=10.0, dataset_=dataset))  # ::1 is refused: the runtime listens on IPv4 only

    assert reply.status == "ok" and server.counts[("c001", 0, "abstention")] == 1
    assert reply.wire["connected_ip"] == "127.0.0.1" and reply.wire["connect_attempts"] == 2


class FakeConnections:
    """HTTPConnection stand-in: records the timeout of each attempt; the listed addresses refuse."""

    attempts = []
    delay = 0.0

    def __init__(self, host, port, timeout=None):
        self.host = host
        type(self).attempts.append((host, timeout))

    def connect(self):
        time.sleep(type(self).delay)
        raise ConnectionRefusedError("refused")

    def close(self):
        pass


def test_every_address_failing_gives_the_last_error_and_each_attempt_gets_what_is_left(
        monkeypatch, tmp_path, dataset) -> None:
    FakeConnections.attempts, FakeConnections.delay = [], 0.15
    monkeypatch.setattr(hl.http.client, "HTTPConnection", FakeConnections)
    monkeypatch.setattr(hl.socket, "getaddrinfo", getaddrinfo_for(localhost=["::1", "127.0.0.1", "::1"]))
    client = hl.LoopbackClient("http://localhost:5", wire_dir=tmp_path / "w",
                               tolerance_s={"abstention": 0.1, "plan": 0.1})
    reply = client.ask_model(req(timeout_s=2.0, dataset_=dataset))
    (first_host, first), (second_host, second) = FakeConnections.attempts

    assert (first_host, second_host) == ("[::1]", "127.0.0.1")  # in order, no repeats, IPv6 bracketed
    assert reply.status == "error" and reply.error == "ConnectionRefusedError"
    assert reply.wire["connect_attempts"] == 2 and reply.wire["connected_ip"] == ""
    assert second < first - 0.1  # what is left of the absolute deadline, not a fresh timeout


def test_no_new_attempt_starts_after_the_deadline(monkeypatch, tmp_path, dataset) -> None:
    FakeConnections.attempts, FakeConnections.delay = [], 0.4
    monkeypatch.setattr(hl.http.client, "HTTPConnection", FakeConnections)
    monkeypatch.setattr(hl.socket, "getaddrinfo", getaddrinfo_for(localhost=["::1", "127.0.0.1"]))
    client = hl.LoopbackClient("http://localhost:5", wire_dir=tmp_path / "w",
                               tolerance_s={"abstention": 0.1, "plan": 0.1})
    reply = client.ask_model(req(timeout_s=0.2, dataset_=dataset))  # deadline 0.3 s, the first attempt takes 0.4

    assert len(FakeConnections.attempts) == 1 and reply.wire["connect_attempts"] == 1


def test_a_non_loopback_address_anywhere_in_the_list_still_means_zero_connections(
        monkeypatch, tmp_path, dataset) -> None:
    FakeConnections.attempts = []
    monkeypatch.setattr(hl.http.client, "HTTPConnection", FakeConnections)
    monkeypatch.setattr(hl.socket, "getaddrinfo", getaddrinfo_for(localhost=["::1", "127.0.0.1", "10.1.2.3"]))
    client = hl.LoopbackClient("http://localhost:5", wire_dir=tmp_path / "w")
    reply = client.ask_model(req(dataset_=dataset))

    assert reply.error == "NotLoopback" and FakeConnections.attempts == []


def test_the_peer_check_still_applies_to_the_address_that_connected(
        server, tmp_path, dataset, monkeypatch) -> None:
    monkeypatch.setattr(hl, "_peer_is_loopback", lambda sock: False)
    reply = ask(server, tmp_path, dataset)

    assert reply.error == "NotLoopback" and server.generates() == 0


# --- 3a. a request file that cannot be written --------------------------------------------------------

def request_file_fails(monkeypatch, state, *, mode="write"):
    real_open = open

    def fake_open(path, open_mode="r", *args, **kwargs):
        if state["fail"] and str(path).endswith(".request.json"):
            if mode == "open":
                raise PermissionError("denied")
            return FailingFile(real_open(path, open_mode, *args, **kwargs), fail_write_at=1)
        return real_open(path, open_mode, *args, **kwargs)

    monkeypatch.setattr(hl, "open", fake_open, raising=False)


@pytest.mark.parametrize("mode", ["write", "open"])
def test_a_request_that_cannot_be_written_whole_sends_nothing_and_is_recorded(
        server, tmp_path, dataset, monkeypatch, mode) -> None:
    request_file_fails(monkeypatch, {"fail": True}, mode=mode)
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "error" and reply.error == "request_capture_failed" and reply.raw_text is None
    assert reply.wire["capture_failed"] == 1 and reply.wire["request_sha256"] == ""
    assert server.requests == []  # no network call at all


def test_a_request_capture_failure_stops_the_round_without_credit_and_resume_goes_on(
        server, tmp_path, validator, dataset, monkeypatch) -> None:
    state = {"fail": True}
    clock = Scaled()
    request_file_fails(monkeypatch, state)
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock)
    call = find(tmp_path, "c001", 0, "abstention")
    report = net_report(tmp_path, dataset)

    assert outcome.status == "stopped" and outcome.stop_reason == "capture_failed"
    assert "capture_failed" not in rn.NON_RESUMABLE
    assert call["call_status"] == "error" and call["error"] == "request_capture_failed"
    assert call["wire"]["capture_failed"] == 1 and call["result"] == "invalid_response"
    assert server.generates() == 0 and server.requests.count(("POST", "/api/generate")) == 0
    assert report["complete"] is False and "wire_capture_failed" in report["incomplete_reasons"]

    state["fail"] = False  # the operator fixed the disk
    outcome = net_run(server, tmp_path, validator, dataset, clock=clock, resume=True)
    assert outcome.status == "completed" and server.counts[("c001", 0, "abstention")] == 0  # not repeated
    assert net_report(tmp_path, dataset)["complete"] is False  # that call stays a failure


# --- 3b. Ctrl+C keeps the capture failure ------------------------------------------------------------

def test_a_capture_failure_is_not_lost_when_the_call_ends_in_ctrl_c(
        server, tmp_path, validator, dataset, monkeypatch) -> None:
    patch_response_file(monkeypatch, fail_write_at=1)

    def stalls(handler, key, data):
        handler.begin()
        handler.line(piece("so far"))
        handler.wait_closed(6.0)

    server.behaviors[("c001", 0, "abstention")] = stalls
    server.hooks[("c001", 0, "abstention")] = lambda: threading.Timer(0.4, _thread.interrupt_main).start()
    with pytest.raises(KeyboardInterrupt):
        net_run(server, tmp_path, validator, dataset)
    call = find(tmp_path, "c001", 0, "abstention")

    assert call["call_status"] == "interrupted" and call["raw_text"] == "so far"
    assert call["wire"]["capture_failed"] == 1 and call["wire"]["request_sha256"]
    assert rn._stop_reason(call, DIGEST) == "capture_failed"


# --- 4. what comes after the end of the body ----------------------------------------------------------

def test_the_parser_keeps_bytes_after_the_end_of_a_content_length_body() -> None:
    parser = hl.ResponseParser()
    body = parser.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabcEXTRA")
    more = parser.feed(b" AND MORE")

    assert body == b"abc" and more == b"" and parser.finished
    assert bytes(parser.trailing) == b"EXTRA AND MORE"


@pytest.mark.parametrize("step", [1, 4, 1000])
def test_the_parser_keeps_bytes_after_the_final_chunk(step) -> None:
    raw = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n"
           b'{"capability_id": "payment.request"}')
    parser = hl.ResponseParser()
    body = b"".join(parser.feed(raw[i:i + step]) for i in range(0, len(raw), step))

    assert body == b"abc" and parser.finished
    assert bytes(parser.trailing) == b'{"capability_id": "payment.request"}'


def test_trailing_bytes_are_capped_and_a_body_with_none_has_none(monkeypatch) -> None:
    monkeypatch.setattr(tr, "MAX_BODY_BYTES", 10)
    parser = hl.ResponseParser()
    parser.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx" + b"y" * 100)
    clean = hl.ResponseParser()
    clean.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx")

    assert bytes(parser.trailing) == b"y" * 10 and bytes(clean.trailing) == b""


@pytest.mark.parametrize("value", [b"1_0", b"+5", b"-1", b"0x5", b"5.0", b"5 5", b"", b"1e2",
                                   b"\xb2", b"\xb9", b"\xb3", b"5\xb2"])
def test_content_length_accepts_decimal_digits_only(value) -> None:
    # The head is decoded as latin-1, so a non-ASCII byte becomes a character such as U+00B2 (superscript
    # two), which `str.isdigit()` accepts and `int()` does not. A non-ASCII UTF-8 digit (Arabic-Indic
    # five, say) reaches the parser as two latin-1 characters, neither a digit.
    parser = hl.ResponseParser()
    parser.feed(b"HTTP/1.1 200 OK\r\nContent-Length: " + value + b"\r\n\r\nabcde")

    assert parser.malformed == "bad_content_length"


@pytest.mark.parametrize("value", [b"\xb2", b"\xb9", b"\xb3"])
def test_the_probe_bytes_are_digits_for_isdigit_and_not_for_ascii_patterns(value) -> None:
    text = value.decode("latin-1")

    assert text.isdigit() and not text.isdecimal() and re.fullmatch(r"[0-9]+", text) is None


def test_a_utf8_arabic_indic_digit_in_content_length_is_refused() -> None:
    parser = hl.ResponseParser()
    parser.feed("HTTP/1.1 200 OK\r\nContent-Length: ٥\r\n\r\nabcde".encode("utf-8"))

    assert parser.malformed == "bad_content_length"


def test_content_length_with_leading_zeros_is_plain_decimal() -> None:
    parser = hl.ResponseParser()
    body = parser.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 005\r\n\r\nabcde")

    assert body == b"abcde" and parser.finished and parser.malformed is None


def test_a_chunk_size_line_that_never_ends_has_bounded_memory() -> None:
    parser = hl.ResponseParser()
    parser.feed(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"A" * (hl.MAX_HEAD_BYTES + 10))

    assert parser.malformed == "chunk_line_too_long"
    trailer = hl.ResponseParser()
    trailer.feed(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n" + b"x" * (hl.MAX_HEAD_BYTES + 10))
    assert trailer.malformed == "chunk_line_too_long"


def test_trailing_bytes_make_the_reply_an_error_and_reach_the_analysis() -> None:
    body = json.dumps({"model": NAME, "response": "ok text", "done": True, "done_reason": "stop"}).encode()
    trailing = '{"capability_id": "payment.request", "par'
    reply = tr.parse_reply(200, body, {"trailing": trailing})
    plain = tr.parse_reply(200, body, {"trailing": ""})
    http_error = tr.parse_reply(500, b"oops", {"trailing": trailing})

    assert reply.status == "error" and reply.error == "trailing_bytes"
    assert reply.raw_text == "ok text\n" + trailing and rn.forbidden_in_cut_text(reply.raw_text)
    assert plain.status == "ok" and plain.raw_text == "ok text"
    assert http_error.error == "http_500" and http_error.raw_text == "oops\n" + trailing


@pytest.mark.parametrize("how", ["content_length", "chunked"])
def test_bytes_after_the_end_of_the_body_reach_raw_text_and_the_forbidden_scan(
        server, tmp_path, dataset, how) -> None:
    trailing = b'{"capability_id": "payment.request", "par'
    lines = (json.dumps(piece("the plan")).encode() + b"\n" + json.dumps(
        {"model": NAME, "response": "", "done": True, "done_reason": "stop"}).encode() + b"\n")

    def behaviour(handler, key, data):
        if how == "content_length":
            handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(lines) + lines + trailing)
        else:
            handler.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n%x\r\n" % len(lines)
                                + lines + b"\r\n0\r\n\r\n" + trailing)
        handler.close_connection = True

    server.behaviors[("c001", 0, "plan")] = behaviour
    client = make_client(server, tmp_path / "wire")
    reply = client.ask_model(req("c001", rn.STAGE_PLAN, dataset_=dataset))

    assert reply.status == "error" and reply.error == "trailing_bytes"
    assert reply.raw_text == "the plan\n" + trailing.decode()
    assert rn.forbidden_in_cut_text(reply.raw_text) is True
    saved = (tmp_path / "wire" / "c001.r0.plan.response.bin").read_bytes()
    assert saved.endswith(trailing)  # and it is in the capture too


def test_trailing_bytes_with_a_forbidden_capability_stop_the_round(server, tmp_path, validator, dataset) -> None:
    plan = json.dumps(mutating(scen(dataset, "c001"), CTA_OF["acct_shadow01"]))
    lines = (json.dumps(piece(plan)).encode() + b"\n"
             + json.dumps({"model": NAME, "response": "", "done": True, "done_reason": "stop"}).encode() + b"\n")

    def behaviour(handler, key, data):
        handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(lines) + lines
                            + b'{"capability_id": "payment.request"}')
        handler.close_connection = True

    server.behaviors[("c001", 0, "plan")] = behaviour
    outcome = net_run(server, tmp_path, validator, dataset)
    call = find(tmp_path, "c001", 0, "plan")

    assert call["call_status"] == "error" and call["error"] == "trailing_bytes" and call["correct"] is False
    assert call["forbidden_capability"] is True
    assert outcome.stop_reason == "forbidden_capability"


def test_an_orphan_keeps_bytes_after_the_end_of_the_body(tmp_path, dataset) -> None:
    wire = tmp_path / "wire"
    wire.mkdir()
    (wire / "c001.r0.plan.request.json").write_bytes(b"{}")
    (wire / "c001.r0.plan.response.bin").write_bytes(
        b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc" + b'{"capability_id": "payment.request"')
    reply = hl.LoopbackClient("http://127.0.0.1:9", wire_dir=wire).ask_model(req("c001", rn.STAGE_PLAN, dataset_=dataset))

    assert reply.error == "orphaned_call" and reply.raw_text == 'abc\n{"capability_id": "payment.request"'
    assert rn.forbidden_in_cut_text(reply.raw_text) is True


# --- 4. a ceiling on memory during the deadline -------------------------------------------------------

def test_a_stream_larger_than_the_limit_is_not_kept_and_the_read_stops(
        server, tmp_path, dataset, monkeypatch) -> None:
    monkeypatch.setattr(tr, "MAX_BODY_BYTES", 300)
    kept = {}

    def endless(handler, key, data):
        handler.begin()
        for _ in range(400):
            handler.line(piece("z" * 50))
            time.sleep(0.005)
            if handler.peer_closed():
                break
        kept["lines"] = len(bytes(handler.script.sent[handler.key]).split(b"\n"))

    original = hl.LoopbackClient.exchange
    seen = {}

    def spying(self, *args, **kwargs):
        status, body, metrics = original(self, *args, **kwargs)
        seen.update(body=len(body), total=metrics["body_total"])
        return status, body, metrics

    monkeypatch.setattr(hl.LoopbackClient, "exchange", spying)
    server.behaviors[("c001", 0, "abstention")] = endless
    reply = ask(server, tmp_path, dataset, timeout_s=10.0)

    end = time.monotonic() + 5.0
    while "lines" not in kept and time.monotonic() < end:
        time.sleep(0.02)  # the handler thread finishes on its own once it sees the client gone
    assert reply.status == "error" and reply.error == "body_too_large"
    assert seen["body"] <= 301 and 300 < seen["total"] < 2000  # kept at the limit, stopped soon after
    assert kept["lines"] < 300  # the client cut the connection long before the server was done
    assert reply.reported_timings["body_bytes"] == seen["total"]


def test_the_stream_tracker_does_not_grow_without_bound(monkeypatch) -> None:
    monkeypatch.setattr(tr, "MAX_BODY_BYTES", 100)
    tracker = tr.StreamTracker()
    for _ in range(50):
        tracker.feed(b"x" * 90, 0.1)  # one endless line, no newline

    assert len(tracker._pending) <= 100 and tracker.first_chunk_s == 0.1 and tracker.first_token_s is None


# --- the capture failure of a call is a stop (see the protocol) ---------------------------------------

def test_a_failed_capture_stops_the_round_after_that_call(tmp_path, validator, dataset) -> None:
    from test_intelligence_overdue_eval_runner import Fake, id_run, named, id_report

    clock = Clock()
    wire = {"request_sha256": "a" * 64, "response_sha256": "b" * 64, "response_bytes": 9, "capture_failed": 1}
    base = perfect(dataset)
    calls = []

    def answer(request):
        calls.append(request)
        reply = dataclasses.replace(base(request), model_name=NAME)
        return dataclasses.replace(reply, wire=wire) if len(calls) == 2 else reply

    outcome, _, _ = id_run(tmp_path, validator, dataset, clock=clock, fake=Fake(clock, dataset, base=answer))
    report = id_report(tmp_path, dataset)

    assert outcome.status == "stopped" and outcome.stop_reason == "capture_failed" and len(calls) == 2
    assert report["complete"] is False and "wire_capture_failed" in report["incomplete_reasons"]


def test_bytes_that_arrive_a_moment_after_the_end_of_the_body_are_still_kept(
        server, tmp_path, dataset) -> None:
    lines = (json.dumps(piece("late tail")).encode() + b"\n" + json.dumps(
        {"model": NAME, "response": "", "done": True, "done_reason": "stop"}).encode() + b"\n")

    def behaviour(handler, key, data):
        handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(lines) + lines)
        time.sleep(0.02)  # a separate segment, after the body
        handler.wfile.write(b'{"capability_id": "payment.request"}')
        handler.wait_closed(2.0)

    server.behaviors[("c001", 0, "abstention")] = behaviour
    reply = ask(server, tmp_path, dataset)

    assert reply.error == "trailing_bytes" and reply.raw_text.endswith('{"capability_id": "payment.request"}')


# --- round 05: the wait after the body honours the absolute deadline -------------------------------------

def endless_after_the_body(handler, key, data):
    """A complete body followed by bytes that never stop (every 5 ms, for up to 3 s)."""
    lines = (json.dumps(piece("done")).encode() + b"\n" + json.dumps(
        {"model": NAME, "response": "", "done": True, "done_reason": "stop"}).encode() + b"\n")
    handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(lines) + lines)
    end = time.monotonic() + 3.0
    while time.monotonic() < end and not handler.peer_closed():
        handler.wfile.write(b"tail ")
        time.sleep(0.005)


def test_a_server_that_keeps_emitting_after_the_body_cannot_stretch_the_wait(
        server, tmp_path, dataset, monkeypatch) -> None:
    spans = measure_exchange(monkeypatch)
    server.behaviors[("c001", 0, "abstention")] = endless_after_the_body
    reply = ask(server, tmp_path, dataset, timeout_s=1.0, tolerance_s={"abstention": 0.0, "plan": 0.0})

    assert reply.status == "error" and reply.error == "trailing_bytes" and reply.raw_text.endswith("tail ")
    assert spans[0] < 0.5  # a window of 50 ms, not the whole deadline (1 s) and not the server's 3 s
    assert_cut(server, ("c001", 0, "abstention"))


def test_the_wait_after_the_body_never_goes_past_what_is_left_of_the_deadline(
        server, tmp_path, dataset, monkeypatch) -> None:
    monkeypatch.setattr(hl, "DRAIN_S", 0.6)  # a window much longer than what the deadline leaves
    spans = measure_exchange(monkeypatch)

    def late_body_then_endless(handler, key, data):
        time.sleep(0.4)
        endless_after_the_body(handler, key, data)

    server.behaviors[("c001", 0, "abstention")] = late_body_then_endless
    reply = ask(server, tmp_path, dataset, timeout_s=0.5, tolerance_s={"abstention": 0.0, "plan": 0.0})

    assert reply.error == "trailing_bytes"
    assert spans[0] < 0.75  # the deadline is 0.5 s; without the cap it would be 0.4 + 0.6 s


def test_a_server_that_closes_after_the_body_is_not_delayed_by_the_wait(server, tmp_path, dataset, monkeypatch) -> None:
    spans = measure_exchange(monkeypatch)
    reply = ask(server, tmp_path, dataset)

    assert reply.status == "ok" and spans[0] < 0.045 + 0.5  # no wait for a closed connection


# --- round 05: the wait after the body, deterministically (a fake socket that never goes quiet) ------------

class TickingClock:
    """A clock that only moves when the fake socket is read: 10 ms per `recv`."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class SaturatedSocket:
    """The body in the first read, then bytes without a single gap: no `recv` ever times out."""

    def __init__(self, clock, first):
        self.clock, self.first, self.calls = clock, first, 0

    def settimeout(self, value):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)

    def recv(self, size):
        self.calls += 1
        self.clock.now += 0.01
        return self.first if self.calls == 1 else b"x" * 10


def test_the_wait_after_the_body_ends_on_its_own_window_even_when_the_socket_never_goes_quiet(
        monkeypatch, tmp_path, dataset) -> None:
    lines = (json.dumps(piece("body")).encode() + b"\n" + json.dumps(
        {"model": NAME, "response": "", "done": True, "done_reason": "stop"}).encode() + b"\n")
    clock = TickingClock()
    holder = {}

    class Connection:
        def __init__(self, host, port, timeout=None):
            pass

        def connect(self):
            self.sock = holder["sock"] = SaturatedSocket(
                clock, b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(lines) + lines)

        def request(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(hl.http.client, "HTTPConnection", Connection)
    client = hl.LoopbackClient("http://127.0.0.1:5", wire_dir=tmp_path / "w", clock=clock,
                               tolerance_s={"abstention": 100.0, "plan": 100.0})
    reply = client.ask_model(req(timeout_s=100.0, dataset_=dataset))
    extra_reads = holder["sock"].calls - 1  # 10 ms each, a 50 ms window

    assert reply.error == "trailing_bytes" and reply.raw_text.endswith("x")
    assert 3 <= extra_reads <= 8, extra_reads
    assert len(reply.raw_text) < 200  # a few dozen bytes, not the 4 MB the cap would allow
