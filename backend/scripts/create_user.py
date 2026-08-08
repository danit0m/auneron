import argparse
from getpass import getpass

from pydantic import EmailStr
from pydantic import TypeAdapter
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.authentication import hash_password
from app.core.authentication import normalize_email
from app.core.authorization import USER_ROLES
from app.database.database import SessionLocal
from app.models.user import User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cria um usuário do Auneron sem "
            "armazenar senha em arquivo ou Git."
        )
    )

    parser.add_argument(
        "--name",
        required=True,
        help="Nome do usuário.",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="E-mail de login.",
    )
    parser.add_argument(
        "--role",
        choices=USER_ROLES,
        default="administrator",
        help="Papel inicial do usuário.",
    )

    return parser.parse_args()


def read_password() -> str:
    password = getpass(
        "Senha (mínimo 12 caracteres): "
    )

    if len(password) < 12:
        raise SystemExit(
            "A senha precisa ter pelo menos "
            "12 caracteres."
        )

    if len(password) > 128:
        raise SystemExit(
            "A senha pode ter no máximo "
            "128 caracteres."
        )

    confirmation = getpass(
        "Confirme a senha: "
    )

    if password != confirmation:
        raise SystemExit(
            "As senhas não conferem."
        )

    return password


def main() -> None:
    args = parse_args()
    name = args.name.strip()

    try:
        validated_email = TypeAdapter(
            EmailStr
        ).validate_python(
            args.email
        )
    except ValidationError as error:
        raise SystemExit(
            "Informe um e-mail válido."
        ) from error

    email = normalize_email(
        str(validated_email)
    )

    if len(name) < 2:
        raise SystemExit(
            "O nome precisa ter pelo menos "
            "2 caracteres."
        )

    password = read_password()

    db = SessionLocal()

    try:
        existing = (
            db.query(User)
            .filter(User.email == email)
            .one_or_none()
        )

        if existing is not None:
            raise SystemExit(
                "Já existe um usuário com "
                "esse e-mail."
            )

        user = User(
            name=name,
            email=email,
            password_hash=hash_password(
                password
            ),
            role=args.role,
            active=True,
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        print(
            "Usuário criado:",
            user.email,
            f"role={user.role}",
        )
    except IntegrityError as error:
        db.rollback()
        raise SystemExit(
            "Não foi possível criar o usuário "
            "por conflito de dados."
        ) from error
    finally:
        db.close()


if __name__ == "__main__":
    main()
