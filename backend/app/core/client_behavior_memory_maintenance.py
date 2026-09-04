import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timezone

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.database import SessionLocal
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.repositories.memory_repository import MemoryRepository
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService
from app.services.memory_service import RememberResult
from app.services.memory_service import SupersedeResult


logger = logging.getLogger("auneron.client_behavior_memory")

MEMORY_KEY = "client_behavior_payment_pattern"
MAX_EVIDENCE_CYCLES = 20


CANDIDATE_EMAILS_SQL = text(
    """
    SELECT DISTINCT a.email
    FROM account_events ae
    JOIN accounts a ON a.id = ae.account_id
    WHERE ae.new_status = 'pago'
      AND a.email IS NOT NULL
      AND ae.occurred_at > COALESCE(
        (
            SELECT MAX(mi.created_at)
            FROM memory_items mi
            JOIN accounts a2 ON mi.account_id = a2.id
            WHERE a2.email = a.email
              AND mi.memory_type = 'observation'
              AND mi.status = 'active'
        ),
        '-infinity'
      )
    ORDER BY a.email
    """
)


@dataclass(frozen=True)
class ClientBehaviorCycle:
    account_id: int
    vencimento: date
    resolved_at: date
    atraso_dias: int


@dataclass(frozen=True)
class ClientBehaviorPattern:
    email: str
    oldest_account_id: int
    ocorrencias_resolvidas: int
    atraso_medio_dias: float
    atraso_min_dias: int
    atraso_max_dias: int
    taxa_pagamento: float
    confidence: float
    cycles: tuple[ClientBehaviorCycle, ...]


def list_client_behavior_recalculation_candidate_emails(
    db: Session,
) -> list[str]:
    return list(
        db.execute(CANDIDATE_EMAILS_SQL).scalars().all()
    )


