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
from app.core.receivable_lifecycle import business_today
from app.database.database import SessionLocal
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.account_payment import AccountPayment
from app.repositories.memory_repository import MemoryRepository
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService
from app.services.memory_service import RememberResult
from app.services.memory_service import SupersedeResult


logger = logging.getLogger("auneron.client_behavior_memory")

MEMORY_KEY = "client_behavior_payment_pattern"
MAX_EVIDENCE_CYCLES = 20
# Marcador de versao da taxa: denominador = titulos ja vencidos.
TAXA_BASE_VENCIDOS = "vencidos"


# DATA OFICIAL de pagamento = account_payments.paid_on (data efetiva do
# pagamento registrado). O evento 'pago' de account_events registra
# quando o Auneron executou o lancamento e serve so para auditoria; aqui
# ele continua sendo usado apenas como GATILHO ("houve pagamento novo"),
# nunca como data de negocio. Candidato = e-mail com pagamento
# registrado (evento 'pago' ou linha em account_payments) posterior a
# ultima memoria ativa.
#
# Memorias antigas, gravadas quando a fonte era a data do evento, NAO
# sao recalculadas por migracao nem por marcador: sao recalculadas no
# PROXIMO pagamento do cliente. (Um gatilho por marcador ausente poderia
# ficar em loop aqui, pois apply_client_behavior_memory_pattern nao
# grava nada quando ha menos ocorrencias que o minimo.) A classificacao,
# que sempre faz supersede, usa o marcador -- ver client_classification.
CANDIDATE_EMAILS_SQL = text(
    """
    SELECT DISTINCT p.email
    FROM (
        SELECT a.email AS email, ae.occurred_at AS registered_at
        FROM account_events ae
        JOIN accounts a ON a.id = ae.account_id
        WHERE ae.new_status = 'pago'
          AND a.email IS NOT NULL
        UNION ALL
        SELECT a.email AS email, ap.created_at AS registered_at
        FROM account_payments ap
        JOIN accounts a ON a.id = ap.account_id
        WHERE a.email IS NOT NULL
    ) p
    WHERE p.registered_at > COALESCE(
        (
            SELECT MAX(mi.created_at)
            FROM memory_items mi
            JOIN accounts a2 ON mi.account_id = a2.id
            WHERE a2.email = p.email
              AND mi.memory_type = 'observation'
              AND mi.status = 'active'
        ),
        '-infinity'
      )
    ORDER BY p.email
    """
)


@dataclass(frozen=True)
class ClientBehaviorCycle:
    account_id: int
    vencimento: date
    resolved_at: date
    atraso_dias: int
    # resolved_at e a data efetiva do pagamento (account_payments.paid_on).
    # "evento_estimado" nao e produzido por padrao: so existiria se um
    # indicador optasse explicitamente por estimar com o evento.
    data_pagamento_origem: str = "efetiva"


@dataclass(frozen=True)
class ClientBehaviorPattern:
    email: str
    oldest_account_id: int
    ocorrencias_resolvidas: int
    atraso_medio_dias: float
    atraso_min_dias: int
    atraso_max_dias: int
    taxa_pagamento: float | None
    confidence: float
    cycles: tuple[ClientBehaviorCycle, ...]
    # Titulos pagos (evento 'pago') sem data efetiva registrada: ficam
    # fora de atraso/pontualidade e sao contados aqui, sem estimativa.
    ocorrencias_sem_data_efetiva: int = 0
    # Titulos NAO pagos e ainda nao vencidos: ficam fora do denominador
    # da taxa e sao contados a parte.
    titulos_a_vencer: int = 0


def list_client_behavior_recalculation_candidate_emails(
    db: Session,
) -> list[str]:
    return list(
        db.execute(CANDIDATE_EMAILS_SQL).scalars().all()
    )


def list_effective_payment_cycles(
    db: Session,
    accounts: list[Account],
) -> tuple[list[ClientBehaviorCycle], int]:
    cycles, sem_data_ids = _payment_cycles_and_undated_ids(db, accounts)
    return cycles, len(sem_data_ids)


