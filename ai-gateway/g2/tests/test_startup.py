"""Partida fail-closed (nao depende de TLS fim a fim)."""

import socket
import subprocess
import sys

import pytest
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "tools"))

from harness import Gateway

def _run_main(gw_obj, cfg_text=None):
    path = gw_obj.write_config()
    if cfg_text is not None:
        path.write_text(cfg_text, encoding="utf-8")
    import os

    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(__import__("harness").SRC), __import__("sysconfig").get_paths()["purelib"]]), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [__import__("harness").PYEXE, "-m", "auneron_ai_gateway", "--config", str(path)],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(gw_obj.tmp),
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::", "::1", "127.0.0.2", "192.168.0.10", ""])
def test_refuses_to_start_on_non_loopback_host(pki, ollama, host):
    g = Gateway(pki, ollama.port, host=host)
    res = _run_main(g)
    assert res.returncode == 2
    assert "PARTIDA RECUSADA" in res.stderr
    g.cleanup()


def test_refuses_start_with_missing_cert_file(pki, ollama):
    g = Gateway(pki, ollama.port)
    txt = g.config_text().replace("server.pem", "nao-existe.pem")
    res = _run_main(g, txt)
    assert res.returncode == 2
    g.cleanup()


def test_refuses_start_when_key_does_not_match_cert(pki, ollama):
    g = Gateway(pki, ollama.port)
    txt = g.config_text().replace(
        str(pki["server"].key_path).replace("\\", "/"), str(pki["client_ok"].key_path).replace("\\", "/")
    )
    res = _run_main(g, txt)
    assert res.returncode == 2 and "certificados" in res.stderr
    g.cleanup()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("allowed_client_fingerprints = [", "allowed_client_fingerprints = [] # ["),
        lambda t: t.replace("[server]", "[server]\nsegredo = 'x'"),
        lambda t: t.replace("queue_size = 2", "queue_size = 99"),
        lambda t: t.replace("per_minute = 10", "per_minute = 1000"),
        lambda t: t.replace("total_timeout = 120.0", "total_timeout = 3600"),
        lambda t: t.replace('min_version = "1.3"', 'min_version = "1.0"'),
        lambda t: t + "\n[extra]\nx = 1\n",
        lambda t: t.replace("max_connections = 4", "max_connections = 400"),
    ],
)
def test_invalid_config_refused(pki, ollama, mutate):
    g = Gateway(pki, ollama.port)
    res = _run_main(g, mutate(g.config_text()))
    assert res.returncode == 2
    g.cleanup()


def test_bad_fingerprint_value_refused(pki, ollama):
    g = Gateway(pki, ollama.port)
    txt = g.config_text().replace(pki["client_ok"].fingerprint, "ABC")
    assert _run_main(g, txt).returncode == 2
    g.cleanup()


