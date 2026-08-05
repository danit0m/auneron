from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select, text

from app.core.money import to_money
from app.database.database import engine
from app.models.account import Account
from app.models.knowledge import Knowledge


SQLITE_PATH = BACKEND_DIR / "auneron.db"

ACCOUNT_COLUMNS = {
    "id",
    "cliente",
    "email",
    "whatsapp",
    "valor",
    "vencimento",
    "status",
    "created_at",
}

KNOWLEDGE_COLUMNS = {
    "id",
    "agent_name",
    "event_name",
    "knowledge_type",
    "severity",
    "title",
    "message",
    "account_id",
    "resolved",
    "created_at",
}


def parse_date(value: Any) -> date | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return date.fromisoformat(str(value)[:10])


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)

    # SQLite CURRENT_TIMESTAMP normalmente representa UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def get_sqlite_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    rows = connection.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()

    return {str(row["name"]) for row in rows}


def validate_columns(
    connection: sqlite3.Connection,
    table_name: str,
    required_columns: set[str],
) -> None:
    existing_columns = get_sqlite_columns(
        connection,
        table_name,
    )

    missing_columns = required_columns - existing_columns

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(
            f"A tabela {table_name!r} não possui as colunas: {missing}"
        )


def load_sqlite_rows(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[dict[str, Any]]:
    cursor = connection.execute(
        f'SELECT * FROM "{table_name}" ORDER BY id'
    )

    return [dict(row) for row in cursor.fetchall()]


def normalize_accounts(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for row in rows:
        if row["cliente"] is None:
            raise RuntimeError(
                f"Conta ID {row['id']} está sem cliente."
            )

        if row["valor"] is None:
            raise RuntimeError(
                f"Conta ID {row['id']} está sem valor."
            )

        if row["vencimento"] is None:
            raise RuntimeError(
                f"Conta ID {row['id']} está sem vencimento."
            )

        normalized.append(
            {
                "id": int(row["id"]),
                "cliente": str(row["cliente"]),
                "email": row["email"],
                "whatsapp": row["whatsapp"],
                "valor": to_money(row["valor"]),
                "vencimento": parse_date(row["vencimento"]),
                "status": row["status"],
                "created_at": parse_datetime(row["created_at"]),
            }
        )

    return normalized


def normalize_knowledge(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    required_fields = (
        "agent_name",
        "event_name",
        "knowledge_type",
        "severity",
        "title",
        "message",
        "resolved",
        "created_at",
    )

    for row in rows:
        missing_values = [
            field
            for field in required_fields
            if row[field] is None
        ]

        if missing_values:
            fields = ", ".join(missing_values)
            raise RuntimeError(
                f"Knowledge ID {row['id']} possui campos nulos: {fields}"
            )

        normalized.append(
            {
                "id": int(row["id"]),
                "agent_name": str(row["agent_name"]),
                "event_name": str(row["event_name"]),
                "knowledge_type": str(row["knowledge_type"]),
                "severity": str(row["severity"]),
                "title": str(row["title"]),
                "message": str(row["message"]),
                "account_id": (
                    int(row["account_id"])
                    if row["account_id"] is not None
                    else None
                ),
                "resolved": bool(row["resolved"]),
                "created_at": parse_datetime(row["created_at"]),
            }
        )

    return normalized


def validate_unique_ids(
    table_name: str,
    rows: list[dict[str, Any]],
) -> None:
    ids = [row["id"] for row in rows]

    if len(ids) != len(set(ids)):
        raise RuntimeError(
            f"A tabela {table_name!r} possui IDs duplicados."
        )


def load_source_data() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    if not SQLITE_PATH.exists():
        raise FileNotFoundError(
            f"Banco SQLite não encontrado: {SQLITE_PATH}"
        )

    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row

    try:
        validate_columns(
            connection,
            "accounts",
            ACCOUNT_COLUMNS,
        )
        validate_columns(
            connection,
            "knowledge",
            KNOWLEDGE_COLUMNS,
        )

        accounts = normalize_accounts(
            load_sqlite_rows(connection, "accounts")
        )
        knowledge = normalize_knowledge(
            load_sqlite_rows(connection, "knowledge")
        )

        validate_unique_ids("accounts", accounts)
        validate_unique_ids("knowledge", knowledge)

        return accounts, knowledge
    finally:
        connection.close()


def get_target_counts(connection: Any) -> tuple[int, int]:
    accounts_count = connection.scalar(
        select(func.count()).select_from(Account.__table__)
    )

    knowledge_count = connection.scalar(
        select(func.count()).select_from(Knowledge.__table__)
    )

    return int(accounts_count or 0), int(knowledge_count or 0)


def reset_sequences(connection: Any) -> None:
    connection.execute(
        text(
            """
            SELECT setval(
                pg_get_serial_sequence('accounts', 'id'),
                COALESCE((SELECT MAX(id) FROM accounts), 1),
                EXISTS (SELECT 1 FROM accounts)
            )
            """
        )
    )

    connection.execute(
        text(
            """
            SELECT setval(
                pg_get_serial_sequence('knowledge', 'id'),
                COALESCE((SELECT MAX(id) FROM knowledge), 1),
                EXISTS (SELECT 1 FROM knowledge)
            )
            """
        )
    )


def print_orphan_report(
    accounts: list[dict[str, Any]],
    knowledge: list[dict[str, Any]],
) -> None:
    account_ids = {row["id"] for row in accounts}

    orphan_rows = [
        row
        for row in knowledge
        if row["account_id"] is not None
        and row["account_id"] not in account_ids
    ]

    print(f"Referências órfãs em knowledge: {len(orphan_rows)}")

    if orphan_rows:
        examples = ", ".join(
            f"knowledge={row['id']} -> account={row['account_id']}"
            for row in orphan_rows[:10]
        )
        print(f"Exemplos: {examples}")


def detach_orphan_references(
    accounts: list[dict[str, Any]],
    knowledge: list[dict[str, Any]],
) -> int:
    """
    Remove apenas associações inválidas.

    Os registros de conhecimento são preservados, mas referências
    para contas inexistentes são convertidas para NULL no PostgreSQL.
    """

    account_ids = {row["id"] for row in accounts}
    detached_count = 0

    for row in knowledge:
        account_id = row["account_id"]

        if (
            account_id is not None
            and account_id not in account_ids
        ):
            row["account_id"] = None
            detached_count += 1

    return detached_count

def migrate(
    accounts: list[dict[str, Any]],
    knowledge: list[dict[str, Any]],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "LOCK TABLE accounts, knowledge "
                "IN EXCLUSIVE MODE"
            )
        )

        target_accounts, target_knowledge = get_target_counts(
            connection
        )

        if target_accounts != 0 or target_knowledge != 0:
            raise RuntimeError(
                "Migração cancelada: o PostgreSQL não está vazio. "
                f"accounts={target_accounts}, "
                f"knowledge={target_knowledge}"
            )

        if accounts:
            connection.execute(
                Account.__table__.insert(),
                accounts,
            )

        if knowledge:
            connection.execute(
                Knowledge.__table__.insert(),
                knowledge,
            )

        reset_sequences(connection)

        migrated_accounts, migrated_knowledge = get_target_counts(
            connection
        )

        if migrated_accounts != len(accounts):
            raise RuntimeError(
                "Quantidade migrada de accounts não confere."
            )

        if migrated_knowledge != len(knowledge):
            raise RuntimeError(
                "Quantidade migrada de knowledge não confere."
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migra dados do SQLite do Auneron para PostgreSQL."
        )
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Executa a migração. Sem essa opção, faz apenas validação.",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("AUNERON — MIGRAÇÃO SQLITE PARA POSTGRESQL")
    print("=" * 60)
    print(f"SQLite: {SQLITE_PATH}")
    print(f"Destino: {engine.dialect.name}")
    print()

    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            "O banco de destino configurado não é PostgreSQL."
        )

    accounts, knowledge = load_source_data()

    print(f"SQLite accounts:  {len(accounts)}")
    print(f"SQLite knowledge: {len(knowledge)}")
    print(f"Total:            {len(accounts) + len(knowledge)}")
    print()

    print_orphan_report(accounts, knowledge)
    print()

    detached_orphans = detach_orphan_references(
        accounts,
        knowledge,
    )

    if detached_orphans:
        print(
            "Correção planejada: "
            f"{detached_orphans} referência(s) órfã(s) "
            "serão migradas com account_id=NULL."
        )
        print()

    with engine.connect() as connection:
        target_accounts, target_knowledge = get_target_counts(
            connection
        )

    print(f"PostgreSQL accounts:  {target_accounts}")
    print(f"PostgreSQL knowledge: {target_knowledge}")
    print()

    if not args.execute:
        print("DRY RUN concluído.")
        print("Nenhum dado foi alterado.")
        print()
        print(
            "Para executar a migração, use:"
        )
        print(
            "python scripts/migrate_sqlite_to_postgres.py --execute"
        )
        return

    migrate(accounts, knowledge)

    print("Migração concluída com sucesso.")
    print(f"Accounts migradas:  {len(accounts)}")
    print(f"Knowledge migradas: {len(knowledge)}")
    print("O banco SQLite original não foi modificado.")


if __name__ == "__main__":
    main()