"""G2 r3: (A) permissao de escrita do certificado e da CA; (B) reverificacao periodica do Ollama."""

import asyncio
import dataclasses
import json
import subprocess
import sys

import pytest

from harness import Gateway, dumps, free_port, good_payload
from test_units import make_cfg


# ================================================================= A. permissao do certificado / CA
def test_posix_write_rule_allows_public_read_and_refuses_write_by_others():
    from auneron_ai_gateway.fsperm import posix_write_violation as rule

    for mode in (0o100600, 0o100644, 0o100640, 0o100604, 0o100444, 0o100755):  # leitura por outros: ok
        assert rule(mode, 1000, 1000) is None, oct(mode)
    for mode in (0o100664, 0o100602, 0o100666, 0o100620, 0o100606, 0o100660):  # escrita grupo/outros
        assert rule(mode, 1000, 1000), oct(mode)
    assert rule(0o100644, 0, 1000) is None  # dono root: ok
    assert rule(0o100644, 1234, 1000)  # dono e outro usuario
    assert rule(0o040755, 1000, 1000)  # diretorio nao e arquivo regular


def test_windows_write_rule_pure_logic():
    from auneron_ai_gateway.fsperm import WRITE_MASK, windows_violations

    me = {"S-1-5-21-1-2-3-1001"}
    read = 0x120089  # FILE_GENERIC_READ
    rx = 0x1200A9  # leitura e execucao
    # leitura por Todos/Usuarios/Autenticados: aceita para arquivo publico, recusada para chave
    for sid in ("S-1-1-0", "S-1-5-32-545", "S-1-5-11"):
        assert windows_violations([(0, 0, sid, read)], me, write_only=True) == []
        assert windows_violations([(0, 0, sid, rx)], me, write_only=True) == []
        assert windows_violations([(0, 0, sid, read)], me) == [sid]  # modo chave inalterado
    # cada bit de escrita recusa
    for bit in (0x2, 0x4, 0x10, 0x100, 0x10000, 0x40000, 0x80000, 0x10000000, 0x40000000):
        assert bit & WRITE_MASK
        assert windows_violations([(0, 0, "S-1-5-32-545", read | bit)], me, write_only=True) == ["S-1-5-32-545"]
    assert windows_violations([(0, 0, "S-1-1-0", 0x1301BF)], me, write_only=True)  # modificar
    assert windows_violations([(0, 0, "S-1-1-0", 0x1F01FF)], me, write_only=True)  # controle total
    assert windows_violations([(9, 0, "S-1-5-11", 0x2)], me, write_only=True)  # callback com escrita
    # sem mascara conhecida = falha fechada
    assert windows_violations([(0, 0, "S-1-1-0")], me, write_only=True) == ["S-1-1-0"]
    # dono, SYSTEM, Administradores com escrita: ok
    ok = [(0, 0, "S-1-5-21-1-2-3-1001", 0x1F01FF), (0, 0, "S-1-5-18", 0x1F01FF), (0, 0, "S-1-5-32-544", 0x1F01FF)]
    assert windows_violations(ok, me, write_only=True) == []
    assert windows_violations([(1, 0, "S-1-1-0", 0x1F01FF)], me, write_only=True) == []  # negar nao conta
    assert windows_violations([(0, 0x08, "S-1-1-0", 0x1F01FF)], me, write_only=True) == []  # so filhos
    assert windows_violations([(5, 0, "?", 0)], me, write_only=True)  # ACE de objeto: recusa


def test_public_check_fails_closed_when_verification_is_impossible(tmp_path, monkeypatch):
    from auneron_ai_gateway import fsperm

    with pytest.raises(fsperm.KeyPermissionError):
        fsperm.check_public_file_not_writable(tmp_path / "nao-existe.pem")
    f = tmp_path / "c.pem"
    f.write_bytes(b"x")

    def boom(*a, **k):
        raise RuntimeError("falha")

    monkeypatch.setattr(fsperm, "_read_windows_acl", boom)
    monkeypatch.setattr(fsperm, "_check_posix", boom)
    with pytest.raises(fsperm.KeyPermissionError, match="nao verificavel"):
        fsperm.check_public_file_not_writable(f)


