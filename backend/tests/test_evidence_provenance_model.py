"""
VALUE-3.4D-2b -- garantias do BANCO para a proveniencia da evidencia.

Provadas contra PostgreSQL real (migration aplicada):

* CHECK condicional por tipo (`ck_escalation_observations_provenance_by_type`):
  novo `observed_fact` exige contexto E pass; `human_assessment` exige os
  dois NULL; FK RESTRICT; `provenance_context_id` NUNCA `NOT NULL` global;
* legado `observed_fact` (NULL/NULL) continua legivel (CHECK `NOT VALID`);
* contexto imutavel (UPDATE/DELETE) e proveniencia da observation nao pode
  ser re-apontada (triggers); cascades e `TRUNCATE` continuam funcionando;
* CHECKs do contexto (P2 no banco: `git_dirty = false`, SHA 40 hex, etc.) e
  UNIQUE de conteudo independente do digest calculado pela aplicacao.

Nada aqui executa `VALIDATE CONSTRAINT`.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.database.database import engine
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.escalation_observation import EscalationObservation
from app.models.user import User
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.work_service import WorkActor

from evidence_provenance_helpers import make_binding
from evidence_provenance_helpers import purge_contexts
from evidence_provenance_helpers import unique_floor


CONFTEST = Path(__file__).with_name("conftest.py")


@pytest.fixture(autouse=True)
def _purge_provenance_contexts():
    purge_contexts()
    yield
    purge_contexts()


def conftest_truncate_sql() -> str:
    match = re.search(
        r"(TRUNCATE TABLE.*?RESTART IDENTITY\s+CASCADE)",
        CONFTEST.read_text(encoding="utf-8"),
        re.S,
    )
    assert match is not None
    return match.group(1)


def constraint_of(error: pytest.ExceptionInfo) -> str | None:
    return getattr(
        getattr(error.value.orig, "diag", None), "constraint_name", None
    )


def message_of(error: pytest.ExceptionInfo) -> str:
    return str(
        getattr(
            getattr(error.value.orig, "diag", None),
            "message_primary",
            error.value.orig,
        )
    )


class Scenario:
    def __init__(self, db: Session) -> None:
        suffix = uuid.uuid4().hex[:10]
        due = date.today() - timedelta(days=10)
        self.user = User(
            name="prov-model",
            email=f"prov-model-{suffix}@example.com",
            password_hash=hash_password("not-used-Aa1!"),
            role="administrator",
            active=True,
        )
        db.add(self.user)
        db.commit()
        self.account = Account(
            cliente="prov-model",
            email=f"prov-model-acc-{suffix}@example.com",
            whatsapp=None,
            valor=500,
            vencimento=due,
            status="atrasado",
        )
        db.add(self.account)
        db.commit()
        self.work_item = HumanEscalationMaterializationService(
            db
        ).materialize(
            account=self.account,
            due_date=due,
            actor=WorkActor(
                actor_type="user",
                actor_reference=f"user:{self.user.id}",
                actor_user_id=self.user.id,
            ),
        ).work_item
        self.events = []
        for index in range(1, 4):
            event = AccountEvent(
                account_id=self.account.id,
                event_type="status_changed",
                actor_type="system",
                actor_reference=f"prov-model:{index}",
                previous_status="atrasado",
                new_status="pago",
                occurred_at=self.work_item.created_at
                + timedelta(hours=index),
            )
            db.add(event)
            self.events.append(event)
        db.commit()
        for event in self.events:
            db.refresh(event)

    def observed_fact_params(self, index: int, **overrides) -> dict:
        params = {
            "w": self.work_item.id,
            "e": self.events[index].id,
            "k": f"k-{uuid.uuid4().hex}",
            "c": None,
            "p": None,
        }
        params.update(overrides)
        return params


OBSERVED_FACT = text(
    "INSERT INTO escalation_observations (escalation_work_item_id, "
    "observation_type, linked_account_event_id, observed_at, "
    "idempotency_key, provenance_context_id, producer_pass_id) VALUES "
    "(:w, 'observed_fact', :e, now(), :k, :c, :p) RETURNING id"
)
HUMAN_ASSESSMENT = text(
    "INSERT INTO escalation_observations (escalation_work_item_id, "
    "observation_type, assessment_code, declared_by_user_id, "
    "declared_by_role, declared_at, provenance_context_id, "
    "producer_pass_id) VALUES (:w, 'human_assessment', 'contact_made', :u, "
    "'administrator', now(), :c, :p) RETURNING id"
)


@pytest.fixture
def scenario(db_session: Session) -> Scenario:
    return Scenario(db_session)


def execute(statement, params=None):
    with engine.begin() as connection:
        result = connection.execute(statement, params or {})
        return result.scalar() if result.returns_rows else None


# ---------------------------------------------------------------------
# 1. CHECK condicional por tipo
# ---------------------------------------------------------------------


def test_new_observed_fact_with_context_and_pass_is_accepted(
    scenario: Scenario,
) -> None:
    binding = make_binding()

    observation_id = execute(
        OBSERVED_FACT,
        scenario.observed_fact_params(
            0, c=binding.context_id, p=str(binding.pass_id)
        ),
    )

    assert observation_id is not None


@pytest.mark.parametrize(
    "case",
    ["no_provenance", "context_only", "pass_only"],
)
def test_new_observed_fact_without_complete_provenance_is_rejected(
    scenario: Scenario, case: str
) -> None:
    binding = make_binding()
    overrides = {
        "no_provenance": {},
        "context_only": {"c": binding.context_id},
        "pass_only": {"p": str(binding.pass_id)},
    }[case]

    with pytest.raises(DBAPIError) as error:
        execute(OBSERVED_FACT, scenario.observed_fact_params(0, **overrides))

    assert constraint_of(error) == (
        "ck_escalation_observations_provenance_by_type"
    )


def test_observed_fact_with_a_nonexistent_context_is_rejected_by_the_fk(
    scenario: Scenario,
) -> None:
    binding = make_binding()

    with pytest.raises(DBAPIError) as error:
        execute(
            OBSERVED_FACT,
            scenario.observed_fact_params(
                0, c=binding.context_id + 10_000_000, p=str(uuid.uuid4())
            ),
        )

    assert constraint_of(error) == (
        "fk_escalation_observations_provenance_context_id_contexts"
    )


def test_human_assessment_stays_without_provenance(
    scenario: Scenario,
) -> None:
    assert (
        execute(
            HUMAN_ASSESSMENT,
            {"w": scenario.work_item.id, "u": scenario.user.id, "c": None, "p": None},
        )
        is not None
    )


@pytest.mark.parametrize("case", ["with_context", "with_pass", "with_both"])
def test_human_assessment_with_automatic_provenance_is_rejected(
    scenario: Scenario, case: str
) -> None:
    binding = make_binding()
    params = {
        "w": scenario.work_item.id,
        "u": scenario.user.id,
        "c": binding.context_id if case != "with_pass" else None,
        "p": str(binding.pass_id) if case != "with_context" else None,
    }

    with pytest.raises(DBAPIError) as error:
        execute(HUMAN_ASSESSMENT, params)

    assert constraint_of(error) == (
        "ck_escalation_observations_provenance_by_type"
    )


def test_the_provenance_columns_are_nullable_globally() -> None:
    with engine.connect() as connection:
        nullable = dict(
            connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM "
                    "information_schema.columns WHERE "
                    "table_name = 'escalation_observations' AND "
                    "column_name IN ('provenance_context_id', "
                    "'producer_pass_id')"
                )
            ).all()
        )

    assert nullable == {
        "provenance_context_id": "YES",
        "producer_pass_id": "YES",
    }


# ---------------------------------------------------------------------
# 2. legado: CHECK ... NOT VALID (simulado dentro de uma transacao revertida)
# ---------------------------------------------------------------------


def test_legacy_observed_fact_survives_the_not_valid_check(
    scenario: Scenario,
) -> None:
    constraint = "ck_escalation_observations_provenance_by_type"
    check_sql = (
        "CHECK ((observation_type = 'observed_fact' AND "
        "provenance_context_id IS NOT NULL AND producer_pass_id IS NOT NULL)"
        " OR (observation_type = 'human_assessment' AND "
        "provenance_context_id IS NULL AND producer_pass_id IS NULL)) "
        "NOT VALID"
    )
    connection = engine.connect()
    transaction = connection.begin()
    try:
        # estado "pre-D-2b": sem a constraint, um observed_fact legado entra
        connection.execute(
            text(
                f"ALTER TABLE escalation_observations "
                f"DROP CONSTRAINT {constraint}"
            )
        )
        legacy_id = connection.execute(
            OBSERVED_FACT, scenario.observed_fact_params(0)
        ).scalar()
        # estado "pos-D-2b": a constraint volta como NOT VALID
        connection.execute(
            text(
                f"ALTER TABLE escalation_observations "
                f"ADD CONSTRAINT {constraint} {check_sql}"
            )
        )

        assert (
            connection.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conname = :n"
                ),
                {"n": constraint},
            ).scalar()
            is False
        )
        # o legado continua legivel, NULL/NULL
        row = connection.execute(
            text(
                "SELECT observation_type, provenance_context_id, "
                "producer_pass_id FROM escalation_observations "
                "WHERE id = :i"
            ),
            {"i": legacy_id},
        ).one()
        assert tuple(row) == ("observed_fact", None, None)

        # ...mas NENHUM novo observed_fact sem proveniencia entra
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError) as error:
            connection.execute(
                OBSERVED_FACT, scenario.observed_fact_params(1)
            )
        savepoint.rollback()
        assert constraint_of(error) == constraint

        # ...e um novo COM proveniencia entra normalmente ao lado do legado
        binding = make_binding()
        assert (
            connection.execute(
                OBSERVED_FACT,
                scenario.observed_fact_params(
                    2, c=binding.context_id, p=str(binding.pass_id)
                ),
            ).scalar()
            is not None
        )
    finally:
        transaction.rollback()
        connection.close()


def test_the_real_constraint_is_not_validated_after_the_migration() -> None:
    # a migration NAO valida (decisao: so a preparacao de ativacao valida)
    with engine.connect() as connection:
        validated = connection.execute(
            text(
                "SELECT convalidated FROM pg_constraint WHERE conname = "
                "'ck_escalation_observations_provenance_by_type'"
            )
        ).scalar()

    assert validated is False


# ---------------------------------------------------------------------
# 3. imutabilidade
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "column,value",
    [
        ("git_sha", "9" * 40),
        ("source_file_count", 1),
        ("producer_spec", "other_spec:v2"),
        ("activation_floor", datetime(2040, 1, 1, tzinfo=timezone.utc)),
        ("actual_database_revision", "changedrevis"),
        ("context_digest", "e" * 64),
    ],
)
def test_context_update_is_forbidden(column: str, value: object) -> None:
    binding = make_binding()

    with pytest.raises(DBAPIError) as error:
        execute(
            text(
                f"UPDATE evidence_provenance_contexts SET {column} = :v "
                "WHERE id = :i"
            ),
            {"v": value, "i": binding.context_id},
        )

    assert "immutable" in message_of(error)


def test_context_delete_is_forbidden_even_when_unreferenced() -> None:
    binding = make_binding()

    with pytest.raises(DBAPIError) as error:
        execute(
            text("DELETE FROM evidence_provenance_contexts WHERE id = :i"),
            {"i": binding.context_id},
        )

    assert "immutable" in message_of(error)


def test_context_delete_is_forbidden_when_referenced(
    scenario: Scenario,
) -> None:
    binding = make_binding()
    execute(
        OBSERVED_FACT,
        scenario.observed_fact_params(
            0, c=binding.context_id, p=str(binding.pass_id)
        ),
    )

    with pytest.raises(DBAPIError):
        execute(
            text("DELETE FROM evidence_provenance_contexts WHERE id = :i"),
            {"i": binding.context_id},
        )


@pytest.mark.parametrize("change", ["to_null", "to_other_context", "pass"])
def test_observation_provenance_cannot_be_repointed(
    scenario: Scenario, change: str
) -> None:
    first = make_binding()
    other = make_binding()
    observation_id = execute(
        OBSERVED_FACT,
        scenario.observed_fact_params(
            0, c=first.context_id, p=str(first.pass_id)
        ),
    )
    statement = {
        "to_null": (
            "UPDATE escalation_observations SET provenance_context_id = "
            "NULL WHERE id = :i",
            {"i": observation_id},
        ),
        "to_other_context": (
            "UPDATE escalation_observations SET provenance_context_id = "
            ":c WHERE id = :i",
            {"i": observation_id, "c": other.context_id},
        ),
        "pass": (
            "UPDATE escalation_observations SET producer_pass_id = "
            "gen_random_uuid() WHERE id = :i",
            {"i": observation_id},
        ),
    }[change]

    with pytest.raises(DBAPIError) as error:
        execute(text(statement[0]), statement[1])

    assert "immutable" in message_of(error)


def test_non_provenance_columns_of_a_new_observation_remain_updatable(
    scenario: Scenario,
) -> None:
    binding = make_binding()
    observation_id = execute(
        OBSERVED_FACT,
        scenario.observed_fact_params(
            0, c=binding.context_id, p=str(binding.pass_id)
        ),
    )

    execute(
        text(
            "UPDATE escalation_observations SET idempotency_key = :k "
            "WHERE id = :i"
        ),
        {"k": "renamed-key", "i": observation_id},
    )

    assert (
        execute(
            text(
                "SELECT idempotency_key FROM escalation_observations "
                "WHERE id = :i"
            ),
            {"i": observation_id},
        )
        == "renamed-key"
    )


# ---------------------------------------------------------------------
# 4. interacoes: cascade, TRUNCATE, FK
# ---------------------------------------------------------------------


def test_work_item_delete_still_cascades_to_observations(
    scenario: Scenario,
) -> None:
    binding = make_binding()
    execute(
        OBSERVED_FACT,
        scenario.observed_fact_params(
            0, c=binding.context_id, p=str(binding.pass_id)
        ),
    )
    connection = engine.connect()
    transaction = connection.begin()
    try:
        before = connection.execute(
            text(
                "SELECT count(*) FROM escalation_observations WHERE "
                "escalation_work_item_id = :w"
            ),
            {"w": scenario.work_item.id},
        ).scalar()
        connection.execute(
            text("DELETE FROM work_items WHERE id = :w"),
            {"w": scenario.work_item.id},
        )
        after = connection.execute(
            text(
                "SELECT count(*) FROM escalation_observations WHERE "
                "escalation_work_item_id = :w"
            ),
            {"w": scenario.work_item.id},
        ).scalar()
    finally:
        transaction.rollback()
        connection.close()

    assert before == 1
    assert after == 0


def test_the_conftest_truncate_still_works_and_keeps_contexts() -> None:
    binding = make_binding()
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text(conftest_truncate_sql()))
        observations = connection.execute(
            text("SELECT count(*) FROM escalation_observations")
        ).scalar()
        survivors = connection.execute(
            text(
                "SELECT count(*) FROM evidence_provenance_contexts "
                "WHERE id = :i"
            ),
            {"i": binding.context_id},
        ).scalar()
    finally:
        transaction.rollback()
        connection.close()

    assert observations == 0
    assert survivors == 1  # lado referenciado da FK nao e truncado


def test_both_triggers_exist_and_are_the_only_new_triggers() -> None:
    with engine.connect() as connection:
        names = sorted(
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgname LIKE 'trg_%provenance%'"
                )
            )
        )

    assert names == [
        "trg_escalation_observations_provenance_immutable",
        "trg_evidence_provenance_contexts_immutable",
    ]


# ---------------------------------------------------------------------
# 5. CHECKs e UNIQUEs do contexto
# ---------------------------------------------------------------------

BASE_CONTEXT = {
    "digest": "a" * 64,
    "algo": "epc1",
    "sha": "b" * 40,
    "dirty": False,
    "sd_algo": "sd1",
    "sd": "c" * 64,
    "count": 216,
    "spec": "escalation_payment_observation:v1",
    "fp_algo": "pf1",
    "fp": "d" * 64,
    "exp": "7432a1c2dd66",
    "act": "7432a1c2dd66",
}

INSERT_RAW = text(
    "INSERT INTO evidence_provenance_contexts (context_digest, "
    "context_digest_algorithm, git_sha, git_dirty, source_digest_algorithm, "
    "source_digest, source_file_count, producer_spec, "
    "producer_fingerprint_algorithm, producer_fingerprint, "
    "activation_floor, expected_schema_revision, actual_database_revision) "
    "VALUES (:digest, :algo, :sha, :dirty, :sd_algo, :sd, :count, :spec, "
    ":fp_algo, :fp, :floor, :exp, :act) RETURNING id"
)


def insert_context(**overrides):
    params = {
        **BASE_CONTEXT,
        "digest": uuid.uuid4().hex + uuid.uuid4().hex,
        "floor": unique_floor(),
    }
    params.update(overrides)
    return execute(INSERT_RAW, params)


def test_a_valid_context_row_is_accepted() -> None:
    assert insert_context() is not None


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        ({"dirty": True}, "ck_evidence_provenance_contexts_git_clean"),
        ({"sha": "B" * 40}, "ck_evidence_provenance_contexts_git_sha"),
        ({"sha": "b" * 39}, "ck_evidence_provenance_contexts_git_sha"),
        ({"sha": "unknown"}, "ck_evidence_provenance_contexts_git_sha"),
        ({"sd": "z" * 64}, "ck_evidence_provenance_contexts_source_digest_hex"),
        ({"sd": "c" * 63}, "ck_evidence_provenance_contexts_source_digest_hex"),
        ({"fp": "G" * 64}, "ck_evidence_provenance_contexts_fingerprint_hex"),
        ({"count": 0}, "ck_evidence_provenance_contexts_file_count"),
        ({"count": -1}, "ck_evidence_provenance_contexts_file_count"),
        ({"spec": "NoVersion"}, "ck_evidence_provenance_contexts_producer_spec"),
        ({"spec": "a:b"}, "ck_evidence_provenance_contexts_producer_spec"),
        ({"spec": "Upper:v1"}, "ck_evidence_provenance_contexts_producer_spec"),
        ({"spec": "x:v"}, "ck_evidence_provenance_contexts_producer_spec"),
        ({"algo": "EPC1"}, "ck_evidence_provenance_contexts_algorithms"),
        ({"sd_algo": "sd"}, "ck_evidence_provenance_contexts_algorithms"),
        ({"fp_algo": "1pf"}, "ck_evidence_provenance_contexts_algorithms"),
        ({"exp": "   "}, "ck_evidence_provenance_contexts_revisions"),
        ({"act": ""}, "ck_evidence_provenance_contexts_revisions"),
    ],
    ids=[
        "dirty_true",
        "sha_uppercase",
        "sha_39",
        "sha_unknown",
        "source_digest_non_hex",
        "source_digest_63",
        "fingerprint_non_hex",
        "count_zero",
        "count_negative",
        "spec_without_version",
        "spec_with_letter_version",
        "spec_uppercase",
        "spec_empty_version",
        "algorithm_uppercase",
        "algorithm_without_number",
        "algorithm_number_first",
        "expected_blank",
        "actual_empty",
    ],
)
def test_context_check_constraints(overrides: dict, constraint: str) -> None:
    with pytest.raises(DBAPIError) as error:
        insert_context(**overrides)

    assert constraint_of(error) == constraint


def test_context_sha_longer_than_40_is_rejected_by_the_column_type() -> None:
    with pytest.raises(DBAPIError):
        insert_context(sha="b" * 41)


@pytest.mark.parametrize("digest", ["g" * 64, "a" * 63, "A" * 64, ""])
def test_context_digest_must_be_lowercase_hex_64(digest: str) -> None:
    with pytest.raises(DBAPIError) as error:
        insert_context(digest=digest)

    assert constraint_of(error) == (
        "ck_evidence_provenance_contexts_digest_hex"
    )


def test_context_digest_is_unique() -> None:
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    insert_context(digest=digest)

    with pytest.raises(DBAPIError) as error:
        insert_context(digest=digest)

    assert constraint_of(error) == "uq_evidence_provenance_contexts_digest"


def test_context_content_is_unique_even_with_a_forged_digest() -> None:
    floor = unique_floor()
    insert_context(floor=floor)

    with pytest.raises(DBAPIError) as error:
        insert_context(floor=floor)  # outro digest, mesmo CONTEUDO

    assert constraint_of(error) == "uq_evidence_provenance_contexts_content"


def test_context_created_at_defaults_to_the_database_clock() -> None:
    context_id = insert_context()

    created_at = execute(
        text(
            "SELECT created_at FROM evidence_provenance_contexts "
            "WHERE id = :i"
        ),
        {"i": context_id},
    )

    assert created_at is not None
    assert created_at.tzinfo is not None


# ---------------------------------------------------------------------
# 6. ORM
# ---------------------------------------------------------------------


def test_orm_roundtrip_of_the_provenance_columns(
    db_session: Session, scenario: Scenario
) -> None:
    binding = make_binding()
    observation = EscalationObservation(
        escalation_work_item_id=scenario.work_item.id,
        observation_type="observed_fact",
        linked_account_event_id=scenario.events[0].id,
        observed_at=scenario.events[0].occurred_at,
        idempotency_key=f"orm-{uuid.uuid4().hex}",
        provenance_context_id=binding.context_id,
        producer_pass_id=binding.pass_id,
    )
    db_session.add(observation)
    db_session.commit()
    db_session.expire_all()

    reloaded = db_session.get(EscalationObservation, observation.id)

    assert reloaded.provenance_context_id == binding.context_id
    assert reloaded.producer_pass_id == binding.pass_id
    assert isinstance(reloaded.producer_pass_id, uuid.UUID)
