import os
import socket
import ssl
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = Path(os.environ.get("AUNERON_GW_SRC") or (ROOT / "src")).resolve()
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

import harness  # noqa: E402
from fake_ollama import FakeOllama  # noqa: E402
from harness import Gateway, client_ctx  # noqa: E402
from make_test_certs import make_test_pki  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_tls: precisa de mTLS real em loopback (pula se interceptado)")


def probe_tls_works(pki) -> tuple[bool, str]:
    """Handshake mTLS real em loopback com o contexto de producao. Falso se algo interceptar."""
    from auneron_ai_gateway.startup import build_ssl_context

    cfg = SimpleNamespace(
        min_tls="1.3", ca_file=pki.ca_path, cert_file=pki["server"].cert_path, key_file=pki["server"].key_path
    )
    sctx = build_ssl_context(cfg)
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    lsock.settimeout(5)
    seen = []

    def srv():
        try:
            c, _ = lsock.accept()
            c.settimeout(5)
            s = sctx.wrap_socket(c, server_side=True)
            seen.append(s.getpeercert(binary_form=True))
            s.close()
        except Exception as e:  # noqa: BLE001
            seen.append(repr(type(e).__name__))

    t = threading.Thread(target=srv, daemon=True)
    t.start()
    try:
        cctx = client_ctx(pki["client_ok"], pki.ca_path)
        raw = socket.create_connection(lsock.getsockname(), timeout=5)
        s = cctx.wrap_socket(raw, server_hostname=harness.SERVER_NAME)
        s.close()
        client_ok = True
        why = ""
    except Exception as e:  # noqa: BLE001
        client_ok = False
        why = type(e).__name__
    t.join(6)
    lsock.close()
    import hashlib

    ok = (
        client_ok
        and seen
        and isinstance(seen[0], bytes)
        and hashlib.sha256(seen[0]).hexdigest() == pki["client_ok"].fingerprint
    )
    return bool(ok), why or str(seen[:1])[:80]


@pytest.fixture(scope="session")
def pki():
    with make_test_pki() as p:
        yield p


@pytest.fixture(scope="session", autouse=True)
def tls_mode(pki):
    forced = os.environ.get("AUNERON_GW_FORCE_PLAIN") == "1"
    ok, why = (False, "forcado") if forced else probe_tls_works(pki)
    harness.TLS_MODE["tls"] = ok
    harness.TLS_MODE["why"] = why
    yield ok


@pytest.fixture(autouse=True)
def _skip_needs_tls(request, tls_mode):
    if request.node.get_closest_marker("needs_tls") and not tls_mode:
        pytest.skip(f"mTLS em loopback indisponivel neste host ({harness.TLS_MODE.get('why')})")


@pytest.fixture(scope="session")
def ollama():
    f = FakeOllama()
    f.start()
    yield f
    f.stop()


@pytest.fixture(autouse=True)
def _reset_ollama(ollama):
    ollama.reset()
    yield


@pytest.fixture(scope="module")
def gw(pki, ollama, tls_mode):
    g = Gateway(pki, ollama.port).start()
    yield g
    g.cleanup()


@pytest.fixture()
def gwf(pki, ollama, tls_mode):
    """Gateway novo por teste (limitador de taxa zerado)."""
    made = []

    def make(**kw):
        g = Gateway(pki, ollama.port, **kw).start()
        made.append(g)
        return g

    yield make
    for g in made:
        g.cleanup()
