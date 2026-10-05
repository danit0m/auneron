"""
VALUE-3.4D-2b -- worker automatico x proveniencia (fail-closed).

Contrato provado com o worker REAL e PostgreSQL real:

* proveniencia valida -> `observed_fact` persistido COM
  `provenance_context_id` + `producer_pass_id` (mesmo pass => mesmo id);
* qualquer falha de proveniencia -> o pass ABSTEM: ZERO observations
  escritas, codigo estavel no resumo, a aplicacao nao cai (nenhuma excecao);
* resolucao PREGUICOSA: antes da primeira materializacao -- pass sem
  candidato, so com abstencoes do worker, ou com floor ausente nao toca a
  proveniencia;
* contexto orfao e aceitavel; observation automatica sem proveniencia e
  impossivel; retry/concorrencia nao mudam a semantica de idempotencia;
* extracao `_resolve_episode_work_item` comportamentalmente neutra.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.core.escalation_payment_observation_maintenance as worker
from app.core import build_identity as bi
from app.core.escalation_payment_observation_maintenance import (
    EscalationPaymentObservationRecoverySummary,
)
from app.core.escalation_payment_observation_maintenance import (
    run_escalation_payment_observation_recovery,
)
from app.core.evidence_producer_spec import diagnose_producer_fingerprint
from app.core.human_escalation_eligibility import work_key_for_episode
from app.core.schema_identity import SchemaIdentityError
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.escalation_observation import EscalationObservation
from app.models.evidence_provenance_context import EvidenceProvenanceContext
from app.models.work import WorkItem
from app.services.escalation_observation_service import (
    EscalationObservationService,
)
from app.services.escalation_observation_service import (
    EscalationObservationValidationError,
)
from app.services.evidence_provenance_service import BLOCK_CODES
from app.services.evidence_provenance_service import EvidenceProvenanceService
from app.services.evidence_provenance_service import ProvenanceBinding

from evidence_provenance_helpers import FAKE_REVISION
from evidence_provenance_helpers import VALID_SHA
from evidence_provenance_helpers import make_service
from evidence_provenance_helpers import purge_contexts
from test_escalation_payment_observation_maintenance import _account
from test_escalation_payment_observation_maintenance import _actor_user
from test_escalation_payment_observation_maintenance import _capture_statements
from test_escalation_payment_observation_maintenance import _observations
from test_escalation_payment_observation_maintenance import _payment_event
from test_escalation_payment_observation_maintenance import _setup_escalation
from test_escalation_payment_observation_maintenance import _unique_floor
from test_escalation_payment_observation_maintenance import _work_actor
from test_escalation_payment_observation_maintenance import _write_targets


LOGGER_NAME = "auneron.escalation_payment_observation_maintenance"


@pytest.fixture(autouse=True)
def _purge_provenance_contexts():
    purge_contexts()
    yield
    purge_contexts()


@pytest.fixture(autouse=True)
def _isolated_log_state():
    worker.reset_blocked_log_state()
    yield
    worker.reset_blocked_log_state()


class SpyService(EvidenceProvenanceService):
    """Servico real que conta chamadas a `resolve`."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.calls = 0

    def resolve(self, **kwargs):
        self.calls += 1
        return super().resolve(**kwargs)


def spy_service(**overrides) -> SpyService:
    options = {
        "identity_source": make_service()._identity_source,
        "producer_source": make_service()._producer_source,
        "expected_revision_source": lambda: FAKE_REVISION,
        "actual_revision_source": lambda: FAKE_REVISION,
    }
    options.update(overrides)
    return SpyService(SessionLocal, **options)


def run(floor: datetime, service=None, **kwargs):
    return run_escalation_payment_observation_recovery(
        activation_floor=floor,
        provenance_service=service or spy_service(),
        **kwargs,
    )


def materializable(db_session: Session, floor: datetime):
    account, actor_user, work_item = _setup_escalation(db_session)
    event = _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(seconds=1),
    )
    return account, actor_user, work_item, event


def observed_facts(db_session: Session, work_item_id: int):
    return [
        o
        for o in _observations(db_session, work_item_id)
        if o.observation_type == "observed_fact"
    ]


# ---------------------------------------------------------------------
# 1. caminho feliz
# ---------------------------------------------------------------------


