"""Item 7 do plano: marcadores sinteticos ausentes de logs, saidas e arquivos."""

import json
from pathlib import Path

import pytest

from harness import ROOT, SRC, build_request, dumps, good_payload

M_INSTR = "MARCADOR-INSTR-7f3a91"
M_IN = "MARCADOR-ENTRADA-9c1d42"
M_OUT = "MARCADOR-SAIDA-2b8e07"
M_SCHEMA = "MARCADOR-ESQUEMA-5e4f66"
M_HDR = "MARCADOR-HEADER-1a2b3c"
M_EXTRA = "MARCADOR-EXTRA-8d7c5b"
M_THINK = "MARCADOR-RACIOCINIO-4e6f01"
ALL = [M_INSTR, M_IN, M_OUT, M_SCHEMA, M_HDR, M_EXTRA, M_THINK]

ALLOWED_KEYS = {
    "ts", "event", "request_id", "profile", "digest", "status", "in_bytes", "out_bytes",
    "ms_total", "ms_queue", "ms_model", "code",
}


def exercise(g, ollama):
    schema = {
        "type": "object",
        "description": M_SCHEMA,
        "properties": {"ok": {"type": "boolean"}, "t": {"type": "string"}},
        "required": ["ok"],
    }
    ollama.thinking = M_THINK
    ollama.content = json.dumps({"ok": True, "t": M_OUT})
    kw = dict(instructions=M_INSTR, input_text=M_IN, output_schema=schema)
    hdr = {"X-Marker": M_HDR, "Authorization": "Bearer " + M_HDR}
    assert g.request(build_request("POST", "/v1/generate", dumps(good_payload(**kw)), headers=hdr)).status == 200
    # erros: contrato, json, modelo, saida, tamanho, rota
    g.post(good_payload(**kw, extra=M_EXTRA))
    g.post(good_payload(**{**kw, "data_classification": M_EXTRA}))
    g.post(b'{"instructions": "' + M_INSTR.encode() + b'", "instructions": "x"}')
    g.post(b"\xff" + M_IN.encode())
    g.request(build_request("GET", "/" + M_IN, None, headers=hdr))
    g.request(build_request("PUT", "/v1/generate", M_IN.encode(), headers=hdr))
    ollama.content = M_OUT + " nao eh json"
    assert g.post(good_payload(**kw, request_id="req-priv-0002")).code == "invalid_output"
    ollama.content = json.dumps({"ok": "no", "t": M_OUT})
    assert g.post(good_payload(**kw, request_id="req-priv-0003")).code == "invalid_output"
    ollama.chat_status = 500
    ollama.error_text = M_OUT
    g.post(good_payload(**kw, request_id="req-priv-0004"))
    ollama.chat_status = 200
    ollama.digest = "ee" * 32
    g.post(good_payload(**kw, request_id="req-priv-0005"))
    ollama.digest = "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"
    ollama.content = json.dumps({"ok": True, "t": "y" * 70000})
    g.post(good_payload(**kw, request_id="req-priv-0006"))


def test_markers_absent_from_logs_files_stdout_stderr_and_error_responses(gwf, ollama):
    g = gwf(enabled='"qwen3-4b-instruct-2507", "qwen3-4b-think"')
    exercise(g, ollama)
    g.stop()
    scanned = 0
    for f in g.all_files():
        data = f.read_bytes().decode("utf-8", "replace")
        scanned += 1
        for m in ALL:
            assert m not in data, f"{m} em {f.name}"
    assert scanned >= 3  # config, log, stdout, stderr


def test_error_responses_never_echo_input(gwf, ollama):
    g = gwf()
    r = g.post(good_payload(input_text=M_IN, extra=M_EXTRA))
    assert r.status == 422 and M_IN.encode() not in r.raw and M_EXTRA.encode() not in r.raw
    ollama.content = M_OUT + " x"
    r = g.post(good_payload(input_text=M_IN, request_id="req-priv-1001"))
    assert M_OUT.encode() not in r.raw and M_IN.encode() not in r.raw


def test_log_records_use_closed_field_list(gwf, ollama):
    g = gwf()
    exercise(g, ollama)
    recs = g.log_records()
    assert recs
    for r in recs:
        assert set(r) <= ALLOWED_KEYS, set(r) - ALLOWED_KEYS
        assert r["event"] in {"startup", "shutdown", "request", "conn_refused", "conn_limit", "internal"}
    req = [r for r in recs if r["event"] == "request" and "request_id" in r]
    assert req and all({"profile", "digest", "code", "ms_total"} <= set(r) for r in req)
    ok = [r for r in req if r["code"] == "ok"][0]
    assert ok["in_bytes"] > 0 and ok["out_bytes"] > 0 and "ms_queue" in ok and "ms_model" in ok


def test_audit_log_drops_unknown_fields_and_bad_values(tmp_path):
    from auneron_ai_gateway.glog import AuditLog

    log = AuditLog(tmp_path / "l")
    log.event("request", request_id="req-ok-0001", input_text=M_IN, output=M_OUT, headers={"a": M_HDR},
              code="ok", status=200, ms_total=5, profile="qwen3-4b-instruct-2507")
    log.event("request", request_id="evil\ninjection" + M_IN, code=M_IN, profile=M_IN, digest=M_IN,
              status=M_IN, ms_total=-1)
    log.event(M_IN + "-evento")
    log.close()
    text = (tmp_path / "l" / "gateway.log").read_text(encoding="utf-8")
    assert M_IN not in text and M_OUT not in text and M_HDR not in text
    lines = text.splitlines()
    assert len(lines) == 3 and all(json.loads(x) for x in lines)  # sem quebra de linha injetada
    assert json.loads(lines[0])["request_id"] == "req-ok-0001"


def test_audit_log_rotation_and_retention_30_days(tmp_path):
    import logging.handlers

    from auneron_ai_gateway.glog import AuditLog

    log = AuditLog(tmp_path)
    h = log._handler
    assert isinstance(h, logging.handlers.TimedRotatingFileHandler)
    assert h.backupCount == 30 and h.when == "MIDNIGHT" and log.retention_days == 30
    log.close()


def test_no_secrets_or_real_data_in_project_files():
    needles = ["-----BEGIN " + "PRIVATE KEY", "-----BEGIN " + "RSA PRIVATE", "-----BEGIN " + "EC PRIVATE",
               "sk-" + "ant", "AKIA" + "ABCD"]
    for base in (SRC, ROOT / "tests", ROOT / "tools", ROOT / "config"):
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in {".py", ".toml", ".md", ".txt", ".in"}:
                text = f.read_text(encoding="utf-8", errors="replace")
                for n in needles:
                    assert n not in text, (f.name, n)
    for f in (ROOT / "config").glob("*.toml"):
        text = f.read_text(encoding="utf-8")
        assert "password" not in text.lower() and "token" not in text.lower() and "secret" not in text.lower()


def test_example_config_loads_and_has_no_secret_values():
    from auneron_ai_gateway.config import load_config

    cfg = load_config(ROOT / "config" / "gateway.example.toml")
    assert cfg.host == "127.0.0.1" and cfg.port == 8443 and cfg.ollama_port == 11434
    assert cfg.enabled_profiles == {"qwen3-4b-instruct-2507"}
    assert cfg.min_tls == "1.3" and cfg.per_minute == 10 and cfg.queue_size == 2 and cfg.total_timeout == 120
