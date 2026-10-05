"""
VALUE-3.4D-2b -- servico de proveniencia da evidencia.

Resolve o `EvidenceProvenanceContext` sob o qual o produtor automatico
interpreta a evidencia e entrega um `ProvenanceBinding`
(`provenance_context_id` + `producer_pass_id`) para a observation.

Duas perguntas distintas (nunca misturar):

* contexto  -- "sob qual contexto verificavel este Auneron interpretou a
  evidencia?" (build + produtor + floor efetivo + schema; imutavel,
  content-addressed por `epc1`, uma linha por combinacao distinta);
* `producer_pass_id` -- "em qual passagem concreta do worker esta
  observation foi materializada?" (UUID gerado na entrada do pass; sem
  tabela de passes).

Lifecycle de `resolve()` (NUNCA levanta; falha => resultado com codigo
estavel e NENHUMA escrita de evidencia):

    identidade de build -> floor canonico -> produtor (spec/fingerprint)
    -> revisao esperada -> revisao real -> esperada == real
    -> epc1 -> SELECT por digest (estado estavel: so leitura)
    -> (ausente) INSERT ... ON CONFLICT DO NOTHING + commit proprio
    -> SELECT/validacao das 12 colunas + recomputo do digest.

O contexto e commitado em SESSAO PROPRIA ANTES da observation: um crash
entre os dois deixa no maximo um contexto orfao (inofensivo); jamais uma
observation automatica sem proveniencia (FK + CHECK no banco).

Algoritmo `epc1` (versionado; mudar campos, ordem ou serializacao exige um
novo id): `context_digest = sha256(UTF8(JSON canonico))`, JSON restrito
(chaves em ordem lexicografica, sem espacos, ASCII, somente string /
inteiro / booleano -- nunca `null`, nunca float, nunca `repr()` Python),
`activation_floor` no formato canonico UTC com 6 digitos de microssegundo.
Excluidos do digest: `id`, `created_at`, o proprio `context_digest` e todo
dado de execucao (pass, host, PID, tempo).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.build_identity import BuildIdentityDiagnosis
from app.core.build_identity import process_build_identity_diagnosis
from app.core.evidence_floor_contract import canonical_utc
from app.core.evidence_producer_spec import PRODUCER_SPEC
from app.core.evidence_producer_spec import ProducerFingerprintDiagnosis
from app.core.evidence_producer_spec import (
    process_producer_fingerprint_diagnosis,
)
from app.core.schema_identity import CODE_MISMATCH
from app.core.schema_identity import SchemaIdentityError
from app.core.schema_identity import process_expected_schema_revision
from app.core.schema_identity import read_actual_database_revision
from app.core.schema_identity import revision_view
from app.models.evidence_provenance_context import EvidenceProvenanceContext


CONTEXT_DIGEST_ALGORITHM = "epc1"

# Os 12 campos OBRIGATORIOS do digest (ordem lexicografica = ordem de
# serializacao). Nenhum e opcional; nenhum pode ser `null`.
CONTEXT_FIELD_NAMES = (
    "activation_floor",
    "actual_database_revision",
    "context_digest_algorithm",
    "expected_schema_revision",
    "git_dirty",
    "git_sha",
    "producer_fingerprint",
    "producer_fingerprint_algorithm",
    "producer_spec",
    "source_digest",
    "source_digest_algorithm",
    "source_file_count",
)

# Codigos de bloqueio ESTAVEIS (conjunto fechado, contrato V1).
CODE_FLOOR_NOT_TIMEZONE_AWARE = "floor_not_timezone_aware"
CODE_CONTEXT_PERSIST_FAILED = "context_persist_failed"
CODE_CONTEXT_INTEGRITY_ERROR = "context_integrity_error"
CODE_UNEXPECTED_ERROR = "provenance_unexpected_error"

BLOCK_CODES = (
    # identidade de build (D-2a, repassados)
    "identity_sha_invalid",
    "identity_dirty_invalid",
    "identity_dirty",
    "source_digest_error",
    # produtor
    "producer_fingerprint_unavailable",
    "producer_fingerprint_mismatch",
    # floor
    CODE_FLOOR_NOT_TIMEZONE_AWARE,
    # schema
    "expected_revision_unavailable",
    "actual_revision_unavailable",
    CODE_MISMATCH,
    # persistencia do contexto
    CODE_CONTEXT_PERSIST_FAILED,
    CODE_CONTEXT_INTEGRITY_ERROR,
    CODE_UNEXPECTED_ERROR,
)

_HEX_64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ProvenanceBinding:
    """Par imutavel entregue a cada observation automatica."""

    context_id: int
    pass_id: uuid.UUID

    def __post_init__(self) -> None:
        if (
            isinstance(self.context_id, bool)
            or not isinstance(self.context_id, int)
            or self.context_id < 1
        ):
            raise ValueError("context_id invalido.")
        if not isinstance(self.pass_id, uuid.UUID):
            raise ValueError("pass_id invalido.")


@dataclass(frozen=True)
class ProvenanceResolution:
    binding: ProvenanceBinding | None
    code: str | None
    failures: tuple[str, ...] = ()
    expected_revision: str | None = None
    actual_revision: str | None = None
    context_created: bool = False

    @property
    def blocked(self) -> bool:
        return self.binding is None


# ---------------------------------------------------------------------
# epc1
# ---------------------------------------------------------------------


def _encode_value(name: str, value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError(f"{name}: valor nao ASCII.") from None
        if '"' in value or "\\" in value or any(
            ord(character) < 0x20 for character in value
        ):
            raise ValueError(f"{name}: caractere nao canonico.")
        return f'"{value}"'
    raise ValueError(f"{name}: tipo nao permitido.")


def canonical_json(fields: Mapping[str, object]) -> str:
    """JSON canonico do `epc1`: exatamente os 12 campos, ordem
    lexicografica, sem espacos, tipos restritos."""
    if set(fields) != set(CONTEXT_FIELD_NAMES):
        raise ValueError("campos do contexto incompletos ou extras.")
    parts = []
    for name in sorted(CONTEXT_FIELD_NAMES):
        parts.append(f'"{name}":{_encode_value(name, fields[name])}')
    return "{" + ",".join(parts) + "}"


def compute_context_digest(fields: Mapping[str, object]) -> str:
    return hashlib.sha256(
        canonical_json(fields).encode("utf-8")
    ).hexdigest()


def context_fields_from_row(
    row: EvidenceProvenanceContext,
) -> dict[str, object]:
    return {
        "activation_floor": canonical_utc(row.activation_floor),
        "actual_database_revision": row.actual_database_revision,
        "context_digest_algorithm": row.context_digest_algorithm,
        "expected_schema_revision": row.expected_schema_revision,
        "git_dirty": row.git_dirty,
        "git_sha": row.git_sha,
        "producer_fingerprint": row.producer_fingerprint,
        "producer_fingerprint_algorithm": (
            row.producer_fingerprint_algorithm
        ),
        "producer_spec": row.producer_spec,
        "source_digest": row.source_digest,
        "source_digest_algorithm": row.source_digest_algorithm,
        "source_file_count": row.source_file_count,
    }


def verify_row_integrity(row: EvidenceProvenanceContext) -> bool:
    """O digest armazenado == epc1 recomputado das colunas da linha."""
    try:
        return (
            _HEX_64.fullmatch(row.context_digest) is not None
            and compute_context_digest(context_fields_from_row(row))
            == row.context_digest
        )
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------
# servico
# ---------------------------------------------------------------------


class EvidenceProvenanceService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        identity_source: Callable[
            [], BuildIdentityDiagnosis
        ] = process_build_identity_diagnosis,
        producer_source: Callable[
            [], ProducerFingerprintDiagnosis
        ] = process_producer_fingerprint_diagnosis,
        expected_revision_source: Callable[
            [], str
        ] = process_expected_schema_revision,
        actual_revision_source: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._identity_source = identity_source
        self._producer_source = producer_source
        self._expected_revision_source = expected_revision_source
        self._actual_revision_source = (
            actual_revision_source
            if actual_revision_source is not None
            else lambda: read_actual_database_revision(session_factory)
        )

    @staticmethod
    def _blocked(
        code: str,
        failures: tuple[str, ...] = (),
        *,
        expected: str | None = None,
        actual: str | None = None,
    ) -> ProvenanceResolution:
        return ProvenanceResolution(
            binding=None,
            code=code,
            failures=failures or (code,),
            expected_revision=revision_view(expected),
            actual_revision=revision_view(actual),
        )

    def resolve(
        self,
        *,
        activation_floor: datetime,
        pass_id: uuid.UUID,
    ) -> ProvenanceResolution:
        """NUNCA levanta: falha vira resultado com codigo estavel."""
        try:
            return self._resolve(activation_floor, pass_id)
        except Exception:
            return self._blocked(CODE_UNEXPECTED_ERROR)

    def _resolve(
        self, activation_floor: datetime, pass_id: uuid.UUID
    ) -> ProvenanceResolution:
        # 1. identidade de build (D-2a): conjuntiva, sem excecao
        identity = self._identity_source()
        if not identity.valid or identity.identity is None:
            return self._blocked(
                str(identity.code), tuple(identity.failures)
            )

        # 2. floor efetivo, canonico
        if (
            not isinstance(activation_floor, datetime)
            or activation_floor.tzinfo is None
        ):
            return self._blocked(CODE_FLOOR_NOT_TIMEZONE_AWARE)
        canonical_floor = canonical_utc(activation_floor)

        # 3. produtor: spec (constante) + fingerprint medido == pin
        producer = self._producer_source()
        if not producer.valid or producer.measured is None:
            return self._blocked(str(producer.code))

        # 4. revisao esperada (codigo)
        try:
            expected = self._expected_revision_source()
        except SchemaIdentityError as error:
            return self._blocked(error.code)

        # 5. revisao real (banco; leitura simples)
        try:
            actual = self._actual_revision_source()
        except SchemaIdentityError as error:
            return self._blocked(error.code, expected=expected)

        # 6. esperada == real, senao ABSTER
        if expected != actual:
            return self._blocked(
                CODE_MISMATCH, expected=expected, actual=actual
            )

        # 7. epc1
        build = identity.identity
        fields: dict[str, object] = {
            "activation_floor": canonical_floor,
            "actual_database_revision": actual,
            "context_digest_algorithm": CONTEXT_DIGEST_ALGORITHM,
            "expected_schema_revision": expected,
            "git_dirty": build.git_dirty,
            "git_sha": build.git_sha,
            "producer_fingerprint": producer.measured.digest,
            "producer_fingerprint_algorithm": (
                producer.measured.algorithm
            ),
            "producer_spec": PRODUCER_SPEC,
            "source_digest": build.source_digest.digest,
            "source_digest_algorithm": build.source_digest.algorithm,
            "source_file_count": build.source_digest.file_count,
        }
        digest = compute_context_digest(fields)

        # 8. contexto: SELECT primeiro (estado estavel = so leitura)
        outcome = self._get_or_create(
            digest, fields, activation_floor
        )
        if isinstance(outcome, str):
            return self._blocked(
                outcome, expected=expected, actual=actual
            )
        context_id, created = outcome

        return ProvenanceResolution(
            binding=ProvenanceBinding(
                context_id=context_id, pass_id=pass_id
            ),
            code=None,
            expected_revision=revision_view(expected),
            actual_revision=revision_view(actual),
            context_created=created,
        )

    def _get_or_create(
        self,
        digest: str,
        fields: Mapping[str, object],
        activation_floor: datetime,
    ) -> tuple[int, bool] | str:
        """Sessao PROPRIA e commit PROPRIO (antes da observation)."""
        session = self._session_factory()
        try:
            row = self._select(session, digest)
            created = False

            if row is None:
                statement = (
                    postgresql_insert(EvidenceProvenanceContext)
                    .values(
                        context_digest=digest,
                        context_digest_algorithm=fields[
                            "context_digest_algorithm"
                        ],
                        git_sha=fields["git_sha"],
                        git_dirty=fields["git_dirty"],
                        source_digest_algorithm=fields[
                            "source_digest_algorithm"
                        ],
                        source_digest=fields["source_digest"],
                        source_file_count=fields["source_file_count"],
                        producer_spec=fields["producer_spec"],
                        producer_fingerprint_algorithm=fields[
                            "producer_fingerprint_algorithm"
                        ],
                        producer_fingerprint=fields[
                            "producer_fingerprint"
                        ],
                        activation_floor=activation_floor,
                        expected_schema_revision=fields[
                            "expected_schema_revision"
                        ],
                        actual_database_revision=fields[
                            "actual_database_revision"
                        ],
                    )
                    .on_conflict_do_nothing()
                    .returning(EvidenceProvenanceContext.id)
                )
                inserted = session.execute(statement).scalar_one_or_none()
                session.commit()
                created = inserted is not None
                row = self._select(session, digest)

            if row is None:
                # conflito de CONTEUDO com outro digest (digest forjado
                # ou algoritmo inconsistente): nunca silencioso
                return CODE_CONTEXT_INTEGRITY_ERROR

            if not verify_row_integrity(row) or (
                context_fields_from_row(row) != dict(fields)
            ):
                return CODE_CONTEXT_INTEGRITY_ERROR

            return int(row.id), created
        except SQLAlchemyError:
            try:
                session.rollback()
            except SQLAlchemyError:
                pass
            return CODE_CONTEXT_PERSIST_FAILED
        finally:
            session.close()

    @staticmethod
    def _select(
        session: Session, digest: str
    ) -> EvidenceProvenanceContext | None:
        return session.execute(
            select(EvidenceProvenanceContext).where(
                EvidenceProvenanceContext.context_digest == digest
            )
        ).scalar_one_or_none()
