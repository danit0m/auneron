"""Item 3 do plano: contrato estrito e limite de 32 KiB no corpo bruto."""

import copy
import json

import pytest

from harness import SCHEMA, build_request, dumps, good_payload

LIMIT = 32 * 1024


def payload_of_size(n: int) -> bytes:
    base = len(dumps(good_payload(input_text="x")))
    body = dumps(good_payload(input_text="x" * (1 + n - base)))
    assert len(body) == n
    return body


def deep_schema(depth: int) -> dict:
    node: dict = {"type": "string"}
    for _ in range(depth):
        node = {"type": "object", "properties": {"a": node}}
    return node


def nodes_schema(n: int) -> dict:
    return {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(n)}}


BAD_CONTRACT = {
    "extra_top_field": lambda p: {**p, "extra": 1},
    "extra_nested_schema_kw": lambda p: {**p, "output_schema": {**p["output_schema"], "pattern": "x"}},
    "model_override": lambda p: {**p, "model": "qwen3:4b"},
    "options_override": lambda p: {**p, "options": {"temperature": 1}},
    "missing_instructions": lambda p: {k: v for k, v in p.items() if k != "instructions"},
    "missing_class": lambda p: {k: v for k, v in p.items() if k != "data_classification"},
    "missing_request_id": lambda p: {k: v for k, v in p.items() if k != "request_id"},
    "instructions_int": lambda p: {**p, "instructions": 5},
    "input_list": lambda p: {**p, "input_text": ["a"]},
    "input_null": lambda p: {**p, "input_text": None},
    "input_empty": lambda p: {**p, "input_text": ""},
    "request_id_int": lambda p: {**p, "request_id": 12345678},
    "schema_str": lambda p: {**p, "output_schema": "{}"},
    "schema_list": lambda p: {**p, "output_schema": []},
    "profile_unknown": lambda p: {**p, "profile": "gpt-9"},
    "profile_is_model_name": lambda p: {**p, "profile": "qwen3:4b-instruct-2507-q4_K_M"},
    "profile_disabled_think": lambda p: {**p, "profile": "qwen3-4b-think"},
    "profile_case": lambda p: {**p, "profile": "QWEN3-4B-INSTRUCT-2507"},
    "class_real": lambda p: {**p, "data_classification": "real"},
    "class_personal": lambda p: {**p, "data_classification": "personal"},
    "class_case": lambda p: {**p, "data_classification": "SYNTHETIC"},
    "class_space": lambda p: {**p, "data_classification": "synthetic "},
    "class_bool": lambda p: {**p, "data_classification": True},
    "rid_short": lambda p: {**p, "request_id": "abc"},
    "rid_space": lambda p: {**p, "request_id": "req 0000001"},
    "rid_newline": lambda p: {**p, "request_id": "req-0000001\nINJECT"},
    "rid_unicode": lambda p: {**p, "request_id": "req-0000001é"},
    "rid_long": lambda p: {**p, "request_id": "a" * 65},
    "rid_slash": lambda p: {**p, "request_id": "../../etc/passwd"},
    "schema_ref": lambda p: {**p, "output_schema": {"type": "object", "properties": {"a": {"$ref": "http://x/y"}}}},
    "schema_root_ref": lambda p: {**p, "output_schema": {"$ref": "#/definitions/a", "type": "object"}},
    "schema_defs": lambda p: {**p, "output_schema": {"type": "object", "$defs": {}}},
    "schema_allof": lambda p: {**p, "output_schema": {"type": "object", "allOf": []}},
    "schema_not_object_root": lambda p: {**p, "output_schema": {"type": "string"}},
    "schema_too_deep": lambda p: {**p, "output_schema": deep_schema(9)},
    "schema_too_many_nodes": lambda p: {**p, "output_schema": nodes_schema(250)},
    "schema_ap_schema": lambda p: {**p, "output_schema": {"type": "object", "additionalProperties": {"type": "string"}}},
    "schema_bad_type": lambda p: {**p, "output_schema": {"type": "object", "properties": {"a": {"type": "blob"}}}},
    "schema_huge_text": lambda p: {**p, "output_schema": {"type": "object", "description": "x" * 9000}},
    "strict_bool_as_str": lambda p: {**p, "instructions": True},
}


