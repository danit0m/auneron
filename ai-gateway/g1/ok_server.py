"""G1: servidor de teste que SO responde "ok". Nao e o Gateway e nao fala com o Ollama.

mTLS 1.3 obrigatorio, so em 127.0.0.1, so o cliente cuja impressao digital esta em allow.txt.
Le no maximo 64 bytes, responde "ok G1\n" e fecha. Desliga sozinho apos --minutos (padrao 30).
Cada conexao vira uma linha JSON no log (sem conteudo, so veredito e impressao digital abreviada).

Uso: python ok_server.py <pasta-pki> <arquivo-log> [--porta 8443] [--minutos 30]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import ssl
import sys
import time
from pathlib import Path

MAX_CONN = 4


def contexto(pki: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=str(pki / "ca.pem"))
    ctx.load_cert_chain(str(pki / "server.pem"), str(pki / "server.key.pem"))
    return ctx


class Servidor:
    def __init__(self, pki: Path, log: Path) -> None:
        self.allow = {ln.strip() for ln in (pki / "allow.txt").read_text().splitlines() if ln.strip()}
        self.log = log
        self.ativos = 0

    def registrar(self, **kw) -> None:
        kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(kw, sort_keys=True) + "\n")
        print(json.dumps(kw, sort_keys=True), flush=True)

    async def atender(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            if self.ativos >= MAX_CONN:
                self.registrar(veredito="limite", peer=str(peer))
                return
            self.ativos += 1
            der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
            fp = hashlib.sha256(der).hexdigest() if der else ""
            if fp not in self.allow:
                self.registrar(veredito="recusado_nao_listado", peer=str(peer), cliente=fp[:16])
                return
            try:
                await asyncio.wait_for(reader.read(64), timeout=5)
            except asyncio.TimeoutError:
                pass
            writer.write(b"ok G1\n")
            await asyncio.wait_for(writer.drain(), timeout=5)
            self.registrar(veredito="ok", peer=str(peer), cliente=fp[:16],
                           tls=writer.get_extra_info("ssl_object").version())
        except Exception as exc:  # nunca derruba o servidor
            self.registrar(veredito="erro", peer=str(peer), tipo=type(exc).__name__)
        finally:
            self.ativos = max(0, self.ativos - 1)
            writer.close()


async def principal(a: argparse.Namespace) -> int:
    pki = Path(a.pki)
    srv = Servidor(pki, Path(a.log))
    server = await asyncio.start_server(srv.atender, "127.0.0.1", a.porta, ssl=contexto(pki),
                                        ssl_handshake_timeout=5, reuse_address=False)
    srv.registrar(veredito="inicio", porta=a.porta, minutos=a.minutos)
    print("PRONTO", flush=True)
    try:
        await asyncio.wait_for(server.serve_forever(), timeout=a.minutos * 60)
    except asyncio.TimeoutError:
        pass
    finally:
        server.close()
        srv.registrar(veredito="fim")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pki")
    p.add_argument("log")
    p.add_argument("--porta", type=int, default=8443)
    p.add_argument("--minutos", type=float, default=30)
    a = p.parse_args()
    if not 1 <= a.minutos <= 120:
        print("minutos fora da faixa (1 a 120)")
        return 2
    return asyncio.run(principal(a))


if __name__ == "__main__":
    sys.exit(main())
