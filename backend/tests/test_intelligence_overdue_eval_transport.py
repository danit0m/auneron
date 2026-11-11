"""The SIMULATED transport of the overdue evaluation: scripted `post`, no network, no Ollama.

Every response here is made up by these tests. Nothing proves anything about a real server.
"""

import ast
import json
import re
from pathlib import Path

import pytest

import intelligence_abstention_contract as ac
import intelligence_overdue_eval_runner as rn
import intelligence_overdue_eval_transport as tr
from intelligence_overdue_eval_runner import ModelReply
from test_intelligence_overdue_eval_runner import DIGEST  # noqa: F401
from test_intelligence_overdue_eval_runner import OTHER_DIGEST
from test_intelligence_overdue_eval_runner import Clock
from test_intelligence_overdue_eval_runner import calls_of
from test_intelligence_overdue_eval_runner import dataset  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import find
from test_intelligence_overdue_eval_runner import make_manifest
from test_intelligence_overdue_eval_runner import perfect
from test_intelligence_overdue_eval_runner import real_120  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import records_of
from test_intelligence_overdue_eval_runner import report_of
from test_intelligence_overdue_eval_runner import run
from test_intelligence_overdue_eval_runner import twelve_case_protocol  # noqa: F401  (autouse fixture)
from test_intelligence_overdue_eval_runner import validator  # noqa: F401  (fixture)

TESTS = Path(__file__).resolve().parent
BASE = "http://simulated.invalid:0"


def enc(body) -> bytes:
    return json.dumps(body).encode("utf-8")


def ok_body(raw, **over):
    body = {"model": "qwen3:4b", "response": raw, "done": True, "done_reason": "stop",
            "digest": DIGEST, "prompt_eval_count": 120, "eval_count": 60,
            "total_duration": 3_000_000_000, "load_duration": 1_000_000_000,
            "prompt_eval_duration": 500_000_000, "eval_duration": 1_500_000_000}
    body.update(over)
    return body


def request(stage=rn.STAGE_ABSTENTION, timeout_s=150, seed=1000):
    return rn.ModelRequest(stage, "c001", 0, "PROMPT", {
        "temperature": 0.0, "seed": seed, "num_predict": 160}, timeout_s)


class Post:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, url, body, timeout_s):
        self.calls.append((url, body, timeout_s))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


# --- the local request ---------------------------------------------------------

def test_the_request_is_built_locally_with_fixed_effective_parameters() -> None:
    body = tr.build_body(request(seed=1002), "qwen3:4b")

    assert body["model"] == "qwen3:4b" and body["prompt"] == "PROMPT"
    assert body["stream"] is True and body["think"] is False and body["keep_alive"] == "30m"
    assert body["options"] == {"top_k": 1, "top_p": 1.0, "repeat_penalty": 1.0, "num_ctx": 8192,
                               "temperature": 0.0, "seed": 1002, "num_predict": 160}
    assert body["format"] == ac.ABSTENTION_SCHEMA  # stage 1: the abstention.v1 schema
    # Changed by the plan-format round: the plan stage used to send NO `format`. The candidate
    # model is reasoning-only, so without a grammar the answer would start with prose; the plan
    # stage now sends the plan.v1 schema (details in test_intelligence_overdue_eval_plan_format.py).
    plan_body = tr.build_body(request(rn.STAGE_PLAN), "qwen3:4b")
    assert plan_body["format"] == rn.plan_format_schema() and plan_body["think"] is False
    assert tr.CANDIDATE_MODEL == "qwen3:4b"
    assert tr.encode(body) == tr.encode(dict(reversed(list(body.items()))))  # canonical bytes


def test_the_timeout_is_imposed_on_the_caller_and_the_url_is_joined() -> None:
    post = Post((200, enc(ok_body("x"))))
    tr.make_ask_model(post, base_url=BASE + "/")(request(timeout_s=150))
    tr.make_ask_model(post, base_url=BASE)(request(rn.STAGE_PLAN, timeout_s=400))

    assert [(u, t) for u, _, t in post.calls] == [(BASE + "/api/generate", 150),
                                                   (BASE + "/api/generate", 400)]
    assert json.loads(post.calls[0][1])["options"]["seed"] == 1000


# --- simulated responses -------------------------------------------------------

