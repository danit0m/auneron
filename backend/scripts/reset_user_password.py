"""
Redefine a senha de um usuario por uma senha TEMPORARIA (recuperacao manual).

Script operacional, rodado manualmente (comando redefinir-senha do
homologacao.ps1) a partir de backend/:
    python -m scripts.reset_user_password <email> [--permitir-ultimo-admin]

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- Gera senha forte (secrets, 24 caracteres), grava o hash scrypt pelo mesmo
  utilitario do login (hash_password), must_change_password=true,
  temp_password_expires_at = agora + 30 min, e REVOGA TODAS as sessoes do
  usuario na MESMA transacao (um unico commit).
- A senha temporaria e impressa UMA vez no stdout (linha "SENHA TEMPORARIA:")
  e nunca em log. Nenhum hash e impresso.
- Recusa usuario inexistente (3) e conta de servico role=system (5).
- Recusa (6) redefinir o UNICO administrador ativo sem --permitir-ultimo-admin.
  Justificativa: a temporaria vence em 30 min e a sessao do administrador e
  revogada; se a troca nao for feita, nao resta nenhum administrador que
  consiga entrar no app. O script nao e bloqueado por isso (pode ser rodado de
  novo), mas a recusa padrao evita surpresa; a flag torna a decisao explicita.
  Vale tambem para administrador inativo? Nao: so conta se o alvo e
  administrator ativo e nao existe outro administrator ativo.

Codigos de saida:
    0  senha temporaria gerada e sessoes revogadas
    1  erro inesperado (nada gravado: rollback)
    2  uso incorreto (e-mail ausente ou invalido)
    3  usuario inexistente (nada gravado)
    4  banco diferente do esperado (nada gravado)
    5  conta de servico role=system (nada gravado)
    6  unico administrador ativo sem --permitir-ultimo-admin (nada gravado)
"""

import secrets
import string
import sys
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.authentication import normalize_email
from app.core.authentication import revoke_user_sessions
from app.core.authentication import utc_now
from app.models.user import User

EXPECTED_DATABASE = "auneron_neon_synthetic"
TEMP_PASSWORD_LENGTH = 24
TEMP_PASSWORD_TTL_MINUTES = 30
PASSWORD_MARKER = "SENHA TEMPORARIA: "

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_WRONG_DATABASE = 4
EXIT_SYSTEM_ACCOUNT = 5
EXIT_LAST_ADMIN = 6

_ALPHABET = string.ascii_letters + string.digits + "-_.!#%+="


def generate_temp_password() -> str:
    """Forte e digitavel: letras, digitos e simbolos; sempre os tres grupos."""
    while True:
        password = "".join(
            secrets.choice(_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH)
        )
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


def _active_admin_count(db: Session) -> int:
    return (
        db.query(User)
        .filter(User.role == "administrator", User.active.is_(True))
        .count()
    )


def run(
    db: Session,
    email: str,
    allow_last_admin: bool = False,
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

    normalized = normalize_email(email)
    user = (
        db.query(User)
        .filter(User.email == normalized)
        .with_for_update()
        .one_or_none()
    )

    if user is None:
        db.rollback()
        print("RECUSADO: usuario inexistente. Nada foi gravado.")
        return EXIT_NOT_FOUND

    if user.role == "system":
        db.rollback()
        print(
            "RECUSADO: conta de servico (role=system) nao tem senha de "
            "login. Nada foi gravado."
        )
        return EXIT_SYSTEM_ACCOUNT

    if (
        user.role == "administrator"
        and user.active
        and not allow_last_admin
        and _active_admin_count(db) <= 1
    ):
        db.rollback()
        print(
            "RECUSADO: este e o UNICO administrador ativo. Se a troca nao "
            "for feita em "
            f"{TEMP_PASSWORD_TTL_MINUTES} min, ninguem administra o app. "
            "Repita com --permitir-ultimo-admin se for intencional. "
            "Nada foi gravado."
        )
        return EXIT_LAST_ADMIN

    password = generate_temp_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.temp_password_expires_at = utc_now() + timedelta(
        minutes=TEMP_PASSWORD_TTL_MINUTES
    )
    revoked = revoke_user_sessions(db, user.id)
    db.commit()

    print(f"  banco            : {database}")
    print(f"  usuario          : {user.email}")
    print(f"  papel            : {user.role}")
    print(f"  sessoes revogadas: {revoked}")
    print(
        f"  validade         : {TEMP_PASSWORD_TTL_MINUTES} min; troca "
        "obrigatoria no primeiro acesso"
    )
    print(PASSWORD_MARKER + password)
    print("Anote agora: a senha nao sera mostrada de novo.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    allow_last_admin = "--permitir-ultimo-admin" in args
    positional = [a for a in args if a != "--permitir-ultimo-admin"]

    if len(positional) != 1 or positional[0].startswith("-"):
        print(
            "Uso: python -m scripts.reset_user_password <email> "
            "[--permitir-ultimo-admin]"
        )
        return EXIT_USAGE

    email = positional[0].strip()

    if "@" not in email or len(email) < 3 or len(email) > 254:
        print("Informe um e-mail valido.")
        return EXIT_USAGE

    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        return run(db, email, allow_last_admin)
    except Exception:
        db.rollback()
        print("ERRO inesperado ao redefinir a senha. Nada foi gravado.")
        return EXIT_ERROR
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
