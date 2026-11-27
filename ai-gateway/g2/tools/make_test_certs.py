"""Gerador de certificados SO PARA TESTE. Nunca usar em producao.

Cria, em pasta temporaria apagada ao sair do contexto: CA, CA errada, servidor, servidor falso,
servidor com nome errado e clientes (valido, valido nao listado, vencido, ainda nao valido,
CA errada, EKU errado). Chaves EC P-256 efemeras; nada e gravado fora da pasta temporaria.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SERVER_NAME = "ai.auneron.internal"


@dataclass
class Pair:
    cert_path: Path
    key_path: Path
    fingerprint: str  # sha256 hex do DER


@dataclass
class TestPKI:
    __test__ = False  # nao e classe de teste do pytest
    root: Path
    ca_path: Path
    wrong_ca_path: Path
    pairs: dict[str, Pair] = field(default_factory=dict)

    def __getitem__(self, name: str) -> Pair:
        return self.pairs[name]


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


_MY_SID: list[str] = []


def restrict_private_file(path: Path) -> None:
    """Teste: deixa so o usuario atual (+SYSTEM/Administradores) na ACL (Windows) ou 0600 (POSIX).

    O instalador real (G4) fara o equivalente; aqui a pasta temporaria herda ACEs de outros SIDs."""
    import os
    import subprocess

    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    if not _MY_SID:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
        _MY_SID.append(out.stdout.strip().split(",")[-1].strip().strip('"'))
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{_MY_SID[0]}:(F)", "/grant:r", "*S-1-5-18:(F)"],
        capture_output=True, check=True,
    )


def _write(root: Path, name: str, cert: x509.Certificate, key: ec.EllipticCurvePrivateKey) -> Pair:
    cp, kp = root / f"{name}.pem", root / f"{name}.key.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    restrict_private_file(kp)
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return Pair(cp, kp, fp)


def _make_ca(cn: str) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(_name(cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _leaf(
    cn: str,
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    *,
    eku: x509.ObjectIdentifier,
    san: str | None = None,
    not_before: dt.timedelta = dt.timedelta(days=-1),
    not_after: dt.timedelta = dt.timedelta(days=30),
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + not_before)
        .not_valid_after(now + not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=False, crl_sign=False,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
    )
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return b.sign(ca_key, hashes.SHA256()), key


class make_test_pki:
    """Context manager: with make_test_pki() as pki: ... (pasta apagada ao sair)."""

    def __enter__(self) -> TestPKI:
        self._tmp = tempfile.TemporaryDirectory(prefix="aigw-testpki-", ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        ca, ca_key = _make_ca("AUNERON TEST CA")
        bad, bad_key = _make_ca("EVIL TEST CA")
        (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (root / "wrong_ca.pem").write_bytes(bad.public_bytes(serialization.Encoding.PEM))
        pki = TestPKI(root, root / "ca.pem", root / "wrong_ca.pem")
        S, C = ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH
        d = dt.timedelta
        spec = {
            "server": (ca, ca_key, S, SERVER_NAME, d(days=-1), d(days=30)),
            "server_wronghost": (ca, ca_key, S, "outro.exemplo.internal", d(days=-1), d(days=30)),
            "server_expired": (ca, ca_key, S, SERVER_NAME, d(days=-60), d(days=-30)),
            "server_notyet": (ca, ca_key, S, SERVER_NAME, d(days=10), d(days=40)),
            "server_fake": (bad, bad_key, S, SERVER_NAME, d(days=-1), d(days=30)),
            "client_ok": (ca, ca_key, C, None, d(days=-1), d(days=30)),
            "client_ok2": (ca, ca_key, C, None, d(days=-1), d(days=30)),
            "client_unlisted": (ca, ca_key, C, None, d(days=-1), d(days=30)),
            "client_expired": (ca, ca_key, C, None, d(days=-60), d(days=-30)),
            "client_notyet": (ca, ca_key, C, None, d(days=10), d(days=40)),
            "client_wrongca": (bad, bad_key, C, None, d(days=-1), d(days=30)),
            "client_wrongeku": (ca, ca_key, S, None, d(days=-1), d(days=30)),
        }
        for name, (issuer, ikey, eku, san, nb, na) in spec.items():
            cert, key = _leaf(name, issuer, ikey, eku=eku, san=san, not_before=nb, not_after=na)
            pki.pairs[name] = _write(root, name, cert, key)
        return pki

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


if __name__ == "__main__":
    with make_test_pki() as p:
        print(f"{len(p.pairs)} certificados de teste criados em pasta temporaria e apagados ao sair")