def ask(result, **kw):
    return tr.make_ask_model(Post(result), base_url=BASE, **kw)(request())


def test_success_maps_text_tokens_digest_and_timings() -> None:
    reply = ask((200, enc(ok_body("hello"))))

    assert reply.status == "ok" and reply.raw_text == "hello" and reply.error is None
    assert (reply.input_tokens, reply.output_tokens, reply.model_digest) == (120, 60, DIGEST)
    # durations stay in nanoseconds, as the runtime informs them
    assert reply.reported_timings == {
        "body_bytes": len(enc(ok_body("hello"))), "total_duration": 3_000_000_000,
        "load_duration": 1_000_000_000, "prompt_eval_duration": 500_000_000,
        "eval_duration": 1_500_000_000}
    assert (reply.model_name, reply.done_reason) == ("qwen3:4b", "stop")


def test_a_length_cut_is_noted_but_kept() -> None:
    reply = ask((200, enc(ok_body("cut", done_reason="length"))))

    assert reply.status == "ok" and reply.raw_text == "cut" and reply.error == "done_reason_length"


def test_a_timeout_from_the_caller_is_a_timeout_reply() -> None:
    reply = ask(TimeoutError("slow"))

    assert reply.status == "timeout" and reply.raw_text is None and reply.error == "timeout"


@pytest.mark.parametrize("result", [
    (200, b""),
    (200, b"\xff\xfe\x00"),
    (200, enc(ok_body(None))),
    (200, enc(ok_body(5))),
    (200, enc({"done": True})),
    ("200", b"{}"),
    (200, "text not bytes"),
])
def test_a_capture_failure_is_an_error_reply(result) -> None:
    reply = ask(result)

    assert reply.status == "error" and reply.error == "capture_failed"


@pytest.mark.parametrize("result, kept", [
    (200, enc(ok_body("x" * 50))[:40]),
    (200, enc(ok_body("x", done=False))),
])
def test_a_truncated_body_is_an_error_reply_that_keeps_the_text(result, kept) -> None:
    reply = ask((result, kept))

    assert reply.status == "error" and reply.error == "body_truncated"
    if kept.endswith(b"}"):  # a readable envelope that says `done: false`: the model's text is kept
        assert reply.raw_text == "x"
    else:
        assert reply.raw_text == kept.decode("utf-8")
    assert reply.reported_timings["body_bytes"] == len(kept)


@pytest.mark.parametrize("data", [b"not json}", b"[1, 2]", b'"text"', b"{'a': 1}"])
def test_invalid_json_is_an_error_reply(data) -> None:
    reply = ask((200, data))

    assert reply.status == "error" and reply.error == "invalid_json" and reply.raw_text == data.decode()


@pytest.mark.parametrize("status, data, error", [
    (500, b"", "http_500"), (404, enc({"error": "model not found"}), "http_404"),
    (503, b"<html>", "http_503"), (302, b"", "http_302"),
])
def test_an_http_error_is_an_error_reply(status, data, error) -> None:
    reply = ask((status, data))

    assert reply.status == "error" and reply.error == error


def test_an_error_object_in_a_200_body_is_a_server_error() -> None:
    reply = ask((200, enc({"error": "boom"})))

    assert reply.status == "error" and reply.error == "server_error"


def test_any_other_failure_of_the_caller_is_recorded_by_name_and_not_retried() -> None:
    post = Post(ConnectionResetError("reset"))
    reply = tr.make_ask_model(post, base_url=BASE)(request())

    assert reply.status == "error" and reply.error == "ConnectionResetError"
    assert len(post.calls) == 1


def test_an_interrupt_is_not_swallowed() -> None:
    with pytest.raises(KeyboardInterrupt):
        tr.make_ask_model(Post(KeyboardInterrupt()), base_url=BASE)(request())


def test_an_oversized_body_is_cut_at_the_limit_but_its_content_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(tr, "MAX_BODY_BYTES", 20)
    data = enc(ok_body("x" * 100))
    reply = ask((200, data))

    assert reply.status == "error" and reply.error == "body_too_large"
    assert reply.raw_text == data[:20].decode("utf-8")
    assert reply.reported_timings == {"body_bytes": len(data)}


