"""
VALUE-3.4D-1 -- preflight de ativacao do activation floor (SOMENTE LEITURA).

Comandos:

    python scripts/evidence_floor_preflight.py capture
        Le o relogio do PostgreSQL (clock_timestamp()) e imprime o T0
        canonico (UTC, microssegundos, "Z"). O T0 e CAPTURADO aqui; nunca
        digitado/formatado a mao.

    python scripts/evidence_floor_preflight.py check <T0> [--now <ISO>]
        Valida <T0> contra o contrato estrito (app/core/
        evidence_floor_contract.py) e contra o relogio do PostgreSQL
        (ou --now, que precisa respeitar o mesmo formato estrito e
        existe para execucao offline/testes).

Saidas: exit 0 = OK; exit 2 = "ACTIVATION PREFLIGHT FAIL: <codigo>"
(violacao do contrato); exit 3 = banco indisponivel (fail-closed). Nunca
imprime traceback para entrada invalida.

Este script NAO escreve em banco, NAO configura floor, NAO recria
containers. A conexao usa a sessao read-only do PostgreSQL
(SET TRANSACTION READ ONLY antes do SELECT).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.evidence_floor_contract import FloorContractError
from app.core.evidence_floor_contract import canonical_utc
from app.core.evidence_floor_contract import parse_strict
from app.core.evidence_floor_contract import validate_for_activation


class DatabaseUnavailableError(RuntimeError):
    pass


def _db_clock_now() -> tuple[datetime, str]:
    """(clock_timestamp() do PostgreSQL em UTC, nome do banco)."""
    # Import tardio: `check --now` nao exige ambiente/Settings/banco.
    # Ambiente ausente/invalido vira fail-closed, nunca traceback.
    try:
        from sqlalchemy import text

        from app.core.config import settings
        from app.database.database import engine
    except Exception as error:
        raise DatabaseUnavailableError(
            f"settings_unavailable:{type(error).__name__}"
        ) from error

    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            value = connection.execute(
                text("SELECT clock_timestamp()")
            ).scalar_one()
    except Exception as error:
        raise DatabaseUnavailableError(
            type(error).__name__
        ) from error

    if value.tzinfo is None:
        raise DatabaseUnavailableError("clock_timestamp_naive")

    return value.astimezone(timezone.utc), settings.database_name


def _fail(code: str, detail: str = "") -> int:
    suffix = f" ({detail})" if detail else ""
    print(f"ACTIVATION PREFLIGHT FAIL: {code}{suffix}")
    return 2


def _run_capture() -> int:
    try:
        db_now, database_name = _db_clock_now()
    except DatabaseUnavailableError as error:
        print(f"ACTIVATION PREFLIGHT FAIL: db_unavailable ({error})")
        return 3

    print(f"T0={canonical_utc(db_now)}")
    print(f"clock_source=postgresql database={database_name}")
    return 0


def _run_check(raw_value: str, raw_now: str | None) -> int:
    database_name = "n/a (--now)"

    try:
        if raw_now is None:
            db_now, database_name = _db_clock_now()
        else:
            db_now = parse_strict(raw_now)
    except DatabaseUnavailableError as error:
        print(f"ACTIVATION PREFLIGHT FAIL: db_unavailable ({error})")
        return 3
    except FloorContractError as error:
        return _fail(f"now_{error.code}", "--now fora do contrato estrito")

    try:
        t0 = validate_for_activation(raw_value, db_now)
    except FloorContractError as error:
        return _fail(error.code)

    age_seconds = int(
        (db_now - parse_strict(t0)).total_seconds()
    )
    print(f"OK T0={t0}")
    print(f"db_now={canonical_utc(db_now)} age_seconds={age_seconds}")
    print(f"clock_source={'postgresql' if raw_now is None else 'argument'}")
    print(f"database={database_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence_floor_preflight",
        description=(
            "Preflight estrito do activation floor (somente leitura)."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser(
        "capture",
        help="le o relogio do PostgreSQL e imprime o T0 canonico",
    )

    check = subcommands.add_parser(
        "check", help="valida um T0 contra o contrato estrito"
    )
    check.add_argument("value", help="T0 a validar")
    check.add_argument(
        "--now",
        default=None,
        help=(
            "relogio de referencia (ISO estrito); omitido = "
            "relogio do PostgreSQL"
        ),
    )

    arguments = parser.parse_args(argv)

    if arguments.command == "capture":
        return _run_capture()

    return _run_check(arguments.value, arguments.now)


if __name__ == "__main__":
    sys.exit(main())
