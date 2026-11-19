"""
Fatia 2A -- Entendimento (classificacao de cliente).

Classifica o padrao de comportamento de pagamento (calculado pela
Fatia 1, app/core/client_behavior_memory_maintenance.py) em uma de tres
categorias: PAGAMENTO_REGULAR, ATRASO_RECORRENTE ou INSUFFICIENT_DATA.

Regra V1 (congelada em design freeze com Tomaz em 09/09/2026):

    ocorrencias_resolvidas < 3
        -> INSUFFICIENT_DATA (persistida explicitamente, com a
           contagem disponivel e reason=resolved_occurrences_below_minimum)
    ocorrencias_resolvidas >= 3 e proporcao_atraso >= limiar (0.50)
        -> ATRASO_RECORRENTE
    ocorrencias_resolvidas >= 3 e proporcao_atraso < limiar
        -> PAGAMENTO_REGULAR

Sem peso por gravidade do atraso (1 dia e 30 dias contam igual) e sem
janela temporal (usa todo o historico resolvido disponivel por email,
igual a Fatia 1) nesta versao.

Puramente advisory -- nao bloqueia cliente, nao altera limite, nao
dispara cobranca. So informa; qualquer efeito real continua passando
pelas camadas de aprovacao/governanca ja existentes.

Reaproveita por importacao apenas compute_client_behavior_pattern.
A consulta de candidatos e propria desta fatia
(CLASSIFICATION_CANDIDATE_EMAILS_SQL), porque a consulta da Fatia 1
filtra por memory_type='observation' e nunca reconheceria uma memoria
de classificacao (memory_type='decision'). O builder de ciclos
resolvidos (_list_resolved_cycles, via list_effective_payment_cycles), usado somente quando o padrao da Fatia 1
retorna None (menos de 3 ocorrencias resolvidas) -- a Fatia 1 descarta
esses ciclos ao retornar None, mas o caso INSUFFICIENT_DATA desta
fatia precisa deles para ficar auditavel (evidencia real, nao so uma
contagem).

FONTE DA DATA (decisao de Daniel): a data oficial de pagamento e a
data efetiva registrada (account_payments.paid_on), nao a data do
evento 'pago' (account_events.occurred_at, so auditoria). Titulos
pagos sem data efetiva ficam fora de atraso/proporcao e sao contados em
ocorrencias_sem_data_efetiva (sem estimativa). A regra recurrence_v1 em
si nao mudou. (O antigo bugfix de fuso sobre occurred_at deixa de se
aplicar: paid_on e uma data de calendario.)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.client_behavior_memory_maintenance import (
    ClientBehaviorCycle,
)
from app.core.client_behavior_memory_maintenance import (
    compute_client_behavior_pattern,
)
from app.core.client_behavior_memory_maintenance import (
    list_effective_payment_cycles,
)
from app.core.config import settings
from app.database.database import SessionLocal
from app.models.account import Account
from app.repositories.memory_repository import MemoryRepository
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService
from app.services.memory_service import RememberResult
from app.services.memory_service import SupersedeResult


logger = logging.getLogger("auneron.client_classification")

MEMORY_KEY = "client_classification_v1"
RULE_VERSION = "recurrence_v1"
ANALYSIS_SCOPE = "all_available_history"
MAX_EVIDENCE_CYCLES = 20

LABEL_PAGAMENTO_REGULAR = "PAGAMENTO_REGULAR"
LABEL_ATRASO_RECORRENTE = "ATRASO_RECORRENTE"
LABEL_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

REASON_BELOW_MINIMUM = "resolved_occurrences_below_minimum"

# Consulta de candidatos propria da Fatia 2A. Nao reaproveita
# CANDIDATE_EMAILS_SQL (Fatia 1) porque aquela consulta filtra por
# memory_type='observation' -- a memoria que a propria Fatia 1 grava.
# Como esta fatia grava memory_type='decision' (memory_key=MEMORY_KEY),
# reusar a consulta da Fatia 1 nunca encontraria a memoria de
# classificacao ja criada, e todo cliente ja classificado voltaria a
# aparecer como candidato em todo ciclo de recalculo, gerando supersede
# desnecessario e infinito. Corrigido apos falha detectada em
# test_recalculate_all_client_classifications_summary (09/09/2026).
#
# DATA OFICIAL de pagamento = account_payments.paid_on; o evento 'pago'
# e so gatilho (ver client_behavior_memory_maintenance). Candidato
# tambem quando a classificacao ativa foi gravada antes da fonte oficial
# (context_data sem 'ocorrencias_sem_data_efetiva'): recalcula uma vez.
CLASSIFICATION_CANDIDATE_EMAILS_SQL = text(
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
              AND mi.memory_type = 'decision'
              AND mi.memory_key = 'client_classification_v1'
              AND mi.status = 'active'
        ),
        '-infinity'
      )
      OR EXISTS (
        SELECT 1
        FROM memory_items mi
        JOIN accounts a3 ON mi.account_id = a3.id
        WHERE a3.email = p.email
          AND mi.memory_type = 'decision'
          AND mi.memory_key = 'client_classification_v1'
          AND mi.status = 'active'
          AND (mi.context_data ->> 'ocorrencias_sem_data_efetiva')
              IS NULL
      )
    ORDER BY p.email
    """
)