def test_preflight_exposure_decisions(pki, ollama):
    from auneron_ai_gateway.config import load_config
    from auneron_ai_gateway.startup import StartupError, preflight

    g = Gateway(pki, ollama.port)
    cfg = load_config(g.write_config())
    port = cfg.ollama_port
    ok_keys = lambda p: None  # noqa: E731  (permissao da chave tem teste proprio)
    kw = dict(key_check=ok_keys)
    # curinga IPv4/IPv6 e endereco especifico nao-loopback => recusa
    for listener in ("0.0.0.0", "::", "192.168.1.9", "fe80::1", "2001:db8::5", "::ffff:10.0.0.1"):
        with pytest.raises(StartupError):
            preflight(cfg, listeners=lambda l=listener: [(l, port)], **kw)
    # so loopback (v4 e v6) => ok
    for listener in (("127.0.0.1",), ("::1",), ("127.0.0.1", "::1")):
        _c, st = preflight(cfg, listeners=lambda ls=listener: [(a, port) for a in ls], **kw)
        assert st == "ok_table"
    # outra porta com curinga nao importa; sem escuta => ok_not_listening
    _c, st = preflight(cfg, listeners=lambda: [("0.0.0.0", port + 1)], **kw)
    assert st == "ok_not_listening"
    # tabela indisponivel: sondagem por conexao (IPv4 e IPv6 com escopo)
    probed = []
    _c, st = preflight(cfg, listeners=lambda: None, addresses=lambda: ["10.0.0.5", "fe80::1%12", "2001:db8::5"],
                       probe=lambda a, p, t: probed.append(a) or False, **kw)
    assert st == "ok_probe" and probed == ["10.0.0.5", "fe80::1%12", "2001:db8::5"]
    for hit in ("10.0.0.5", "fe80::1%12", "2001:db8::5"):
        with pytest.raises(StartupError):
            preflight(cfg, listeners=lambda: None, addresses=lambda: ["10.0.0.5", "fe80::1%12", "2001:db8::5"],
                      probe=lambda a, p, t, h=hit: a == h, **kw)
    # nada verificavel: recusa por padrao; "allow" so por decisao documentada no TOML
    with pytest.raises(StartupError):
        preflight(cfg, listeners=lambda: None, addresses=lambda: [], **kw)
    allow_cfg = load_config(Gateway(pki, ollama.port, exposure="allow").write_config())
    _c, st = preflight(allow_cfg, listeners=lambda: None, addresses=lambda: [], **kw)
    assert st == "unverifiable_allowed"
    g.cleanup()


def test_real_system_listener_table_sees_fake_ollama_on_loopback_only(ollama):
    from auneron_ai_gateway.exposure import classify, system_listeners

    table = system_listeners()
    assert table is not None, "tabela de escuta do sistema indisponivel neste host"
    mine = [a for a, p in table if p == ollama.port]
    assert mine and all(classify(a) == "loopback" for a in mine)