def test_valid_provenance_persists_the_observed_fact_with_provenance(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, event = materializable(db_session, floor)

    summary = run(floor)

    assert summary.created_count == 1
    assert summary.failure_count == 0
    assert summary.provenance_blocked is False
    assert summary.provenance_code is None
    facts = observed_facts(db_session, work_item.id)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.linked_account_event_id == event.id
    assert fact.provenance_context_id == summary.provenance_context_id
    assert isinstance(fact.producer_pass_id, uuid.UUID)
    context = db_session.get(EvidenceProvenanceContext, fact.provenance_context_id)
    assert context.activation_floor == floor
    assert context.git_sha == VALID_SHA
    assert context.git_dirty is False
    assert context.producer_spec == "escalation_payment_observation:v1"
    assert context.expected_schema_revision == FAKE_REVISION


def test_end_to_end_with_the_real_schema_identity(
    db_session: Session,
) -> None:
    # revisao esperada (arquivos) e real (alembic_version) REAIS: so passa
    # com o banco no head do codigo (schema migrado).
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)
    service = SpyService(
        SessionLocal,
        identity_source=make_service()._identity_source,
        producer_source=make_service()._producer_source,
    )

    summary = run(floor, service)

    assert summary.provenance_blocked is False, summary.provenance_code
    assert summary.created_count == 1
    context = db_session.get(
        EvidenceProvenanceContext, summary.provenance_context_id
    )
    assert context.expected_schema_revision == (
        context.actual_database_revision
    )