def test_a_diverging_digest_is_passed_to_the_runner_unchanged() -> None:
    reply = ask((200, enc(ok_body("x", digest=OTHER_DIGEST))))

    assert reply.status == "ok" and reply.model_digest == OTHER_DIGEST


def test_a_missing_digest_stays_unknown() -> None:
    body = ok_body("x")
    del body["digest"]

    assert ask((200, enc(body))).model_digest is None


# --- no failure path discards content -----------------------------------------------

def enc_utf8(body) -> bytes:
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


FAILURES = [
    # (name, result, error, expected raw text)
    ("http_with_text", (500, b"internal oops"), "http_500", "internal oops"),
    ("http_with_json", (404, enc({"error": "model not found"})), "http_404", enc({"error": "model not found"}).decode()),
    ("http_not_utf8", (502, b"bad \xff gateway"), "http_502", "bad \ufffd gateway"),
    ("not_utf8", (200, b'{"response": "\xff\xfe"}'), "capture_failed", '{"response": "\ufffd\ufffd"}'),
    ("cut_in_a_multibyte_char", (200, enc_utf8(ok_body("\u00e9" * 4))[:-2] + "\u00e9".encode()[:1]),
     "body_truncated", None),
    ("invalid_json", (200, b"not json}"), "invalid_json", "not json}"),
    ("json_list", (200, b"[1, 2]"), "invalid_json", "[1, 2]"),
    ("cut_json", (200, enc(ok_body("x" * 50))[:40]), "body_truncated", enc(ok_body("x" * 50))[:40].decode()),
    ("server_error", (200, enc({"error": "boom"})), "server_error", enc({"error": "boom"}).decode()),
    ("server_error_with_text", (200, enc({"error": "boom", "response": "partial plan"})), "server_error", "partial plan"),
    ("not_done", (200, enc(ok_body("half a pla", done=False))), "body_truncated", "half a pla"),
    ("response_missing", (200, enc({"done": True, "model": "m"})), "capture_failed", enc({"done": True, "model": "m"}).decode()),
    ("response_not_text", (200, enc({"done": True, "response": 5})), "capture_failed", enc({"done": True, "response": 5}).decode()),
    ("status_not_int", ("200", b"kept anyway"), "capture_failed", "kept anyway"),
    ("body_as_text", (200, "text not bytes"), "capture_failed", "text not bytes"),
]


@pytest.mark.parametrize("name, result, error, raw", FAILURES, ids=[f[0] for f in FAILURES])
def test_every_failure_path_keeps_the_content_and_the_original_size(name, result, error, raw) -> None:
    reply = ask(result)
    data = result[1]
    size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)

    assert reply.status == "error" and reply.error == error
    if raw is not None:
        assert reply.raw_text == raw
    else:
        assert reply.raw_text and "\ufffd" in reply.raw_text  # the cut character, replaced
    assert reply.reported_timings["body_bytes"] == size


def test_a_cut_in_the_middle_of_a_multibyte_character_is_a_truncated_body_not_a_capture_failure() -> None:
    whole = enc_utf8(ok_body("\u00e9\u00e9\u00e9"))
    cut = whole[: whole.index("\u00e9".encode()) + 1]  # first byte of the first two-byte character

    assert ask((200, cut)).error == "body_truncated"
    # an invalid byte that is NOT at the end stays a capture failure
    assert ask((200, b"\xff" + whole)).error == "capture_failed"
    assert ask((200, b'{"a": "\xc3' + b'x"}')).error == "capture_failed"


def test_an_empty_body_has_nothing_to_keep_but_its_size() -> None:
    reply = ask((200, b""))

    assert reply.error == "capture_failed" and reply.raw_text is None
    assert reply.reported_timings == {"body_bytes": 0}
    assert ask((500, b"")).raw_text is None


def test_the_preserved_text_is_cut_at_the_limit_and_the_size_is_the_original(monkeypatch) -> None:
    monkeypatch.setattr(tr, "MAX_BODY_BYTES", 10)
    reply = ask((500, b"0123456789abcdefghij"))

    assert reply.raw_text == "0123456789" and reply.reported_timings["body_bytes"] == 20
    reply = ask((200, b"\xff" * 30))
    assert reply.raw_text == "\ufffd" * 10 and reply.reported_timings["body_bytes"] == 30


