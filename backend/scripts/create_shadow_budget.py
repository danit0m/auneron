"""
Provisiona o orcamento de implantacao da rodada S5 do shadow de vencidos.

Script operacional, rodado manualmente (comando provisionar-orcamento-shadow
do homologacao.ps1) a partir de backend/:
    python -m scripts.create_shadow_budget

O orcamento e do tipo deployment_period e dimensionado para EXATAMENTE UMA
chamada do perfil local-qwen3-4b (preco zero):

- Dinheiro (micro-USD): o preco local e zero, entao a liquidacao real e 0 e o
  limite de dinheiro NAO restringe quantidade de chamadas (cada reserva vale
  1 micro e e devolvida na liquidacao). Fica em 1, apenas o minimo aceito.
- Tokens: e o limite que garante "uma chamada". limit_tokens = a reserva de
  pior caso de UMA tentativa (bytes de instrucoes + entrada + schema, mais
  256 tokens de saida, mais a margem configurada), calculada com as mesmas
  constantes e a mesma funcao do gateway. Enquanto a chamada esta em voo, a
  reserva ocupa o limite inteiro (R = L); depois da liquidacao o consumo real
  (entrada + saida, minimo 2 tokens, na pratica centenas) fica em S e uma
  segunda reserva (R2 ~ L) nao cabe (S + R2 > L). Se a chamada falhar com
  estado desconhecido, U = R tambem bloqueia a seguinte (e o breaker de
  desconhecido dispara). O predicado e atomico no UPDATE do ledger (BI-2).
- Complementos fora deste script (subir-backend -Shadow): uma conta, no maximo
  1 conta por rodada e INTELLIGENCE_MAX_ATTEMPTS=1.

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- Idempotente: se o orcamento ja existe (scope_kind + scope_ref), NAO altera
  nada; informa o id e o estado dos baldes. Divergencia de limites ou periodo
  vencido encerra com 3.
- Apos gravar, le de novo o registro e imprime o id.

Codigos de saida:
    0  provisionado agora, ou ja provisionado (limites iguais, periodo vigente)
    3  existe, mas com limites diferentes do esperado ou periodo vencido
    4  banco diferente do esperado (nada gravado)
"""

import json
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.intelligence.gateway.pricing import PRICE_TABLE
from app.intelligence.gateway.pricing import Reservation
from app.intelligence.gateway.pricing import input_token_upper_bound
from app.intelligence.gateway.pricing import worst_case_reservation
from app.models.intelligence_budget import IntelligenceBudget
from app.services import overdue_detection_shadow as shadow

EXPECTED_DATABASE = "auneron_neon_synthetic"
SCOPE_KIND = "deployment_period"
SCOPE_REF = "s5-shadow-overdue-synthetic"
PERIOD_DAYS = 7
PRICE_KEY = "local-zero"

# Entrada de pior caso do shadow: dias de atraso com 3 digitos (999).
_WORST_DAYS_AFTER_DUE = 999

EXIT_OK = 0
EXIT_DIVERGENT = 3
EXIT_WRONG_DATABASE = 4


def one_call_reservation(margin_bps: int) -> Reservation:
    """Reserva de pior caso de UMA tentativa do shadow (mesma conta do gateway)."""
    due = date(2000, 1, 1)
    input_text = shadow.build_model_input(
        SimpleNamespace(
            due_date=due.isoformat(),
            status="aberto",
            business_date=(
                due + timedelta(days=_WORST_DAYS_AFTER_DUE)
            ).isoformat(),
        )
    )

    return worst_case_reservation(
        input_upper_bound_tokens=input_token_upper_bound(
            shadow.INSTRUCTIONS,
            input_text,
            json.dumps(dict(shadow.OUTPUT_SCHEMA), sort_keys=True),
        ),
        max_output_tokens=shadow.MAX_OUTPUT_TOKENS,
        price=PRICE_TABLE[PRICE_KEY],
        margin_bps=margin_bps,
    )


def _find(db: Session) -> IntelligenceBudget | None:
    return db.execute(
        select(IntelligenceBudget).where(
            IntelligenceBudget.scope_kind == SCOPE_KIND,
            IntelligenceBudget.scope_ref == SCOPE_REF,
        )
    ).scalar_one_or_none()


def _report(database: str, budget: IntelligenceBudget) -> None:
    print(f"  banco        : {database}")
    print(f"  budget_id    : {budget.id}")
    print(f"  scope        : {budget.scope_kind} / {budget.scope_ref}")
    print(f"  periodo      : {budget.period_start} ate {budget.period_end}")
    print(f"  limit_micro  : {budget.limit_micro}")
    print(f"  limit_tokens : {budget.limit_tokens}")
    print(
        f"  reservado/liquidado/desconhecido/excedente (tokens): "
        f"{budget.reserved_tokens}/{budget.settled_tokens}/"
        f"{budget.unknown_tokens}/{budget.overrun_tokens}"
    )


def _refused_database(database: str, expected: str) -> int:
    print(
        f"RECUSADO: banco conectado e '{database}', esperado "
        f"'{expected}'. Nada foi gravado."
    )
    return EXIT_WRONG_DATABASE


def provision(
    db: Session,
    database: str,
    margin_bps: int,
    now: datetime | None = None,
) -> int:
    reservation = one_call_reservation(margin_bps)
    expected_micro = 1
    expected_tokens = reservation.tokens
    current = now or datetime.now(timezone.utc)

    existing = _find(db)

    if existing is not None:
        _report(database, existing)
        db.rollback()

        if (
            existing.limit_micro != expected_micro
            or existing.limit_tokens != expected_tokens
        ):
            print(
                "DIVERGENCIA: o orcamento existe, mas com limites "
                f"diferentes do esperado (micro={expected_micro}, "
                f"tokens={expected_tokens}). Nada foi alterado."
            )
            return EXIT_DIVERGENT

        if existing.period_end is not None and existing.period_end <= current:
            print(
                "DIVERGENCIA: o periodo do orcamento ja venceu. "
                "Nada foi alterado."
            )
            return EXIT_DIVERGENT

        print("Orcamento ja provisionado. Nada foi alterado.")
        return EXIT_OK

    # Tolerancia de relogio: o periodo comeca 5 minutos no passado.
    db.add(
        IntelligenceBudget(
            scope_kind=SCOPE_KIND,
            scope_ref=SCOPE_REF,
            period_start=current - timedelta(minutes=5),
            period_end=current + timedelta(days=PERIOD_DAYS),
            limit_micro=expected_micro,
            limit_tokens=expected_tokens,
            updated_at=current,
        )
    )
    db.commit()

    db.expire_all()
    created = _find(db)

    if created is None:
        print("ERRO: o orcamento nao foi encontrado depois de gravar.")
        return 1

    print("Orcamento provisionado agora.")
    _report(database, created)
    print(f"ID_ORCAMENTO={created.id}")
    db.rollback()
    return EXIT_OK


def run(
    db: Session,
    expected_database: str = EXPECTED_DATABASE,
    margin_bps: int | None = None,
) -> int:
    database = db.execute(text("SELECT current_database()")).scalar()

    if database != expected_database:
        db.rollback()
        return _refused_database(database, expected_database)

    margin = (
        settings.intelligence_reservation_margin_bps
        if margin_bps is None
        else margin_bps
    )
    return provision(db, database, margin)


def main() -> int:
    from app.database.database import SessionLocal

    db = SessionLocal()

    try:
        return run(db)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
