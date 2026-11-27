"""Verificacao REAL de mTLS por socket (Windows ou qualquer SO). PREPARADA; nao contorna nada.

1) Detecta interceptacao de TLS em loopback (antivirus/proxy): o cliente confere se o certificado que
   recebeu e o do servidor de teste e se o servidor recebeu o certificado do cliente.
2) Se houver interceptacao: RELATA (emissor visto) e sai com codigo 3, SEM tentar contornar
   (nao muda portas para fugir, nao desliga verificacao, nao mexe no antivirus).
3) Se nao houver: roda tests/test_access.py (mTLS fim a fim: certificados vencidos, CA errada, etc.).

Uso: python tools/windows_mtls_check.py        (codigos: 0 ok, 3 interceptado, 4 falha/inconclusivo)
"""

import hashlib
import json
import socket
import ssl
import subprocess
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools"), str(ROOT / "tests")]


def observe(pki) -> dict:
    from auneron_ai_gateway.startup import build_ssl_context

    sctx = build_ssl_context(
        types.SimpleNamespace(
            ca_file=pki.ca_path, cert_file=pki["server"].cert_path, key_file=pki["server"].key_path, min_tls="1.3"
        )
    )
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    lsock.settimeout(8)
    seen: dict = {}

    def serve():
        try:
            c, _ = lsock.accept()
            c.settimeout(8)
            s = sctx.wrap_socket(c, server_side=True)
            der = s.getpeercert(binary_form=True)
            seen["client_fp"] = hashlib.sha256(der).hexdigest() if der else None
            s.close()
        except Exception as e:  # noqa: BLE001
            seen["server_error"] = type(e).__name__

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cctx.check_hostname = False
    cctx.verify_mode = ssl.CERT_NONE  # so OBSERVA o que chega; a verificacao real e feita pelos testes
    cctx.load_cert_chain(str(pki["client_ok"].cert_path), str(pki["client_ok"].key_path))
    report: dict = {}
    try:
        with cctx.wrap_socket(socket.create_connection(lsock.getsockname(), timeout=8), server_hostname="x") as cs:
            der = cs.getpeercert(binary_form=True)
            report["server_cert_matches"] = hashlib.sha256(der).hexdigest() == pki["server"].fingerprint
            try:
                from cryptography import x509

                report["issuer_seen"] = x509.load_der_x509_certificate(der).issuer.rfc4514_string()
            except Exception:  # noqa: BLE001
                report["issuer_seen"] = "?"
    except Exception as e:  # noqa: BLE001
        report["client_error"] = type(e).__name__
    t.join(10)
    lsock.close()
    report["server_saw_client_cert"] = seen.get("client_fp") == pki["client_ok"].fingerprint
    report.update({k: v for k, v in seen.items() if k == "server_error"})
    return report


def main() -> int:
    from make_test_certs import make_test_pki

    with make_test_pki() as pki:
        rep = observe(pki)
    print(json.dumps(rep, indent=1))
    if rep.get("server_cert_matches") is True and rep.get("server_saw_client_cert") is True:
        print("Sem interceptacao detectada: executando o mTLS fim a fim.")
        return subprocess.call([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(ROOT / "tests" / "test_access.py")])
    if rep.get("server_cert_matches") is False or (
        rep.get("server_cert_matches") is True and rep.get("server_saw_client_cert") is False
    ):
        print("INTERCEPTACAO DE TLS DETECTADA em loopback (emissor visto acima). NAO CONTORNADO.\n"
              "Decisao necessaria: executar em host sem interceptacao (ou o roteiro Docker).")
        return 3
    print("Resultado inconclusivo (erro de handshake/cliente). Nada foi contornado.")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
