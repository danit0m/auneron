"""
Testes do script de redefinicao de senha (scripts/reset_user_password.py):
senha temporaria forte impressa uma vez, hash gravado pelo mesmo utilitario do
login, troca obrigatoria, sessoes revogadas na mesma transacao e recusas
(inexistente, system, ultimo administrador, banco errado).
"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy import text

from app.core.authentication import create_session
from app.core.authentication import hash_password
from app.core.authentication import verify_password
from app.models.auth_session import AuthSession
from app.models.user import User
from scripts import reset_user_password as script

OLD_PASSWORD = "Senha-Antiga-Auneron-123!"


def _database_name(db_session) -> str:
    return db_session.execute(text("SELECT current_database()")).scalar()


def _user(db_session, email="alvo@example.com", role="manager", active=True):
    user = User(
        name="Usuario Alvo",
        email=email,
        password_hash=hash_password(OLD_PASSWORD),
        role=role,
        active=active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _temp_password(out: str) -> str:
    line = [x for x in out.splitlines() if x.startswith(script.PASSWORD_MARKER)]
    assert len(line) == 1
    return line[0][len(script.PASSWORD_MARKER):]


def test_generates_strong_temp_password_and_requires_change(db_session, capsys):
    user = _user(db_session)
    before = datetime.now(timezone.utc)

    code = script.run(db_session, "ALVO@example.com", expected_database=_database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    password = _temp_password(out)
    assert len(password) >= 20
    assert out.count(password) == 1
    db_session.expire_all()
    user = db_session.get(User, user.id)
    assert verify_password(password, user.password_hash)
    assert not verify_password(OLD_PASSWORD, user.password_hash)
    assert user.password_hash.startswith("scrypt$")
    assert user.password_hash not in out
    assert user.must_change_password is True
    delta = user.temp_password_expires_at - before
    assert timedelta(minutes=29) < delta < timedelta(minutes=31)


def test_passwords_differ_between_runs(db_session, capsys):
    _user(db_session)
    db_name = _database_name(db_session)
    script.run(db_session, "alvo@example.com", expected_database=db_name)
    first = _temp_password(capsys.readouterr().out)
    script.run(db_session, "alvo@example.com", expected_database=db_name)
    second = _temp_password(capsys.readouterr().out)
    assert first != second


def test_revokes_all_sessions_of_the_user_only(db_session, capsys):
    target = _user(db_session)
    other = _user(db_session, email="outro@example.com")
    create_session(db_session, target)
    create_session(db_session, target)
    create_session(db_session, other)

    code = script.run(db_session, "alvo@example.com", expected_database=_database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "sessoes revogadas: 2" in out
    db_session.expire_all()
    mine = db_session.query(AuthSession).filter(AuthSession.user_id == target.id).all()
    theirs = db_session.query(AuthSession).filter(AuthSession.user_id == other.id).all()
    assert len(mine) == 2 and all(s.revoked_at is not None for s in mine)
    assert len(theirs) == 1 and theirs[0].revoked_at is None


def test_refuses_unknown_user_without_writing(db_session, capsys):
    code = script.run(db_session, "nao.existe@example.com", expected_database=_database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_NOT_FOUND
    assert script.PASSWORD_MARKER not in out


def test_refuses_system_account(db_session, capsys):
    system = _user(db_session, email="sistema@example.com", role="system")
    old_hash = system.password_hash

    code = script.run(db_session, "sistema@example.com", expected_database=_database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_SYSTEM_ACCOUNT
    assert script.PASSWORD_MARKER not in out
    db_session.expire_all()
    assert db_session.get(User, system.id).password_hash == old_hash
    assert db_session.get(User, system.id).must_change_password is False


def test_refuses_wrong_database_without_writing(db_session, capsys):
    user = _user(db_session)
    old_hash = user.password_hash

    code = script.run(db_session, "alvo@example.com", expected_database="banco_errado")
    out = capsys.readouterr().out

    assert code == script.EXIT_WRONG_DATABASE
    assert script.PASSWORD_MARKER not in out
    db_session.expire_all()
    assert db_session.get(User, user.id).password_hash == old_hash


def test_refuses_the_only_active_administrator_without_flag(db_session, capsys):
    admin = _user(db_session, email="admin@example.com", role="administrator")
    _user(db_session, email="inativo@example.com", role="administrator", active=False)
    old_hash = admin.password_hash
    create_session(db_session, admin)

    code = script.run(db_session, "admin@example.com", expected_database=_database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_LAST_ADMIN
    assert script.PASSWORD_MARKER not in out
    db_session.expire_all()
    assert db_session.get(User, admin.id).password_hash == old_hash
    assert db_session.query(AuthSession).filter(AuthSession.revoked_at.isnot(None)).count() == 0


def test_only_active_administrator_allowed_with_explicit_flag(db_session, capsys):
    _user(db_session, email="admin@example.com", role="administrator")

    code = script.run(
        db_session,
        "admin@example.com",
        allow_last_admin=True,
        expected_database=_database_name(db_session),
    )

    assert code == script.EXIT_OK
    assert script.PASSWORD_MARKER in capsys.readouterr().out


def test_administrator_is_allowed_when_another_active_administrator_exists(db_session, capsys):
    _user(db_session, email="admin@example.com", role="administrator")
    _user(db_session, email="admin2@example.com", role="administrator")

    code = script.run(db_session, "admin@example.com", expected_database=_database_name(db_session))

    assert code == script.EXIT_OK
    assert script.PASSWORD_MARKER in capsys.readouterr().out


def test_main_usage_errors_do_not_touch_the_database(capsys):
    assert script.main([]) == script.EXIT_USAGE
    assert script.main(["sem-arroba"]) == script.EXIT_USAGE
    assert script.main(["a@b.com", "c@d.com"]) == script.EXIT_USAGE
    assert script.main(["--x"]) == script.EXIT_USAGE


def test_temp_password_generator_is_strong():
    values = {script.generate_temp_password() for _ in range(50)}
    assert len(values) == 50
    for value in values:
        assert len(value) >= 20
        assert any(c.islower() for c in value)
        assert any(c.isupper() for c in value)
        assert any(c.isdigit() for c in value)


# --- Modo --senha-arquivo (senha so no arquivo, nunca no stdout) -------------


def test_run_with_password_file_keeps_password_out_of_stdout(db_session, tmp_path, capsys):
    user = _user(db_session)
    target = tmp_path / "senha.txt"

    code = script.run(
        db_session,
        "alvo@example.com",
        expected_database=_database_name(db_session),
        password_file=str(target),
    )
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    password = target.read_text(encoding="utf-8").strip()
    assert len(password) >= 20
    assert password not in out
    assert script.PASSWORD_MARKER not in out
    assert f"senha gravada em {target}" in out
    db_session.expire_all()
    assert verify_password(password, db_session.get(User, user.id).password_hash)


def test_run_with_existing_password_file_refuses_without_writing(db_session, tmp_path, capsys):
    user = _user(db_session)
    old_hash = user.password_hash
    target = tmp_path / "senha.txt"
    target.write_text("antigo", encoding="utf-8")

    code = script.run(
        db_session,
        "alvo@example.com",
        expected_database=_database_name(db_session),
        password_file=str(target),
    )

    assert code == script.EXIT_USAGE
    assert target.read_text(encoding="utf-8") == "antigo"
    db_session.expire_all()
    assert db_session.get(User, user.id).password_hash == old_hash


def test_write_password_file_creates_new_file_and_never_overwrites(tmp_path):
    target = tmp_path / "senha.txt"
    script.write_password_file(str(target), "Segredo-123")
    assert target.read_text(encoding="utf-8").strip() == "Segredo-123"
    try:
        script.write_password_file(str(target), "Outro-456")
    except FileExistsError:
        pass
    else:
        raise AssertionError("deveria recusar arquivo existente")
    assert target.read_text(encoding="utf-8").strip() == "Segredo-123"


def test_main_password_file_usage_errors_do_not_touch_the_database(tmp_path, capsys):
    existing = tmp_path / "ja-existe.txt"
    existing.write_text("x", encoding="utf-8")
    missing_dir = tmp_path / "nao-existe" / "senha.txt"

    assert script.main(["a@b.com", "--senha-arquivo"]) == script.EXIT_USAGE
    assert script.main(["a@b.com", "--senha-arquivo", "--permitir-ultimo-admin"]) == script.EXIT_USAGE
    assert script.main(["a@b.com", "--senha-arquivo", str(existing)]) == script.EXIT_USAGE
    assert script.main(["a@b.com", "--senha-arquivo", str(missing_dir)]) == script.EXIT_USAGE
    assert existing.read_text(encoding="utf-8") == "x"
    assert not missing_dir.exists()
