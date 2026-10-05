"""
VALUE-3.4D-2b -- helpers de teste da proveniencia da evidencia.

NAO e um modulo de teste (nao coleta testes) e NAO altera `conftest.py`.

* `make_service(...)`: `EvidenceProvenanceService` com fontes VALIDAS por
  padrao (identidade de build valida sobre a arvore real, fingerprint
  medido == pin); qualquer fonte pode ser sobrescrita por teste.
* `make_binding(db)`: contexto REAL gravado no banco (commit proprio, como
  em producao) e o `ProvenanceBinding` correspondente -- para os testes que
  chamam `record_observed_fact` ou constroem observations diretamente.
* contextos NAO sao limpos pelo `conftest` (a tabela nao esta no TRUNCATE e
  e imutavel por trigger): cada chamada usa um floor unico, entao os testes
  nunca dependem de contextos pre-existentes.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from functools import lru_cache
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import build_identity as build_identity_module
from app.core.evidence_producer_spec import diagnose_producer_fingerprint
from app.database.database import SessionLocal
from app.database.database import engine
from app.services.evidence_provenance_service import (
    EvidenceProvenanceService,
)
from app.services.evidence_provenance_service import ProvenanceBinding


VALID_SHA = "0123456789abcdef0123456789abcdef01234567"
FAKE_REVISION = "7432a1c2dd66"

_FLOOR_BASE = datetime(2031, 1, 1, tzinfo=timezone.utc)
_FLOOR_COUNTER = itertools.count(1)


@lru_cache(maxsize=1)
def valid_identity_diagnosis():
    """Identidade valida sobre a arvore REAL (calculada uma vez)."""
    return build_identity_module.diagnose_build_identity(
        {
            build_identity_module.ENV_GIT_SHA: VALID_SHA,
            build_identity_module.ENV_GIT_DIRTY: "false",
        }
    )


def valid_identity_source():
    return valid_identity_diagnosis()


def valid_producer_source():
    return diagnose_producer_fingerprint()


def unique_floor() -> datetime:
    """Floor tz-aware unico por chamada (contexto distinto por teste)."""
    return _FLOOR_BASE + timedelta(
        microseconds=next(_FLOOR_COUNTER) * 7919
        + uuid.uuid4().int % 7000
    )


def make_service(
    session_factory: Callable[[], Session] = SessionLocal,
    **overrides,
) -> EvidenceProvenanceService:
    options = {
        "identity_source": valid_identity_source,
        "producer_source": valid_producer_source,
        "expected_revision_source": lambda: FAKE_REVISION,
        "actual_revision_source": lambda: FAKE_REVISION,
    }
    options.update(overrides)
    return EvidenceProvenanceService(session_factory, **options)


def make_binding(
    db: Session | None = None,
    *,
    floor: datetime | None = None,
    pass_id: uuid.UUID | None = None,
) -> ProvenanceBinding:
    """Contexto real no banco + binding (o `db` e aceito por simetria)."""
    resolution = make_service().resolve(
        activation_floor=floor or unique_floor(),
        pass_id=pass_id or uuid.uuid4(),
    )
    assert resolution.binding is not None, resolution.code
    return resolution.binding


def provenance_columns() -> dict[str, object]:
    """Colunas de proveniencia para construir `EscalationObservation`
    de `observed_fact` diretamente (contexto real no banco)."""
    binding = make_binding()
    return {
        "provenance_context_id": binding.context_id,
        "producer_pass_id": binding.pass_id,
    }


def purge_contexts() -> None:
    """Limpa contextos (e, por CASCADE, observations) de testes.

    O `conftest` nao limpa `evidence_provenance_contexts` (nao esta no
    TRUNCATE e a tabela e imutavel por trigger de UPDATE/DELETE -- o
    TRUNCATE nao dispara trigger de linha). Os modulos que criam contextos
    deliberadamente invalidos (digest forjado, linha SQL crua) chamam isto
    para nao deixar linhas "corrompidas" que a auditoria apontaria.
    """
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE evidence_provenance_contexts CASCADE")
        )
