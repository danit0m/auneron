"""
DW-7.4 -- Connected Autonomous Trigger Mechanism (account.mark_overdue).

Componente de deteccao/reconciliacao periodica, independente dos dois
corredores legados encontrados no Discovery
(OverdueDetectionService/AuthenticatedAdvisoryProposal/AIOrchestrator e
pilot_mutation_maintenance.py) -- nenhum dos dois e importado aqui.

Fronteira vinculante (DW-7.4.1 Design Freeze, T1-T13): este modulo
descobre episodios operacionalmente elegiveis
(Account.status=='aberto' AND Account.vencimento<hoje) e os oferece a
PolicyAccountMarkOverdueExecutionService -- nunca consulta, cria,
revoga ou amplia PolicyAuthorityGrant. A distincao entre "elegivel" e
"autorizado" e preservada classificando apenas o TIPO da excecao
publica que execute() ja levanta (ApprovalAuthorizationError = handle
'authority_unavailable'), nunca inspecionando o Grant diretamente.

Achado transacional do PRE-APPLY, vinculante: execute() so faz
rollback nos dois `except` finais em torno da persistencia -- todo o
resto de suas validacoes fail-closed anteriores (grant ausente/
expirado, conta em estado errado, etc.) levanta excecao SEM rollback,
podendo deixar locks retidos. Por isso este runner faz
`db.rollback()` incondicional apos QUALQUER excecao de execute(),
nunca condicional ao tipo.

A falha da propria consulta de elegibilidade (SELECT eligible
Accounts) NAO e capturada aqui -- propaga para o loop de manutencao
externo, que ja trata exception de ciclo inteiro (nunca derruba o
loop). Isso evita que uma indisponibilidade estrutural do banco vire
uma colecao enganosa de "falhas de candidato".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.config import settings
from app.database.database import SessionLocal
from app.models.account import Account
from app.services.policy_account_mark_overdue_execution_service import (
    PolicyAccountMarkOverdueExecutionService,
)

logger = logging.getLogger(
    "auneron.policy_account_mark_overdue_trigger"
)


@dataclass(frozen=True)
class PolicyAccountMarkOverdueTriggerResult:
    candidates_detected: int
    executions_attempted: int
    executions_succeeded: int
    executions_duplicate: int
    executions_authority_unavailable: int
    executions_failed_other: int

    def as_dict(self) -> dict[str, int]:
        return {
            "candidates_detected": self.candidates_detected,
            "executions_attempted": self.executions_attempted,
            "executions_succeeded": self.executions_succeeded,
            "executions_duplicate": self.executions_duplicate,
            "executions_authority_unavailable": (
                self.executions_authority_unavailable
            ),
            "executions_failed_other": self.executions_failed_other,
        }


def _normalized_limit(limit: int | None) -> int:
    effective = (
        settings.work_skill_recovery_batch_size
        if limit is None
        else limit
    )
    if (
        isinstance(effective, bool)
        or not isinstance(effective, int)
        or effective < 1
        or effective > 1000
    ):
        raise ValueError(
            "Limite inválido para o trigger de "
            "account.mark_overdue."
        )
    return effective


def run_policy_account_mark_overdue_trigger(
    *,
    today: date | None = None,
    limit: int | None = None,
) -> PolicyAccountMarkOverdueTriggerResult:
    effective_today = today if today is not None else date.today()
    effective_limit = _normalized_limit(limit)

    executions_succeeded = 0
    executions_duplicate = 0
    executions_authority_unavailable = 0
    executions_failed_other = 0

    with SessionLocal() as db:
        candidates = db.execute(
            select(Account.id, Account.vencimento)
            .where(
                Account.status == "aberto",
                Account.vencimento < effective_today,
            )
            .order_by(Account.id.asc())
            .limit(effective_limit)
        ).all()

        service = PolicyAccountMarkOverdueExecutionService(db)

        for account_id, vencimento in candidates:
            try:
                result = service.execute(
                    account_id=account_id,
                    due_date=vencimento,
                )
                if result.duplicate:
                    executions_duplicate += 1
                else:
                    executions_succeeded += 1
            except ApprovalAuthorizationError:
                db.rollback()
                executions_authority_unavailable += 1
            except Exception:  # noqa: BLE001
                db.rollback()
                executions_failed_other += 1

    return PolicyAccountMarkOverdueTriggerResult(
        candidates_detected=len(candidates),
        executions_attempted=len(candidates),
        executions_succeeded=executions_succeeded,
        executions_duplicate=executions_duplicate,
        executions_authority_unavailable=(
            executions_authority_unavailable
        ),
        executions_failed_other=executions_failed_other,
    )


async def run_policy_account_mark_overdue_trigger_async() -> (
    PolicyAccountMarkOverdueTriggerResult
):
    return await asyncio.to_thread(
        run_policy_account_mark_overdue_trigger
    )


async def policy_account_mark_overdue_trigger_maintenance_loop() -> (
    None
):
    while True:
        await asyncio.sleep(
            settings.work_skill_recovery_interval_seconds
        )
        try:
            result = (
                await run_policy_account_mark_overdue_trigger_async()
            )
            logger.info(
                "policy_account_mark_overdue_trigger_completed",
                extra={
                    "event": "policy.trigger.completed",
                    **result.as_dict(),
                },
            )
        except Exception as error:  # noqa: BLE001
            logger.exception(
                "policy_account_mark_overdue_trigger_maintenance_failed",
                extra={
                    "event": (
                        "policy.trigger.maintenance_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