def test_candidate_addresses_include_ipv6_link_local_with_scope_when_present(monkeypatch):
    import socket as sk

    from auneron_ai_gateway import exposure

    monkeypatch.setattr(sk, "gethostname", lambda: "h")
    monkeypatch.setattr(sk, "if_nameindex", lambda: [(3, "a"), (7, "b")])

    def fake_gai(host, port, family=0, *a, **k):
        if family == sk.AF_INET6:
            return [(sk.AF_INET6, 0, 0, "", ("fe80::abcd%3", 0, 0, 3)), (sk.AF_INET6, 0, 0, "", ("2001:db8::9", 0, 0, 0)),
                    (sk.AF_INET6, 0, 0, "", ("::1", 0, 0, 0))]
        return [(sk.AF_INET, 0, 0, "", ("192.168.1.7", 0)), (sk.AF_INET, 0, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(sk, "getaddrinfo", fake_gai)
    monkeypatch.setattr(sk, "gethostbyname_ex", lambda n: (n, [], ["192.168.1.7", "127.0.0.1"]))
    got = exposure.local_candidate_addresses()
    assert "192.168.1.7" in got and "2001:db8::9" in got
    assert "fe80::abcd%3" in got and "fe80::abcd%7" in got  # link-local com escopo de cada interface
    assert not any(a.startswith(("127.", "::1")) for a in got)


def test_parse_proc_net_listeners():
    from auneron_ai_gateway.exposure import parse_proc_net

    v4 = "  sl  local_address rem_address   st\n   0: 0100007F:2CAA 00000000:0000 0A 0\n   1: 00000000:1F90 00000000:0000 0A 0\n   2: 0100007F:2CAB 0100007F:0001 01 0\n"
    assert parse_proc_net(v4, False) == [("127.0.0.1", 11434), ("0.0.0.0", 8080)]
    v6 = "  sl  local_address rem_address st\n   0: 00000000000000000000000001000000:2CAA 00000000000000000000000000000000:0000 0A 0\n   1: 00000000000000000000000000000000:2CAA 00000000000000000000000000000000:0000 0A 0\n"
    assert parse_proc_net(v6, True) == [("::1", 11434), ("::", 11434)]


def test_real_listening_socket_is_loopback_only(gw):
    """O processo real so aceita conexoes em 127.0.0.1: enderecos LAN/IPv6 da maquina nao conectam."""
    from auneron_ai_gateway.exposure import local_candidate_addresses, system_listeners

    for a in local_candidate_addresses():
        with pytest.raises(OSError):
            socket.create_connection((a, gw.port), timeout=2)
    table = system_listeners()
    mine = [a for a, p in table if p == gw.port]
    assert mine == ["127.0.0.1"], mine


def test_exclusive_port_second_bind_fails(tmp_path):
    from auneron_ai_gateway.netbind import bind_exclusive

    first = bind_exclusive("127.0.0.1", 0)
    port = first.getsockname()[1]
    try:
        for reuse in (False, True):  # nem com SO_REUSEADDR o segundo consegue a mesma porta
            second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if reuse:
                second.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                second.bind(("127.0.0.1", port))
            second.close()
        with pytest.raises(OSError):
            bind_exclusive("127.0.0.1", port)
    finally:
        first.close()


def test_exclusive_bind_sets_exclusiveaddruse_and_never_reuseaddr(monkeypatch):
    from auneron_ai_gateway import netbind

    calls = []

    class S:
        def __init__(self, *a):
            pass

        def setsockopt(self, level, opt, val):
            calls.append(opt)

        def bind(self, a):
            pass

        def listen(self, n):
            pass

        def setblocking(self, b):
            pass

        def close(self):
            pass

    monkeypatch.setattr(netbind.socket, "socket", S)
    monkeypatch.setattr(netbind.socket, "SO_EXCLUSIVEADDRUSE", 1234, raising=False)
    netbind.bind_exclusive("127.0.0.1", 1)
    assert 1234 in calls and socket.SO_REUSEADDR not in calls


def test_second_gateway_on_same_port_refuses(gwf):
    g = gwf()
    other = Gateway(g.pki, g.ollama_port)
    other.port = g.port  # mesma porta
    res = subprocess.run(
        [__import__("harness").PYEXE, "-m", "auneron_ai_gateway", "--config", str(other.write_config())],
        capture_output=True, text=True, timeout=30,
        env=dict(__import__("os").environ, PYTHONPATH=__import__("os").pathsep.join(
            [str(__import__("harness").SRC), __import__("sysconfig").get_paths()["purelib"]])),
    )
    assert res.returncode == 2 and 'porta indisponivel' in res.stderr
    other.cleanup()


# ------------------------------------------------------------- chave privada e validade do certificado
def test_key_permissions_ok_for_default_temp_key(pki):
    from auneron_ai_gateway.fsperm import check_private_key_permissions

    check_private_key_permissions(pki["server"].key_path)


@pytest.mark.skipif(sys.platform != "win32", reason="ACL do Windows")
def test_windows_acl_with_everyone_or_users_is_refused_and_restored(pki, tmp_path):
    from auneron_ai_gateway.fsperm import KeyPermissionError, check_private_key_permissions

    key = tmp_path / "k.pem"
    key.write_bytes(pki["server"].key_path.read_bytes())
    __import__("make_test_certs").restrict_private_file(key)
    check_private_key_permissions(key)
    for sid in ("S-1-1-0", "S-1-5-32-545", "S-1-5-11"):  # Todos, Usuarios, Autenticados (por SID: sem idioma)
        subprocess.run(["icacls", str(key), "/grant", f"*{sid}:(R)"], capture_output=True, check=True)
        with pytest.raises(KeyPermissionError):
            check_private_key_permissions(key)
        subprocess.run(["icacls", str(key), "/remove", f"*{sid}"], capture_output=True, check=True)
        check_private_key_permissions(key)


@pytest.mark.skipif(sys.platform != "win32", reason="ACL do Windows")
def test_windows_acl_unreadable_or_missing_is_refused(tmp_path):
    from auneron_ai_gateway.fsperm import KeyPermissionError, check_private_key_permissions

    with pytest.raises(KeyPermissionError):
        check_private_key_permissions(tmp_path / "nao-existe.pem")


def test_acl_and_posix_pure_decision_logic():
    from auneron_ai_gateway.fsperm import posix_violation, windows_violations

    assert posix_violation(0o100600, 1000, 1000) is None
    assert posix_violation(0o100400, 1000, 1000) is None
    for mode in (0o100640, 0o100604, 0o100660, 0o100666, 0o100644, 0o100610):
        assert posix_violation(mode, 1000, 1000)
    assert posix_violation(0o100600, 0, 1000)  # dono diferente
    assert posix_violation(0o040700, 1000, 1000)  # nao e arquivo regular
    me = {"S-1-5-21-1-2-3-1001"}
    ok = [(0, 0, "S-1-5-21-1-2-3-1001"), (0, 0, "S-1-5-18"), (0, 0, "S-1-5-32-544"), (1, 0, "S-1-1-0")]  # negar nao conta
    assert windows_violations(ok, me) == []
    assert windows_violations([(0, 0, "S-1-1-0")], me) == ["S-1-1-0"]
    assert windows_violations([(0, 0, "S-1-5-32-545")], me)
    assert windows_violations([(0, 0x08, "S-1-1-0")], me) == []  # herdada so para filhos
    assert windows_violations([(5, 0, "?")], me)  # ACE de objeto: recusa
    assert windows_violations([(9, 0, "S-1-5-11")], me)  # callback


def test_preflight_refuses_when_key_permission_check_fails(pki, ollama):
    from auneron_ai_gateway.config import load_config
    from auneron_ai_gateway.fsperm import KeyPermissionError
    from auneron_ai_gateway.startup import StartupError, preflight

    g = Gateway(pki, ollama.port)
    cfg = load_config(g.write_config())

    def bad(_p):
        raise KeyPermissionError("x")

    with pytest.raises(StartupError, match="permissao da chave"):
        preflight(cfg, listeners=lambda: [], key_check=bad)
    g.cleanup()


def test_cert_validity_reader_and_preflight_refusal(pki, ollama):
    from auneron_ai_gateway.certinfo import CertError, check_currently_valid, validity_of_pem
    from auneron_ai_gateway.config import load_config
    from auneron_ai_gateway.startup import StartupError, preflight

    nb, na = validity_of_pem(pki["server"].cert_path)
    assert nb < __import__("time").time() < na and 20 * 86400 < na - nb < 40 * 86400
    check_currently_valid(pki["server"].cert_path)
    with pytest.raises(CertError, match="vencido"):
        check_currently_valid(pki["server_expired"].cert_path)
    with pytest.raises(CertError, match="ainda nao"):
        check_currently_valid(pki["server_notyet"].cert_path)
    with pytest.raises(CertError):
        check_currently_valid(pki.root / "nao-existe.pem")
    check_currently_valid(pki["server"].cert_path, now=nb + 10)
    with pytest.raises(CertError):
        check_currently_valid(pki["server"].cert_path, now=na + 1)
    g = Gateway(pki, ollama.port)
    cfg = load_config(g.write_config())
    for name in ("server_expired", "server_notyet"):
        import dataclasses

        bad = dataclasses.replace(cfg, cert_file=pki[name].cert_path, key_file=pki[name].key_path)
        with pytest.raises(StartupError):
            preflight(bad, listeners=lambda: [], key_check=lambda p: None)
    # CA vencida (relogio no futuro)
    with pytest.raises(StartupError):
        preflight(cfg, listeners=lambda: [], key_check=lambda p: None, now=na + 90 * 86400)
    g.cleanup()

