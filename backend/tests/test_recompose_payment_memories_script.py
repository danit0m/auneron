"""
Testes da recomposicao unica das memorias de comportamento de pagamento
(scripts/recompose_payment_memories.py): recompõe memoria antiga com
pagamentos efetivos, encerra (sem apagar) a de ciclos insuficientes,
e e idempotente; banco errado = 4 sem escrever.
"""

from datetime import date
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.core.client_behavior_memory_maintenance import MEMORY_KEY
from app.models.account import Account
from app.models.memory import MemoryItem
from app.services.memory_service import MemoryService
from scripts import recompose_payment_memories as script
from tests.payment_date_helpers import make_effective_payment


def _database_name(db_session) -> str:
    return db_session.execute(text("SELECT current_database()")).scalar()


def _unique_email() -> str:
    return f"recompor.{uuid4().hex[:12]}@example.com"


def _make_paid_accounts(db, *, email, count, with_payment=True):
    accounts = []
    for index in range(count):
        vencimento = date(2026, 3, 1 + index)
        account = Account(
            cliente="Cliente Recomposicao",
            email=email,
            whatsapp=None,
            valor=Decimal("100.00"),
            vencimento=vencimento,
            status="pago",
        )
        db.add(account)
        db.flush()
        if with_payment:
            make_effective_payment(
                db, account=account, paid_on=vencimento + timedelta(days=2)
            )
        accounts.append(account)
    db.commit()
    return accounts


def _legacy_memory(db, account, email) -> MemoryItem:
    # Memoria no formato antigo: sem 'ocorrencias_sem_data_efetiva'.
    result = MemoryService(db).remember(
        memory_type="observation",
        title=f"Padrao antigo {email}",
        content="Padrao calculado pela data do evento.",
        scope_type="account",
        account_id=account.id,
        source_type="derived",
        source_reference=f"client_behavior:{email}",
        confidence=0.5,
        memory_key=MEMORY_KEY,
        context_data={"email": email, "atraso_medio_dias": 9.0},
    )
    return result.memory


def _reload(db, memory_id) -> MemoryItem:
    db.expire_all()
    return db.get(MemoryItem, memory_id)


def _active_for(db, account_id) -> list[MemoryItem]:
    db.expire_all()
    return (
        db.query(MemoryItem)
        .filter(
            MemoryItem.account_id == account_id,
            MemoryItem.memory_key == MEMORY_KEY,
            MemoryItem.status == "active",
        )
        .all()
    )


def test_recomposes_old_memory_with_effective_payments(
    db_session, capsys
) -> None:
    email = _unique_email()
    accounts = _make_paid_accounts(db_session, email=email, count=3)
    old = _legacy_memory(db_session, accounts[0], email)
    old_id = old.id

    code = script.run(
        db_session, "confirmar", expected_database=_database_name(db_session)
    )

    out = capsys.readouterr().out
    assert code == script.EXIT_OK
    assert email not in out
    assert script.mask_email(email) in out
    assert _reload(db_session, old_id).status == "superseded"
    active = _active_for(db_session, accounts[0].id)
    assert len(active) == 1
    assert active[0].context_data["ocorrencias_sem_data_efetiva"] == 0
    assert active[0].context_data["atraso_medio_dias"] == 2.0


def test_closes_old_memory_with_insufficient_cycles_without_deleting(
    db_session,
) -> None:
    email = _unique_email()
    accounts = _make_paid_accounts(db_session, email=email, count=2)
    old = _legacy_memory(db_session, accounts[0], email)
    old_id = old.id

    code = script.run(
        db_session, "confirmar", expected_database=_database_name(db_session)
    )

    assert code == script.EXIT_OK
    closed = _reload(db_session, old_id)
    assert closed is not None
    assert closed.status == "invalidated"
    assert closed.status_reason == script.CLOSE_REASON
    assert _active_for(db_session, accounts[0].id) == []


def test_simulate_does_not_write(db_session, capsys) -> None:
    email = _unique_email()
    accounts = _make_paid_accounts(db_session, email=email, count=3)
    old = _legacy_memory(db_session, accounts[0], email)
    old_id = old.id

    code = script.run(
        db_session, "simular", expected_database=_database_name(db_session)
    )

    out = capsys.readouterr().out
    assert code == script.EXIT_OK
    assert "SIMULACAO" in out
    assert email not in out
    assert _reload(db_session, old_id).status == "active"


def test_second_run_is_idempotent(db_session, capsys) -> None:
    email = _unique_email()
    accounts = _make_paid_accounts(db_session, email=email, count=3)
    _legacy_memory(db_session, accounts[0], email)
    short_email = _unique_email()
    short = _make_paid_accounts(db_session, email=short_email, count=2)
    _legacy_memory(db_session, short[0], short_email)
    database = _database_name(db_session)

    assert script.run(db_session, "confirmar", expected_database=database) == 0
    capsys.readouterr()

    before = db_session.query(MemoryItem).count()
    code = script.run(db_session, "confirmar", expected_database=database)
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "0 pendentes" in out
    assert db_session.query(MemoryItem).count() == before


def test_wrong_database_exits_4_and_writes_nothing(
    db_session, capsys
) -> None:
    email = _unique_email()
    accounts = _make_paid_accounts(db_session, email=email, count=3)
    old = _legacy_memory(db_session, accounts[0], email)
    old_id = old.id

    code = script.run(
        db_session, "confirmar", expected_database="outro_banco"
    )

    assert code == script.EXIT_WRONG_DATABASE == 4
    assert "RECUSADO" in capsys.readouterr().out
    assert _reload(db_session, old_id).status == "active"


def test_main_rejects_invalid_usage(capsys) -> None:
    assert script.main([]) == script.EXIT_USAGE
    assert script.main(["apagar"]) == script.EXIT_USAGE
    assert script.main(["simular", "confirmar"]) == script.EXIT_USAGE


def test_mask_email_hides_name() -> None:
    assert script.mask_email("maria.silva@empresa.com") == "mar***@empresa.com"
    assert script.mask_email("ab@x.com") == "ab***@x.com"


def test_recomposes_memory_with_date_marker_but_old_rate_base(
    db_session,
) -> None:
    # Memoria ja com 'ocorrencias_sem_data_efetiva' mas sem
    # taxa_base='vencidos' (taxa calculada sobre todos os titulos) e
    # pendente; a gravada pelo calculo novo nao e mais.
    email = _unique_email()
    accounts = _make_paid_accounts(db_session, email=email, count=3)
    old = MemoryService(db_session).remember(
        memory_type="observation",
        title=f"Padrao {email}",
        content="Taxa sobre todos os titulos.",
        scope_type="account",
        account_id=accounts[0].id,
        source_type="derived",
        source_reference=f"client_behavior:{email}",
        confidence=0.5,
        memory_key=MEMORY_KEY,
        context_data={"email": email, "ocorrencias_sem_data_efetiva": 0},
    ).memory
    db_session.commit()

    assert (old.id, email) in script._pending(db_session)

    code = script.run(
        db_session, "confirmar", expected_database=_database_name(db_session)
    )

    assert code == script.EXIT_OK
    active = _active_for(db_session, accounts[0].id)
    assert active[0].context_data["taxa_base"] == "vencidos"
    assert (active[0].id, email) not in script._pending(db_session)
