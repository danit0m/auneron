"""
Fatia F3 -- primeiro escritor vivo e continuo de Knowledge.

Observa a carteira de contas e registra em Knowledge apenas as
*transicoes* de situacao operacional (app/core/receivable_lifecycle.py),
nunca a cada ciclo. E puramente advisory: nunca muta Account.status,
nunca decide uma aprovacao, nunca usa nem reativa os agentes legados em
quarentena (AnalyticsAgent/FinanceAgent/NotificationAgent/RiskAgent) --
ver app/agents/event_bus.py.

Ordem congelada em design freeze com Tomaz em 12/09/2026 (corrige um
bug de "resolve incondicional" que resolveria o alerta ativo em todo
ciclo mesmo sem nenhuma mudanca real de estado):

    1. calcular (state, correlation_key) da conta
    2. SE ja existe uma linha de Knowledge com essa correlation_key
       exata (resolvida ou nao) -> nao fazer nada. Essa transicao ja
       foi registrada antes; se um humano resolveu manualmente no
       Brain, isso significa "alerta reconhecido" e nao deve ser
       reaberto automaticamente.
    3. SENAO -> resolver as linhas ativas (resolved=False,
       knowledge_type="receivable_lifecycle") desta conta e so entao
       inserir a nova linha.

Os passos 2 e 3 ocorrem na mesma transacao por conta. "open" e "paid"
nao geram alerta novo (nao ha o que "avisar"); apenas resolvem um
alerta anterior que deixou de se aplicar (ex.: vencimento editado para
uma data futura, ou conta paga).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.receivable_lifecycle import ReceivableLifecycleState
from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle
from app.database.database import SessionLocal
from app.models.account import Account
from app.models.knowledge import Knowledge


logger = logging.getLogger("auneron.receivables_monitor")


AGENT_NAME = "ReceivablesMonitorAgent"
EVENT_NAME = "receivable_lifecycle_changed"
KNOWLEDGE_TYPE = "receivable_lifecycle"

ALERT_STATES: frozenset[ReceivableLifecycleState] = frozenset(
    {"due_soon", "due_today", "overdue", "overdue_alert"}
)

SEVERITY_BY_STATE: dict[ReceivableLifecycleState, str] = {
    "due_soon": "info",
    "due_today": "medium",
    "overdue": "high",
    "overdue_alert": "critical",
}


def _correlation_key(account: Account, state: str) -> str:
    return (
        f"receivable_lifecycle:v1:{account.id}:"
        f"{account.vencimento.isoformat()}:{state}"
    )


def _title_and_message(
    account: Account,
    *,
    state: ReceivableLifecycleState,
    days_to_due: int,
    days_overdue: int,
) -> tuple[str, str]:
    if state == "due_soon":
        return (
            f"Vencimento próximo — {account.cliente}",
            f"Faltam {days_to_due} dias para o vencimento.",
        )

    if state == "due_today":
        return (
            f"Vence hoje — {account.cliente}",
            "O pagamento vence hoje.",
        )

    if state == "overdue":
        return (
            f"Pagamento atrasado — {account.cliente}",
            f"Vencido há {days_overdue} dias.",
        )

    return (
        f"Atraso crítico — {account.cliente}",
        f"{days_overdue} dias em atraso — requer atenção.",
    )


def _resolve_active_receivable_knowledge(
    db: Session,
    account_id: int,
    *,
    now: datetime,
) -> int:
    return (
        db.query(Knowledge)
        .filter(
            Knowledge.account_id == account_id,
            Knowledge.knowledge_type == KNOWLEDGE_TYPE,
            Knowledge.resolved.is_(False),
        )
        .update(
            {
                Knowledge.resolved: True,
                Knowledge.resolved_at: now,
            },
            synchronize_session=False,
        )
    )


@dataclass(frozen=True)
class ReceivablesMonitorRunResult:
    accounts_evaluated: int
    knowledge_created: int
    knowledge_resolved: int
    skipped: int
    failed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "accounts_evaluated": self.accounts_evaluated,
            "knowledge_created": self.knowledge_created,
            "knowledge_resolved": self.knowledge_resolved,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def _evaluate_and_record_one(
    db: Session,
    account: Account,
    *,
    today,
) -> tuple[bool, int]:
    """
    Retorna (created, resolved_count) para uma unica conta. Os dois
    nao sao mutuamente exclusivos: uma transicao normal de alerta
    (ex.: overdue -> overdue_alert) resolve a linha anterior E cria a
    nova na mesma chamada. Assume que o chamador isola erros por conta
    (rollback + continue).
    """

    lifecycle = evaluate_receivable_lifecycle(
        financial_status=account.status,
        vencimento=account.vencimento,
        today=today,
    )

    now = datetime.now(timezone.utc)

    if lifecycle.state not in ALERT_STATES:
        resolved_count = _resolve_active_receivable_knowledge(
            db, account.id, now=now
        )
        db.commit()

        return (False, resolved_count)

    correlation_key = _correlation_key(account, lifecycle.state)

    existing = (
        db.query(Knowledge)
        .filter(Knowledge.correlation_key == correlation_key)
        .first()
    )

    if existing is not None:
        db.rollback()
        return (False, 0)

    resolved_count = _resolve_active_receivable_knowledge(
        db, account.id, now=now
    )

    title, message = _title_and_message(
        account,
        state=lifecycle.state,
        days_to_due=lifecycle.days_to_due,
        days_overdue=lifecycle.days_overdue,
    )

    knowledge = Knowledge(
        agent_name=AGENT_NAME,
        event_name=EVENT_NAME,
        knowledge_type=KNOWLEDGE_TYPE,
        severity=SEVERITY_BY_STATE[lifecycle.state],
        title=title,
        message=message,
        account_id=account.id,
        correlation_key=correlation_key,
        resolved=False,
    )

    db.add(knowledge)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return (False, 0)

    return (True, resolved_count)


def run_receivables_monitor() -> ReceivablesMonitorRunResult:
    """
    Percorre todas as contas e registra em Knowledge apenas as
    transicoes reais de situacao operacional. Abre sua propria sessao,
    no mesmo padrao dos demais maintenance workers.
    """

    today = business_today()

    created = 0
    resolved = 0
    skipped = 0
    failed = 0
    evaluated = 0

    with SessionLocal() as db:
        accounts = db.query(Account).all()

        for account in accounts:
            evaluated += 1

            try:
                account_created, account_resolved_count = (
                    _evaluate_and_record_one(
                        db, account, today=today
                    )
                )
            except Exception as error:
                db.rollback()
                failed += 1
                logger.warning(
                    "receivables_monitor_account_failed",
                    extra={
                        "event": (
                            "receivables_monitor.account_failed"
                        ),
                        "account_id": account.id,
                        "error_type": type(error).__name__,
                    },
                )
                continue

            if account_created:
                created += 1

            resolved += account_resolved_count

            if not account_created and not account_resolved_count:
                skipped += 1

    result = ReceivablesMonitorRunResult(
        accounts_evaluated=evaluated,
        knowledge_created=created,
        knowledge_resolved=resolved,
        skipped=skipped,
        failed=failed,
    )

    logger.info(
        "receivables_monitor_completed",
        extra={
            "event": "receivables_monitor.completed",
            **result.as_dict(),
        },
    )

    return result


async def run_receivables_monitor_async() -> ReceivablesMonitorRunResult:
    return await asyncio.to_thread(run_receivables_monitor)


async def receivables_monitor_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings.receivables_monitor_interval_seconds
        )
        try:
            await run_receivables_monitor_async()
        except Exception as error:
            logger.exception(
                "receivables_monitor_maintenance_failed",
                extra={
                    "event": (
                        "receivables_monitor.maintenance_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
