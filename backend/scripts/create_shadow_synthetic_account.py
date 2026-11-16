"""
Provisiona UMA conta vencida sintetica para a rodada S5 do shadow de vencidos.

Script operacional, rodado manualmente (comando
provisionar-conta-vencida-sintetica do homologacao.ps1) a partir de backend/:
    python -m scripts.create_shadow_synthetic_account

Cria exatamente uma conta:
- cliente "SINTETICO S5 SHADOW", email s5.shadow@example.com (sintetico);
- valor pequeno (10.00), status aberto;
- vencimento = hoje (BUSINESS_TIMEZONE) - 10 dias.
Nada mais e criado (nenhum evento, pedido, proposta ou titulo).

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- Idempotente pelo email: se a conta ja existe, NAO altera nada; informa o id.
  Conta existente que nao esteja aberta, ou com outro cliente, e divergencia.
- Apos gravar, le de novo o registro e imprime o id.

Codigos de saida:
    0  criada agora, ou ja existente (cliente sintetico e status aberto)
    3  existe, mas divergente (outro cliente ou status diferente de aberto)
    4  banco diferente do esperado (nada gravado)
"""

from datetime import date
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.receivable_lifecycle import business_today
from app.models.account import Account

EXPECTED_DATABASE = "auneron_neon_synthetic"
CLIENTE = "SINTETICO S5 SHADOW"
EMAIL = "s5.shadow@example.com"
VALOR = Decimal("10.00")
DIAS_DE_ATRASO = 10

EXIT_OK = 0
EXIT_DIVERGENT = 3
EXIT_WRONG_DATABASE = 4


def _find(db: Session) -> Account | None:
    return db.execute(
        select(Account).where(Account.email == EMAIL)
    ).scalars().first()


def _report(database: str, account: Account) -> None:
    print(f"  banco      : {database}")
    print(f"  account_id : {account.id}")
    print(f"  cliente    : {account.cliente}")
    print(f"  status     : {account.status}")
    print(f"  vencimento : {account.vencimento}")


def _refused_database(database: str, expected: str) -> int:
    print(
        f"RECUSADO: banco conectado e '{database}', esperado "
        f"'{expected}'. Nada foi gravado."
    )
    return EXIT_WRONG_DATABASE


def provision(
    db: Session,
    database: str,
    today: date | None = None,
) -> int:
    existing = _find(db)

    if existing is not None:
        _report(database, existing)
        db.rollback()

        if existing.cliente != CLIENTE or existing.status != "aberto":
            print(
                "DIVERGENCIA: a conta existe, mas nao e a conta sintetica "
                "aberta esperada. Nada foi alterado."
            )
            return EXIT_DIVERGENT

        print("Conta sintetica ja provisionada. Nada foi alterado.")
        return EXIT_OK

    reference = today or business_today()
    db.add(
        Account(
            cliente=CLIENTE,
            email=EMAIL,
            valor=VALOR,
            vencimento=reference - timedelta(days=DIAS_DE_ATRASO),
            status="aberto",
        )
    )
    db.commit()

    db.expire_all()
    created = _find(db)

    if created is None:
        print("ERRO: a conta nao foi encontrada depois de gravar.")
        return 1

    print("Conta sintetica criada agora.")
    _report(database, created)
    print(f"ID_CONTA={created.id}")
    db.rollback()
    return EXIT_OK


def run(
    db: Session,
    expected_database: str = EXPECTED_DATABASE,
    today: date | None = None,
) -> int:
    database = db.execute(text("SELECT current_database()")).scalar()

    if database != expected_database:
        db.rollback()
        return _refused_database(database, expected_database)

    return provision(db, database, today)


def main() -> int:
    from app.database.database import SessionLocal

    db = SessionLocal()

    try:
        return run(db)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
