"""
VALUE-3.4D-2b -- identidade de schema: revisao ESPERADA pelo codigo x
revisao REAL do banco.

Sao evidencias distintas (o codigo pode ter sido construido esperando a
revisao X enquanto o banco esta em Y). Registrar so uma esconderia
exatamente a classe de problema que a proveniencia deve revelar.

* `expected_schema_revision`: head do diretorio `migrations/` do CODIGO,
  via `alembic.script.ScriptDirectory` (somente le arquivos; nenhum banco,
  nenhuma escrita). Exatamente 1 head.
* `actual_database_revision`: `SELECT version_num FROM alembic_version` em
  sessao propria e curta (leitura simples). Exatamente 1 linha.

Nenhuma migration e executada. `unknown` nunca e uma revisao valida.
Falha em qualquer um dos dois => erro tipado com codigo estavel (o
produtor abstem).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session


CODE_EXPECTED_UNAVAILABLE = "expected_revision_unavailable"
CODE_ACTUAL_UNAVAILABLE = "actual_revision_unavailable"
CODE_MISMATCH = "schema_revision_mismatch"

INVALID_REVISION_MARKER = "<invalid>"

_REVISION_FORMAT = re.compile(r"[A-Za-z0-9_]{1,64}")


class SchemaIdentityError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def is_valid_revision(value: object) -> bool:
    return (
        isinstance(value, str)
        and _REVISION_FORMAT.fullmatch(value) is not None
        and value.lower() != "unknown"
    )


def revision_view(value: object) -> str | None:
    """Representacao diagnostica: so uma revisao valida aparece; qualquer
    outro valor presente vira um marcador constante (nunca o valor cru)."""
    if value is None:
        return None
    return str(value) if is_valid_revision(value) else INVALID_REVISION_MARKER


def default_migrations_dir() -> Path:
    # backend/app/core/schema_identity.py -> backend/migrations
    return Path(__file__).resolve().parents[2] / "migrations"


def expected_schema_revision(
    migrations_dir: Path | str | None = None,
) -> str:
    directory = Path(
        default_migrations_dir()
        if migrations_dir is None
        else migrations_dir
    )
    try:
        # import tardio: ~0,5 s a frio; so quando realmente necessario
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory(str(directory)).get_heads()
    except Exception:
        raise SchemaIdentityError(
            CODE_EXPECTED_UNAVAILABLE, "script_directory_unreadable"
        ) from None

    if len(heads) != 1:
        raise SchemaIdentityError(
            CODE_EXPECTED_UNAVAILABLE, f"heads={len(heads)}"
        )
    if not is_valid_revision(heads[0]):
        raise SchemaIdentityError(
            CODE_EXPECTED_UNAVAILABLE, "head_not_a_valid_revision"
        )
    return heads[0]


def read_actual_database_revision(
    session_factory: Callable[[], Session],
) -> str:
    """Leitura simples; sessao propria, sempre fechada (rollback)."""
    try:
        session = session_factory()
    except Exception:
        raise SchemaIdentityError(
            CODE_ACTUAL_UNAVAILABLE, "session_unavailable"
        ) from None

    try:
        rows = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).fetchall()
    except Exception:
        raise SchemaIdentityError(
            CODE_ACTUAL_UNAVAILABLE, "query_failed"
        ) from None
    finally:
        session.close()

    if len(rows) != 1:
        raise SchemaIdentityError(
            CODE_ACTUAL_UNAVAILABLE, f"rows={len(rows)}"
        )
    value = rows[0][0]
    if not is_valid_revision(value):
        raise SchemaIdentityError(
            CODE_ACTUAL_UNAVAILABLE, "value_not_a_valid_revision"
        )
    return str(value)


# Memoizacao por processo do head ESPERADO (so le arquivos do codigo);
# o `actual` NUNCA e memoizado (le o banco a cada resolucao).


class _ProcessExpectedCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: str | None = None

    def get(self) -> str:
        with self._lock:
            if self._value is None:
                self._value = expected_schema_revision()
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = None


_PROCESS_EXPECTED = _ProcessExpectedCache()


def process_expected_schema_revision() -> str:
    return _PROCESS_EXPECTED.get()


def reset_process_expected_schema_revision() -> None:
    """Reset controlado da memoizacao (uso de testes)."""
    _PROCESS_EXPECTED.reset()