def test_output_captured_before_a_caller_failure_is_kept() -> None:
    class Slow(TimeoutError):
        partial_output = '{"steps": ['

    class Reset(ConnectionResetError):
        partial_output = "half"

    timeout = ask(Slow("slow"))
    reset = ask(Reset("reset"))
    plain = ask(TimeoutError("slow"))

    assert timeout.status == "timeout" and timeout.raw_text == '{"steps": ['
    assert reset.status == "error" and reset.raw_text == "half" and reset.error == "Reset"
    assert plain.raw_text is None


def test_a_large_but_allowed_response_is_still_parsed_and_analysable() -> None:
    big = "y" * 300_000
    reply = ask((200, enc(ok_body(big))))

    assert reply.status == "ok" and reply.raw_text == big
    assert reply.reported_timings["body_bytes"] > 300_000


def test_a_failed_reply_with_a_forbidden_structured_item_still_stops_the_round(
        tmp_path, validator, dataset) -> None:
    from test_intelligence_overdue_eval_runner import forbidden_step_plan
    clock = Clock()
    evil = json.dumps(forbidden_step_plan(dataset))
    # the stream ended early (`done: false`): an error, but the text of the plan is kept
    post = scripted_post(clock, dataset, {("c001", 0, "plan"): (200, enc(ok_body(evil, done=False)))})
    outcome, _ = via_transport(tmp_path, validator, dataset, post, clock)
    call = find(tmp_path, "c001", 0, "plan")

    assert call["call_status"] == "error" and call["error"] == "body_truncated"
    assert call["raw_text"] == evil and call["forbidden_capability"] is True
    assert outcome.status == "stopped" and outcome.stop_reason == "forbidden_capability"


def test_the_runner_record_keeps_the_text_and_the_size_of_a_failed_http_call(
        tmp_path, validator, dataset) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset, {("c004", 0, "abstention"): (500, b"internal oops")})
    via_transport(tmp_path, validator, dataset, post, clock)
    call = find(tmp_path, "c004", 0, "abstention")

    assert call["call_status"] == "error" and call["error"] == "http_500"
    assert call["raw_text"] == "internal oops" and call["reported_timings"] == {"body_bytes": 13}


# --- model_info ----------------------------------------------------------------

class Get:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, url, timeout_s):
        self.calls.append((url, timeout_s))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def tags_body(*entries):
    return enc({"models": [{"name": n, "model": n, "digest": d} for n, d in entries]})


def test_model_info_reads_the_catalog_digest_of_the_EXACT_name() -> None:
    get = Get((200, tags_body(("qwen3:4b-instruct-2507-q4_K_M", OTHER_DIGEST), ("qwen3:4b", DIGEST))))
    info = tr.make_model_info(get, base_url=BASE, timeout_s=20)()

    assert info == {"digest": DIGEST}  # not the look-alike that comes first
    assert get.calls == [(BASE + "/api/tags", 20)]


@pytest.mark.parametrize("result, reason", [
    (TimeoutError("t"), "model_info_timeout"),
    ((500, b""), "model_info_http_500"),
    ((200, enc({})), "model_info_invalid_json"),
    ((200, tags_body(("qwen3:4b", ""))), "model_info_digest_missing"),
    ((200, tags_body(("qwen3:4b-instruct-2507-q4_K_M", OTHER_DIGEST))), "model_info_digest_missing"),
    ((200, tags_body()), "model_info_digest_missing"),
    ((200, b"nope}"), "model_info_invalid_json"),
    ((200, b""), "model_info_capture_failed"),
])
def test_model_info_failures_are_explicit(result, reason) -> None:
    with pytest.raises(RuntimeError, match=reason):
        tr.make_model_info(Get(result), base_url=BASE)()


# --- no network, hashed with the manifest ---------------------------------------