def compute_client_behavior_pattern(
    db: Session,
    email: str,
) -> ClientBehaviorPattern | None:
    accounts = (
        db.execute(
            select(Account)
            .where(Account.email == email)
            .order_by(Account.id.asc())
        )
        .scalars()
        .all()
    )

    if not accounts:
        return None

    oldest_account_id = accounts[0].id

    cycles: list[ClientBehaviorCycle] = []
    total_ciclos_observados = 0

    for account in accounts:
        total_ciclos_observados += 1

        latest_paid_event = (
            db.execute(
                select(AccountEvent)
                .where(
                    AccountEvent.account_id == account.id,
                    AccountEvent.new_status == "pago",
                )
                .order_by(AccountEvent.occurred_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

        if latest_paid_event is None:
            continue

        resolved_at = latest_paid_event.occurred_at.date()
        atraso_dias = (resolved_at - account.vencimento).days

        cycles.append(
            ClientBehaviorCycle(
                account_id=account.id,
                vencimento=account.vencimento,
                resolved_at=resolved_at,
                atraso_dias=atraso_dias,
            )
        )

    ocorrencias_resolvidas = len(cycles)

    if (
        ocorrencias_resolvidas
        < settings.client_behavior_min_occurrences_for_pattern
    ):
        return None

    atrasos = [cycle.atraso_dias for cycle in cycles]
    atraso_medio_dias = sum(atrasos) / ocorrencias_resolvidas
    atraso_min_dias = min(atrasos)
    atraso_max_dias = max(atrasos)
    taxa_pagamento = (
        ocorrencias_resolvidas / total_ciclos_observados
    )
    confidence = min(
        1.0,
        1 - (1 / ocorrencias_resolvidas),
    )

    return ClientBehaviorPattern(
        email=email,
        oldest_account_id=oldest_account_id,
        ocorrencias_resolvidas=ocorrencias_resolvidas,
        atraso_medio_dias=atraso_medio_dias,
        atraso_min_dias=atraso_min_dias,
        atraso_max_dias=atraso_max_dias,
        taxa_pagamento=taxa_pagamento,
        confidence=confidence,
        cycles=tuple(cycles),
    )


def _pattern_title(email: str) -> str:
    return f"Padrão de pagamento — {email}"


def _pattern_content(pattern: ClientBehaviorPattern) -> str:
    return (
        f"Cliente {pattern.email} paga suas contas com atraso médio de "
        f"{pattern.atraso_medio_dias:.1f} dia(s) (mínimo "
        f"{pattern.atraso_min_dias}, máximo {pattern.atraso_max_dias}), "
        f"com base em {pattern.ocorrencias_resolvidas} ciclo(s) pago(s). "
        f"Taxa de pagamento observada: {pattern.taxa_pagamento:.0%}. "
        f"Confiança do padrão: {pattern.confidence:.2f}."
    )


def _pattern_context_data(
    pattern: ClientBehaviorPattern,
) -> dict[str, object]:
    return {
        "email": pattern.email,
        "oldest_account_id": pattern.oldest_account_id,
        "ocorrencias_resolvidas": pattern.ocorrencias_resolvidas,
        "atraso_medio_dias": pattern.atraso_medio_dias,
        "atraso_min_dias": pattern.atraso_min_dias,
        "atraso_max_dias": pattern.atraso_max_dias,
        "taxa_pagamento": pattern.taxa_pagamento,
        "confidence": pattern.confidence,
    }


def _pattern_evidence(
    pattern: ClientBehaviorPattern,
) -> tuple[EvidenceInput, ...]:
    # MemoryService aceita no maximo 20 evidencias por chamada. Nao ha
    # id do AccountEvent no ClientBehaviorCycle (dataclass congelado do
    # incremento 4a), entao o source_reference e derivado de
    # account_id + data resolvida, que identifica o evento de origem
    # de forma equivalente.
    recent_cycles = sorted(
        pattern.cycles,
        key=lambda cycle: cycle.resolved_at,
        reverse=True,
    )[:MAX_EVIDENCE_CYCLES]

    return tuple(
        EvidenceInput(
            relation="supports",
            source_type="database",
            source_reference=(
                f"account_event:account_id={cycle.account_id}:"
                f"new_status=pago:occurred_at="
                f"{cycle.resolved_at.isoformat()}"
            ),
            evidence_text=(
                f"Conta {cycle.account_id}: vencimento "
                f"{cycle.vencimento.isoformat()}, pago em "
                f"{cycle.resolved_at.isoformat()} (atraso de "
                f"{cycle.atraso_dias} dia(s))."
            ),
            observed_at=datetime(
                cycle.resolved_at.year,
                cycle.resolved_at.month,
                cycle.resolved_at.day,
                tzinfo=timezone.utc,
            ),
        )
        for cycle in recent_cycles
    )


def apply_client_behavior_memory_pattern(
    db: Session,
    memory_service: MemoryService,
    email: str,
) -> RememberResult | SupersedeResult | None:
    """
    Aplica (cria ou supersede) a Memoria de comportamento de pagamento
    de um cliente, a partir do padrao computado pelo incremento 4a.

    Retorna None quando nao ha padrao (poucas ocorrencias resolvidas
    -- ver compute_client_behavior_pattern), sem escrever nada.
    """
    pattern = compute_client_behavior_pattern(db, email)

    if pattern is None:
        return None

    evidence = _pattern_evidence(pattern)
    context_data = _pattern_context_data(pattern)

    existing = MemoryRepository(db).find_active_by_key(
        scope_type="account",
        memory_key=MEMORY_KEY,
        account_id=pattern.oldest_account_id,
    )

    if existing is None:
        return memory_service.remember(
            memory_type="observation",
            title=_pattern_title(email),
            content=_pattern_content(pattern),
            scope_type="account",
            account_id=pattern.oldest_account_id,
            source_type="derived",
            source_reference=f"client_behavior:{email}",
            confidence=pattern.confidence,
            memory_key=MEMORY_KEY,
            context_data=context_data,
            evidence=evidence,
        )

    return memory_service.supersede(
        existing.id,
        reason=(
            "Recálculo do padrão de comportamento de pagamento "
            f"após novo evento de pagamento (email: {email})."
        ),
        memory_type="observation",
        title=_pattern_title(email),
        content=_pattern_content(pattern),
        source_type="derived",
        source_reference=f"client_behavior:{email}",
        confidence=pattern.confidence,
        context_data=context_data,
        evidence=evidence,
    )


@dataclass(frozen=True)
class ClientBehaviorMemoryRecalculationSummary:
    candidates: int
    created: int
    superseded: int
    skipped: int


def recalculate_all_client_behavior_patterns(
    db: Session,
    memory_service: MemoryService,
) -> ClientBehaviorMemoryRecalculationSummary:
    """
    Percorre todos os candidatos a recalculo (incremento 4a) e aplica
    a Memoria de comportamento de cada um (incremento 4b).

    Nao possui wiring em main.py/scheduler -- isso fica para o
    incremento 4c.
    """
    emails = list_client_behavior_recalculation_candidate_emails(db)

    created = 0
    superseded = 0
    skipped = 0

    for email in emails:
        try:
            result = apply_client_behavior_memory_pattern(
                db, memory_service, email
            )
        except Exception as error:
            db.rollback()
            skipped += 1
            logger.warning(
                "client_behavior_memory_recalculation_failed",
                extra={
                    "event": (
                        "client_behavior_memory."
                        "recalculation_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
            continue

        if result is None:
            skipped += 1
        elif isinstance(result, RememberResult):
            if result.created:
                created += 1
            else:
                skipped += 1
        else:
            superseded += 1

    return ClientBehaviorMemoryRecalculationSummary(
        candidates=len(emails),
        created=created,
        superseded=superseded,
        skipped=skipped,
    )


def run_client_behavior_memory_recalculation() -> (
    ClientBehaviorMemoryRecalculationSummary
):
    """
    Abre sua propria sessao de banco e executa um ciclo completo de
    recalculo da Memoria de comportamento de pagamento (incremento
    4b). Ponto de entrada sincrono usado pelo wrapper assincrono e
    pela recuperacao de inicializacao, no mesmo padrao ja usado em
    app/core/pilot_mutation_maintenance.py.
    """
    with SessionLocal() as db:
        memory_service = MemoryService(db)
        return recalculate_all_client_behavior_patterns(
            db, memory_service
        )


async def run_client_behavior_memory_recalculation_async() -> (
    ClientBehaviorMemoryRecalculationSummary
):
    return await asyncio.to_thread(
        run_client_behavior_memory_recalculation
    )


async def client_behavior_memory_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings.client_behavior_recalculation_interval_seconds
        )
        await run_client_behavior_memory_recalculation_async()