def test_observations_of_one_pass_share_the_pass_id_and_the_context(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    first = materializable(db_session, floor)
    second = materializable(db_session, floor)

    summary = run(floor)

    assert summary.created_count == 2
    facts = observed_facts(db_session, first[2].id) + observed_facts(
        db_session, second[2].id
    )
    assert len(facts) == 2
    assert facts[0].producer_pass_id == facts[1].producer_pass_id
    assert facts[0].provenance_context_id == facts[1].provenance_context_id


def test_another_pass_gets_another_pass_id_but_the_same_context(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    first = materializable(db_session, floor)
    run(floor)
    second = materializable(db_session, floor)
    run(floor)

    fact_one = observed_facts(db_session, first[2].id)[0]
    fact_two = observed_facts(db_session, second[2].id)[0]

    assert fact_one.producer_pass_id != fact_two.producer_pass_id
    assert fact_one.provenance_context_id == fact_two.provenance_context_id


# ---------------------------------------------------------------------
# 2. fail-closed: o pass ABSTEM, ZERO escrita de evidencia
# ---------------------------------------------------------------------


def identity_with(environ: dict[str, str]):
    return lambda: bi.diagnose_build_identity(environ)


def raising(error: Exception):
    def source():
        raise error

    return source


class FailingCommitSession(SessionLocal.class_):  # type: ignore[name-defined]
    def commit(self):
        raise OperationalError("commit", {}, Exception("boom"))


BLOCKING_SCENARIOS = {
    "sha_unknown": (
        {
            "identity_source": identity_with(
                {bi.ENV_GIT_SHA: "unknown", bi.ENV_GIT_DIRTY: "false"}
            )
        },
        "identity_sha_invalid",
    ),
    "no_claims": (
        {"identity_source": identity_with({})},
        "identity_sha_invalid",
    ),
    "dirty_true": (
        {
            "identity_source": identity_with(
                {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "true"}
            )
        },
        "identity_dirty",
    ),
    "dirty_unknown": (
        {
            "identity_source": identity_with(
                {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "unknown"}
            )
        },
        "identity_dirty_invalid",
    ),
    "source_digest_failure": (
        {
            "identity_source": lambda: bi.diagnose_build_identity(
                {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "false"},
                "Z:/definitely/missing/root",
            )
        },
        "source_digest_error",
    ),
    "fingerprint_mismatch": (
        {
            "producer_source": lambda: diagnose_producer_fingerprint(
                pinned="0" * 64
            )
        },
        "producer_fingerprint_mismatch",
    ),
    "fingerprint_unavailable": (
        {
            "producer_source": lambda: diagnose_producer_fingerprint(
                sources={"app/services/escalation_observation_service.py": "x"}
            )
        },
        "producer_fingerprint_unavailable",
    ),
    "expected_missing": (
        {
            "expected_revision_source": raising(
                SchemaIdentityError("expected_revision_unavailable", "heads=0")
            )
        },
        "expected_revision_unavailable",
    ),
    "actual_missing": (
        {
            "actual_revision_source": raising(
                SchemaIdentityError("actual_revision_unavailable", "rows=0")
            )
        },
        "actual_revision_unavailable",
    ),
    "schema_mismatch": (
        {
            "expected_revision_source": lambda: "aaaaaaaaaaaa",
            "actual_revision_source": lambda: "bbbbbbbbbbbb",
        },
        "schema_revision_mismatch",
    ),
}


@pytest.mark.parametrize("name", list(BLOCKING_SCENARIOS))
def test_provenance_failure_makes_the_pass_abstain_without_writing_evidence(
    db_session: Session, name: str
) -> None:
    overrides, code = BLOCKING_SCENARIOS[name]
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)
    service = spy_service(**overrides)
    holder: list[EscalationPaymentObservationRecoverySummary] = []

    statements = _capture_statements(
        lambda: holder.append(run(floor, service))
    )

    summary = holder[0]
    assert summary.provenance_blocked is True
    assert summary.provenance_code == code
    assert code in BLOCK_CODES
    assert summary.created_count == 0
    assert summary.provenance_context_id is None
    assert observed_facts(db_session, work_item.id) == []
    # ZERO escrita de evidencia: nem observation, nem contexto
    assert _write_targets(statements) == {}
    assert service.calls == 1


def test_persist_failure_abstains_with_a_typed_code(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)
    service = SpyService(
        lambda: FailingCommitSession(bind=engine, expire_on_commit=False),
        identity_source=make_service()._identity_source,
        producer_source=make_service()._producer_source,
        expected_revision_source=lambda: FAKE_REVISION,
        actual_revision_source=lambda: FAKE_REVISION,
    )

    summary = run(floor, service)

    assert summary.provenance_blocked is True
    assert summary.provenance_code == "context_persist_failed"
    assert observed_facts(db_session, work_item.id) == []


def test_integrity_error_abstains(db_session: Session) -> None:
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)
    # contexto de MESMO conteudo com digest forjado: nunca silencioso
    identity = make_service()._identity_source().identity
    producer = diagnose_producer_fingerprint().measured
    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO evidence_provenance_contexts (context_digest, "
                "context_digest_algorithm, git_sha, git_dirty, "
                "source_digest_algorithm, source_digest, source_file_count, "
                "producer_spec, producer_fingerprint_algorithm, "
                "producer_fingerprint, activation_floor, "
                "expected_schema_revision, actual_database_revision) VALUES "
                "(:d, 'epc1', :sha, false, 'sd1', :sd, :n, "
                "'escalation_payment_observation:v1', 'pf1', :fp, :floor, "
                ":rev, :rev)"
            ),
            {
                "d": "e" * 64,
                "sha": identity.git_sha,
                "sd": identity.source_digest.digest,
                "n": identity.source_digest.file_count,
                "fp": producer.digest,
                "floor": floor,
                "rev": FAKE_REVISION,
            },
        )
        session.commit()

    summary = run(floor)

    assert summary.provenance_code == "context_integrity_error"
    assert observed_facts(db_session, work_item.id) == []


