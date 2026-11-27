"""Conferencias de partida, todas fail-closed, e contexto TLS."""

from __future__ import annotations

import ssl
from collections.abc import Callable
from typing import Any

from .certinfo import CertError, check_currently_valid
from .config import Config, is_allowed_bind_host
from .exposure import (
    ExposureError,
    check_ollama_not_exposed,
    local_candidate_addresses,
    system_listeners,
)
from .fsperm import KeyPermissionError, check_private_key_permissions
from .ollama import OLLAMA_HOST


class StartupError(Exception):
    """Partida recusada. Mensagem so com o motivo, sem conteudo de arquivos."""


def build_ssl_context(cfg: Any) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = (
        ssl.TLSVersion.TLSv1_3 if cfg.min_tls == "1.3" else ssl.TLSVersion.TLSv1_2
    )
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    ctx.check_hostname = False  # servidor: nao se aplica
    ctx.load_verify_locations(cafile=str(cfg.ca_file))
    ctx.load_cert_chain(certfile=str(cfg.cert_file), keyfile=str(cfg.key_file))
    ctx.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_RENEGOTIATION
    return ctx


def preflight(
    cfg: Config,
    *,
    listeners: Callable[[], Any] = system_listeners,
    addresses: Callable[[], list[str]] = local_candidate_addresses,
    probe: Callable[..., bool] | None = None,
    key_check: Callable[[Any], None] = check_private_key_permissions,
    now: float | None = None,
) -> tuple[ssl.SSLContext, str]:
    if not is_allowed_bind_host(cfg.host) or OLLAMA_HOST != "127.0.0.1":
        raise StartupError("host nao e loopback")
    for label, path in (
        ("ca_file", cfg.ca_file),
        ("cert_file", cfg.cert_file),
        ("key_file", cfg.key_file),
    ):
        try:
            with open(path, "rb") as fh:
                fh.read(1)
        except OSError:
            raise StartupError(f"arquivo ilegivel: {label}") from None
    try:
        key_check(cfg.key_file)
    except KeyPermissionError as exc:
        raise StartupError(f"permissao da chave privada: {exc}") from None
    for label, path in (("cert_file", cfg.cert_file), ("ca_file", cfg.ca_file)):
        try:
            check_currently_valid(path, now)
        except CertError as exc:
            raise StartupError(f"{label}: {exc}") from None
    try:
        ctx = build_ssl_context(cfg)  # tambem prova que chave e certificado formam um par
    except (ssl.SSLError, OSError, ValueError):
        raise StartupError("certificados invalidos ou chave nao confere") from None
    if ctx.verify_mode != ssl.CERT_REQUIRED:
        raise StartupError("mTLS nao esta obrigatorio")
    try:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        wprobe = cfg.log_dir / ".write_probe"
        wprobe.write_bytes(b"")
        wprobe.unlink()
    except OSError:
        raise StartupError("diretorio de log nao gravavel") from None
    kwargs: dict[str, Any] = {}
    if probe is not None:
        kwargs["probe"] = probe
    try:
        exposure = check_ollama_not_exposed(
            cfg.ollama_port,
            listeners=listeners,
            addresses=addresses,
            on_unverifiable=cfg.exposure_unverifiable,
            **kwargs,
        )
    except ExposureError as exc:
        raise StartupError(str(exc)) from None
    return ctx, exposure
