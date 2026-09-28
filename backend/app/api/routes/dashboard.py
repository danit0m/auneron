from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle
from app.database.database import get_db
from app.models.account import Account
from app.schemas.governed_operations_summary import (
    AutonomousEffectVerificationSummary,
    GovernedOperationsSummaryPeriod,
    GovernedOperationsSummaryResponse,
)
from app.services.governed_operations_summary_service import (
    GovernedOperationsSummaryService,
)

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)


@router.get("/")
def dashboard(db: Session = Depends(get_db)):

    total_clientes = db.query(Account).count()

    faturamento_total = (
        db.query(func.sum(Account.valor))
        .scalar() or 0
    )

    recebido = (
        db.query(func.sum(Account.valor))
        .filter(func.lower(Account.status) == "pago")
        .scalar() or 0
    )

    pendente = (
        db.query(func.sum(Account.valor))
        .filter(func.lower(Account.status) != "pago")
        .scalar() or 0
    )

    # F3 -- situacao operacional (canonica) substitui os hardcodes de
    # status == "atrasado" abaixo. Ver app/core/receivable_lifecycle.py.
    hoje = business_today()

    contas_atrasadas = [
        conta
        for conta in db.query(Account).all()
        if evaluate_receivable_lifecycle(
            financial_status=conta.status,
            vencimento=conta.vencimento,
            today=hoje,
        ).state
        in ("overdue", "overdue_alert")
    ]

    atrasado = sum(
        (conta.valor for conta in contas_atrasadas),
        Decimal("0"),
    )

    taxa_recebimento = (
        (recebido / faturamento_total) * 100
        if faturamento_total else 0
    )

    ticket_medio = (
        faturamento_total / total_clientes
        if total_clientes else 0
    )

    clientes_atrasados = len(contas_atrasadas)

    pagos = (
        db.query(Account)
        .filter(func.lower(Account.status) == "pago")
        .count()
    )

    abertos = (
        db.query(Account)
        .filter(func.lower(Account.status) == "aberto")
        .count()
    )

    atrasados = len(contas_atrasadas)

    maiores_clientes = (
        db.query(Account)
        .order_by(Account.valor.desc())
        .limit(5)
        .all()
    )

    ranking = [
        {
            "cliente": c.cliente,
            "valor": c.valor,
            "status": c.status
        }
        for c in maiores_clientes
    ]

    lista_vencimentos = (
        db.query(Account)
        .order_by(Account.vencimento.asc())
        .limit(10)
        .all()
    )

    vencimentos = [
        {
            "cliente": c.cliente,
            "valor": c.valor,
            "vencimento": c.vencimento,
            "status": c.status
        }
        for c in lista_vencimentos
    ]

    lista_alertas = contas_atrasadas[:5]

    alertas = [
        {
            "cliente": c.cliente,
            "mensagem": "Pagamento atrasado",
            "valor": c.valor
        }
        for c in lista_alertas
    ]

    return {

        "resumo": {
            "clientes_total": total_clientes,
            "faturamento_total": round(faturamento_total, 2),
            "recebido": round(recebido, 2),
            "pendente": round(pendente, 2),
            "atrasado": round(atrasado, 2)
        },

        "indicadores": {
            "taxa_recebimento": f"{taxa_recebimento:.2f}%",
            "ticket_medio": round(ticket_medio, 2),
            "clientes_atrasados": clientes_atrasados
        },

        "status_clientes": {
            "pago": pagos,
            "aberto": abertos,
            "atrasado": atrasados
        },

        "ranking_clientes": ranking,

        "alertas": alertas,

        "vencimentos": vencimentos
    }


@router.get(
    "/governed-operations-summary",
    response_model=GovernedOperationsSummaryResponse,
)
def governed_operations_summary(
    period_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> GovernedOperationsSummaryResponse:
    result = GovernedOperationsSummaryService(db).compute(
        period_days=period_days,
    )

    return GovernedOperationsSummaryResponse(
        period=GovernedOperationsSummaryPeriod(
            start=result.period_start,
            end=result.period_end,
            days=result.period_days,
        ),
        eligible_accounts_identified=result.eligible_accounts_identified,
        autonomous_dispositions=result.autonomous_dispositions,
        human_governed_dispositions=result.human_governed_dispositions,
        autonomous_disposition_rate=result.autonomous_disposition_rate,
        autonomous_effect_verification=(
            AutonomousEffectVerificationSummary(
                verified=result.verification_verified,
                checked_other=result.verification_checked_other,
                not_yet_checked=result.verification_not_yet_checked,
                total=result.verification_total,
                verification_rate=result.verification_rate,
            )
        ),
        pending_overdue_accounts_now=result.pending_overdue_accounts_now,
    )