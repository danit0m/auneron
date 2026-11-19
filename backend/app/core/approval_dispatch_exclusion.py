"""
Exclusao mutua, por pedido de aprovacao, entre os varredores de
manutencao que executam o mesmo efeito governado (despacho consultivo e
recuperacao do piloto de mark_overdue).

Os dois varredores rodam no mesmo intervalo e enxergam o mesmo pedido
aprovado ainda sem consumo; sem exclusao os dois entram em
AccountMarkOverdueExecutionService.execute ao mesmo tempo. O efeito segue
unico (idempotencia por ApprovalConsumption), mas as tentativas
concorrentes disputam locks de linha (ex. work_skill_executions) e uma
delas falha com OperationalError, virando um WARNING falso de falha.
Na homologacao de 19/11/2026 (pedido 9) o Postgres registrou
"deadlock detected" (40P01) em work_skill_executions; lock_timeout
(55P03) e o outro desfecho possivel da mesma disputa.

Aqui a segunda tentativa simplesmente sai, sem erro: quem nao obtem o
lock deixa o pedido para o varredor que ja o esta executando (ou para o
proximo ciclo, se aquele falhar). Nao altera nenhuma regra de
governanca: so impede a entrada concorrente.

Usa advisory lock de sessao do PostgreSQL numa conexao dedicada (o lock
de transacao seria liberado pelo primeiro commit do efeito). Em outros
bancos nao ha exclusao (comportamento anterior).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.core.config import settings
from app.database import database

# Namespace fixo (int32) na parte alta da chave bigint.
_NAMESPACE = 0x41505256  # "APRV"


def advisory_key(approval_request_id: int) -> int:
    if (
        isinstance(approval_request_id, bool)
        or not isinstance(approval_request_id, int)
        or approval_request_id <= 0
    ):
        raise ValueError("approval_request_id invalido.")
    return (_NAMESPACE << 32) | (approval_request_id & 0xFFFFFFFF)


@contextmanager
def exclusive_approval_dispatch(
    approval_request_id: int,
) -> Iterator[bool]:
    """
    Devolve True se esta tentativa e a unica em andamento para o pedido;
    False se outra ja o executa (o chamador deve apenas sair).
    """
    key = advisory_key(approval_request_id)

    if not settings.is_postgresql:
        yield True
        return

    connection = database.engine.connect()
    acquired = False
    try:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": key},
            ).scalar()
        )
        connection.commit()
        yield acquired
    finally:
        try:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": key},
                    )
                    connection.commit()
                except Exception:
                    # Nao devolve ao pool uma conexao que pode reter o
                    # lock: descartar a conexao real o libera.
                    connection.invalidate()
                    raise
        finally:
            connection.close()
