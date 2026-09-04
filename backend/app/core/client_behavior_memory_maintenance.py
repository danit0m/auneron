from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account
from app.models.account_event import AccountEvent


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