def list_client_classification_recalculation_candidate_emails(
    db: Session,
) -> list[str]:
    return list(
        db.execute(CLASSIFICATION_CANDIDATE_EMAILS_SQL).scalars().all()
    )


@dataclass(frozen=True)
class ClientClassification:
    email: str
    oldest_account_id: int
    label: str
    ocorrencias_resolvidas: int
    ciclos_em_atraso: int | None
    proporcao_atraso: float | None
    confidence: float
    reason: str | None
    period_start: date | None
    period_end: date | None
    cycles: tuple[ClientBehaviorCycle, ...]
    ocorrencias_sem_data_efetiva: int = 0


def _oldest_account_id(
    db: Session,
    email: str,
) -> int | None:
    return (
        db.execute(
            select(Account.id)
            .where(Account.email == email)
            .order_by(Account.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _list_resolved_cycles(
    db: Session,
    email: str,
) -> tuple[tuple[ClientBehaviorCycle, ...], int]:
    """
    Lista os ciclos resolvidos de um email pela DATA OFICIAL de
    pagamento (account_payments.paid_on), sem aplicar o limiar minimo
    da Fatia 1 (settings.client_behavior_min_occurrences_for_pattern),
    e a contagem de titulos pagos sem data efetiva (fora dos ciclos,
    sem estimativa). Usado somente para evidencia do caso
    INSUFFICIENT_DATA -- a Fatia 1 descarta esses ciclos ao retornar
    None abaixo do minimo.
    """
    accounts = (
        db.execute(
            select(Account)
            .where(Account.email == email)
            .order_by(Account.id.asc())
        )
        .scalars()
        .all()
    )

    cycles, sem_data_efetiva = list_effective_payment_cycles(
        db, list(accounts)
    )

    return tuple(cycles), sem_data_efetiva


def compute_client_classification(
    db: Session,
    email: str,
) -> ClientClassification | None:
    """
    Classifica um cliente (por email). Retorna None somente quando nao
    ha nenhuma conta ou nenhum ciclo resolvido para esse email -- guarda
    de seguranca, nao deveria ocorrer para um candidato real, ja que
    list_client_classification_recalculation_candidate_emails exige ao
    menos um evento 'pago'.
    """
    oldest_account_id = _oldest_account_id(db, email)

    if oldest_account_id is None:
        return None

    pattern = compute_client_behavior_pattern(db, email)

    if pattern is None:
        cycles, sem_data_efetiva = _list_resolved_cycles(db, email)

        if not cycles and not sem_data_efetiva:
            return None

        resolved_dates = [cycle.resolved_at for cycle in cycles]

        return ClientClassification(
            email=email,
            oldest_account_id=oldest_account_id,
            label=LABEL_INSUFFICIENT_DATA,
            ocorrencias_resolvidas=len(cycles),
            ciclos_em_atraso=None,
            proporcao_atraso=None,
            confidence=0.0,
            reason=REASON_BELOW_MINIMUM,
            period_start=(
                min(resolved_dates) if resolved_dates else None
            ),
            period_end=(
                max(resolved_dates) if resolved_dates else None
            ),
            cycles=cycles,
            ocorrencias_sem_data_efetiva=sem_data_efetiva,
        )

    ciclos_em_atraso = sum(
        1 for cycle in pattern.cycles if cycle.atraso_dias > 0
    )
    proporcao_atraso = (
        ciclos_em_atraso / pattern.ocorrencias_resolvidas
    )
    label = (
        LABEL_ATRASO_RECORRENTE
        if proporcao_atraso
        >= settings.client_classification_atraso_threshold
        else LABEL_PAGAMENTO_REGULAR
    )
    resolved_dates = [
        cycle.resolved_at for cycle in pattern.cycles
    ]

    return ClientClassification(
        email=email,
        oldest_account_id=oldest_account_id,
        label=label,
        ocorrencias_resolvidas=pattern.ocorrencias_resolvidas,
        ciclos_em_atraso=ciclos_em_atraso,
        proporcao_atraso=proporcao_atraso,
        confidence=pattern.confidence,
        reason=None,
        period_start=min(resolved_dates),
        period_end=max(resolved_dates),
        cycles=pattern.cycles,
        ocorrencias_sem_data_efetiva=(
            pattern.ocorrencias_sem_data_efetiva
        ),
    )


def _classification_title(email: str) -> str:
    return f"Classificação de cliente — {email}"


def _classification_content(
    classification: ClientClassification,
) -> str:
    sem_data = (
        f" {classification.ocorrencias_sem_data_efetiva} título(s) "
        "pago(s) sem data efetiva registrada ficaram fora do cálculo."
        if classification.ocorrencias_sem_data_efetiva
        else ""
    )

    if classification.label == LABEL_INSUFFICIENT_DATA:
        return (
            f"Cliente {classification.email}: dados insuficientes "
            "para classificar padrão de pagamento "
            f"({classification.ocorrencias_resolvidas} ciclo(s) "
            "resolvido(s), mínimo exigido "
            f"{settings.client_behavior_min_occurrences_for_pattern})."
            + sem_data
        )

    return (
        f"Cliente {classification.email} classificado como "
        f"{classification.label}: {classification.ciclos_em_atraso} "
        f"de {classification.ocorrencias_resolvidas} ciclo(s) "
        "pago(s) tiveram atraso (proporção "
        f"{classification.proporcao_atraso:.0%}). Critério: "
        f"{RULE_VERSION} (limiar "
        f"{settings.client_classification_atraso_threshold:.0%})."
        + sem_data
    )


def _classification_context_data(
    classification: ClientClassification,
) -> dict[str, object]:
    return {
        "email": classification.email,
        "oldest_account_id": classification.oldest_account_id,
        "label": classification.label,
        "ocorrencias_resolvidas": (
            classification.ocorrencias_resolvidas
        ),
        "ciclos_em_atraso": classification.ciclos_em_atraso,
        "proporcao_atraso": classification.proporcao_atraso,
        "reason": classification.reason,
        "rule_version": RULE_VERSION,
        "analysis_scope": ANALYSIS_SCOPE,
        "ocorrencias_sem_data_efetiva": (
            classification.ocorrencias_sem_data_efetiva
        ),
        "fonte_data_pagamento": "account_payments.paid_on",
        "period_start": (
            classification.period_start.isoformat()
            if classification.period_start is not None
            else None
        ),
        "period_end": (
            classification.period_end.isoformat()
            if classification.period_end is not None
            else None
        ),
    }


def _classification_evidence(
    classification: ClientClassification,
) -> tuple[EvidenceInput, ...]:
    recent_cycles = sorted(
        classification.cycles,
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


def apply_client_classification(
    db: Session,
    memory_service: MemoryService,
    email: str,
) -> RememberResult | SupersedeResult | None:
    """
    Aplica (cria ou supersede) a Memoria de classificacao de um
    cliente. Retorna None somente na guarda de seguranca de
    compute_client_classification (sem nenhuma conta/ciclo para o
    email) -- na pratica, nao deveria ocorrer para um candidato real.
    """
    classification = compute_client_classification(db, email)

    if classification is None:
        return None

    evidence = _classification_evidence(classification)
    context_data = _classification_context_data(classification)
    confidence = Decimal(str(classification.confidence))

    existing = MemoryRepository(db).find_active_by_key(
        scope_type="account",
        memory_key=MEMORY_KEY,
        account_id=classification.oldest_account_id,
    )

    if existing is None:
        return memory_service.remember(
            memory_type="decision",
            title=_classification_title(email),
            content=_classification_content(classification),
            scope_type="account",
            account_id=classification.oldest_account_id,
            source_type="derived",
            source_reference=f"client_classification:{email}",
            confidence=confidence,
            memory_key=MEMORY_KEY,
            context_data=context_data,
            evidence=evidence,
        )

    return memory_service.supersede(
        existing.id,
        reason=(
            "Reclassificação após novo evento de pagamento "
            f"(email: {email})."
        ),
        memory_type="decision",
        title=_classification_title(email),
        content=_classification_content(classification),
        source_type="derived",
        source_reference=f"client_classification:{email}",
        confidence=confidence,
        context_data=context_data,
        evidence=evidence,
    )


@dataclass(frozen=True)
class ClientClassificationRecalculationSummary:
    candidates: int
    created: int
    superseded: int
    skipped: int


def recalculate_all_client_classifications(
    db: Session,
    memory_service: MemoryService,
) -> ClientClassificationRecalculationSummary:
    """
    Percorre os candidatos proprios da Fatia 2A: e-mails com pagamento
    novo desde a ultima memoria de classificacao ativa (memory_type=
    'decision', memory_key=MEMORY_KEY) e aplica a classificacao de
    cada um. Usa CLASSIFICATION_CANDIDATE_EMAILS_SQL em vez da consulta
    de candidatos da Fatia 1 (que filtra por memory_type='observation'
    e nunca reconheceria uma memoria ja classificada por esta fatia).
    """
    emails = (
        list_client_classification_recalculation_candidate_emails(db)
    )

    created = 0
    superseded = 0
    skipped = 0

    for email in emails:
        try:
            result = apply_client_classification(
                db, memory_service, email
            )
        except Exception as error:
            db.rollback()
            skipped += 1
            logger.warning(
                "client_classification_recalculation_failed",
                extra={
                    "event": (
                        "client_classification."
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

    return ClientClassificationRecalculationSummary(
        candidates=len(emails),
        created=created,
        superseded=superseded,
        skipped=skipped,
    )


def run_client_classification_recalculation() -> (
    ClientClassificationRecalculationSummary
):
    """
    Abre sua propria sessao de banco e executa um ciclo completo de
    recalculo de classificacao, no mesmo padrao ja usado em
    app/core/client_behavior_memory_maintenance.py.
    """
    with SessionLocal() as db:
        memory_service = MemoryService(db)
        return recalculate_all_client_classifications(
            db, memory_service
        )


async def run_client_classification_recalculation_async() -> (
    ClientClassificationRecalculationSummary
):
    return await asyncio.to_thread(
        run_client_classification_recalculation
    )


async def client_classification_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings
            .client_classification_recalculation_interval_seconds
        )
        try:
            await run_client_classification_recalculation_async()
        except Exception as error:
            logger.exception(
                "client_classification_maintenance_failed",
                extra={
                    "event": (
                        "client_classification.maintenance_failed"
                    ),
                    "error_type": type(error).__name__,
                },
            )
