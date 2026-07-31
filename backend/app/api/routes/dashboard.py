from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.database import get_db
from app.models.account import Account

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

    atrasado = (
        db.query(func.sum(Account.valor))
        .filter(func.lower(Account.status) == "atrasado")
        .scalar() or 0
    )

    taxa_recebimento = (
        (recebido / faturamento_total) * 100
        if faturamento_total else 0
    )

    ticket_medio = (
        faturamento_total / total_clientes
        if total_clientes else 0
    )

    clientes_atrasados = (
        db.query(Account)
        .filter(func.lower(Account.status) == "atrasado")
        .count()
    )

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

    atrasados = (
        db.query(Account)
        .filter(func.lower(Account.status) == "atrasado")
        .count()
    )

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

    lista_alertas = (
        db.query(Account)
        .filter(func.lower(Account.status) == "atrasado")
        .limit(5)
        .all()
    )

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