def test_the_application_and_the_loop_never_see_an_exception(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    materializable(db_session, floor)
    service = spy_service(identity_source=lambda: 1 / 0)

    summary = run(floor, service)  # nao levanta

    assert summary.provenance_code == "provenance_unexpected_error"
    assert summary.provenance_blocked is True


def test_a_blocked_pass_stops_the_sweep_at_the_first_materialization(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    first = materializable(db_session, floor)
    second = materializable(db_session, floor)
    service = spy_service(
        expected_revision_source=lambda: "aaaaaaaaaaaa",
        actual_revision_source=lambda: "bbbbbbbbbbbb",
    )

    summary = run(floor, service)

    assert summary.provenance_blocked is True
    assert summary.candidate_count == 1  # parou no primeiro
    assert service.calls == 1  # nao tenta de novo por candidato
    assert observed_facts(db_session, first[2].id) == []
    assert observed_facts(db_session, second[2].id) == []


def test_after_the_provenance_recovers_the_same_event_is_materialized(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)

    blocked = run(
        floor,
        spy_service(
            expected_revision_source=lambda: "aaaaaaaaaaaa",
            actual_revision_source=lambda: "bbbbbbbbbbbb",
        ),
    )
    recovered = run(floor)

    assert blocked.created_count == 0
    assert recovered.created_count == 1
    assert len(observed_facts(db_session, work_item.id)) == 1


def test_naive_floor_abstains_with_a_typed_code_and_no_write(
    db_session: Session,
) -> None:
    service = spy_service()
    holder: list[EscalationPaymentObservationRecoverySummary] = []

    statements = _capture_statements(
        lambda: holder.append(
            run_escalation_payment_observation_recovery(
                activation_floor=datetime(2031, 1, 1),
                provenance_service=service,
            )
        )
    )

    assert holder[0].provenance_blocked is True
    assert holder[0].provenance_code == "floor_not_timezone_aware"
    assert _write_targets(statements) == {}
    assert service.calls == 0


# ---------------------------------------------------------------------
# 3. resolucao PREGUICOSA (P8)
# ---------------------------------------------------------------------


def test_empty_pass_does_not_touch_provenance(db_session: Session) -> None:
    floor = _unique_floor()
    service = spy_service(identity_source=lambda: 1 / 0)  # explodiria se usado

    summary = run(floor, service)

    assert summary.candidate_count == 0
    assert summary.provenance_blocked is False
    assert service.calls == 0


def test_missing_floor_is_disabled_and_does_not_touch_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker.settings,
        "escalation_payment_observation_activation_floor",
        None,
    )
    service = spy_service()

    summary = run_escalation_payment_observation_recovery(
        provenance_service=service
    )

    assert summary.disabled is True
    assert summary.provenance_blocked is False
    assert service.calls == 0


def test_candidates_that_abstain_in_the_worker_never_resolve_provenance(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    # conta COM escalonamento, mas o vencimento muda: o WorkItem do episodio
    # atual nao existe => abstencao do worker, ANTES da proveniencia
    account, _, _ = _setup_escalation(db_session)
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(seconds=1),
    )
    account.vencimento = account.vencimento + timedelta(days=1)
    db_session.commit()
    service = spy_service(identity_source=lambda: 1 / 0)

    first = run(floor, service)
    second = run(floor, service)  # candidato inelegivel e relistado

    assert first.abstained_count == 1
    assert first.provenance_blocked is False
    assert second.abstained_count == 1
    assert service.calls == 0


def test_provenance_is_resolved_once_per_pass_at_the_first_materialization(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    # 1o candidato: abstem no worker (vencimento mudou); 2o e 3o materializam
    account, _, _ = _setup_escalation(db_session)
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(seconds=1),
    )
    account.vencimento = account.vencimento + timedelta(days=1)
    db_session.commit()
    materializable(db_session, floor)
    materializable(db_session, floor)
    service = spy_service()

    summary = run(floor, service)

    assert summary.created_count == 2
    assert summary.abstained_count == 1
    assert service.calls == 1


# ---------------------------------------------------------------------
# 4. atomicidade, retry e concorrencia
# ---------------------------------------------------------------------


def test_orphan_context_is_acceptable_but_an_orphan_observation_is_not(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)

    def crash(self, **kwargs):
        raise RuntimeError("crash entre o commit do contexto e a observation")

    monkeypatch.setattr(
        EscalationObservationService, "record_observed_fact", crash
    )

    summary = run(floor)

    assert summary.failure_count == 1
    assert summary.created_count == 0
    # o contexto JA estava commitado (orfao: aceitavel e inofensivo)...
    with SessionLocal() as session:
        orphan = session.execute(
            select(EvidenceProvenanceContext).where(
                EvidenceProvenanceContext.activation_floor == floor
            )
        ).scalar_one_or_none()
    assert orphan is not None
    # ...e NENHUMA observation sem proveniencia existe
    assert observed_facts(db_session, work_item.id) == []
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM escalation_observations WHERE "
                    "observation_type = 'observed_fact' AND "
                    "(provenance_context_id IS NULL OR producer_pass_id IS NULL)"
                    " AND escalation_work_item_id = :w"
                ),
                {"w": work_item.id},
            ).scalar()
            == 0
        )


def test_replay_is_silent_and_keeps_the_original_provenance(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)
    first = run(floor)
    original = observed_facts(db_session, work_item.id)[0]
    holder: list[EscalationPaymentObservationRecoverySummary] = []

    statements = _capture_statements(lambda: holder.append(run(floor)))

    assert first.created_count == 1
    assert holder[0].created_count == 0
    assert _write_targets(statements) == {}
    again = observed_facts(db_session, work_item.id)
    assert [o.id for o in again] == [original.id]
    assert again[0].producer_pass_id == original.producer_pass_id
    assert again[0].provenance_context_id == original.provenance_context_id