def test_the_transport_imports_no_network_client_ollama_or_clock() -> None:
    tree = ast.parse(Path(tr.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert imported <= {"__future__", "dataclasses", "hashlib", "json", "collections.abc", "typing",
                        "intelligence_abstention_contract", "intelligence_overdue_eval_runner"}
    banned = re.compile(r"socket|http|urllib|request|ollama|subprocess|asyncio|\btime\b|\bssl\b|aiohttp")
    assert not [m for m in imported if banned.search(m)]
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {"urlopen", "connect", "socket", "requests", "httpx", "Popen", "sleep"} & names


def test_the_transport_file_is_pinned_by_the_manifest(dataset) -> None:
    manifest = make_manifest(dataset)
    entry = next(e["sha256"] for e in manifest["entries"] if e["name"] == "transport")

    assert entry == rn.file_sha256(Path(tr.__file__)) == rn.source_hashes()["transport"]
    assert "transport" in rn.REQUIRED_ENTRIES


# --- through the runner ----------------------------------------------------------

def scripted_post(clock, dataset_, overrides=None, latency=5.0, digest=DIGEST):
    answer = perfect(dataset_)
    overrides = overrides or {}
    seen = []

    def post(url, body, timeout_s):
        data = json.loads(body) if body else None
        seen.append((url, data, timeout_s))
        if url.endswith(tr.SHOW_PATH):
            return 200, enc({"digest": digest})
        if url.endswith(tr.TAGS_PATH):
            return 200, tags_body(("qwen3:4b", digest))
        prompt = data["prompt"]
        sid = json.loads(prompt.split("Facts:\n", 1)[1])["scenario_id"]
        stage = rn.STAGE_ABSTENTION if prompt.startswith("You check whether") else rn.STAGE_PLAN
        rep = data["options"]["seed"] - 1000
        item = overrides.get((sid, rep, stage))
        clock.now += latency
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, tuple) and item and item[0] == "late":
            clock.now += item[1]
        elif isinstance(item, tuple):
            return item
        raw = answer(rn.ModelRequest(stage, sid, rep, prompt, {}, timeout_s)).raw_text
        return 200, enc(ok_body(raw, digest=digest))

    post.seen = seen
    return post


def via_transport(tmp_path, validator, dataset_, post, clock, **kw):
    ask_model = tr.make_ask_model(post, base_url=BASE)
    info = tr.make_model_info(lambda url, timeout_s: post(url, None, timeout_s), base_url=BASE)
    return run(tmp_path, validator, dataset_, clock=clock, fake=ask_model, info=info, **kw)


def test_a_whole_round_runs_through_the_simulated_transport(tmp_path, validator, dataset) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset)
    outcome, _ = via_transport(tmp_path, validator, dataset, post, clock)

    assert outcome.status == "completed"
    generate = [(d, t) for u, d, t in post.seen if u.endswith(tr.GENERATE_PATH)]
    assert len(generate) == 45 and all(d["model"] == "qwen3:4b" for d, _ in generate)
    # the first words of the final templates identify the stage ("You assess"/"Produce" were the draft's)
    assert {t for d, t in generate if d["prompt"].startswith("You check whether")} == {150}
    assert {t for d, t in generate if d["prompt"].startswith("Write a plan.v1")} == {400}
    assert [d["options"]["seed"] - 1000 for d, _ in generate][:2] == [0, 0]
    for data, _ in generate:  # the request carries no verdict field
        assert not {"family", "expected", "canary_refs"} & set(data)
        assert '"family"' not in data["prompt"] and '"expected"' not in data["prompt"]
    report = report_of(tmp_path, dataset)
    assert report["complete"] is True and rn.evaluate_criteria(report)["overall"] == "pass"
    assert calls_of(tmp_path)[0]["reported_timings"]["total_duration"] == 3_000_000_000


def test_a_timeout_through_the_transport_is_recorded_and_the_round_goes_on(
        tmp_path, validator, dataset) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset, {("c003", 0, "abstention"): TimeoutError("slow")})
    outcome, _ = via_transport(tmp_path, validator, dataset, post, clock)
    call = find(tmp_path, "c003", 0, "abstention")

    assert outcome.status == "completed"
    assert call["call_status"] == "timeout" and call["result"] == "invalid_response"
    assert report_of(tmp_path, dataset)["errors_and_timeouts"]["by_status"]["timeout"] == 1


def test_a_late_answer_through_the_transport_is_a_timeout_in_the_runner(
        tmp_path, validator, dataset) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset, {("c003", 0, "abstention"): ("late", 200.0)})
    via_transport(tmp_path, validator, dataset, post, clock)
    call = find(tmp_path, "c003", 0, "abstention")

    assert call["call_status"] == "timeout" and call["elapsed_s"] == 205.0


