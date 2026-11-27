"""Validade de um certificado PEM sem dependencias (leitor DER minimo)."""

from __future__ import annotations

import ssl
import time
from pathlib import Path


class CertError(Exception):
    pass


def _tlv(buf: bytes, pos: int) -> tuple[int, int, int]:
    """Retorna (tag, inicio_do_valor, fim_do_valor)."""
    try:
        tag = buf[pos]
        first = buf[pos + 1]
        pos += 2
        if first < 0x80:
            length = first
        else:
            n = first & 0x7F
            if n == 0 or n > 4:
                raise CertError("DER invalido")
            length = int.from_bytes(buf[pos : pos + n], "big")
            pos += n
    except IndexError:
        raise CertError("DER truncado") from None
    end = pos + length
    if end > len(buf):
        raise CertError("DER truncado")
    return tag, pos, end


def _parse_time(tag: int, raw: bytes) -> float:
    text = raw.decode("ascii", "strict")
    if tag == 0x17:  # UTCTime YYMMDDHHMMSSZ
        yy = int(text[0:2])
        year = 1900 + yy if yy >= 50 else 2000 + yy
        rest = text[2:]
    elif tag == 0x18:  # GeneralizedTime YYYYMMDDHHMMSSZ
        year = int(text[0:4])
        rest = text[4:]
    else:
        raise CertError("tipo de data invalido")
    if not rest.endswith("Z") or len(rest) != 11:
        raise CertError("formato de data invalido")
    mon, day, hh, mm, ss = (int(rest[i : i + 2]) for i in range(0, 10, 2))
    import calendar

    return float(calendar.timegm((year, mon, day, hh, mm, ss, 0, 0, 0)))


def validity_of_pem(path: Path) -> tuple[float, float]:
    try:
        der = ssl.PEM_cert_to_DER_cert(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise CertError("certificado ilegivel") from None
    tag, s, e = _tlv(der, 0)  # Certificate
    if tag != 0x30:
        raise CertError("nao e certificado")
    tag, s, e = _tlv(der, s)  # TBSCertificate
    if tag != 0x30:
        raise CertError("tbs invalido")
    pos = s
    tag, vs, ve = _tlv(der, pos)
    if tag == 0xA0:  # version [0]
        pos = ve
        tag, vs, ve = _tlv(der, pos)
    # (vs, ve) = serial; avanca por signature alg e issuer
    for _ in range(2):
        pos = ve
        tag, vs, ve = _tlv(der, pos)
    pos = ve  # proximo elemento: Validity
    tag, vs, ve = _tlv(der, pos)
    if tag != 0x30:
        raise CertError("validity invalido")
    t1, a, b = _tlv(der, vs)
    nb = _parse_time(t1, der[a:b])
    t2, c, d = _tlv(der, b)
    na = _parse_time(t2, der[c:d])
    return nb, na


def check_currently_valid(path: Path, now: float | None = None) -> None:
    now = time.time() if now is None else now
    nb, na = validity_of_pem(path)
    if now < nb:
        raise CertError("certificado ainda nao valido")
    if now > na:
        raise CertError("certificado vencido")