def test_public_check_accepts_default_test_cert_and_ca(pki):
    from auneron_ai_gateway.fsperm import check_public_file_not_writable

    check_public_file_not_writable(pki["server"].cert_path)
    check_public_file_not_writable(pki.ca_path)


@pytest.mark.skipif(sys.platform != "win32", reason="ACL do Windows")
def test_windows_public_file_read_ok_write_refused(pki, tmp_path):
    from auneron_ai_gateway.fsperm import KeyPermissionError, check_public_file_not_writable

    f = tmp_path / "ca.pem"
    f.write_bytes(pki.ca_path.read_bytes())
    __import__("make_test_certs").restrict_private_file(f)
    check_public_file_not_writable(f)
    for sid in ("S-1-1-0", "S-1-5-32-545", "S-1-5-11"):
        subprocess.run(["icacls", str(f), "/grant", f"*{sid}:(R)"], capture_output=True, check=True)
        check_public_file_not_writable(f)  # leitura publica: aceita
        for perm in ("(W)", "(M)", "(F)", "(AD)", "(D)", "(WDAC)"):
            subprocess.run(["icacls", str(f), "/grant", f"*{sid}:{perm}"], capture_output=True, check=True)
            with pytest.raises(KeyPermissionError):
                check_public_file_not_writable(f)
            subprocess.run(["icacls", str(f), "/remove:g", f"*{sid}"], capture_output=True, check=True)
            subprocess.run(["icacls", str(f), "/grant", f"*{sid}:(R)"], capture_output=True, check=True)
        subprocess.run(["icacls", str(f), "/remove", f"*{sid}"], capture_output=True, check=True)
        check_public_file_not_writable(f)


@pytest.mark.skipif(sys.platform == "win32", reason="modo POSIX")
def test_posix_public_file_real_modes(tmp_path, pki):
    import os

    from auneron_ai_gateway.fsperm import KeyPermissionError, check_public_file_not_writable

    f = tmp_path / "c.pem"
    f.write_bytes(b"x")
    os.chmod(f, 0o644)
    check_public_file_not_writable(f)
    for mode in (0o664, 0o646, 0o666):
        os.chmod(f, mode)
        with pytest.raises(KeyPermissionError):
            check_public_file_not_writable(f)


def _cfg_for(pki, ollama, **repl):
    from auneron_ai_gateway.config import load_config

    g = Gateway(pki, ollama.port)
    cfg = load_config(g.write_config())
    return g, dataclasses.replace(cfg, **repl)


def test_preflight_checks_cert_and_ca_with_public_check(pki, ollama):
    from auneron_ai_gateway.startup import preflight

    g, cfg = _cfg_for(pki, ollama)
    seen = []
    preflight(cfg, listeners=lambda: [], key_check=lambda p: None, public_check=seen.append)
    assert seen == [cfg.cert_file, cfg.ca_file]
    g.cleanup()


def test_preflight_refuses_ca_or_cert_writable_by_others(pki, ollama):
    from auneron_ai_gateway.fsperm import KeyPermissionError
    from auneron_ai_gateway.startup import StartupError, preflight

    g, cfg = _cfg_for(pki, ollama)

    def refuse_only(target):
        def check(p):
            if p == target:
                raise KeyPermissionError("gravavel por outros")

        return check

    with pytest.raises(StartupError, match="CA de clientes"):
        preflight(cfg, listeners=lambda: [], key_check=lambda p: None, public_check=refuse_only(cfg.ca_file))
    with pytest.raises(StartupError, match="certificado do servidor"):
        preflight(cfg, listeners=lambda: [], key_check=lambda p: None, public_check=refuse_only(cfg.cert_file))
    g.cleanup()


def test_preflight_refuses_when_public_verification_fails(pki, ollama):
    from auneron_ai_gateway.fsperm import KeyPermissionError
    from auneron_ai_gateway.startup import StartupError, preflight

    g, cfg = _cfg_for(pki, ollama)

    def cant(_p):
        raise KeyPermissionError("permissao nao verificavel")

    with pytest.raises(StartupError, match="permissao de escrita"):
        preflight(cfg, listeners=lambda: [], key_check=lambda p: None, public_check=cant)
    g.cleanup()