@pytest.mark.parametrize("kind, data, error", [
    ("http", (500, b""), "http_500"),
    ("truncated", (200, enc(ok_body("x" * 80))[:30]), "body_truncated"),
    ("invalid", (200, b"nope}"), "invalid_json"),
    ("capture", (200, b""), "capture_failed"),
])
def test_transport_failures_are_error_calls_that_keep_what_was_captured(
        tmp_path, validator, dataset, kind, data, error) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset, {("c004", 0, "abstention"): data})
    outcome, _ = via_transport(tmp_path, validator, dataset, post, clock)
    call = find(tmp_path, "c004", 0, "abstention")

    assert outcome.status == "completed"
    assert call["call_status"] == "error" and call["error"] == error
    assert call["result"] == "invalid_response"
    if kind in ("truncated", "invalid"):
        assert call["raw_text"]  # the decoded text is preserved


def test_a_late_answer_is_kept_as_evidence_without_replacing_the_timeout_or_earning_credit(
        tmp_path, validator, dataset) -> None:
    from test_intelligence_overdue_eval_runner import resp
    clock = Clock()
    # both answers have CORRECT content and arrive after the limit (150 s and 400 s)
    post = scripted_post(clock, dataset, {("c003", 0, "abstention"): ("late", 150.0),
                                          ("c001", 0, "plan"): ("late", 400.0)})
    outcome, _ = via_transport(tmp_path, validator, dataset, post, clock)
    stage1 = find(tmp_path, "c003", 0, "abstention")
    stage2 = find(tmp_path, "c001", 0, "plan")

    assert outcome.status == "completed"
    for call in (stage1, stage2):
        assert call["call_status"] == "timeout" and call["reply_status"] == "ok"  # both kept
        assert call["elapsed_s"] > call["timeout_s"] and call["raw_text"]
    assert json.loads(stage1["raw_text"]) == resp("c003", "abstain", "not_due")  # the evidence
    assert stage1["result"] == "invalid_response" and stage1["model_decision"] is None
    assert stage2["late_analysed"] is True and stage2["correct"] is False
    assert stage2["admission"] == "not_evaluated" and stage2["unsafe"] is False
    assert '"steps"' in stage2["raw_text"]
    assert stage1["late_response"] is True and stage1["late_content"] == "correct"
    assert stage2["late_response"] is True and stage2["late_content"] == "parsed"
    report = report_of(tmp_path, dataset)
    assert report["late_responses"] == {"count": 2, "credited": False,
                                        "stage1": {"correct": 1, "incorrect": 0, "malformed": 0},
                                        "stage2": {"parsed": 1, "malformed": 0}}
    assert report["timeouts"] == 2
    assert report["abstention_by_code"]["not_due"]["numerator"] == 8  # c003 rep 0 earned nothing
    assert report["eligible_success"]["numerator"] == 8  # c001 rep 0 plan earned nothing
    assert report["errors_and_timeouts"]["by_status"]["timeout"] == 2


def test_a_diverging_digest_through_the_transport_stops_the_round(
        tmp_path, validator, dataset) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset)
    inner = post

    def swapping(url, body, timeout_s):
        status, data = inner(url, body, timeout_s)
        if url.endswith(tr.GENERATE_PATH) and clock.now > 1000.0 + 5.0 * 4:  # from the 5th call on
            payload = json.loads(data)
            payload["digest"] = OTHER_DIGEST
            data = enc(payload)
        return status, data

    outcome, _ = via_transport(tmp_path, validator, dataset, swapping, clock)

    assert outcome.status == "stopped" and outcome.stop_reason == "model_mismatch"
    with pytest.raises(rn.ResumeRefused):
        run(tmp_path, validator, dataset, clock=clock, resume=True)


def test_a_candidate_whose_digest_differs_from_the_manifest_is_refused_at_the_preflight(
        tmp_path, validator, dataset) -> None:
    clock = Clock()
    post = scripted_post(clock, dataset, digest=OTHER_DIGEST)

    with pytest.raises(rn.PreflightRefused) as info:
        via_transport(tmp_path, validator, dataset, post, clock)

    assert info.value.reasons == ("model_mismatch",)
    assert not [1 for u, _, _ in post.seen if u.endswith(tr.GENERATE_PATH)]  # no generate call
    assert list(tmp_path.iterdir()) == []


