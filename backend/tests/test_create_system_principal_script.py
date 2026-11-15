"""
Testes do script de provisionamento do system_principal
(scripts/create_system_principal.py): cria quando ausente, nao altera
conta existente, recusa banco diferente do esperado e nunca imprime
senha nem hash.
"""

import pytest
from sqlalchemy import text

from app.core.authentication import hash_password
from app.core.authority_provenance import SYSTEM_PRINCIPAL_CANONICAL_EMAIL
from app.core.authority_provenance import resolve_system_principal
from app.models.user import User
from scripts import create_system_principal as script


def _database_name(db_session) -> str:
    return db_session.execute(text("SELECT current_database()")).scalar()


def _principal(db_session):
    db_session.expire_all()
    return (
        db_session.query(User)
        .filter(User.email == SYSTEM_PRINCIPAL_CANONICAL_EMAIL)
        .one_or_none()
    )


def _make_existing(db_session, *, role: str, active: bool) -> User:
    user = User(
        name="Conta preexistente",
        email=SYSTEM_PRINCIPAL_CANONICAL_EMAIL,
        password_hash=hash_password("Senha-Teste-Auneron-123!"),
        role=role,
        active=active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_creates_when_absent(db_session, capsys):
    code = script.provision(db_session, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    user = _principal(db_session)
    assert user is not None
    assert user.role == "system"
    assert user.active is True
    assert user.name == script.PRINCIPAL_NAME
    assert resolve_system_principal(db_session).authority_user_id == user.id
    assert f"system_principal resolvido: user_id={user.id}" in out
    assert SYSTEM_PRINCIPAL_CANONICAL_EMAIL in out
    assert "password" not in out.lower()
    assert user.password_hash not in out
    assert "$" not in out


def test_does_not_alter_eligible_existing(db_session, capsys):
    existing = _make_existing(db_session, role="system", active=True)
    old_hash = existing.password_hash

    code = script.provision(db_session, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "ja provisionada" in out
    user = _principal(db_session)
    assert user.id == existing.id
    assert user.name == "Conta preexistente"
    assert user.password_hash == old_hash


@pytest.mark.parametrize(
    ("role", "active"),
    [("administrator", True), ("system", False)],
)
def test_ineligible_existing_exits_3_without_change(
    db_session, capsys, role, active
):
    existing = _make_existing(db_session, role=role, active=active)
    old_hash = existing.password_hash

    code = script.provision(db_session, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_INELIGIBLE == 3
    assert "DIVERGENCIA" in out
    user = _principal(db_session)
    assert user.role == role
    assert user.active is active
    assert user.password_hash == old_hash
    assert "system_principal resolvido" not in out


def test_wrong_database_writes_nothing(db_session, capsys):
    code = script.provision(db_session, "outro_banco_qualquer")
    out = capsys.readouterr().out

    assert code == script.EXIT_WRONG_DATABASE == 4
    assert "RECUSADO" in out
    assert _principal(db_session) is None


def test_default_expected_database_is_synthetic_and_not_test_db(db_session):
    assert script.EXPECTED_DATABASE == "auneron_neon_synthetic"
    assert _database_name(db_session) != script.EXPECTED_DATABASE
    assert script.provision(db_session) == script.EXIT_WRONG_DATABASE
    assert _principal(db_session) is None