@pytest.mark.skipif(sys.platform != "win32", reason="ACL do Windows")
def test_preflight_real_acl_ca_writable_refused_then_read_only_accepted(pki, ollama, tmp_path):
    from auneron_ai_gateway.startup import StartupError, preflight

    ca = tmp_path / "ca.pem"
    ca.write_bytes(pki.ca_path.read_bytes())
    __import__("make_test_certs").restrict_private_file(ca)
    g, cfg = _cfg_for(pki, ollama, ca_file=ca)
    kw = dict(listeners=lambda: [], key_check=lambda p: None)
    preflight(cfg, **kw)
    subprocess.run(["icacls", str(ca), "/grant", "*S-1-5-32-545:(R)"], capture_output=True, check=True)
    preflight(cfg, **kw)  # leitura publica ok
    subprocess.run(["icacls", str(ca), "/grant", "*S-1-5-32-545:(M)"], capture_output=True, check=True)
    with pytest.raises(StartupError, match="CA de clientes"):
        preflight(cfg, **kw)
    g.cleanup()


# ================================================================= B. reverificacao do Ollama
def _toml_with(pki, ollama, extra_line):
    g = Gateway(pki, ollama.port)
    text = g.config_text().replace(
        '[startup]\nexposure_unverifiable = "refuse"', '[startup]\nexposure_unverifiable = "refuse"\n' + extra_line
    )
    assert extra_line in text
    return g, text


def test_recheck_interval_config_default_min_and_bounds(pki, ollama, tmp_path):
    from auneron_ai_gateway.config import ConfigError, load_config

    g = Gateway(pki, ollama.port)
    assert load_config(g.write_config()).exposure_recheck_seconds == 60.0  # padrao
    for line, expected in (("exposure_recheck_seconds = 10", 10.0), ("exposure_recheck_seconds = 300.5", 300.5),
                           ("exposure_recheck_seconds = 3600", 3600.0)):
        g2, text = _toml_with(pki, ollama, line)
        p = tmp_path / "ok.toml"
        p.write_text(text, encoding="utf-8")
        assert load_config(p).exposure_recheck_seconds == expected
        g2.cleanup()
    for line in ("exposure_recheck_seconds = 9.99", "exposure_recheck_seconds = 9", "exposure_recheck_seconds = 0",
                 "exposure_recheck_seconds = -60", "exposure_recheck_seconds = 3600.5",
                 'exposure_recheck_seconds = "60"', "exposure_recheck_seconds = true"):
        g2, text = _toml_with(pki, ollama, line)
        p = tmp_path / "bad.toml"
        p.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="exposure_recheck_seconds"):
            load_config(p)
        g2.cleanup()
    g.cleanup()


def test_example_toml_loads_with_recheck(tmp_path):
    from pathlib import Path

    import tomllib

    raw = tomllib.loads((Path(__file__).resolve().parent.parent / "config" / "gateway.example.toml").read_text("utf-8"))
    assert raw["startup"]["exposure_recheck_seconds"] == 60


class StubOllama:
    def __init__(self):
        self.calls = 0

    async def version(self):
        self.calls += 1
        return {"version": "x"}

    async def generate(self, *a, **k):
        self.calls += 1
        return {"ok": True}


async def _call(app, method, path, body=b""):
    sent = []

    given = []

    async def receive():
        if not given:  # corpo uma vez; depois o cliente fica conectado (nunca desconecta)
            given.append(1)
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(m):
        sent.append(m)

    headers = [(b"host", b"x")]
    if method == "POST":
        headers += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    scope = {"type": "http", "method": method, "raw_path": path.encode(), "query_string": b"", "headers": headers}
    await app(scope, receive, send)
    hdrs = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    return sent[0]["status"], hdrs, sent[1]["body"]


def _app(pki, tmp_path, check, interval=0.01):
    from auneron_ai_gateway.app import GatewayApp
    from auneron_ai_gateway.exposure_watch import ExposureWatch
    from auneron_ai_gateway.glog import AuditLog

    cfg = make_cfg(pki, log_dir=tmp_path / "logs")
    log = AuditLog(tmp_path / "logs")
    stub = StubOllama()
    watch = ExposureWatch(cfg, log, check=check, interval=interval)
    return GatewayApp(cfg, stub, log, watch), stub, watch, log


