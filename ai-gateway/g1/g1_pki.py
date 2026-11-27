"""G1: PKI descartavel so para a prova de conexao (validade 2 dias).

Gera, numa pasta NOVA: ca.pem (+ chave), server.pem (SAN ai.auneron.internal), client_ok.pem
(listado), client_outro.pem (mesma CA, NAO listado) e outra_ca.pem (CA estranha, para o controle
"servidor falso"). Grava allow.txt com a impressao digital do client_ok e server_fp.txt.
Nao e a CA do G4: e apagada no fim do G1.

Uso: python g1_pki.py <pasta-nova>
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HOST = "ai.auneron.internal"
DIAS = 2


def _restrict(path: Path) -> None:
    """Chave so para o usuario atual (Windows: icacls; POSIX: 0600)."""
    if os.name == "nt":
        user = os.environ["USERNAME"]
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                       check=True, capture_output=True)
    else:
        os.chmod(path, 0o600)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(cn: str):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn)).issuer_name(_name(cn))
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=DIAS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _leaf(cn: str, ca_cert, ca_key, *, server: bool):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(cn)).issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=DIAS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage(
            [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    )
    if server:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
    return b.sign(ca_key, hashes.SHA256()), key


def _save(root: Path, name: str, cert, key=None) -> str:
    der = cert.public_bytes(serialization.Encoding.DER)
    (root / f"{name}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if key is not None:
        kp = root / f"{name}.key.pem"
        kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
        _restrict(kp)
    return hashlib.sha256(der).hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: g1_pki.py <pasta-nova>")
        return 2
    root = Path(sys.argv[1])
    root.mkdir(parents=False, exist_ok=False)  # recusa pasta existente
    ca, ca_key = _ca("AUNERON G1 TESTE CA")
    outra, _ = _ca("CA ESTRANHA G1")
    _save(root, "ca", ca)  # a chave da CA nao e gravada: nada mais pode ser emitido depois
    _save(root, "outra_ca", outra)
    s, sk = _leaf(HOST, ca, ca_key, server=True)
    ok, okk = _leaf("vps-teste-g1", ca, ca_key, server=False)
    ou, ouk = _leaf("cliente-nao-listado-g1", ca, ca_key, server=False)
    server_fp = _save(root, "server", s, sk)
    ok_fp = _save(root, "client_ok", ok, okk)
    _save(root, "client_outro", ou, ouk)
    (root / "allow.txt").write_text(ok_fp + "\n", encoding="ascii")
    (root / "server_fp.txt").write_text(server_fp + "\n", encoding="ascii")
    print(f"PKI G1 pronta em {root} (validade {DIAS} dias; chave da CA descartada)")
    print(f"servidor {server_fp[:16]}...  cliente listado {ok_fp[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
