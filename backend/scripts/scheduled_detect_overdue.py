"""
Auneron - varredura agendada de deteccao de vencimento.

Chamada pelo Windows Task Scheduler (uma vez por dia), normalmente via
`backend/scripts/homologacao.ps1 detectar-vencidos`, que carrega a chave
vigente do cofre DPAPI e a entrega a este processo SOMENTE pela variavel de
ambiente API_KEY.

- api_key: somente da variavel de ambiente API_KEY (nunca de arquivo).
- email e senha da conta de servico: arquivo JSON de credenciais fora do
  repositorio (campos "email" e "password"). Qualquer campo "api_key" nesse
  arquivo e IGNORADO.
- Faz login (POST /auth/login) e chama POST /accounts/detect-overdue.
- A sessao NAO e revogada de proposito: precisa continuar viva para a janela
  de aprovacao humana e o despacho automatico (expira pelo TTL normal).

Codigos de saida: 0 ok, 1 erro, 2 falhas parciais (contas com falha).
Nenhum segredo e escrito no log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE_URL = "http://localhost:8000"

CREDENTIALS_PATH = Path(
    r"C:\Users\Tomaz\Downloads\arquivos gerados\overdue_scan_credentials.json"
)
LOG_PATH = Path(r"C:\Users\Tomaz\Downloads\arquivos gerados\overdue_scan_log.txt")

# Segredos conhecidos nesta execucao; mascarados em qualquer texto logado.
_segredos: list[str] = []


def _mascarar(texto: str) -> str:
    for s in sorted(_segredos, key=len, reverse=True):
        if s and len(s) >= 4:
            texto = texto.replace(s, "***")
    return texto


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{timestamp}] {_mascarar(message)}"
    print(line)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # falha de log nao derruba a varredura
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--credenciais",
        default=None,
        help="JSON com email e password da conta de servico "
        "(padrao: caminho fixo fora do repositorio).",
    )
    args = parser.parse_args(argv)
    cred_path = Path(args.credenciais) if args.credenciais else CREDENTIALS_PATH

    api_key = os.environ.get("API_KEY", "")
    if not api_key.strip():
        log(
            "ERRO: variavel de ambiente API_KEY ausente. Rode via "
            "homologacao.ps1 detectar-vencidos (a chave vem do cofre)."
        )
        return 1
    _segredos.append(api_key)

    if not cred_path.is_file():
        log(f"ERRO: arquivo de credenciais nao encontrado: {cred_path}")
        return 1

    try:
        creds = json.loads(cred_path.read_text(encoding="utf-8"))
        email = creds["email"]
        password = creds["password"]
    except Exception as exc:
        # so o tipo da excecao: a mensagem poderia ecoar conteudo do arquivo
        log(f"ERRO: falha ao ler/parsear credenciais ({type(exc).__name__}).")
        return 1
    # o campo api_key do arquivo (se existir) e ignorado de proposito
    if not email or not password:
        log("ERRO: credenciais sem email ou password.")
        return 1
    _segredos.append(str(password))

    session = requests.Session()
    # X-API-Key e exigido a nivel de router (inclusive em /auth/login).
    session.headers.update({"X-API-Key": api_key})

    # 1. autenticacao -- a sessao (cookie httponly) fica no requests.Session()
    try:
        login_resp = session.post(
            f"{API_BASE_URL}/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
    except requests.RequestException as exc:
        log(
            f"AUTENTICACAO: FALHOU (conexao em {API_BASE_URL}: "
            f"{type(exc).__name__})."
        )
        return 1

    if login_resp.status_code != 200:
        log(
            "AUTENTICACAO: FALHOU. "
            f"status={login_resp.status_code} body={login_resp.text[:500]!r}"
        )
        return 1

    log(f"AUTENTICACAO: OK (usuario={email}).")

    # 2. varredura -- o cookie de sessao vai junto automaticamente
    try:
        scan_resp = session.post(f"{API_BASE_URL}/accounts/detect-overdue", timeout=120)
    except requests.RequestException as exc:
        log(f"VARREDURA: FALHOU (conexao: {type(exc).__name__}).")
        return 1

    if scan_resp.status_code != 200:
        log(
            "VARREDURA: FALHOU. "
            f"status={scan_resp.status_code} body={scan_resp.text[:500]!r}"
        )
        return 1

    try:
        resultado = scan_resp.json()
    except ValueError:
        log(f"VARREDURA: FALHOU (resposta nao e JSON valido): {scan_resp.text[:500]!r}")
        return 1

    log(
        "VARREDURA: concluida. "
        f"contas_verificadas={resultado.get('contas_verificadas')} "
        f"propostas_criadas={resultado.get('propostas_criadas')} "
        f"ja_com_proposta={resultado.get('ja_com_proposta')} "
        f"falhas={resultado.get('falhas')} "
        f"aprovacoes_solicitadas={resultado.get('aprovacoes_solicitadas')} "
        f"aprovacoes_falharam={resultado.get('aprovacoes_falharam')}"
    )

    falhas = resultado.get("falhas", 0)
    if falhas:
        log(f"ATENCAO: {falhas} conta(s) tiveram falha ao registrar proposta.")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