def _records(tmp_path):
    out = []
    for f in sorted((tmp_path / "logs").glob("gateway.log*")):
        out += [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return out


def test_refusal_mode_returns_503_json_without_calling_ollama_then_recovers(pki, tmp_path):
    from auneron_ai_gateway.exposure import ExposureError

    state = {"exposed": False, "calls": 0}

    def check():
        state["calls"] += 1
        if state["exposed"]:
            raise ExposureError("ollama escuta fora de loopback")
        return "ok_table"

    app, stub, watch, log = _app(pki, tmp_path, check)
    body = dumps(good_payload())

    async def scenario():
        task = asyncio.create_task(watch.run())
        try:
            st, _, _ = await _call(app, "GET", "/v1/health")
            assert st == 200 and stub.calls == 1  # normal
            st, _, out = await _call(app, "POST", "/v1/generate", body)
            assert st == 200 and stub.calls == 2
            state["exposed"] = True  # Ollama passa a escutar fora de loopback DEPOIS da partida
            for _ in range(300):
                if watch.refusing:
                    break
                await asyncio.sleep(0.01)
            assert watch.refusing and watch.reason == "ollama_exposed"
            before = stub.calls
            for method, path, b in (("POST", "/v1/generate", body), ("GET", "/v1/health", b"")):
                st, hdrs, out = await _call(app, method, path, b)
                assert st == 503 and hdrs["content-type"] == "application/json"
                assert json.loads(out) == {"error": {"code": "unavailable"}}
                assert hdrs["connection"] == "close" and int(hdrs["retry-after"]) == 0
            assert stub.calls == before  # NAO chamou o Ollama
            st, _, _ = await _call(app, "GET", "/nada")
            assert st == 404  # roteamento continua igual
            state["exposed"] = False  # volta ao normal sozinho
            for _ in range(300):
                if not watch.refusing:
                    break
                await asyncio.sleep(0.01)
            assert not watch.refusing and watch.reason is None
            st, _, _ = await _call(app, "POST", "/v1/generate", body)
            assert st == 200 and stub.calls == before + 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    log.close()
    recs = _records(tmp_path)
    codes = [r.get("code") for r in recs if r["event"] == "internal"]
    assert "ollama_exposed" in codes and "exposure_recovered" in codes  # motivo registrado
    assert codes.index("ollama_exposed") < codes.index("exposure_recovered")
    refused = [r for r in recs if r["event"] == "request" and r.get("code") == "unavailable"]
    assert len(refused) == 2 and all(r["status"] == 503 for r in refused)


def test_check_failure_or_impossible_verification_enters_refusal_mode(pki, tmp_path):
    from auneron_ai_gateway.exposure import ExposureError

    mode = {"v": "boom"}

    def check():
        if mode["v"] == "boom":
            raise RuntimeError("tabela ilegivel")
        if mode["v"] == "unverifiable":
            raise ExposureError("exposicao do ollama nao verificavel (politica: recusar)")
        return "ok_table"

    app, stub, watch, log = _app(pki, tmp_path, check)

    async def scenario():
        task = asyncio.create_task(watch.run())
        try:
            for expect, reason in (("boom", "exposure_unverifiable"), ("unverifiable", "exposure_unverifiable")):
                mode["v"] = expect
                for _ in range(300):
                    if watch.refusing:
                        break
                    await asyncio.sleep(0.01)
                assert watch.refusing and watch.reason == reason
                st, _, _ = await _call(app, "GET", "/v1/health")
                assert st == 503 and stub.calls == 0
                assert not task.done()  # a tarefa sobrevive a excecao do verificador
                mode["v"] = "ok"
                for _ in range(300):
                    if not watch.refusing:
                        break
                    await asyncio.sleep(0.01)
                assert not watch.refusing
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    log.close()


def test_watch_respects_interval_between_checks(pki, tmp_path):
    n = {"v": 0}

    def check():
        n["v"] += 1
        return "ok_table"

    app, stub, watch, log = _app(pki, tmp_path, check, interval=0.2)

    async def scenario():
        task = asyncio.create_task(watch.run())
        await asyncio.sleep(0.1)
        assert n["v"] == 0  # nao verifica antes do 1o intervalo (a partida ja verificou)
        await asyncio.sleep(0.75)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert 2 <= n["v"] <= 5
    log.close()


def test_watch_evaluate_reasons(pki, tmp_path):
    from auneron_ai_gateway.exposure import ExposureError
    from auneron_ai_gateway.exposure_watch import ExposureWatch
    from auneron_ai_gateway.glog import AuditLog

    log = AuditLog(tmp_path / "logs")

    def make(exc):
        def check():
            if exc is not None:
                raise exc
            return "ok_probe"

        return ExposureWatch(make_cfg(pki), log, check=check, interval=1)

    assert make(None).evaluate() is None
    assert make(ExposureError("ollama escuta fora de loopback")).evaluate() == "ollama_exposed"
    assert make(ExposureError("exposicao do ollama nao verificavel (politica: recusar)")).evaluate() == "exposure_unverifiable"
    assert make(ValueError("x")).evaluate() == "exposure_unverifiable"
    assert ExposureWatch(make_cfg(pki, exposure_recheck_seconds=77.0), log).interval == 77.0  # vem do TOML
    log.close()


def test_default_watch_check_uses_real_exposure_check_with_config_port(pki, tmp_path, monkeypatch):
    from auneron_ai_gateway import exposure_watch
    from auneron_ai_gateway.glog import AuditLog

    seen = {}

    def fake(port, **kw):
        seen["port"], seen["kw"] = port, kw
        return "ok_table"

    monkeypatch.setattr(exposure_watch, "check_ollama_not_exposed", fake)
    log = AuditLog(tmp_path / "logs")
    w = exposure_watch.ExposureWatch(make_cfg(pki, ollama_port=4321, exposure_unverifiable="allow"), log)
    assert w.evaluate() is None
    assert seen["port"] == 4321 and seen["kw"] == {"on_unverifiable": "allow"}
    log.close()


def test_server_shutdown_cancels_watch_task_cleanly(pki, tmp_path):
    from auneron_ai_gateway.exposure_watch import ExposureWatch
    from auneron_ai_gateway.glog import AuditLog
    from auneron_ai_gateway.ollama import OllamaClient
    from auneron_ai_gateway.server import GatewayServer
    from auneron_ai_gateway.startup import build_ssl_context

    cfg = make_cfg(pki, port=free_port(), log_dir=tmp_path / "logs")
    log = AuditLog(tmp_path / "logs")
    checks = {"n": 0}

    def check():
        checks["n"] += 1
        return "ok_table"

    watch = ExposureWatch(cfg, log, check=check, interval=0.01)
    srv = GatewayServer(cfg, build_ssl_context(cfg), OllamaClient(2), log, exposure_watch=watch)
    srv.bind()

    async def scenario():
        main = asyncio.create_task(srv.serve_forever())
        for _ in range(300):
            if checks["n"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert checks["n"] >= 2  # a tarefa periodica esta rodando
        assert srv.watch_task is not None and not srv.watch_task.done()
        main.cancel()
        await asyncio.gather(main, return_exceptions=True)
        assert srv.watch_task.done() and srv.watch_task.cancelled()
        n = checks["n"]
        await asyncio.sleep(0.1)
        assert checks["n"] == n  # parou de verificar
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftover == []  # sem tarefas soltas

    asyncio.run(scenario())
    log.close()


def test_server_without_watch_has_no_task_and_app_never_refuses(pki, tmp_path):
    from auneron_ai_gateway.app import GatewayApp
    from auneron_ai_gateway.glog import AuditLog

    log = AuditLog(tmp_path / "logs")
    stub = StubOllama()
    app = GatewayApp(make_cfg(pki), stub, log)
    st, _, _ = asyncio.run(_call(app, "GET", "/v1/health"))
    assert st == 200 and stub.calls == 1
    log.close()


def test_main_wires_exposure_watch():
    from pathlib import Path

    from harness import SRC

    src = (SRC / "auneron_ai_gateway" / "__main__.py").read_text("utf-8")
    assert "exposure_watch=ExposureWatch(cfg, log)" in src
