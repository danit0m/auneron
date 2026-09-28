"""
VALUE-1.2 V1 -- Governed Operations Summary.

Painel puramente projetivo sobre dado ja persistido pelos corredores
governados de account.mark_overdue (humano e policy-autonomo, DW-7) --
nao cria nenhuma autoridade, execucao ou tabela nova.

KPI-1 (eligible_accounts_identified): contas cujo vencimento cai no
periodo E que nao estavam pagas ate o fim do dia de vencimento na
timezone de negocio (settings.business_timezone, o mesmo valor usado
por app.core.receivable_lifecycle.business_today() -- nao introduz uma
segunda definicao de "hoje"). Account.status nao participa desta
contagem: uma conta paga depois do vencimento continua contando (ver
VALUE-1.2B, achado real com a Account 2). Conhecido: usa
Account.vencimento corrente; se vencimento for alterado apos o
episodio, KPI-1 historico de periodos passados pode exigir
reconstrucao via account_vencimento_changes (nao implementada nesta
V1 -- 0 edicoes existem no dado real ate o momento do design freeze).

KPI-2/KPI-3 (autonomous_dispositions / human_governed_dispositions):
classificados pelo prefixo de AccountEvent.idempotency_key -- nunca
reconciliando PolicyAuthorityConsumption/ApprovalConsumption entre
corredores. Exclui deliberadamente: PUT administrativo (idempotency_key
NULL), account.mark_paid (prefixo distinto) e o corredor agente legado
dormant (prefixo distinto).

KPI-4 (autonomous_effect_verification): PolicyAuthorityConsumption
LEFT JOIN BusinessEffectVerification, restrito a
PolicyAuthorityGrant.skill_key = 'account.mark_overdue' (torna a
restricao de dominio explicita em vez de implicita).
KPI-4.total == KPI-2 e uma integrity assertion -- divergencia gera
apenas um warning estruturado, nunca altera o contrato HTTP publico.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy import Date
from sqlalchemy import cast
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.receivable_lifecycle import business_today
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.business_effect_verification import (
    BusinessEffectVerification,
)
from app.models.policy_authority_consumption import (
    PolicyAuthorityConsumption,
)
from app.models.policy_authority_grant import PolicyAuthorityGrant


logger = logging.getLogger("auneron.governed_operations_summary")


AUTONOMOUS_IDEMPOTENCY_PREFIX = (
    "account_event:effect:policy_account_mark_overdue:"
)
HUMAN_IDEMPOTENCY_PREFIX = (
    "account_event:effect:human_account_mark_overdue:approval:"
)

GOVERNED_MARK_OVERDUE_SKILL_KEY = "account.mark_overdue"


@dataclass(frozen=True)
class GovernedOperationsSummaryResult:
    period_start: datetime
    period_end: datetime
    period_days: int
    eligible_accounts_identified: int
    autonomous_dispositions: int
    human_governed_dispositions: int
    autonomous_disposition_rate: float | None
    verification_verified: int
    verification_checked_other: int
    verification_not_yet_checked: int
    verification_total: int
    verification_rate: float | None
    pending_overdue_accounts_now: int
    estimate: None = None


class GovernedOperationsSummaryService:
    def __init__(self, db: Session):
        self.db = db

    def compute(
        self,
        *,
        period_days: int,
        now: datetime | None = None,
        today: date | None = None,
    ) -> GovernedOperationsSummaryResult:
        period_end = now if now is not None else datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=period_days)

        today = today if today is not None else business_today()
        vencimento_end = today
        vencimento_start = today - timedelta(days=period_days)

        eligible_accounts_identified = self._compute_kpi1(
            vencimento_start=vencimento_start,
            vencimento_end=vencimento_end,
        )

        autonomous, human = self._compute_kpi2_kpi3(
            period_start=period_start,
            period_end=period_end,
        )

        (
            verified,
            checked_other,
            not_yet_checked,
            verification_total,
        ) = self._compute_kpi4(
            period_start=period_start,
            period_end=period_end,
        )

        if verification_total != autonomous:
            logger.warning(
                "governed_operations_summary.kpi_mismatch",
                extra={
                    "autonomous_dispositions": autonomous,
                    "verification_total": verification_total,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                },
            )

        disposition_denominator = autonomous + human
        autonomous_disposition_rate = (
            autonomous / disposition_denominator
            if disposition_denominator
            else None
        )

        verification_rate = (
            verified / verification_total
            if verification_total
            else None
        )

        pending_overdue_accounts_now = (
            self._compute_pending_overdue_accounts_now(today=today)
        )

        return GovernedOperationsSummaryResult(
            period_start=period_start,
            period_end=period_end,
            period_days=period_days,
            eligible_accounts_identified=eligible_accounts_identified,
            autonomous_dispositions=autonomous,
            human_governed_dispositions=human,
            autonomous_disposition_rate=autonomous_disposition_rate,
            verification_verified=verified,
            verification_checked_other=checked_other,
            verification_not_yet_checked=not_yet_checked,
            verification_total=verification_total,
            verification_rate=verification_rate,
            pending_overdue_accounts_now=pending_overdue_accounts_now,
        )

    def _compute_kpi1(
        self,
        *,
        vencimento_start: date,
        vencimento_end: date,
    ) -> int:
        first_payment = (
            self.db.query(
                AccountEvent.account_id.label("account_id"),
                func.min(AccountEvent.occurred_at).label(
                    "first_paid_at"
                ),
            )
            .filter(AccountEvent.new_status == "pago")
            .group_by(AccountEvent.account_id)
            .subquery()
        )

        first_paid_business_date = cast(
            func.timezone(
                settings.business_timezone,
                first_payment.c.first_paid_at,
            ),
            Date,
        )

        count = (
            self.db.query(func.count(Account.id))
            .outerjoin(
                first_payment,
                first_payment.c.account_id == Account.id,
            )
            .filter(
                Account.vencimento >= vencimento_start,
                Account.vencimento < vencimento_end,
            )
            .filter(
                or_(
                    first_payment.c.first_paid_at.is_(None),
                    first_paid_business_date > Account.vencimento,
                )
            )
            .scalar()
        )

        return count or 0

    def _compute_kpi2_kpi3(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> tuple[int, int]:
        row = (
            self.db.query(
                func.count()
                .filter(
                    AccountEvent.idempotency_key.like(
                        f"{AUTONOMOUS_IDEMPOTENCY_PREFIX}%"
                    )
                )
                .label("autonomous"),
                func.count()
                .filter(
                    AccountEvent.idempotency_key.like(
                        f"{HUMAN_IDEMPOTENCY_PREFIX}%"
                    )
                )
                .label("human"),
            )
            .filter(AccountEvent.new_status == "atrasado")
            .filter(
                AccountEvent.occurred_at >= period_start,
                AccountEvent.occurred_at < period_end,
            )
            .one()
        )

        return (row.autonomous or 0, row.human or 0)

    def _compute_kpi4(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> tuple[int, int, int, int]:
        row = (
            self.db.query(
                func.count()
                .filter(
                    BusinessEffectVerification.result == "verified"
                )
                .label("verified"),
                func.count()
                .filter(
                    BusinessEffectVerification.result.in_(
                        ["contradicted", "unverifiable"]
                    )
                )
                .label("checked_other"),
                func.count()
                .filter(BusinessEffectVerification.id.is_(None))
                .label("not_yet_checked"),
                func.count().label("total"),
            )
            .select_from(PolicyAuthorityConsumption)
            .join(
                PolicyAuthorityGrant,
                PolicyAuthorityGrant.id
                == PolicyAuthorityConsumption.policy_authority_grant_id,
            )
            .outerjoin(
                BusinessEffectVerification,
                BusinessEffectVerification.policy_authority_consumption_id
                == PolicyAuthorityConsumption.id,
            )
            .filter(
                PolicyAuthorityGrant.skill_key
                == GOVERNED_MARK_OVERDUE_SKILL_KEY
            )
            .filter(
                PolicyAuthorityConsumption.consumed_at >= period_start,
                PolicyAuthorityConsumption.consumed_at < period_end,
            )
            .one()
        )

        return (
            row.verified or 0,
            row.checked_other or 0,
            row.not_yet_checked or 0,
            row.total or 0,
        )

    def _compute_pending_overdue_accounts_now(
        self,
        *,
        today: date,
    ) -> int:
        count = (
            self.db.query(func.count(Account.id))
            .filter(
                Account.status == "aberto",
                Account.vencimento < today,
            )
            .scalar()
        )

        return count or 0