def _payment_cycles_and_undated_ids(
    db: Session,
    accounts: list[Account],
) -> tuple[list[ClientBehaviorCycle], set[int]]:
    """
    Ciclos resolvidos pela DATA OFICIAL de pagamento
    (account_payments.paid_on) e contagem de titulos pagos sem data
    efetiva. Titulo com evento 'pago' mas sem account_payments (anterior
    ao registro de paid_on) NAO vira ciclo -- nao e estimado pela data
    do evento; so e contado em ocorrencias_sem_data_efetiva.
    """
    if not accounts:
        return [], set()

    ids = [account.id for account in accounts]

    paid_on_by_account = {
        row.account_id: row.paid_on
        for row in db.execute(
            select(
                AccountPayment.account_id,
                AccountPayment.paid_on,
            ).where(AccountPayment.account_id.in_(ids))
        ).all()
    }
    accounts_with_paid_event = set(
        db.execute(
            select(AccountEvent.account_id)
            .where(
                AccountEvent.account_id.in_(ids),
                AccountEvent.new_status == "pago",
            )
            .distinct()
        )
        .scalars()
        .all()
    )

    cycles: list[ClientBehaviorCycle] = []
    sem_data_ids: set[int] = set()

    for account in accounts:
        paid_on = paid_on_by_account.get(account.id)

        if paid_on is None:
            if account.id in accounts_with_paid_event:
                sem_data_ids.add(account.id)
            continue

        cycles.append(
            ClientBehaviorCycle(
                account_id=account.id,
                vencimento=account.vencimento,
                resolved_at=paid_on,
                atraso_dias=(paid_on - account.vencimento).days,
            )
        )

    return cycles, sem_data_ids


def compute_client_behavior_pattern(
    db: Session,
    email: str,
    today: date | None = None,
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

    today = today or business_today()
    cycles, sem_data_ids = _payment_cycles_and_undated_ids(db, accounts)
    ocorrencias_sem_data_efetiva = len(sem_data_ids)

    ocorrencias_resolvidas = len(cycles)

    if ocorrencias_resolvidas == 0 or (
        ocorrencias_resolvidas
        < settings.client_behavior_min_occurrences_for_pattern
    ):
        return None

    atrasos = [cycle.atraso_dias for cycle in cycles]
    atraso_medio_dias = sum(atrasos) / ocorrencias_resolvidas
    atraso_min_dias = min(atrasos)
    atraso_max_dias = max(atrasos)
    # Taxa de pagamento = pagos / (vencidos + pagos). Denominador: todo
    # titulo ja vencido (vencimento <= hoje, pago ou nao) mais o pago
    # antes do vencimento (resultado ja conhecido). Titulo nao pago e
    # ainda nao vencido fica fora e vira titulos_a_vencer. A taxa nao
    # depende da data efetiva: pago sem data continua sendo pago.
    paid_ids = {cycle.account_id for cycle in cycles} | sem_data_ids
    titulos_a_vencer = sum(
        1
        for account in accounts
        if account.id not in paid_ids and account.vencimento > today
    )
    denominador = len(accounts) - titulos_a_vencer
    taxa_pagamento = (
        len(paid_ids) / denominador if denominador else None
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
        ocorrencias_sem_data_efetiva=ocorrencias_sem_data_efetiva,
        titulos_a_vencer=titulos_a_vencer,
    )


def _pattern_title(email: str) -> str:
    return f"Padrão de pagamento — {email}"


def _pattern_content(pattern: ClientBehaviorPattern) -> str:
    sem_data = (
        f" {pattern.ocorrencias_sem_data_efetiva} título(s) pago(s) "
        "sem data efetiva registrada ficaram fora do cálculo."
        if pattern.ocorrencias_sem_data_efetiva
        else ""
    )
    if pattern.taxa_pagamento is None:
        taxa = "Taxa de pagamento: sem títulos vencidos. "
    else:
        taxa = (
            "Taxa de pagamento observada (títulos vencidos): "
            f"{pattern.taxa_pagamento:.0%}. "
        )
    a_vencer = (
        f" {pattern.titulos_a_vencer} título(s) a vencer ficaram fora "
        "da taxa."
        if pattern.titulos_a_vencer
        else ""
    )
    return (
        f"Cliente {pattern.email} paga suas contas com atraso médio de "
        f"{pattern.atraso_medio_dias:.1f} dia(s) (mínimo "
        f"{pattern.atraso_min_dias}, máximo {pattern.atraso_max_dias}), "
        f"com base em {pattern.ocorrencias_resolvidas} ciclo(s) pago(s) "
        "com data efetiva de pagamento. "
        + taxa
        + f"Confiança do padrão: {pattern.confidence:.2f}."
        + sem_data
        + a_vencer
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
        "ocorrencias_sem_data_efetiva": (
            pattern.ocorrencias_sem_data_efetiva
        ),
        "titulos_a_vencer": pattern.titulos_a_vencer,
        "taxa_base": TAXA_BASE_VENCIDOS,
        "fonte_data_pagamento": "account_payments.paid_on",
    }


def _pattern_evidence(
    pattern: ClientBehaviorPattern,
) -> tuple[EvidenceInput, ...]:
    # MemoryService aceita no maximo 20 evidencias por chamada. A
    # evidencia aponta para a data efetiva do pagamento
    # (account_payments.paid_on), derivada de account_id + data.
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
                f"account_payment:account_id={cycle.account_id}:"
                f"paid_on={cycle.resolved_at.isoformat()}"
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
        try:
            await run_client_behavior_memory_recalculation_async()
        except Exception as error:
            logger.exception(
                "client_behavior_memory_maintenance_failed",
                extra={
                    "event": (
                        "client_behavior_memory.maintenance_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
