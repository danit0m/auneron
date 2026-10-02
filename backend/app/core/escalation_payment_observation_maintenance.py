"""
VALUE-3.3B -- auto-wiring de observed_fact.

Worker de manutencao dedicado que, para cada AccountEvent(new_status=
"pago") posterior ao activation_floor configurado, resolve o WorkItem
de escalonamento do episodio (account_id, account.vencimento) e delega
a persistencia a EscalationObservationService.record_observed_fact()
-- que ja garante temporalidade, provenance e idempotencia (UNIQUE +
IntegrityError reconciliado, seguro sob concorrencia).

Semantica congelada: "pagamento observado apos um escalonamento
inequivocamente correspondente" -- nunca sucesso nem causalidade.
Safe abstention absoluta: qualquer ambiguidade (sem WorkItem,
vencimento divergente do episodio, temporalidade rejeitada) resulta em
nenhuma observation. activation_floor=None significa recurso
DESABILITADO, jamais "sem limite temporal".
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import and_
from sqlalchemy import exists
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.human_escalation_eligibility import work_key_for_episode
from app.database.database import SessionLocal
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.escalation_observation import EscalationObservation
from app.models.work import WorkItem
from app.services.escalation_observation_service import (
    WORK_KEY_PREFIX,
)
from app.services.escalation_observation_service import (
    EscalationObservationConflictError,
)
from app.services.escalation_observation_service import (
    EscalationObservationService,
)
from app.services.escalation_observation_service import (
    EscalationObservationValidationError,
)

maintenance_loop_logger = logging.getLogger(
    "auneron.escalation_payment_observation_maintenance"
)


@dataclass(frozen=True)
class EscalationPaymentObservationRecoverySummary:
    candidate_count: int
    created_count: int
    duplicate_count: int
    abstained_count: int
    failure_count: int
    disabled: bool = False


def _list_candidates(
    db: Session,
    *,
    activation_floor: datetime,
    cursor: tuple[datetime, int] | None,
    limit: int,
) -> list[tuple[datetime, int]]:
    """
    AccountEvent(pago) >= floor, ainda sem observation vinculada, de
    contas que possuem algum WorkItem de escalonamento. O pre-filtro
    EXISTS evita que eventos de pagamento de contas nunca escalonadas
    (a grande maioria) ocupem o lote; a correspondencia exata de
    episodio (vencimento) e resolvida depois, em Python, reusando
    work_key_for_episode().

    Ordem cronologica deterministica: ORDER BY occurred_at, id (id so
    desempata). O cursor composto (occurred_at, id) avanca
    lexicograficamente -- occurred_at > ultimo OU (occurred_at == ultimo
    E id > ultimo_id) -- sem usar id como proxy de tempo e sem pular
    nem repetir eventos de mesmo occurred_at.
    """
    conditions = [
        AccountEvent.new_status == "pago",
        AccountEvent.occurred_at >= activation_floor,
        EscalationObservation.id.is_(None),
        exists().where(
            WorkItem.account_id == AccountEvent.account_id,
            WorkItem.scope_type == "account",
            WorkItem.work_key.like(f"{WORK_KEY_PREFIX}%"),
        ),
    ]
    if cursor is not None:
        last_occurred_at, last_id = cursor
        conditions.append(
            or_(
                AccountEvent.occurred_at > last_occurred_at,
                and_(
                    AccountEvent.occurred_at == last_occurred_at,
                    AccountEvent.id > last_id,
                ),
            )
        )

    statement = (
        select(AccountEvent.occurred_at, AccountEvent.id)
        .outerjoin(
            EscalationObservation,
            EscalationObservation.linked_account_event_id
            == AccountEvent.id,
        )
        .where(*conditions)
        .order_by(AccountEvent.occurred_at, AccountEvent.id)
        .limit(limit)
    )
    return [
        (occurred_at, int(event_id))
        for occurred_at, event_id in db.execute(statement).all()
    ]


def run_escalation_payment_observation_recovery(
    *,
    limit: int | None = None,
    activation_floor: datetime | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> EscalationPaymentObservationRecoverySummary:
    effective_limit = (
        settings.escalation_payment_observation_batch_size
        if limit is None
        else limit
    )
    if (
        isinstance(effective_limit, bool)
        or not isinstance(effective_limit, int)
        or effective_limit < 1
        or effective_limit > 1000
    ):
        raise ValueError(
            "limit inválido para Escalation Payment Observation "
            "recovery."
        )

    effective_floor = (
        settings.escalation_payment_observation_activation_floor
        if activation_floor is None
        else activation_floor
    )

    if effective_floor is None:
        maintenance_loop_logger.info(
            "escalation_payment_observation_disabled",
            extra={
                "event": "escalation_payment_observation.disabled",
            },
        )
        return EscalationPaymentObservationRecoverySummary(
            candidate_count=0,
            created_count=0,
            duplicate_count=0,
            abstained_count=0,
            failure_count=0,
            disabled=True,
        )

    if effective_floor.tzinfo is None:
        raise ValueError(
            "activation_floor exige fuso horario explicito."
        )

    candidate_count = 0
    created_count = 0
    duplicate_count = 0
    abstained_count = 0
    failure_count = 0

    db = session_factory()
    try:
        service = EscalationObservationService(db)
        cursor: tuple[datetime, int] | None = None

        # Varredura por keyset cronologico (occurred_at, id) ate
        # esgotar: candidatos que abstem permanentemente (vencimento
        # divergente, pagamento anterior ao escalonamento) nunca
        # ganham observation e continuam elegiveis na query; sem
        # avancar o cursor, os primeiros `limit` deles bloqueariam
        # candidatos mais novos.
        while True:
            candidates = _list_candidates(
                db,
                activation_floor=effective_floor,
                cursor=cursor,
                limit=effective_limit,
            )
            if not candidates:
                break

            for occurred_at, account_event_id in candidates:
                candidate_count += 1
                cursor = (occurred_at, account_event_id)

                try:
                    account_event = db.get(
                        AccountEvent, account_event_id
                    )
                    account = (
                        db.get(Account, account_event.account_id)
                        if account_event is not None
                        else None
                    )
                    if account_event is None or account is None:
                        abstained_count += 1
                        continue

                    work_item = (
                        db.query(WorkItem)
                        .filter(
                            WorkItem.account_id == account.id,
                            WorkItem.scope_type == "account",
                            WorkItem.work_key
                            == work_key_for_episode(
                                account.id, account.vencimento
                            ),
                        )
                        .one_or_none()
                    )
                    if work_item is None:
                        abstained_count += 1
                        continue

                    result = service.record_observed_fact(
                        escalation_work_item=work_item,
                        account_event=account_event,
                    )
                    if result.created:
                        created_count += 1
                    else:
                        duplicate_count += 1
                except (
                    EscalationObservationValidationError,
                    EscalationObservationConflictError,
                ):
                    db.rollback()
                    abstained_count += 1
                except Exception:
                    db.rollback()
                    failure_count += 1
                    maintenance_loop_logger.exception(
                        "escalation_payment_observation_"
                        "recovery_failed",
                        extra={
                            "event": (
                                "escalation_payment_observation"
                                ".recovery_failed"
                            ),
                            "account_event_id": account_event_id,
                        },
                    )

            if len(candidates) < effective_limit:
                break

        summary = EscalationPaymentObservationRecoverySummary(
            candidate_count=candidate_count,
            created_count=created_count,
            duplicate_count=duplicate_count,
            abstained_count=abstained_count,
            failure_count=failure_count,
        )
        maintenance_loop_logger.info(
            "escalation_payment_observation_recovery_completed",
            extra={
                "event": (
                    "escalation_payment_observation"
                    ".recovery_completed"
                ),
                "candidate_count": summary.candidate_count,
                "created_count": summary.created_count,
                "duplicate_count": summary.duplicate_count,
                "abstained_count": summary.abstained_count,
                "failure_count": summary.failure_count,
            },
        )
        return summary
    finally:
        db.close()


async def run_escalation_payment_observation_recovery_async(
) -> EscalationPaymentObservationRecoverySummary:
    worker = asyncio.create_task(
        asyncio.to_thread(
            run_escalation_payment_observation_recovery
        )
    )

    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        except Exception:
            maintenance_loop_logger.exception(
                "escalation_payment_observation_"
                "shutdown_drain_failed",
                extra={
                    "event": (
                        "escalation_payment_observation"
                        ".shutdown_drain_failed"
                    ),
                },
            )
        raise


async def escalation_payment_observation_maintenance_loop(
) -> None:
    while True:
        await asyncio.sleep(
            settings.escalation_payment_observation_interval_seconds
        )
        try:
            await run_escalation_payment_observation_recovery_async()
        except Exception as error:
            maintenance_loop_logger.exception(
                "escalation_payment_observation_"
                "maintenance_failed",
                extra={
                    "event": (
                        "escalation_payment_observation"
                        ".maintenance_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