@pytest.mark.parametrize("name", sorted(BAD_CONTRACT))
def test_contract_violations_rejected_before_ollama(gw, ollama, name):
    p = BAD_CONTRACT[name](copy.deepcopy(good_payload()))
    r = gw.post(p)
    assert r.status == 422 and r.code == "invalid_contract", (name, r.body)
    assert ollama.calls == []


def test_schema_depth_at_limit_is_accepted(gw, ollama):
    r = gw.post(good_payload(output_schema=deep_schema(6)), name="client_ok")
    # a profundidade e valida; a falha, se houver, e so da saida do modelo (nao do contrato)
    assert r.code != "invalid_contract"


@pytest.mark.parametrize(
    "mk",
    [
        lambda b: b.replace(b'"synthetic"', b'"synthetic","data_classification":"synthetic"'),
        lambda b: b[:-1] + b',"request_id":"req-00000002"}',
        lambda b: b.replace(b'"type": "boolean"', b'"type": "boolean", "type": "string"'),
        lambda b: b.replace(b'"type": "boolean"', b'"type": "boolean", "minimum": NaN'),
        lambda b: b.replace(b'"type": "boolean"', b'"type": "boolean", "minimum": Infinity'),
        lambda b: b.replace(b'"type": "boolean"', b'"type": "boolean", "minimum": -Infinity'),
        lambda b: b.replace(b'"type": "boolean"', b'"type": "boolean", "minimum": 1e999'),
        lambda b: b.replace(b"Texto sintetico", b"Texto \xff\xfe sintetico"),
        lambda b: b.replace(b"Texto sintetico", b"Texto \\ud800 sintetico"),
        lambda b: b.replace(b"Texto sintetico", b"Texto \\udfff\\ud800 sintetico"),
        lambda b: b"\xef\xbb\xbf" + b,
        lambda b: b + b"x",
        lambda b: b[:-1],
        lambda b: b"[" * 3000 + b"]" * 3000,
        lambda b: b'{"a":' * 40 + b"1" + b"}" * 40,
        lambda b: b"",
        lambda b: b"null",
        lambda b: b"[]",
        lambda b: b"'" + b + b"'",
        lambda b: b.replace(b'"', b"'"),
    ],
)
def test_malformed_json_rejected(gw, ollama, mk):
    r = gw.post(mk(dumps(good_payload())))
    assert r.status in (400, 422) and r.code in ("invalid_json", "invalid_contract")
    assert ollama.calls == []


def test_deep_json_within_stack_but_over_depth_limit(gw):
    r = gw.post(b'{"a":' * 33 + b"1" + b"}" * 33)
    assert r.code == "invalid_json"


def test_unicode_escapes_valid_pairs_accepted(gwf, ollama):
    g = gwf()
    r = g.post(good_payload(input_text="emoji \U0001f600 ok"))  # json.dumps escapa como par substituto
    assert r.status == 200
    raw = dumps(good_payload()).replace(b"Texto sintetico", b"Texto \\ud83d\\ude00 sintetico")
    assert g.post(raw).status == 200


# ------------------------------------------------------------- tamanho

def test_exactly_32_kib_accepted_and_one_more_refused(gwf, ollama):
    g = gwf()
    ok = g.post(payload_of_size(LIMIT))
    assert ok.status == 200, ok.body
    over = g.post(payload_of_size(LIMIT + 1))
    assert over.status == 413 and over.code == "payload_too_large"
    assert len(ollama.chat_bodies) == 1


def test_content_length_over_limit_refused_without_reading_body(gw):
    raw = build_request("POST", "/v1/generate", b"", content_length=LIMIT + 1)
    r = gw.request(raw)
    assert r.status == 413


def test_huge_content_length_refused(gw):
    r = gw.request(build_request("POST", "/v1/generate", b"", content_length=10**11))
    assert r.status in (400, 413)