def test_a_duplicate_with_another_binding_keeps_the_first_provenance(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, event = materializable(db_session, floor)
    run(floor)
    original = observed_facts(db_session, work_item.id)[0]
    other_binding = make_service().resolve(
        activation_floor=_unique_floor(), pass_id=uuid.uuid4()
    ).binding

    result = EscalationObservationService(db_session).record_observed_fact(
        escalation_work_item=work_item,
        account_event=event,
        provenance=other_binding,
    )

    assert result.created is False
    assert result.duplicate is True
    db_session.expire_all()
    stored = db_session.get(EscalationObservation, original.id)
    assert stored.provenance_context_id == original.provenance_context_id
    assert stored.producer_pass_id == original.producer_pass_id
    assert stored.provenance_context_id != other_binding.context_id


def test_two_concurrent_workers_produce_one_observation(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, _ = materializable(db_session, floor)
    barrier = threading.Barrier(2)
    summaries: list[EscalationPaymentObservationRecoverySummary] = []
    errors: list[BaseException] = []

    def worker_thread() -> None:
        try:
            barrier.wait(timeout=10)
            summaries.append(run(floor))
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker_thread) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sum(s.created_count for s in summaries) == 1
    facts = observed_facts(db_session, work_item.id)
    assert len(facts) == 1
    assert facts[0].provenance_context_id is not None
    assert facts[0].producer_pass_id is not None
    assert all(s.provenance_blocked is False for s in summaries)


# ---------------------------------------------------------------------
# 5. logs e resumo
# ---------------------------------------------------------------------


def blocked_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "event", "")
        == "escalation_payment_observation.provenance_blocked"
    ]