def test_a_failing_model_info_through_the_transport_refuses_to_start(
        tmp_path, validator, dataset) -> None:
    clock = Clock()

    def down(url, body, timeout_s):
        return 503, b""

    with pytest.raises(rn.PreflightRefused) as info:
        via_transport(tmp_path, validator, dataset, down, clock)

    assert "model_info_failed" in info.value.reasons


def test_five_failures_in_a_row_through_the_transport_open_the_breaker(
        tmp_path, validator, dataset) -> None:
    clock = Clock()
    overrides = {(c, 0, "abstention"): (500, b"") for c in ("c001", "c002", "c003", "c004", "c005")}
    post = scripted_post(clock, dataset, overrides)
    outcome, _ = via_transport(tmp_path, validator, dataset, post, clock)

    assert outcome.stop_reason == "circuit_breaker"
    assert [c["error"] for c in calls_of(tmp_path)] == ["http_500"] * 5


# --- the stream: newline-delimited JSON, parsed without a socket -------------------------

def lines(*items) -> bytes:
    return b"".join(json.dumps(i).encode() + b"\n" for i in items)


def piece(text, **over):
    return {"model": "qwen3:4b", "response": text, "done": False, **over}


def final(**over):
    body = ok_body("", **over)
    body.pop("digest", None)
    return body


def test_a_stream_is_assembled_from_its_pieces_and_the_final_line_carries_the_numbers() -> None:
    data = lines(piece(""), piece('{"deci'), piece('sion": "proceed"}'), final())
    reply = tr.parse_reply(200, data, {"first_chunk_s": 0.5, "first_token_s": 1.25})

    assert reply.status == "ok" and reply.raw_text == '{"decision": "proceed"}'
    assert (reply.input_tokens, reply.output_tokens) == (120, 60)
    assert reply.reported_timings["eval_duration"] == 1_500_000_000  # nanoseconds, as informed
    assert reply.model_digest is None  # the real generate response carries none (assumption)
    assert (reply.first_chunk_s, reply.first_token_s) == (0.5, 1.25)
    assert reply.model_name == "qwen3:4b" and reply.done_reason == "stop"


def test_thinking_is_kept_when_it_comes() -> None:
    reply = tr.parse_reply(200, lines(piece("a", thinking="t1"), final(thinking="t2")))

    assert reply.status == "ok" and reply.thinking == "t1t2"


def test_an_error_line_in_the_middle_of_the_stream_is_a_server_error_that_keeps_the_text() -> None:
    reply = tr.parse_reply(200, lines(piece("half "), {"error": "boom"}))

    assert reply.status == "error" and reply.error == "server_error" and reply.raw_text == "half "


def test_a_stream_that_ends_without_the_final_line_is_truncated_and_keeps_the_text() -> None:
    reply = tr.parse_reply(200, lines(piece("abc"), piece("def")))

    assert reply.status == "error" and reply.error == "body_truncated" and reply.raw_text == "abcdef"


def test_a_cut_last_line_is_truncated_and_an_invalid_middle_line_is_invalid_json() -> None:
    cut = lines(piece("abc")) + b'{"model": "qwen3:4b", "resp'
    middle = lines(piece("abc")) + b"garbage}\n" + lines(final())

    assert tr.parse_reply(200, cut).error == "body_truncated"
    assert tr.parse_reply(200, cut).raw_text == "abc"
    assert tr.parse_reply(200, middle).error == "invalid_json"
    assert tr.parse_reply(200, middle).raw_text == "abc"


def test_the_envelope_name_is_the_first_one_that_is_not_the_candidate() -> None:
    swapped = lines(piece("a"), piece("b", model="qwen3:4b-instruct-2507-q4_K_M"), final())

    assert tr.parse_reply(200, swapped).model_name == "qwen3:4b-instruct-2507-q4_K_M"
    assert tr.parse_reply(200, lines(piece("a"), final())).model_name == "qwen3:4b"


def test_the_partial_text_is_the_pieces_of_the_complete_lines() -> None:
    data = lines(piece("one "), piece("two ")) + b'{"model": "x", "response": "thr'

    assert tr.partial_text(data) == "one two "
    assert tr.partial_text(b"no json here") == "no json here"
    assert tr.partial_text(b"") is None and tr.partial_text("text") is None


