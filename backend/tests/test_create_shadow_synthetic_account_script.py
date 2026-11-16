"""
Testes do script da conta sintetica do shadow S5
(scripts/create_shadow_synthetic_account.py): recusa banco diferente do
esperado, cria exatamente uma conta aberta vencida, e e idempotente pelo
email sem criar evento, pedido ou proposta.
"""

from datetime import date
from datetime import timedelta

from sqlalchemy import text

from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalRequest
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from scripts import create_shadow_synthetic_account as script


def _database_name(db_session) -> str:
    return db_session.execute(text("SELECT current_database()")).scalar()


def _accounts(db_session) -> list[Account]:
    db_session.expire_all()
    return db_session.query(Account).order_by(Account.id).all()


def _governed_counts(db_session) -> tuple[int, int, int]:
    return (
        db_session.query(AccountEvent).count(),
        db_session.query(ApprovalRequest).count(),
        db_session.query(AuthenticatedAdvisoryProposal).count(),
    )


def test_default_expected_database_is_synthetic_and_not_test_db(db_session):
    assert script.EXPECTED_DATABASE == "auneron_neon_synthetic"
    assert _database_name(db_session) != script.EXPECTED_DATABASE
    assert script.run(db_session) == script.EXIT_WRONG_DATABASE


def test_wrong_database_exits_4_without_writing(db_session, capsys):
    code = script.run(db_session, "outro_banco_qualquer")
    out = capsys.readouterr().out

    assert code == script.EXIT_WRONG_DATABASE == 4
    assert "RECUSADO" in out
    assert _accounts(db_session) == []


def test_creates_exactly_one_open_overdue_synthetic_account(
    db_session, capsys
):
    today = date(2026, 11, 16)

    code = script.run(db_session, _database_name(db_session), today)
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    accounts = _accounts(db_session)
    assert len(accounts) == 1
    account = accounts[0]
    assert account.cliente == "SINTETICO S5 SHADOW"
    assert account.email == "s5.shadow@example.com"
    assert account.status == "aberto"
    assert account.vencimento == today - timedelta(days=10)
    assert float(account.valor) == 10.0
    assert _governed_counts(db_session) == (0, 0, 0)
    assert f"ID_CONTA={account.id}" in out
    assert "criada agora" in out


def test_is_idempotent_by_email(db_session, capsys):
    database = _database_name(db_session)
    assert script.run(db_session, database, date(2026, 11, 16)) == 0
    first = _accounts(db_session)[0]
    capsys.readouterr()

    code = script.run(db_session, database, date(2026, 12, 25))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "ja provisionada" in out
    assert f"account_id : {first.id}" in out
    accounts = _accounts(db_session)
    assert [a.id for a in accounts] == [first.id]
    assert accounts[0].vencimento == date(2026, 11, 6)


def test_existing_not_open_exits_3_unchanged(db_session, capsys):
    database = _database_name(db_session)
    assert script.run(db_session, database, date(2026, 11, 16)) == 0
    account = _accounts(db_session)[0]
    account.status = "atrasado"
    db_session.commit()
    capsys.readouterr()

    code = script.run(db_session, database, date(2026, 11, 16))
    out = capsys.readouterr().out

    assert code == script.EXIT_DIVERGENT == 3
    assert "DIVERGENCIA" in out
    assert _accounts(db_session)[0].status == "atrasado"
    assert len(_accounts(db_session)) == 1
