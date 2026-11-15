"""
Provisiona o system_principal canonico da deteccao de vencidos.

Script operacional, rodado manualmente (comando provisionar-deteccao-vencidos
do homologacao.ps1) a partir de backend/:
    python -m scripts.create_system_principal

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- Se o usuario canonico ja existe, NAO altera nada; apenas informa.
- A senha e aleatoria, descartavel e nunca e impressa nem registrada (a conta
  system nao faz login). Nenhum hash e impresso.

Codigos de saida:
    0  provisionada agora, ou ja provisionada (role=system e active=true)
    3  existe, mas com papel diferente de system ou inativa (nada alterado)
    4  banco diferente do esperado (nada gravado)
"""

import secrets

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.authority_provenance import (
    SYSTEM_PRINCIPAL_CANONICAL_EMAIL,
)
from app.core.authority_provenance import resolve_system_principal
from app.models.user import User

EXPECTED_DATABASE = "auneron_neon_synthetic"
PRINCIPAL_NAME = "Sistema Deteccao Vencimentos"

EXIT_OK = 0
EXIT_INELIGIBLE = 3
EXIT_WRONG_DATABASE = 4


def _find(db: Session) -> User | None:
    return (
        db.query(User)
        .filter(User.email == SYSTEM_PRINCIPAL_CANONICAL_EMAIL)
        .one_or_none()
    )


def _report(database: str, user: User) -> None:
    print(f"  banco : {database}")
    print(f"  email : {user.email}")
    print(f"  papel : {user.role}")
    print(f"  active: {user.active}")


def provision(
    db: Session,
    expected_database: str = EXPECTED_DATABASE,
) -> int:
    database = db.execute(text("SELECT current_database()")).scalar()

    if database != expected_database:
        db.rollback()
        print(
            f"RECUSADO: banco conectado e '{database}', esperado "
            f"'{expected_database}'. Nada foi gravado."
        )
        return EXIT_WRONG_DATABASE

    existing = _find(db)
    created = False

    if existing is None:
        user = User(
            name=PRINCIPAL_NAME,
            email=SYSTEM_PRINCIPAL_CANONICAL_EMAIL,
            role="system",
            active=True,
            password_hash=hash_password(secrets.token_urlsafe(48)),
        )
        db.add(user)
        try:
            db.commit()
            created = True
        except IntegrityError:
            db.rollback()
            existing = _find(db)
            if existing is None:
                print("ERRO: conflito ao gravar e a conta nao foi encontrada.")
                return 1

    user = _find(db)
    if user is None:
        print("ERRO: a conta nao foi encontrada apos a gravacao.")
        return 1

    if not created and (user.role != "system" or not user.active):
        print(
            "DIVERGENCIA: a conta ja existe, mas nao esta como "
            "esperado (role=system, active=true). Nada foi alterado."
        )
        _report(database, user)
        return EXIT_INELIGIBLE

    if created:
        print("Conta de sistema provisionada agora.")
    else:
        print("Conta de sistema ja provisionada. Nada foi alterado.")
    _report(database, user)

    principal = resolve_system_principal(db)
    print(f"system_principal resolvido: user_id={principal.authority_user_id}")
    return EXIT_OK


def main() -> int:
    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        return provision(db)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