def test_blocked_log_is_emitted_on_transition_and_on_code_change_only(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    floor = _unique_floor()
    materializable(db_session, floor)
    mismatch = spy_service(
        expected_revision_source=lambda: "aaaaaaaaaaaa",
        actual_revision_source=lambda: "bbbbbbbbbbbb",
    )
    unavailable = spy_service(
        actual_revision_source=raising(
            SchemaIdentityError("actual_revision_unavailable", "rows=0")
        )
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        run(floor, mismatch)
        run(floor, mismatch)  # mesmo codigo: sem novo WARNING
        run(floor, unavailable)  # codigo mudou: novo WARNING
        run(floor)  # recupera e materializa: limpa o estado

    codes = [r.provenance_code for r in blocked_records(caplog)]
    assert codes == ["schema_revision_mismatch", "actual_revision_unavailable"]
    assert all(r.levelno == logging.WARNING for r in blocked_records(caplog))
    first = blocked_records(caplog)[0]
    assert first.expected_schema_revision == "aaaaaaaaaaaa"
    assert first.actual_database_revision == "bbbbbbbbbbbb"


def test_after_recovery_a_new_block_is_logged_again(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    floor = _unique_floor()
    materializable(db_session, floor)
    mismatch = spy_service(
        expected_revision_source=lambda: "aaaaaaaaaaaa",
        actual_revision_source=lambda: "bbbbbbbbbbbb",
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        run(floor, mismatch)
        run(floor)  # recupera
        materializable(db_session, floor)
        run(floor, mismatch)

    assert len(blocked_records(caplog)) == 2


def test_recovery_completed_log_carries_the_provenance_fields(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    floor = _unique_floor()
    materializable(db_session, floor)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        summary = run(floor)

    completed = [
        r
        for r in caplog.records
        if getattr(r, "event", "")
        == "escalation_payment_observation.recovery_completed"
    ]
    assert len(completed) == 1
    assert completed[0].provenance_blocked is False
    assert completed[0].provenance_code is None
    assert completed[0].provenance_context_id == summary.provenance_context_id


def test_a_blocked_pass_logs_no_invalid_raw_claim(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    floor = _unique_floor()
    materializable(db_session, floor)
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    service = spy_service(
        identity_source=identity_with(
            {bi.ENV_GIT_SHA: token, bi.ENV_GIT_DIRTY: "my_api_token_123456789"}
        )
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        summary = run(floor, service)

    assert summary.provenance_code == "identity_sha_invalid"
    serialized = " ".join(
        f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}"
        for r in caplog.records
    )
    assert token not in serialized
    assert "my_api_token_123456789" not in serialized


def test_the_old_summary_constructor_still_works() -> None:
    summary = EscalationPaymentObservationRecoverySummary(
        candidate_count=1,
        created_count=1,
        duplicate_count=0,
        abstained_count=0,
        failure_count=0,
    )

    assert summary.disabled is False
    assert summary.provenance_blocked is False
    assert summary.provenance_code is None
    assert summary.provenance_context_id is None


# ---------------------------------------------------------------------
# 6. extracao NEUTRA de `_resolve_episode_work_item`
# ---------------------------------------------------------------------


def reference_inline_resolution(db: Session, account: Account):
    """Copia LITERAL da consulta inline original (72928738)."""
    return (
        db.query(WorkItem)
        .filter(
            WorkItem.account_id == account.id,
            WorkItem.scope_type == "account",
            WorkItem.work_key
            == work_key_for_episode(account.id, account.vencimento),
        )
        .one_or_none()
    )


def test_resolver_selects_the_episode_work_item(
    db_session: Session,
) -> None:
    account, _, work_item = _setup_escalation(db_session)

    resolved = worker._resolve_episode_work_item(db_session, account)

    assert resolved is not None
    assert resolved.id == work_item.id
    assert resolved.work_key == work_key_for_episode(
        account.id, account.vencimento
    )
    assert resolved.id == reference_inline_resolution(db_session, account).id


def test_resolver_returns_none_when_the_episode_changed(
    db_session: Session,
) -> None:
    account, _, _ = _setup_escalation(db_session)
    account.vencimento = account.vencimento + timedelta(days=3)
    db_session.commit()

    assert worker._resolve_episode_work_item(db_session, account) is None
    assert reference_inline_resolution(db_session, account) is None


def test_resolver_returns_none_without_any_work_item(
    db_session: Session,
) -> None:
    account = _account(
        db_session, vencimento=date.today() - timedelta(days=10)
    )

    assert worker._resolve_episode_work_item(db_session, account) is None
    assert reference_inline_resolution(db_session, account) is None


def test_resolver_does_not_cross_accounts(db_session: Session) -> None:
    first, _, first_item = _setup_escalation(db_session)
    second, _, second_item = _setup_escalation(db_session)

    assert (
        worker._resolve_episode_work_item(db_session, first).id
        == first_item.id
    )
    assert (
        worker._resolve_episode_work_item(db_session, second).id
        == second_item.id
    )
    assert first_item.id != second_item.id


def test_resolver_and_reference_agree_on_every_scenario(
    db_session: Session,
) -> None:
    accounts = []
    for shift in (0, 1, -1, 30):
        account, _, _ = _setup_escalation(db_session)
        account.vencimento = account.vencimento + timedelta(days=shift)
        db_session.commit()
        accounts.append(account)
    accounts.append(
        _account(db_session, vencimento=date.today() - timedelta(days=5))
    )

    for account in accounts:
        extracted = worker._resolve_episode_work_item(db_session, account)
        reference = reference_inline_resolution(db_session, account)
        assert (extracted.id if extracted else None) == (
            reference.id if reference else None
        )


# ---------------------------------------------------------------------
# 7. o service exige proveniencia
# ---------------------------------------------------------------------


def test_record_observed_fact_requires_provenance(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, _, work_item, event = materializable(db_session, floor)

    with pytest.raises(TypeError):
        EscalationObservationService(db_session).record_observed_fact(
            escalation_work_item=work_item, account_event=event
        )  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", [None, 1, "binding", {"context_id": 1}])
def test_record_observed_fact_rejects_a_non_binding(
    db_session: Session, bad: object
) -> None:
    floor = _unique_floor()
    _, _, work_item, event = materializable(db_session, floor)

    with pytest.raises(EscalationObservationValidationError):
        EscalationObservationService(db_session).record_observed_fact(
            escalation_work_item=work_item,
            account_event=event,
            provenance=bad,  # type: ignore[arg-type]
        )

    assert observed_facts(db_session, work_item.id) == []


def test_human_assessment_is_unaffected_by_provenance(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    _, actor_user, work_item, _ = materializable(db_session, floor)

    result = EscalationObservationService(db_session).record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="contact_made",
        actor=_work_actor(actor_user),
        declared_by_role="administrator",
    )

    assert result.created is True
    assert result.observation.provenance_context_id is None
    assert result.observation.producer_pass_id is None
