"""
VALUE-3.4D-2b -- EvidenceProvenanceService.

`resolve()` NUNCA levanta: toda falha vira resultado com codigo estavel e
NENHUMA escrita. A ordem das verificacoes e deterministica (identidade ->
floor -> produtor -> revisao esperada -> revisao real -> igualdade ->
persistencia). O contexto e gravado em sessao propria, com commit proprio,
ANTES da observation; o estado estavel e so leitura.

Os testes de banco criam contextos reais (nao limpos pelo conftest: a tabela
e imutavel por trigger); cada teste usa um floor unico.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import threading
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core import build_identity as bi
from app.core.evidence_floor_contract import canonical_utc
from app.core.evidence_producer_spec import diagnose_producer_fingerprint
from app.core.schema_identity import SchemaIdentityError
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.evidence_provenance_context import EvidenceProvenanceContext
from app.services import evidence_provenance_service as eps

from evidence_provenance_helpers import FAKE_REVISION
from evidence_provenance_helpers import VALID_SHA
from evidence_provenance_helpers import make_service
from evidence_provenance_helpers import purge_contexts
from evidence_provenance_helpers import unique_floor
from evidence_provenance_helpers import valid_identity_diagnosis


@pytest.fixture(autouse=True)
def _purge_provenance_contexts():
    purge_contexts()
    yield
    purge_contexts()


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "evidence_provenance_service.py"
)

EXPECTED_BLOCK_CODES = (
    "identity_sha_invalid",
    "identity_dirty_invalid",
    "identity_dirty",
    "source_digest_error",
    "producer_fingerprint_unavailable",
    "producer_fingerprint_mismatch",
    "floor_not_timezone_aware",
    "expected_revision_unavailable",
    "actual_revision_unavailable",
    "schema_revision_mismatch",
    "context_persist_failed",
    "context_integrity_error",
    "provenance_unexpected_error",
)


def no_database():
    raise AssertionError("o banco NAO deve ser tocado neste cenario")


def resolve_no_db(**overrides):
    service = make_service(no_database, **overrides)
    return service.resolve(
        activation_floor=unique_floor(), pass_id=uuid.uuid4()
    )


def identity_from(environ: dict[str, str], root=None):
    return lambda: bi.diagnose_build_identity(environ, root)


def capture_statements(run) -> list[str]:
    statements: list[str] = []

    def listener(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return statements


def write_statements(statements: list[str]) -> list[str]:
    return [
        s
        for s in statements
        if re.match(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE)\b", s, re.I)
    ]


# ---------------------------------------------------------------------
# 1. contrato fechado de codigos
# ---------------------------------------------------------------------


def test_the_closed_set_of_13_stable_codes() -> None:
    assert eps.BLOCK_CODES == EXPECTED_BLOCK_CODES
    assert len(set(eps.BLOCK_CODES)) == 13


# ---------------------------------------------------------------------
# 2. bloqueios SEM tocar o banco (e na ordem deterministica)
# ---------------------------------------------------------------------

IDENTITY_CASES = [
    pytest.param({}, "identity_sha_invalid", id="no_claims"),
    pytest.param(
        {bi.ENV_GIT_SHA: "unknown", bi.ENV_GIT_DIRTY: "false"},
        "identity_sha_invalid",
        id="sha_unknown",
    ),
    pytest.param(
        {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "unknown"},
        "identity_dirty_invalid",
        id="dirty_unknown",
    ),
    pytest.param(
        {bi.ENV_GIT_SHA: VALID_SHA},
        "identity_dirty_invalid",
        id="dirty_missing",
    ),
    pytest.param(
        {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "true"},
        "identity_dirty",
        id="dirty_true",
    ),
]


@pytest.mark.parametrize("environ,code", IDENTITY_CASES)
def test_invalid_build_identity_blocks_without_touching_the_database(
    environ: dict[str, str], code: str
) -> None:
    resolution = resolve_no_db(identity_source=identity_from(environ))

    assert resolution.binding is None
    assert resolution.blocked is True
    assert resolution.code == code
    assert code in resolution.failures
    assert resolution.code in eps.BLOCK_CODES


@pytest.mark.parametrize(
    "code",
    [
        "identity_sha_invalid",
        "identity_dirty_invalid",
        "identity_dirty",
        "source_digest_error",
    ],
)
def test_valid_false_stays_authoritative_over_a_populated_identity(
    code: str,
) -> None:
    """Contrato defensivo: `valid=False` e a barreira, nao `identity is
    None`. Um diagnostico INCOERENTE (`valid=False` com `identity` nao
    nula, estruturalmente valida) bloqueia com o codigo do proprio
    diagnostico e sem nenhuma escrita de contexto."""
    populated = valid_identity_diagnosis().identity
    assert populated is not None
    inconsistent = dataclasses.replace(
        valid_identity_diagnosis(),
        valid=False,
        code=code,
        failures=(code,),
        identity=populated,
    )
    # a incoerencia e real: e exatamente `valid=False` + `identity` nao nula
    assert inconsistent.valid is False
    assert inconsistent.identity is not None

    statements = capture_statements(
        lambda: _resolve_inconsistent(inconsistent)
    )
    resolution = _resolve_inconsistent(inconsistent)

    assert resolution.binding is None
    assert resolution.blocked is True
    # o codigo vem do diagnostico fornecido, sem codigo especial
    assert resolution.code == code
    assert resolution.failures == (code,)
    assert resolution.code in eps.BLOCK_CODES
    # nenhuma escrita e nenhum acesso a tabela de contextos
    assert write_statements(statements) == []
    assert not [s for s in statements if "evidence_provenance_contexts" in s]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evidence_provenance_contexts")
            ).scalar_one()
            == 0
        )


def _resolve_inconsistent(diagnosis):
    return resolve_no_db(identity_source=lambda: diagnosis)


def test_source_digest_failure_blocks(tmp_path: Path) -> None:
    resolution = resolve_no_db(
        identity_source=identity_from(
            {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "false"},
            tmp_path / "missing-root",
        )
    )

    assert resolution.binding is None
    assert resolution.code == "source_digest_error"


@pytest.mark.parametrize(
    "floor",
    [
        datetime(2031, 1, 1),
        "2031-01-01T00:00:00Z",
        None,
        1_900_000_000,
    ],
    ids=["naive", "string", "none", "epoch_int"],
)
def test_floor_without_timezone_blocks(floor: object) -> None:
    service = make_service(no_database)

    resolution = service.resolve(
        activation_floor=floor, pass_id=uuid.uuid4()  # type: ignore[arg-type]
    )

    assert resolution.binding is None
    assert resolution.code == "floor_not_timezone_aware"


def test_producer_fingerprint_mismatch_blocks() -> None:
    resolution = resolve_no_db(
        producer_source=lambda: diagnose_producer_fingerprint(
            pinned="0" * 64
        )
    )

    assert resolution.binding is None
    assert resolution.code == "producer_fingerprint_mismatch"


def test_producer_fingerprint_unavailable_blocks() -> None:
    resolution = resolve_no_db(
        producer_source=lambda: diagnose_producer_fingerprint(
            sources={"app/services/escalation_observation_service.py": "x"}
        )
    )

    assert resolution.binding is None
    assert resolution.code == "producer_fingerprint_unavailable"


def test_expected_revision_unavailable_blocks() -> None:
    def broken():
        raise SchemaIdentityError("expected_revision_unavailable", "heads=0")

    resolution = resolve_no_db(expected_revision_source=broken)

    assert resolution.binding is None
    assert resolution.code == "expected_revision_unavailable"


def test_actual_revision_unavailable_blocks_and_keeps_the_expected() -> None:
    def broken():
        raise SchemaIdentityError("actual_revision_unavailable", "rows=0")

    resolution = resolve_no_db(actual_revision_source=broken)

    assert resolution.binding is None
    assert resolution.code == "actual_revision_unavailable"
    assert resolution.expected_revision == FAKE_REVISION
    assert resolution.actual_revision is None


def test_schema_mismatch_blocks_and_reports_both_revisions() -> None:
    resolution = resolve_no_db(
        expected_revision_source=lambda: "aaaaaaaaaaaa",
        actual_revision_source=lambda: "bbbbbbbbbbbb",
    )

    assert resolution.binding is None
    assert resolution.code == "schema_revision_mismatch"
    assert resolution.expected_revision == "aaaaaaaaaaaa"
    assert resolution.actual_revision == "bbbbbbbbbbbb"


def test_diagnostic_revisions_are_sanitized() -> None:
    resolution = resolve_no_db(
        expected_revision_source=lambda: "sk-live: secret",
        actual_revision_source=lambda: "other",
    )

    assert resolution.code == "schema_revision_mismatch"
    assert resolution.expected_revision == "<invalid>"
    assert "secret" not in repr(resolution)


def test_deterministic_order_identity_comes_first() -> None:
    def explode():
        raise AssertionError("fonte posterior nao deve ser consultada")

    resolution = make_service(
        no_database,
        identity_source=identity_from({}),
        producer_source=explode,
        expected_revision_source=explode,
        actual_revision_source=explode,
    ).resolve(
        activation_floor=datetime(2031, 1, 1), pass_id=uuid.uuid4()
    )

    # identidade invalida E floor naive: vence a identidade (1o da ordem)
    assert resolution.code == "identity_sha_invalid"


def test_deterministic_order_floor_before_producer_and_schema() -> None:
    def explode():
        raise AssertionError("fonte posterior nao deve ser consultada")

    resolution = make_service(
        no_database,
        producer_source=explode,
        expected_revision_source=explode,
        actual_revision_source=explode,
    ).resolve(
        activation_floor=datetime(2031, 1, 1), pass_id=uuid.uuid4()
    )

    assert resolution.code == "floor_not_timezone_aware"


def test_deterministic_order_producer_before_schema() -> None:
    def explode():
        raise AssertionError("revisao nao deve ser consultada")

    resolution = make_service(
        no_database,
        producer_source=lambda: diagnose_producer_fingerprint(
            pinned="0" * 64
        ),
        expected_revision_source=explode,
        actual_revision_source=explode,
    ).resolve(activation_floor=unique_floor(), pass_id=uuid.uuid4())

    assert resolution.code == "producer_fingerprint_mismatch"


def test_actual_is_not_read_when_expected_is_unavailable() -> None:
    def broken():
        raise SchemaIdentityError("expected_revision_unavailable", "x")

    def explode():
        raise AssertionError("actual nao deve ser lido")

    resolution = resolve_no_db(
        expected_revision_source=broken, actual_revision_source=explode
    )

    assert resolution.code == "expected_revision_unavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"identity_source": lambda: 1 / 0},
        {"producer_source": lambda: 1 / 0},
        {"expected_revision_source": lambda: 1 / 0},
        {"actual_revision_source": lambda: 1 / 0},
        {"identity_source": lambda: None},
        {"producer_source": lambda: object()},
    ],
    ids=[
        "identity_raises",
        "producer_raises",
        "expected_raises",
        "actual_raises",
        "identity_returns_none",
        "producer_returns_garbage",
    ],
)
def test_resolve_never_raises(overrides) -> None:
    resolution = resolve_no_db(**overrides)

    assert resolution.binding is None
    assert resolution.code == "provenance_unexpected_error"


def test_every_emitted_code_belongs_to_the_closed_set() -> None:
    emitted = set()
    for environ, _ in [(p.values[0], p.values[1]) for p in IDENTITY_CASES]:
        emitted.add(
            resolve_no_db(identity_source=identity_from(environ)).code
        )
    emitted.add(resolve_no_db(identity_source=lambda: 1 / 0).code)
    emitted.add(
        resolve_no_db(
            expected_revision_source=lambda: "a", actual_revision_source=lambda: "b"
        ).code
    )

    assert emitted <= set(eps.BLOCK_CODES)


# ---------------------------------------------------------------------
# 3. ProvenanceBinding
# ---------------------------------------------------------------------


@pytest.mark.parametrize("context_id", [0, -1, True, False, "1", None, 1.5])
def test_binding_rejects_an_invalid_context_id(context_id: object) -> None:
    with pytest.raises(ValueError):
        eps.ProvenanceBinding(context_id=context_id, pass_id=uuid.uuid4())  # type: ignore[arg-type]


@pytest.mark.parametrize("pass_id", ["not-a-uuid", None, 1, b"x" * 16])
def test_binding_rejects_an_invalid_pass_id(pass_id: object) -> None:
    with pytest.raises(ValueError):
        eps.ProvenanceBinding(context_id=1, pass_id=pass_id)  # type: ignore[arg-type]


def test_binding_is_frozen() -> None:
    binding = eps.ProvenanceBinding(context_id=1, pass_id=uuid.uuid4())

    with pytest.raises(Exception):
        binding.context_id = 2  # type: ignore[misc]


# ---------------------------------------------------------------------
# 4. contexto real (banco)
# ---------------------------------------------------------------------


def load_context(context_id: int) -> EvidenceProvenanceContext:
    with SessionLocal() as session:
        row = session.get(EvidenceProvenanceContext, context_id)
        assert row is not None
        session.expunge(row)
        return row


def test_valid_provenance_creates_a_committed_context() -> None:
    floor = unique_floor()
    pass_id = uuid.uuid4()

    resolution = make_service().resolve(
        activation_floor=floor, pass_id=pass_id
    )

    assert resolution.binding is not None
    assert resolution.code is None
    assert resolution.failures == ()
    assert resolution.context_created is True
    assert resolution.binding.pass_id == pass_id
    # commitado: visivel por OUTRA sessao
    row = load_context(resolution.binding.context_id)
    assert row.activation_floor == floor
    assert row.git_sha == VALID_SHA
    assert row.git_dirty is False
    assert row.source_digest_algorithm == "sd1"
    assert row.producer_fingerprint_algorithm == "pf1"
    assert row.producer_spec == "escalation_payment_observation:v1"
    assert row.context_digest_algorithm == "epc1"
    assert row.expected_schema_revision == FAKE_REVISION
    assert row.actual_database_revision == FAKE_REVISION
    assert row.source_file_count == (
        valid_identity_diagnosis().source_digest.file_count
    )


def test_the_stored_digest_is_the_recomputed_epc1() -> None:
    resolution = make_service().resolve(
        activation_floor=unique_floor(), pass_id=uuid.uuid4()
    )
    row = load_context(resolution.binding.context_id)

    assert eps.verify_row_integrity(row) is True
    assert eps.compute_context_digest(
        eps.context_fields_from_row(row)
    ) == row.context_digest
    assert eps.context_fields_from_row(row)["activation_floor"] == (
        canonical_utc(row.activation_floor)
    )


def test_steady_state_is_read_only_and_reuses_the_context() -> None:
    floor = unique_floor()
    service = make_service()
    first = service.resolve(activation_floor=floor, pass_id=uuid.uuid4())
    holder: list[eps.ProvenanceResolution] = []

    statements = capture_statements(
        lambda: holder.append(
            service.resolve(activation_floor=floor, pass_id=uuid.uuid4())
        )
    )

    second = holder[0]
    assert second.binding is not None
    assert second.binding.context_id == first.binding.context_id
    assert second.context_created is False
    # estado estavel: nenhuma escrita (so SELECT)
    assert write_statements(statements) == []
    assert any(s.lstrip().upper().startswith("SELECT") for s in statements)


def test_first_creation_writes_only_the_context_table() -> None:
    holder: list[eps.ProvenanceResolution] = []

    statements = capture_statements(
        lambda: holder.append(
            make_service().resolve(
                activation_floor=unique_floor(), pass_id=uuid.uuid4()
            )
        )
    )

    assert holder[0].binding is not None
    writes = write_statements(statements)
    assert len(writes) == 1
    assert "evidence_provenance_contexts" in writes[0]
    assert writes[0].lstrip().upper().startswith("INSERT")


def test_a_different_pass_reuses_the_same_context() -> None:
    floor = unique_floor()
    service = make_service()

    first = service.resolve(activation_floor=floor, pass_id=uuid.uuid4())
    second = service.resolve(activation_floor=floor, pass_id=uuid.uuid4())

    assert first.binding.context_id == second.binding.context_id
    assert first.binding.pass_id != second.binding.pass_id


def test_the_floor_is_part_of_the_context_identity() -> None:
    service = make_service()

    first = service.resolve(
        activation_floor=unique_floor(), pass_id=uuid.uuid4()
    )
    second = service.resolve(
        activation_floor=unique_floor(), pass_id=uuid.uuid4()
    )

    assert first.binding.context_id != second.binding.context_id


def test_same_instant_in_another_offset_is_the_same_context() -> None:
    floor = unique_floor()
    other_offset = floor.astimezone(timezone(timedelta(hours=-3)))
    service = make_service()

    first = service.resolve(activation_floor=floor, pass_id=uuid.uuid4())
    second = service.resolve(
        activation_floor=other_offset, pass_id=uuid.uuid4()
    )

    assert first.binding.context_id == second.binding.context_id


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "expected_revision_source": lambda: "rev000000001",
            "actual_revision_source": lambda: "rev000000001",
        },
    ],
    ids=["other_schema_revision"],
)
def test_any_other_identity_component_makes_another_context(
    overrides,
) -> None:
    floor = unique_floor()

    first = make_service().resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )
    second = make_service(**overrides).resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )

    assert first.binding.context_id != second.binding.context_id


def test_another_build_sha_makes_another_context() -> None:
    floor = unique_floor()
    other_identity = bi.diagnose_build_identity(
        {bi.ENV_GIT_SHA: "f" * 40, bi.ENV_GIT_DIRTY: "false"}
    )

    first = make_service().resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )
    second = make_service(identity_source=lambda: other_identity).resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )

    assert first.binding.context_id != second.binding.context_id
    assert load_context(second.binding.context_id).git_sha == "f" * 40


def test_concurrent_workers_converge_on_one_context() -> None:
    floor = unique_floor()
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[eps.ProvenanceResolution] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                make_service().resolve(
                    activation_floor=floor, pass_id=uuid.uuid4()
                )
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert all(r.binding is not None for r in results)
    assert len({r.binding.context_id for r in results}) == 1
    # exatamente um criou; os demais encontraram
    assert sum(1 for r in results if r.context_created) == 1
    with SessionLocal() as session:
        rows = session.execute(
            select(EvidenceProvenanceContext).where(
                EvidenceProvenanceContext.activation_floor == floor
            )
        ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------
# 5. integridade e falha de persistencia
# ---------------------------------------------------------------------

INSERT_CONTEXT = text(
    "INSERT INTO evidence_provenance_contexts (context_digest, "
    "context_digest_algorithm, git_sha, git_dirty, source_digest_algorithm, "
    "source_digest, source_file_count, producer_spec, "
    "producer_fingerprint_algorithm, producer_fingerprint, activation_floor, "
    "expected_schema_revision, actual_database_revision) VALUES "
    "(:digest, 'epc1', :sha, false, 'sd1', :sd, :count, "
    "'escalation_payment_observation:v1', 'pf1', :fp, :floor, :rev, :rev)"
)


def expected_fields(floor: datetime) -> dict[str, object]:
    identity = valid_identity_diagnosis().identity
    producer = diagnose_producer_fingerprint().measured
    return {
        "activation_floor": canonical_utc(floor),
        "actual_database_revision": FAKE_REVISION,
        "context_digest_algorithm": "epc1",
        "expected_schema_revision": FAKE_REVISION,
        "git_dirty": False,
        "git_sha": identity.git_sha,
        "producer_fingerprint": producer.digest,
        "producer_fingerprint_algorithm": producer.algorithm,
        "producer_spec": "escalation_payment_observation:v1",
        "source_digest": identity.source_digest.digest,
        "source_digest_algorithm": identity.source_digest.algorithm,
        "source_file_count": identity.source_digest.file_count,
    }


def insert_row(
    fields: dict[str, object], *, digest: str, floor: datetime, count=None
) -> None:
    with SessionLocal() as session:
        session.execute(
            INSERT_CONTEXT,
            {
                "digest": digest,
                "sha": fields["git_sha"],
                "sd": fields["source_digest"],
                "count": (
                    fields["source_file_count"] if count is None else count
                ),
                "fp": fields["producer_fingerprint"],
                "floor": floor,
                "rev": FAKE_REVISION,
            },
        )
        session.commit()


def test_same_content_with_a_forged_digest_is_an_integrity_error() -> None:
    floor = unique_floor()
    fields = expected_fields(floor)
    insert_row(fields, digest="f" * 64, floor=floor)

    resolution = make_service().resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )

    assert resolution.binding is None
    assert resolution.code == "context_integrity_error"


def test_a_row_whose_columns_do_not_match_its_digest_is_an_integrity_error() -> (
    None
):
    floor = unique_floor()
    fields = expected_fields(floor)
    digest = eps.compute_context_digest(fields)
    insert_row(
        fields,
        digest=digest,
        floor=floor,
        count=int(fields["source_file_count"]) + 1,
    )

    resolution = make_service().resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )

    assert resolution.binding is None
    assert resolution.code == "context_integrity_error"


def test_verify_row_integrity_detects_a_tampered_instance() -> None:
    resolution = make_service().resolve(
        activation_floor=unique_floor(), pass_id=uuid.uuid4()
    )
    row = load_context(resolution.binding.context_id)
    assert eps.verify_row_integrity(row) is True

    row.source_file_count = row.source_file_count + 1  # so em memoria

    assert eps.verify_row_integrity(row) is False


def test_persist_failure_is_typed_and_leaves_no_context() -> None:
    floor = unique_floor()

    class FailingCommit(SessionLocal.class_):  # type: ignore[name-defined]
        def commit(self):
            raise OperationalError("commit", {}, Exception("boom"))

    def failing_factory():
        return FailingCommit(bind=engine, expire_on_commit=False)

    service = make_service(failing_factory)

    resolution = service.resolve(
        activation_floor=floor, pass_id=uuid.uuid4()
    )

    assert resolution.binding is None
    assert resolution.code == "context_persist_failed"
    with SessionLocal() as session:
        assert (
            session.execute(
                select(EvidenceProvenanceContext).where(
                    EvidenceProvenanceContext.activation_floor == floor
                )
            ).first()
            is None
        )


def test_context_is_resolved_with_its_own_session_and_always_closes() -> (
    None
):
    created: list[object] = []

    class Spy(SessionLocal.class_):  # type: ignore[name-defined]
        closed = False

        def close(self):
            Spy.closed = True
            super().close()

    def factory():
        session = Spy(bind=engine, expire_on_commit=False)
        created.append(session)
        return session

    resolution = make_service(factory).resolve(
        activation_floor=unique_floor(), pass_id=uuid.uuid4()
    )

    assert resolution.binding is not None
    assert len(created) == 1  # uma sessao propria por resolucao
    assert Spy.closed is True


# ---------------------------------------------------------------------
# 6. fronteira estatica
# ---------------------------------------------------------------------


def test_service_never_imports_the_worker_or_decision_modules() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    forbidden_fragments = (
        "escalation_payment_observation_maintenance",
        "escalation_observation_service",
        "nba",
        "policy",
        "approval",
        "orchestrator",
        "agent",
    )
    for module in modules:
        assert not any(
            fragment in module for fragment in forbidden_fragments
        ), module


def test_service_has_no_authority_or_decision_vocabulary() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    for forbidden in ("approve", "execute_action", "recommend", "authority"):
        assert forbidden not in code.replace(
            "provenance", ""
        ), forbidden
