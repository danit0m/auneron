"""Porta de identidade: so o certificado TLS da conexao conta. Nenhum cabecalho HTTP."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from typing import Any


def fingerprint_allowed(der: bytes, allowed: Iterable[str]) -> bool:
    fp = hashlib.sha256(der).hexdigest()
    ok = False
    for candidate in allowed:  # sem curto-circuito
        ok |= hmac.compare_digest(fp, candidate)
    return ok


def check_identity(ssl_obj: Any, allowed: Iterable[str]) -> str:
    """Retorna "ok", "client_cert_missing" ou "client_cert_not_allowed" (falha fechada)."""
    if ssl_obj is None:
        return "client_cert_missing"
    try:
        der = ssl_obj.getpeercert(binary_form=True)
    except Exception:
        return "client_cert_missing"
    if not der:
        return "client_cert_missing"
    if not fingerprint_allowed(der, allowed):
        return "client_cert_not_allowed"
    return "ok"