def test_the_tracker_tells_the_first_block_from_the_first_generated_content() -> None:
    tracker = tr.StreamTracker()
    tracker.feed(b"", 0.1)
    assert tracker.metrics() == {"first_chunk_s": None, "first_token_s": None}
    tracker.feed(lines(piece(""))[:10], 0.2)  # a block with no complete line
    tracker.feed(lines(piece(""))[10:], 0.3)  # completes a line whose response is empty
    assert tracker.metrics() == {"first_chunk_s": 0.2, "first_token_s": None}
    tracker.feed(lines(piece("x")), 0.9)
    tracker.feed(lines(piece("y")), 1.5)
    assert tracker.metrics() == {"first_chunk_s": 0.2, "first_token_s": 0.9}


def test_a_content_line_split_over_blocks_counts_when_it_completes() -> None:
    tracker = tr.StreamTracker()
    data = lines(piece("hello"))
    tracker.feed(data[:20], 0.4)
    tracker.feed(data[20:], 0.8)

    assert tracker.metrics() == {"first_chunk_s": 0.4, "first_token_s": 0.8}


def test_client_metrics_that_are_not_numbers_are_dropped() -> None:
    reply = tr.parse_reply(200, lines(final()), {"first_chunk_s": "x", "first_token_s": -1})

    assert reply.first_chunk_s is None and reply.first_token_s is None


def test_a_runtime_probe_reads_only_metadata_and_hashes_the_template_and_parameters() -> None:
    seen = []

    def get(url, timeout_s):
        seen.append(("GET", url))
        if url.endswith(tr.TAGS_PATH):
            return 200, tags_body(("qwen3:4b-instruct-2507-q4_K_M", OTHER_DIGEST), ("qwen3:4b", DIGEST))
        if url.endswith(tr.PS_PATH):
            return 200, enc({"models": [{"name": "qwen3:4b", "digest": DIGEST, "size": 7, "size_vram": 5}]})
        return 200, enc({"version": "0.40.10"})

    def post(url, body, timeout_s):
        seen.append(("POST", url))
        return 200, enc({"template": "T", "parameters": "P", "details": {}})

    probe = tr.make_runtime_probe(get, post, base_url=BASE)
    pre = probe("preflight")
    before = probe("before")
    after = probe("after")

    assert pre["version"] == "0.40.10" and pre["catalog_digest"] == DIGEST
    assert pre["template_sha256"] == tr.hashlib.sha256(b"T").hexdigest()
    assert pre["parameters_sha256"] == tr.hashlib.sha256(b"P").hexdigest()
    assert pre["loaded"] == [{"name": "qwen3:4b", "digest": DIGEST, "size": 7, "size_vram": 5}]
    assert set(before) == {"catalog_digest", "loaded"} and set(after) == {"loaded"}
    assert all(u.startswith(BASE) and not u.endswith(tr.GENERATE_PATH) for _, u in seen)


@pytest.mark.parametrize("ps_result, reason", [
    ((500, b""), "probe_ps_http_500"),
    ((200, enc({"models": "x"})), "ps_invalid"),
    ((200, b"nope}"), "probe_ps_invalid_json"),
])
def test_a_probe_that_cannot_read_the_runtime_raises(ps_result, reason) -> None:
    def get(url, timeout_s):
        return (200, enc({"models": []})) if url.endswith(tr.TAGS_PATH) else ps_result

    with pytest.raises(RuntimeError, match=reason):
        tr.make_runtime_probe(get, lambda *a: (200, b"{}"), base_url=BASE)("after")


def test_a_loaded_model_without_a_usable_digest_keeps_no_digest_and_the_memory_it_reports() -> None:
    ps = {"models": [{"name": "qwen3:4b", "size": 5}, {"model": "x:1", "digest": ""},
                     {"name": "y", "digest": 5, "size": "big", "size_vram": 2}]}

    assert tr.loaded_models(ps) == [
        {"name": "qwen3:4b", "digest": None, "size": 5, "size_vram": None},
        {"name": "x:1", "digest": None, "size": None, "size_vram": None},
        {"name": "y", "digest": None, "size": None, "size_vram": 2},
    ]
    with pytest.raises(RuntimeError, match="ps_invalid"):
        tr.loaded_models({"models": [1]})