def test_chunked_without_content_length_at_limit_and_over(gwf, ollama):
    g = gwf()
    ok = g.request(build_request("POST", "/v1/generate", payload_of_size(LIMIT), chunked=True))
    assert ok.status == 200
    over = g.request(build_request("POST", "/v1/generate", payload_of_size(LIMIT + 1), chunked=True))
    assert over.status == 413 and over.code == "payload_too_large"


def test_chunked_big_single_chunk_header_refused(gw):
    raw = (
        b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"9000\r\n" + b"a" * 0x9000 + b"\r\n0\r\n\r\n"
    )
    assert gw.request(raw).status == 413  # limite aplicado durante a leitura em stream


@pytest.mark.parametrize(
    "chunks",
    [
        b"5;ext=1\r\nhello\r\n0\r\n\r\n",  # extensao de chunk
        b"5\r\nhello\r\n0\r\nX-Trailer: a\r\n\r\n",  # trailer
        b"zz\r\nhello\r\n0\r\n\r\n",  # tamanho nao hex
        b"5\r\nhelloXX0\r\n\r\n",  # CRLF ausente
    ],
)
def test_chunked_malformed_refused(gw, chunks):
    raw = b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n" + chunks
    assert gw.request(raw).status in (400, 413)


def test_content_length_shorter_than_body_does_not_smuggle(gw, ollama):
    body = dumps(good_payload())
    raw = build_request("POST", "/v1/generate", body, content_length=10)
    r = gw.request(raw)
    assert r.status == 400 and r.code == "invalid_json"
    assert ollama.calls == []


def test_content_length_longer_than_body_times_out(gwf, ollama):
    g = gwf(body_timeout=1.0)
    body = dumps(good_payload())
    r = g.request(build_request("POST", "/v1/generate", body, content_length=len(body) + 500))
    assert r.status == 408 and r.code == "request_timeout"
    assert ollama.calls == []


def test_no_length_no_chunked_refused(gw):
    r = gw.request(b"POST /v1/generate HTTP/1.1\r\nHost: a\r\nContent-Type: application/json\r\n\r\n")
    assert r.status == 400


@pytest.mark.parametrize("ct", [None, "text/plain", "application/x-www-form-urlencoded", "application/jsonx", "json"])
def test_content_type_required(gw, ollama, ct):
    raw = build_request("POST", "/v1/generate", dumps(good_payload()), content_type=ct)
    assert gw.request(raw).status == 415
    assert ollama.calls == []


def test_content_type_with_charset_ok(gwf):
    g = gwf()
    raw = build_request("POST", "/v1/generate", dumps(good_payload()), content_type="application/json; charset=utf-8")
    assert g.request(raw).status == 200


def test_valid_request_reaches_ollama_with_scratch_built_body(gwf, ollama):
    g = gwf()
    p = good_payload(request_id="req-from-client-1", instructions="INSTR", input_text="INPUT")
    r = g.post(p)
    assert r.status == 200
    body = r.json()
    assert set(body) == {"request_id", "profile", "model", "digest", "output"}
    sent = ollama.chat_bodies[0]
    assert set(sent) == {"model", "messages", "stream", "think", "format", "options", "keep_alive"}
    assert sent["model"] == "qwen3:4b-instruct-2507-q4_K_M"
    assert sent["stream"] is False and sent["think"] is False
    assert sent["messages"] == [
        {"role": "system", "content": "INSTR"},
        {"role": "user", "content": "INPUT"},
    ]
    assert sent["format"] == SCHEMA
    assert sent["options"] == {
        "temperature": 0, "top_p": 1.0, "top_k": 1, "seed": 0, "num_predict": 1024, "num_ctx": 8192,
    }
    assert sent["keep_alive"] == "5m"
    assert json.dumps(sent).count("req-from-client-1") == 0  # request_id nao vai ao modelo
    assert ollama.calls == [("GET", "/api/tags"), ("POST", "/api/chat"), ("GET", "/api/tags")]
