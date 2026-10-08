"""
INTERLOCK-D0 (D-1.5E-3/4): o run so entra em `RUNNING` com um token de autorizacao vinculado ao `run_id` e ao RIM.

O token e um arquivo `D0_AUTHORIZATION.json` na pasta do run, de esquema FECHADO, com `token_sha256` sobre o
conteudo canonico. `enter_running` e o UNICO caminho para o par `D0_AUTHORIZED` + `RUN_STARTED` do ledger (o ledger
recusa `D0_AUTHORIZED` por `append` generico, e o ciclo de vida recusa `RUN_STARTED` sem ele).

NENHUM codigo deste pacote escreve token para `kind = RUN` (a decisao de autorizar o D0 da Nova Horizonte e do PO,
num checkpoint proprio). `authorize_rehearsal` so escreve para `REHEARSAL`.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner.errors import InterlockError

TOKEN_NAME = "D0_AUTHORIZATION.json"
TOKEN_SCHEMA = "sim.run.d0_authorization.v1"
TOKEN_KEYS = ("schema", "run_id", "rim_sha256", "kind", "authorized_by", "reference", "token_sha256")


def _body(token: dict) -> dict:
    return {k: v for k, v in token.items() if k != "token_sha256"}


def build_token(run_id: str, rim_sha256: str, kind: str, authorized_by: str, reference: str) -> dict:
    if not authorized_by.strip() or not reference.strip():
        raise InterlockError("autorizacao exige `authorized_by` e `reference` nao vazios")
    token = {"schema": TOKEN_SCHEMA, "run_id": run_id, "rim_sha256": rim_sha256, "kind": kind,
             "authorized_by": authorized_by, "reference": reference}
    token["token_sha256"] = sha256_bytes(canonical_bytes(token))
    return token


def verify_token(token: dict, *, run_id: str, rim_sha256: str, kind: str) -> str:
    """Devolve o `token_sha256` se o token e valido para ESTE run; senao InterlockError."""
    if not isinstance(token, dict) or set(token) != set(TOKEN_KEYS):
        raise InterlockError("token com chaves diferentes do esquema fechado")
    if token["schema"] != TOKEN_SCHEMA:
        raise InterlockError("schema do token desconhecido")
    for field in ("authorized_by", "reference"):
        if not isinstance(token[field], str) or not token[field].strip():
            raise InterlockError(f"token sem {field}")
    if sha256_bytes(canonical_bytes(_body(token))) != token["token_sha256"]:
        raise InterlockError("token_sha256 nao confere (token alterado)")
    if token["run_id"] != run_id or token["rim_sha256"] != rim_sha256 or token["kind"] != kind:
        raise InterlockError("token de outro run, RIM ou kind")
    return token["token_sha256"]


def load_token(run_dir) -> dict:
    path = Path(run_dir) / TOKEN_NAME
    if not path.exists():
        raise InterlockError("D0_AUTHORIZATION.json ausente: o run nao pode entrar em RUNNING")
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except ValueError as error:
        raise InterlockError("D0_AUTHORIZATION.json ilegivel") from error


def authorize_rehearsal(run_dir, run_id: str, rim_sha256: str, kind: str, authorized_by: str, reference: str) -> dict:
    """Escreve o token SOMENTE para ensaio. Para `RUN` recusa sempre."""
    if kind != "REHEARSAL":
        raise InterlockError("este pacote nao emite autorizacao de D0 para kind != REHEARSAL (decisao exclusiva do PO)")
    token = build_token(run_id, rim_sha256, kind, authorized_by, reference)
    path = Path(run_dir) / TOKEN_NAME
    if path.exists():
        raise InterlockError("token ja existe")
    path.write_bytes(canonical_bytes(token))
    return token


def enter_running(ledger, run_dir, *, run_id: str, rim_sha256: str, kind: str) -> dict:
    """PREFLIGHTED -> RUNNING. Sem token valido o estado NAO muda e nada e gravado."""
    if ledger.state != "PREFLIGHTED":
        raise InterlockError(f"so PREFLIGHTED pode entrar em RUNNING (estado: {ledger.state})")
    token_sha = verify_token(load_token(run_dir), run_id=run_id, rim_sha256=rim_sha256, kind=kind)
    ledger.authorize_d0(token_sha, {"run_id": run_id})
    return ledger.append("RUN_STARTED", {"run_id": run_id, "token_sha256": token_sha})


def verify_ledger_authorization(ledger, run_dir) -> None:
    """Pos-fato: se o ledger tem RUN_STARTED, o token em disco tem que existir e bater com o evento gravado."""
    started = [r for r in ledger.events() if r["event"] == "RUN_STARTED"]
    if not started:
        return
    authorized = [r for r in ledger.events() if r["event"] == "D0_AUTHORIZED"]
    open_event = ledger.events()[0]["payload"]
    if len(authorized) != 1:
        raise InterlockError("RUN_STARTED sem exatamente um D0_AUTHORIZED")
    token_sha = verify_token(load_token(run_dir), run_id=open_event["run_id"], rim_sha256=open_event["rim_sha256"],
                             kind=open_event["kind"])
    if authorized[0]["payload"]["token_sha256"] != token_sha:
        raise InterlockError("token em disco difere do registrado no ledger")


__all__ = ["TOKEN_NAME", "build_token", "verify_token", "load_token", "authorize_rehearsal", "enter_running",
           "verify_ledger_authorization"]
