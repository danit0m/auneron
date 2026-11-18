"""A5 -- contato e promessa por titulo: regra, permissao e migracao (offline).

Sem banco: a regra de promessa vigente/vencida e pura; a migracao e lida
por AST e comparada com o modelo. As rotas e os CHECKs reais no PostgreSQL
ficam em test_account_contact_events.py (precisa de auneron_test).
"""

import ast
import importlib.util
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from app.core.authorization import ROLE_PERMISSIONS
from app.core.authorization import has_permission
from app.models.account_contact_event import AccountContactEvent
from app.schemas.account import AccountContactEventCreate
from app.services.account_contact_summary import summarize_contact_events


BACKEND = Path(__file__).resolve().parents[1]
VERSION = (
    BACKEND / "migrations" / "versions" / "f1b7d3a9c520_add_account_contact_events.py"
)
TODAY = date(2026, 11, 18)


@pytest.fixture(autouse=True)
def clean_database():
    """Sobrescreve a limpeza global de banco: nada aqui usa banco."""
    yield


@dataclass
class Event:
    event_type: str
    promised_date: date | None = None
    created_at: datetime | None = None


def at(day: int) -> datetime:
    return datetime(2026, 11, day, 12, 0, tzinfo=timezone.utc)


def summary(events, status="atrasado", today=TODAY):
    return summarize_contact_events(events, account_status=status, today=today)


# ------------------------------------------------------------------ regra


def test_without_events_there_is_nothing() -> None:
    result = summary([])
    assert (result.ultimo_contato_em, result.promessa_vigente) == (None, None)
    assert result.promessa_vencida is False


@pytest.mark.parametrize(
    ("promised", "expected_overdue"),
    [
        (TODAY - timedelta(days=1), True),  # ontem: vencida
        (TODAY, False),  # hoje: ainda vigente, nao vencida
        (TODAY + timedelta(days=1), False),  # amanha
    ],
)
def test_promise_boundaries_use_business_today(promised, expected_overdue) -> None:
    result = summary([Event("promessa", promised, at(1))])
    assert result.promessa_vigente == promised
    assert result.promessa_vencida is expected_overdue


def test_contact_alone_is_not_a_promise() -> None:
    result = summary([Event("contato", None, at(2))])
    assert result.ultimo_contato_em == at(2)
    assert result.promessa_vigente is None
    assert result.promessa_vencida is False


def test_remarcacao_replaces_the_current_promise() -> None:
    old = TODAY - timedelta(days=5)
    new = TODAY + timedelta(days=3)
    result = summary(
        [Event("promessa", old, at(1)), Event("remarcacao", new, at(2))]
    )
    assert result.promessa_vigente == new
    assert result.promessa_vencida is False
    assert result.ultimo_contato_em == at(2)


def test_encerramento_removes_the_promise_and_the_alert() -> None:
    result = summary(
        [
            Event("promessa", TODAY - timedelta(days=2), at(1)),
            Event("encerramento", None, at(3)),
        ]
    )
    assert result.promessa_vigente is None
    assert result.promessa_vencida is False
    # encerramento nao conta como contato com o cliente
    assert result.ultimo_contato_em == at(1)


def test_a_new_promise_after_encerramento_is_vigente_again() -> None:
    promised = TODAY - timedelta(days=1)
    result = summary(
        [
            Event("promessa", TODAY, at(1)),
            Event("encerramento", None, at(2)),
            Event("promessa", promised, at(3)),
        ]
    )
    assert result.promessa_vigente == promised
    assert result.promessa_vencida is True


def test_paid_title_never_has_the_overdue_alert() -> None:
    result = summary(
        [Event("promessa", TODAY - timedelta(days=10), at(1))], status="pago"
    )
    assert result.promessa_vencida is False


def test_a_contact_after_the_promise_keeps_the_promise() -> None:
    promised = TODAY - timedelta(days=1)
    result = summary(
        [Event("promessa", promised, at(1)), Event("contato", None, at(2))]
    )
    assert result.promessa_vigente == promised
    assert result.promessa_vencida is True
    assert result.ultimo_contato_em == at(2)


# ----------------------------------------------------------------- schema


def test_schema_requires_date_for_promise_types_only() -> None:
    AccountContactEventCreate(event_type="contato")
    AccountContactEventCreate(event_type="encerramento", note="  ok ")
    AccountContactEventCreate(event_type="promessa", promised_date=TODAY)
    AccountContactEventCreate(event_type="remarcacao", promised_date=TODAY)
    for bad in (
        {"event_type": "promessa"},
        {"event_type": "remarcacao"},
        {"event_type": "contato", "promised_date": TODAY},
        {"event_type": "encerramento", "promised_date": TODAY},
        {"event_type": "outro"},
        {"event_type": "contato", "note": "x" * 281},
        {"event_type": "contato", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            AccountContactEventCreate(**bad)


def test_blank_note_becomes_absent_and_note_is_stripped() -> None:
    assert AccountContactEventCreate(event_type="contato", note="   ").note is None
    assert AccountContactEventCreate(event_type="contato", note=" a ").note == "a"


# -------------------------------------------------------------- permissao


def test_account_contact_goes_to_the_roles_that_request_payments() -> None:
    holders = {
        role for role, permissions in ROLE_PERMISSIONS.items()
        if "account:contact" in permissions
    }
    assert holders == {"manager", "executive", "administrator", "developer"}
    # mesmo conjunto humano que pede pagamento (skill:execute + execute_mutating)
    requesters = {
        role for role in ROLE_PERMISSIONS
        if has_permission(role, "skill:execute")
        and has_permission(role, "skill:execute_mutating")
        and role != "system"
    }
    assert holders == requesters
    assert not has_permission("viewer", "account:contact")
    assert not has_permission("analyst", "account:contact")
    assert not has_permission("system", "account:contact")


# --------------------------------------------------------------- migracao


def load():
    spec = importlib.util.spec_from_file_location("a5_migration", VERSION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_follows_the_previous_head() -> None:
    migration = load()
    assert migration.revision == "f1b7d3a9c520"
    assert migration.down_revision == "c8e2a5f1d704"


def test_upgrade_uses_only_create_table_and_create_index() -> None:
    tree = ast.parse(VERSION.read_text(encoding="utf-8"))
    upgrade = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    ops = {
        n.func.attr
        for n in ast.walk(upgrade)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "op"
    }
    assert ops == {"create_table", "create_index"}


def test_migration_checks_match_the_model() -> None:
    class Recorder:
        def __init__(self):
            self.tables = {}
            self.indexes = []

        def create_table(self, name, *items):
            self.tables[name] = items

        def create_index(self, name, table, columns):
            self.indexes.append((name, table, list(columns)))

    migration = load()
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()

    items = recorder.tables["account_contact_events"]
    migration_checks = {
        c.name: str(c.sqltext) for c in items if isinstance(c, CheckConstraint)
    }
    model_checks = {
        c.name: str(c.sqltext)
        for c in AccountContactEvent.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    assert migration_checks == model_checks
    assert len(model_checks) == 4
    assert recorder.indexes == [
        (
            "ix_account_contact_events_account_created",
            "account_contact_events",
            ["account_id", "created_at"],
        )
    ]
