"""G1: sonda que roda DENTRO do container, na rede do no Mesh (so biblioteca padrao).

Conecta em ai.auneron.internal:8443 pela Cloudflare e faz 6 provas:
  S1 resolucao: o nome resolve para o intervalo de IP inicial da Cloudflare (172.64.128.0/20)
  S2 sem certificado de cliente -> recusado
  S3 certificado da mesma CA, nao listado -> recusado
  S4 cliente listado -> "ok G1" e o certificado do servidor e EXATAMENTE o nosso (TLS de ponta a
     ponta: a Cloudflare nao abriu o TLS)
  S5 confiando em outra CA -> o proprio cliente recusa o servidor
  S6 recusa seguida de sucesso no MESMO thread (risco do client.py visto no G2)
Sai 0 so se tudo passar. Modo --espera-falha (controle negativo, Mesh parado): sai 0 so se o
cliente listado NAO conseguir chegar.

Uso: python probe.py <pasta-pki> [--host ai.auneron.internal] [--ip IP] [--porta 8443] [--espera-falha]
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import socket
import ssl
import sys
import time
from pathlib import Path

FAIXA_CF = [ipaddress.ip_network("172.64.128.0/20"), ipaddress.ip_network("2606:4700:cf1:4000::/64")]
NOME_TLS = "ai.auneron.internal"


def ctx_cliente(pki: Path, cert: str | None, ca: str = "ca.pem") -> ssl.SSLContext:
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.minimum_version = ssl.TLSVersion.TLSv1_3
    c.load_verify_locations(cafile=str(pki / ca))
    if cert:
        c.load_cert_chain(str(pki / f"{cert}.pem"), str(pki / f"{cert}.key.pem"))
    return c


def tentar(host: str, porta: int, ctx: ssl.SSLContext, timeout: float = 15) -> dict:
    t0 = time.monotonic()
    r: dict = {}
    try:
        raw = socket.create_connection((host, porta), timeout=timeout)
    except OSError as e:
        return {"ok": False, "fase": "tcp", "erro": type(e).__name__, "s": round(time.monotonic() - t0, 2)}
    try:
        with ctx.wrap_socket(raw, server_hostname=NOME_TLS) as s:
            s.settimeout(timeout)
            der = s.getpeercert(binary_form=True)
            r["servidor_fp"] = hashlib.sha256(der).hexdigest()
            r["tls"] = s.version()
            s.sendall(b"G1\n")
            dados = b""
            while len(dados) < 64:
                ch = s.recv(64)
                if not ch:
                    break
                dados += ch
            r["resposta"] = dados.decode("ascii", "replace").strip()
            r["ok"] = r["resposta"] == "ok G1"
            r["fase"] = "resposta"
    except (OSError, ssl.SSLError) as e:
        r.update(ok=False, fase=r.get("fase", "tls"), erro=type(e).__name__)
    finally:
        raw.close()
    r["s"] = round(time.monotonic() - t0, 2)
    return r


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pki")
    p.add_argument("--host", default="ai.auneron.internal")
    p.add_argument("--porta", type=int, default=8443)
    p.add_argument("--ip", help="IP ja resolvido pelo no Mesh, se o DNS do WARP nao chegar a sonda")
    p.add_argument("--espera-falha", action="store_true")
    p.add_argument("--sem-faixa", action="store_true", help="teste offline: nao exige IP da Cloudflare")
    a = p.parse_args()
    pki = Path(a.pki)
    esperado = (pki / "server_fp.txt").read_text().strip()
    res: dict = {}

    if a.ip:
        ips = [str(ipaddress.ip_address(a.ip))]
        res["S1_origem"] = "--ip (resolvido no no Mesh)"
    else:
        try:
            ips = sorted({i[4][0] for i in socket.getaddrinfo(a.host, a.porta, proto=socket.IPPROTO_TCP)})
        except OSError as e:
            ips, res["S1_erro"] = [], type(e).__name__
    alvo = a.ip or a.host
    na_faixa = bool(ips) and all(any(ipaddress.ip_address(ip) in n for n in FAIXA_CF) for ip in ips)
    res["S1_resolucao"] = {"ips": ips, "na_faixa_cloudflare": na_faixa}

    if a.espera_falha:
        r = tentar(alvo, a.porta, ctx_cliente(pki, "client_ok"), timeout=10)
        res["controle_negativo"] = r
        passou = not r.get("ok")
        print(json.dumps(res, indent=1, sort_keys=True))
        print("RESULTADO G1 CONTROLE NEGATIVO:", "PASSOU (nao chegou sem o Mesh)" if passou else "FALHOU (chegou!)")
        return 0 if passou else 1

    res["S2_sem_certificado"] = tentar(alvo, a.porta, ctx_cliente(pki, None))
    res["S3_nao_listado"] = tentar(alvo, a.porta, ctx_cliente(pki, "client_outro"))
    s4 = tentar(alvo, a.porta, ctx_cliente(pki, "client_ok"))
    s4["servidor_e_o_nosso"] = s4.get("servidor_fp") == esperado
    res["S4_listado"] = s4
    res["S5_outra_ca"] = tentar(alvo, a.porta, ctx_cliente(pki, "client_ok", ca="outra_ca.pem"))
    falha = tentar(alvo, a.porta, ctx_cliente(pki, "client_outro"))
    depois = tentar(alvo, a.porta, ctx_cliente(pki, "client_ok"))
    res["S6_recusa_depois_sucesso"] = {"recusa": falha, "sucesso": depois}

    checks = {
        "S1": na_faixa or a.sem_faixa,
        "S2": not res["S2_sem_certificado"].get("ok"),
        "S3": not res["S3_nao_listado"].get("ok"),
        "S4": bool(s4.get("ok")) and s4["servidor_e_o_nosso"] and s4.get("tls") == "TLSv1.3",
        "S5": not res["S5_outra_ca"].get("ok") and res["S5_outra_ca"].get("erro") == "SSLCertVerificationError",
        "S6": (not falha.get("ok")) and bool(depois.get("ok")),
    }
    res["conferencias"] = checks
    print(json.dumps(res, indent=1, sort_keys=True))
    todos = all(checks.values())
    print("RESULTADO G1 SONDA:", "PASSOU 6/6" if todos else f"FALHOU {sum(checks.values())}/6")
    return 0 if todos else 1


if __name__ == "__main__":
    sys.exit(main())
