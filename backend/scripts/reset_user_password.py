"""
Redefine a senha de um usuario por uma senha TEMPORARIA (recuperacao manual).

Script operacional, rodado manualmente.

Homologacao (comando redefinir-senha do homologacao.ps1), a partir de backend/:
    python -m scripts.reset_user_password <email> [--permitir-ultimo-admin]
        [--senha-arquivo <arquivo novo>]

Piloto (container de producao, usuario "auneron", WORKDIR /app; /tmp e
gravavel por esse usuario na imagem python:3.11-slim):
    docker exec -it auneron-backend-prod python -m scripts.reset_user_password
        <email> --senha-arquivo /tmp/<nome-novo>
O arquivo e novo (nunca sobrescreve), modo 0600; apague-o depois de entregar
a senha por canal diferente.

Banco esperado: vem SO da configuracao do ambiente (EXPECTED_DATABASE_NAME,
obrigatoria em producao). Sem a variavel (homologacao), vale o padrao
auneron_neon_synthetic. Nao existe flag de linha de comando para trocar o
banco. Nome fora de [A-Za-z0-9_]{1,63} encerra com codigo 4 sem abrir sessao.

Regras:
- A primeira coisa feita e conferir o banco (SELECT current_database()) na
  MESMA sessao que gravaria; se nao for o esperado, encerra sem escrever.
- Gera senha forte (secrets, 24 caracteres), grava o hash scrypt pelo mesmo
  utilitario do login (hash_password), must_change_password=true,
  temp_password_expires_at = agora + 30 min, e REVOGA TODAS as sessoes do
  usuario na MESMA transacao (um unico commit).
- Sem --senha-arquivo, a senha temporaria e impressa UMA vez no stdout (linha
  "SENHA TEMPORARIA:") e nunca em log (uso manual em console). Com
  --senha-arquivo <caminho>, a senha e gravada SO nesse arquivo (novo, acesso
  restrito ao usuario atual quando possivel) e o stdout traz apenas "senha
  gravada em <caminho>". Nenhum hash e impresso.
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

import os
import re
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
_DATABASE_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,63}")
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


def resolve_expected_database(settings_value: str | None) -> str | None:
    """Banco esperado: o da configuracao do ambiente ou o padrao da homologacao.

    Retorna None se o nome for invalido (so [A-Za-z0-9_], 1 a 63 caracteres).
    """
    name = settings_value if settings_value else EXPECTED_DATABASE
    if _DATABASE_NAME_RE.fullmatch(name) is None:
        return None
    return name


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
    password_file: str | None = None,
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

    if password_file is not None:
        # Grava o arquivo ANTES de alterar o banco: se falhar, nada e gravado.
        try:
            write_password_file(password_file, password)
        except OSError as error:
            db.rollback()
            print(
                "RECUSADO: nao foi possivel criar o arquivo da senha "
                f"({type(error).__name__}). Nada foi gravado."
            )
            return EXIT_USAGE

    try:
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.temp_password_expires_at = utc_now() + timedelta(
            minutes=TEMP_PASSWORD_TTL_MINUTES
        )
        revoked = revoke_user_sessions(db, user.id)
        db.commit()
    except Exception:
        if password_file is not None:
            try:
                os.remove(password_file)
            except OSError:
                pass
        raise

    print(f"  banco            : {database}")
    print(f"  usuario          : {user.email}")
    print(f"  papel            : {user.role}")
    print(f"  sessoes revogadas: {revoked}")
    print(
        f"  validade         : {TEMP_PASSWORD_TTL_MINUTES} min; troca "
        "obrigatoria no primeiro acesso"
    )
    if password_file is not None:
        print(f"senha gravada em {password_file}")
    else:
        print(PASSWORD_MARKER + password)
        print("Anote agora: a senha nao sera mostrada de novo.")
    return EXIT_OK


def write_password_file(path: str, password: str) -> None:
    """Cria um arquivo NOVO (falha se existir) com a senha e acesso restrito.

    Posix: modo 0600. Windows: heranca removida e acesso so do usuario atual
    (icacls), em melhor esforco; se nao der, o aviso nao inclui a senha.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "nt":
            _restrict_windows_acl(path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(password + "\n")
    except BaseException:
        if fd != -1:
            os.close(fd)
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def _restrict_windows_acl(path: str) -> None:
    import getpass
    import subprocess

    try:
        subprocess.run(
            [
                "icacls",
                path,
                "/inheritance:r",
                "/grant:r",
                f"{getpass.getuser()}:F",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except Exception:
        print(
            "AVISO: nao foi possivel restringir as permissoes do arquivo da "
            "senha; proteja-o manualmente."
        )


def _parse_args(args: list[str]):
    """Retorna (email_args, allow_last_admin, password_file) ou None se invalido."""
    allow_last_admin = False
    password_file = None
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--permitir-ultimo-admin":
            allow_last_admin = True
        elif arg == "--senha-arquivo":
            if password_file is not None or index + 1 >= len(args):
                return None
            index += 1
            password_file = args[index]
            if not password_file or password_file.startswith("-"):
                return None
        else:
            positional.append(arg)
        index += 1
    return positional, allow_last_admin, password_file


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parsed = _parse_args(args)
    usage = (
        "Uso: python -m scripts.reset_user_password <email> "
        "[--permitir-ultimo-admin] [--senha-arquivo <arquivo novo>]"
    )
    if parsed is None:
        print(usage)
        return EXIT_USAGE
    positional, allow_last_admin, password_file = parsed

    if len(positional) != 1 or positional[0].startswith("-"):
        print(usage)
        return EXIT_USAGE

    if password_file is not None:
        if os.path.exists(password_file):
            print("RECUSADO: o arquivo da senha ja existe. Nada foi gravado.")
            return EXIT_USAGE
        parent = os.path.dirname(os.path.abspath(password_file))
        if not os.path.isdir(parent):
            print("RECUSADO: a pasta do arquivo da senha nao existe.")
            return EXIT_USAGE

    email = positional[0].strip()

    if "@" not in email or len(email) < 3 or len(email) > 254:
        print("Informe um e-mail valido.")
        return EXIT_USAGE

    from app.core.config import settings

    expected_database = resolve_expected_database(
        settings.expected_database_name
    )
    if expected_database is None:
        print(
            "RECUSADO: EXPECTED_DATABASE_NAME invalido. Nada foi gravado."
        )
        return EXIT_WRONG_DATABASE

    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        return run(
            db,
            email,
            allow_last_admin,
            expected_database=expected_database,
            password_file=password_file,
        )
    except Exception:
        db.rollback()
        print("ERRO inesperado ao redefinir a senha. Nada foi gravado.")
        return EXIT_ERROR
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
