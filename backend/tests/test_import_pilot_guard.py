"""Offline tests for the pure pilot guard (TASK-034). No DB, network, env or file access."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path

from app.core import import_pilot_guard as guard
from app.core.import_pilot_guard import (
    PilotTarget,
    ReadinessCode,
    ReadinessDecision,
    ReadinessObservation,
    ReadinessStatus,
    StaticCode,
    StaticDecision,
    StaticStatus,
    evaluate_readiness,
    evaluate_static,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
HOST = "pilot-db-host-zz"
DB = "pilot_db_name_zz"
HEAD = "b428d1a6c903"
ATTESTATION = "ATT-IMPORT-PILOT-20261030-01"
# Pilot bounds fixed by the PO (RESULT-033 §2.5); deliberately not read from the module.
SPEC_TIMEOUT_BOUNDS_MS = {"lock_timeout_ms": 5000, "statement_timeout_ms": 30000, "idle_in_transaction_timeout_ms": 60000}


def valid_facts(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "pilot_enabled": True,
        "test_enabled": False,
        "app_env": "production",
        "url_driver": "postgresql+psycopg",
        "url_host": HOST,
        "url_port": 5432,
        "url_database": DB,
        "expected_database_host": HOST,
        "expected_database_name": DB,
        "pilot_host": HOST,
        "pilot_port": 5432,
        "pilot_database": DB,
        "pilot_schema_revision": HEAD,
        "code_head_revision": HEAD,
        "attestation_reference": ATTESTATION,
        "lock_timeout_ms": 5000,
        "statement_timeout_ms": 30000,
        "idle_in_transaction_timeout_ms": 60000,
    }
    facts.update(overrides)
    return facts


def valid_observation(**overrides: object) -> ReadinessObservation:
    values: dict[str, object] = {
        "current_database": DB,
        "in_recovery": False,
        "transaction_read_only": False,
        "default_transaction_read_only": False,
        "transaction_isolation": "read committed",
        "default_transaction_isolation": "read committed",
        "lock_timeout_ms": 5000,
        "statement_timeout_ms": 30000,
        "idle_in_transaction_timeout_ms": 60000,
        "alembic_revisions": (HEAD,),
    }
    values.update(overrides)
    return ReadinessObservation(**values)  # type: ignore[arg-type]


def accepted() -> StaticDecision:
    decision = evaluate_static(valid_facts())
    assert decision.status is StaticStatus.ACCEPTED
    return decision


class RecordingMapping(Mapping):
    """Mapping that records every key read (to prove the disabled early return)."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data
        self.accessed: list[str] = []
        self.iterated = False

    def __getitem__(self, key: str) -> object:
        self.accessed.append(key)
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        self.accessed.append(str(key))
        return key in self._data

    def __iter__(self):
        self.iterated = True
        return iter(self._data)

    def __len__(self) -> int:
        self.iterated = True
        return len(self._data)


class ExplodingMapping(Mapping):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError("secret-host password=hunter2")

    def __contains__(self, key: object) -> bool:
        return True

    def __iter__(self):
        raise RuntimeError("secret")

    def __len__(self) -> int:
        return 1


class StaticDisabledTests(unittest.TestCase):
    def test_flag_absent_is_disabled_by_default(self) -> None:
        decision = evaluate_static({})
        self.assertEqual((decision.status, decision.code, decision.target), (StaticStatus.DISABLED, StaticCode.DISABLED, None))

    def test_flag_false_is_disabled_without_reading_other_facts(self) -> None:
        recorder = RecordingMapping(valid_facts(pilot_enabled=False, app_env=object(), unknown=1))
        decision = evaluate_static(recorder)
        self.assertIs(decision.status, StaticStatus.DISABLED)
        self.assertEqual(set(recorder.accessed), {"pilot_enabled"})
        self.assertFalse(recorder.iterated)

    def test_flag_absent_does_not_read_other_facts(self) -> None:
        recorder = RecordingMapping({"app_env": "development"})
        self.assertIs(evaluate_static(recorder).status, StaticStatus.DISABLED)
        self.assertEqual(set(recorder.accessed), {"pilot_enabled"})
        self.assertFalse(recorder.iterated)

    def test_invalid_flag_types_fail_closed(self) -> None:
        for value in (1, 0, "true", "false", "", None, 1.0, float("nan"), [True]):
            with self.subTest(value=repr(value)):
                decision = evaluate_static(valid_facts(pilot_enabled=value))
                self.assertEqual((decision.status, decision.code), (StaticStatus.REJECTED, StaticCode.INVALID_FLAG))

    def test_non_mapping_input_rejected(self) -> None:
        for value in (None, [], "pilot_enabled", 1, (("pilot_enabled", True),)):
            with self.subTest(value=repr(value)):
                self.assertIs(evaluate_static(value).code, StaticCode.INVALID_INPUT)

    def test_mapping_raising_is_rejected_without_leaking(self) -> None:
        decision = evaluate_static(ExplodingMapping())
        self.assertIs(decision.code, StaticCode.INVALID_INPUT)
        self.assertNotIn("hunter2", repr(decision))


class FlagSequenceMapping(dict):
    """dict whose `pilot_enabled` reads return successive values (the last one repeats)."""

    def __init__(self, data: dict[str, object], flag_values: list[object], hide_flag_key: bool = False) -> None:
        super().__init__(data)
        self._flag_values = flag_values
        self._hide_flag_key = hide_flag_key
        self.flag_reads = 0
        self.other_reads: list[str] = []

    def __getitem__(self, key: str) -> object:
        if key != "pilot_enabled":
            self.other_reads.append(key)
            return super().__getitem__(key)
        self.flag_reads += 1
        value = self._flag_values[min(self.flag_reads, len(self._flag_values)) - 1]
        if isinstance(value, BaseException):
            raise value
        return value

    def keys(self):
        keys = list(super().keys())
        return [k for k in keys if k != "pilot_enabled"] if self._hide_flag_key else keys


class StaticFlagSnapshotTests(unittest.TestCase):
    def test_flag_flipping_to_false_after_first_read_fails_closed(self) -> None:
        facts = FlagSequenceMapping(valid_facts(), [True, False])
        decision = evaluate_static(facts)
        self.assertEqual((decision.status, decision.code, decision.target), (StaticStatus.REJECTED, StaticCode.FLAG_CHANGED, None))
        self.assertEqual(facts.flag_reads, 2)

    def test_flag_changing_to_invalid_type_after_first_read_fails_closed(self) -> None:
        for value in (1, 0, "true", "", None, 1.0, float("nan"), [True]):
            with self.subTest(value=repr(value)):
                facts = FlagSequenceMapping(valid_facts(), [True, value])
                decision = evaluate_static(facts)
                self.assertEqual((decision.status, decision.code), (StaticStatus.REJECTED, StaticCode.FLAG_CHANGED))
                self.assertEqual(facts.flag_reads, 2)

    def test_flag_disappearing_from_keys_after_first_read_fails_closed(self) -> None:
        facts = FlagSequenceMapping(valid_facts(), [True], hide_flag_key=True)
        decision = evaluate_static(facts)
        self.assertEqual((decision.status, decision.code), (StaticStatus.REJECTED, StaticCode.FLAG_CHANGED))
        self.assertEqual(facts.flag_reads, 1)

    def test_flag_reread_raising_is_rejected_without_leaking(self) -> None:
        facts = FlagSequenceMapping(valid_facts(), [True, RuntimeError("secret-host password=hunter2")])
        decision = evaluate_static(facts)
        self.assertEqual((decision.status, decision.code), (StaticStatus.REJECTED, StaticCode.INVALID_INPUT))
        self.assertNotIn("hunter2", repr(decision))
        self.assertNotIn("secret-host", repr(decision))

    def test_stable_true_flag_still_accepted(self) -> None:
        facts = FlagSequenceMapping(valid_facts(), [True])
        self.assertIs(evaluate_static(facts).status, StaticStatus.ACCEPTED)
        self.assertEqual(facts.flag_reads, 2)

    def test_false_first_read_returns_disabled_with_single_read(self) -> None:
        facts = FlagSequenceMapping(valid_facts(), [False, True])
        self.assertIs(evaluate_static(facts).status, StaticStatus.DISABLED)
        self.assertEqual((facts.flag_reads, facts.other_reads), (1, []))


class StaticAcceptedTests(unittest.TestCase):
    def test_valid_facts_accepted_with_target(self) -> None:
        decision = evaluate_static(valid_facts())
        self.assertEqual((decision.status, decision.code), (StaticStatus.ACCEPTED, StaticCode.OK))
        self.assertEqual(decision.target, PilotTarget(HOST, 5432, DB, HEAD, ATTESTATION))

    def test_both_allowed_drivers(self) -> None:
        for driver in ("postgresql", "postgresql+psycopg"):
            with self.subTest(driver=driver):
                self.assertIs(evaluate_static(valid_facts(url_driver=driver)).code, StaticCode.OK)

    def test_lower_boundaries_accepted(self) -> None:
        facts = valid_facts(lock_timeout_ms=1, statement_timeout_ms=1, idle_in_transaction_timeout_ms=1, url_port=1, pilot_port=1)
        self.assertIs(evaluate_static(facts).code, StaticCode.OK)

    def test_port_upper_boundary_accepted(self) -> None:
        self.assertIs(evaluate_static(valid_facts(url_port=65535, pilot_port=65535)).code, StaticCode.OK)

    def test_same_input_same_result(self) -> None:
        self.assertEqual(evaluate_static(valid_facts()), evaluate_static(valid_facts()))
        bad = valid_facts(url_port=1)
        self.assertEqual(evaluate_static(bad), evaluate_static(bad))

    def test_input_not_mutated(self) -> None:
        facts = valid_facts()
        before = dict(facts)
        evaluate_static(facts)
        self.assertEqual(facts, before)


class StaticRejectionTests(unittest.TestCase):
    """Each case changes exactly one fact of a valid configuration."""

    def assertRejected(self, code: StaticCode, **overrides: object) -> None:
        decision = evaluate_static(valid_facts(**overrides))
        self.assertEqual((decision.status, decision.code, decision.target), (StaticStatus.REJECTED, code, None))

    def test_timeout_bounds_match_spec(self) -> None:
        self.assertEqual(dict(guard.TIMEOUT_BOUNDS_MS), SPEC_TIMEOUT_BOUNDS_MS)

    def test_unknown_fact(self) -> None:
        self.assertRejected(StaticCode.UNKNOWN_FACT, database_url="postgresql://u:p@h/db")

    def test_each_missing_fact(self) -> None:
        for name in guard.STATIC_FACT_TYPES:
            if name == "pilot_enabled":
                continue
            with self.subTest(name=name):
                facts = valid_facts()
                del facts[name]
                self.assertIs(evaluate_static(facts).code, StaticCode.MISSING_FACT)

    def test_wrong_types_not_coerced(self) -> None:
        cases = {
            "test_enabled": [0, "false", None],
            "app_env": [None, b"production"],
            "url_port": ["5432", 5432.0, float("nan"), True, None],
            "pilot_port": ["5432", 5432.0, False],
            "lock_timeout_ms": ["5000", 5000.0, float("nan"), float("inf"), True],
            "statement_timeout_ms": ["1", 1.0, True],
            "idle_in_transaction_timeout_ms": ["1", 1.0, True],
            "pilot_schema_revision": [None, 1],
            "attestation_reference": [None, 1],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=repr(value)):
                    self.assertRejected(StaticCode.INVALID_FACT_TYPE, **{name: value})

    def test_empty_or_whitespace_text(self) -> None:
        for value in ("", " ", "a b", "x\n", "x\x00"):
            with self.subTest(value=repr(value)):
                self.assertRejected(StaticCode.INVALID_FACT_VALUE, url_host=value)
        self.assertRejected(StaticCode.INVALID_FACT_VALUE, url_host="h" * 256)

    def test_app_env_not_production(self) -> None:
        for env in ("test", "development", "Production", "staging"):
            with self.subTest(env=env):
                self.assertRejected(StaticCode.APP_ENV_NOT_PRODUCTION, app_env=env)

    def test_test_flag_mutually_exclusive(self) -> None:
        self.assertRejected(StaticCode.TEST_FLAG_CONFLICT, test_enabled=True)

    def test_driver_allowlist(self) -> None:
        for driver in ("sqlite", "postgresql+asyncpg", "postgresql+psycopg2", "PostgreSQL", "mysql"):
            with self.subTest(driver=driver):
                self.assertRejected(StaticCode.DRIVER_NOT_ALLOWED, url_driver=driver)

    def test_host_mismatch_each_side(self) -> None:
        for name in ("url_host", "pilot_host", "expected_database_host"):
            for value in ("other-host", HOST.upper(), "localhost"):
                with self.subTest(name=name, value=value):
                    self.assertRejected(StaticCode.HOST_MISMATCH, **{name: value})

    def test_invalid_port(self) -> None:
        for name in ("url_port", "pilot_port"):
            for value in (0, -1, 65536):
                with self.subTest(name=name, value=value):
                    self.assertRejected(StaticCode.INVALID_PORT, **{name: value})

    def test_port_mismatch(self) -> None:
        self.assertRejected(StaticCode.PORT_MISMATCH, url_port=5433)
        self.assertRejected(StaticCode.PORT_MISMATCH, pilot_port=5433)

    def test_test_database_forbidden(self) -> None:
        for value in ("auneron_test", "AUNERON_TEST"):
            with self.subTest(value=value):
                self.assertRejected(
                    StaticCode.TEST_DATABASE_FORBIDDEN,
                    url_database=value,
                    pilot_database=value,
                    expected_database_name=value,
                )
        self.assertRejected(StaticCode.TEST_DATABASE_FORBIDDEN, url_database="auneron_test")

    def test_database_mismatch_each_side(self) -> None:
        for name in ("url_database", "pilot_database", "expected_database_name"):
            for value in ("other_db", DB.upper()):
                with self.subTest(name=name, value=value):
                    self.assertRejected(StaticCode.DATABASE_MISMATCH, **{name: value})

    def test_invalid_revision(self) -> None:
        for value in ("unknown", "UNKNOWN", "b428-d1a6", "r" * 65, "head;drop"):
            with self.subTest(value=value):
                self.assertRejected(StaticCode.INVALID_REVISION, pilot_schema_revision=value, code_head_revision=value)
        self.assertRejected(StaticCode.INVALID_REVISION, code_head_revision="unknown")

    def test_revision_mismatch(self) -> None:
        self.assertRejected(StaticCode.REVISION_MISMATCH, pilot_schema_revision="aaaaaaaaaaaa")
        self.assertRejected(StaticCode.REVISION_MISMATCH, code_head_revision="aaaaaaaaaaaa")
        self.assertRejected(StaticCode.REVISION_MISMATCH, pilot_schema_revision=HEAD.upper())

    def test_invalid_attestation(self) -> None:
        for value in (
            "ATT",
            "att-import-01",
            "REF-IMPORT-01",
            "ATT-import-01",
            "ATT--01",
            "ATT-01-",
            "postgresql://u:p@h/db",
            "ATT-USER@HOST",
            "ATT-A:B",
            "ATT-" + "A" * 17,
            "ATT-" + "-".join(["AAAAAAAAAAAAAAAA"] * 4),  # well-formed segments but > 64 chars
        ):
            with self.subTest(value=value):
                self.assertRejected(StaticCode.INVALID_ATTESTATION, attestation_reference=value)

    def test_timeout_bounds_one_by_one(self) -> None:
        for name, bound in SPEC_TIMEOUT_BOUNDS_MS.items():
            with self.subTest(name=name, edge="at_bound"):
                self.assertIs(evaluate_static(valid_facts(**{name: bound})).code, StaticCode.OK)
            for value in (0, -1, bound + 1, 10**12):
                with self.subTest(name=name, value=value):
                    self.assertRejected(StaticCode.TIMEOUT_OUT_OF_BOUNDS, **{name: value})


class ReadinessTests(unittest.TestCase):
    def assertBlocked(self, code: ReadinessCode, **overrides: object) -> None:
        decision = evaluate_readiness(accepted(), valid_observation(**overrides))
        self.assertEqual((decision.status, decision.code), (ReadinessStatus.BLOCKED, code))

    def test_valid_observation_ready(self) -> None:
        decision = evaluate_readiness(accepted(), valid_observation())
        self.assertEqual((decision.status, decision.code), (ReadinessStatus.READY, ReadinessCode.OK))

    def test_disabled_or_rejected_static_never_ready(self) -> None:
        for facts in ({}, valid_facts(pilot_enabled=False), valid_facts(app_env="test"), valid_facts(url_port=1)):
            with self.subTest(status=evaluate_static(facts).status):
                decision = evaluate_readiness(evaluate_static(facts), valid_observation())
                self.assertEqual((decision.status, decision.code), (ReadinessStatus.BLOCKED, ReadinessCode.STATIC_NOT_ACCEPTED))

    def test_non_decision_static_rejected(self) -> None:
        target = accepted().target
        for value in (None, True, target, {"status": "accepted"}, valid_facts()):
            with self.subTest(value=type(value).__name__):
                self.assertIs(evaluate_readiness(value, valid_observation()).code, ReadinessCode.INVALID_STATIC_DECISION)

    def test_inconsistent_static_decision_cannot_be_constructed(self) -> None:
        target = accepted().target
        bad = [
            (StaticStatus.ACCEPTED, StaticCode.OK, None),
            (StaticStatus.ACCEPTED, StaticCode.DISABLED, target),
            (StaticStatus.DISABLED, StaticCode.OK, target),
            (StaticStatus.DISABLED, StaticCode.DISABLED, target),
            (StaticStatus.REJECTED, StaticCode.DISABLED, None),
            (StaticStatus.REJECTED, StaticCode.OK, None),
            ("accepted", StaticCode.OK, target),
        ]
        for args in bad:
            with self.subTest(args=args[:2]):
                with self.assertRaises(ValueError):
                    StaticDecision(*args)  # type: ignore[arg-type]

    def test_forged_invalid_target_blocked(self) -> None:
        for target in (
            PilotTarget(HOST, 5432, "auneron_test", HEAD, ATTESTATION),
            PilotTarget(HOST, 0, DB, HEAD, ATTESTATION),
            PilotTarget(HOST, True, DB, HEAD, ATTESTATION),  # type: ignore[arg-type]
            PilotTarget(HOST, 5432, DB, "unknown", ATTESTATION),
            PilotTarget(HOST, 5432, DB, HEAD, "nope"),
            PilotTarget("", 5432, DB, HEAD, ATTESTATION),
        ):
            with self.subTest(target=target):
                forged = StaticDecision(StaticStatus.ACCEPTED, StaticCode.OK, target)
                self.assertIs(evaluate_readiness(forged, valid_observation()).code, ReadinessCode.INVALID_STATIC_DECISION)

    def test_non_observation_rejected(self) -> None:
        as_dict = dataclasses.asdict(valid_observation())
        for value in (None, as_dict, True):
            with self.subTest(value=type(value).__name__):
                self.assertIs(evaluate_readiness(accepted(), value).code, ReadinessCode.INVALID_OBSERVATION)

    def test_observation_missing_or_unknown_field_cannot_be_constructed(self) -> None:
        values = dataclasses.asdict(valid_observation())
        del values["in_recovery"]
        with self.assertRaises(TypeError):
            ReadinessObservation(**values)
        with self.assertRaises(TypeError):
            valid_observation(extra=1)

    def test_observation_wrong_types_not_coerced(self) -> None:
        cases = {
            "current_database": [None, b"x"],
            "in_recovery": [0, "false", None],
            "transaction_read_only": [0, "off", None],
            "default_transaction_read_only": [0, "off"],
            "transaction_isolation": [None, 1],
            "default_transaction_isolation": [None],
            "lock_timeout_ms": ["5000", 5000.0, float("nan"), float("inf"), True],
            "statement_timeout_ms": ["1s", 1.0, True],
            "idle_in_transaction_timeout_ms": ["1", 1.0, False],
            "alembic_revisions": [[HEAD], HEAD, None, (1,), (None,)],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=repr(value)):
                    self.assertBlocked(ReadinessCode.INVALID_OBSERVATION, **{name: value})

    def test_database_mismatch(self) -> None:
        for value in ("other_db", DB.upper(), "auneron_test"):
            with self.subTest(value=value):
                self.assertBlocked(ReadinessCode.DATABASE_MISMATCH, current_database=value)

    def test_recovery(self) -> None:
        self.assertBlocked(ReadinessCode.IN_RECOVERY, in_recovery=True)

    def test_read_only(self) -> None:
        self.assertBlocked(ReadinessCode.TRANSACTION_READ_ONLY, transaction_read_only=True)
        self.assertBlocked(ReadinessCode.DEFAULT_READ_ONLY, default_transaction_read_only=True)

    def test_isolation(self) -> None:
        for value in ("repeatable read", "serializable", "read uncommitted", "READ COMMITTED", "read_committed"):
            with self.subTest(value=value):
                self.assertBlocked(ReadinessCode.ISOLATION_NOT_READ_COMMITTED, transaction_isolation=value)
                self.assertBlocked(ReadinessCode.DEFAULT_ISOLATION_NOT_READ_COMMITTED, default_transaction_isolation=value)

    def test_timeout_bounds_one_by_one(self) -> None:
        for name, bound in SPEC_TIMEOUT_BOUNDS_MS.items():
            with self.subTest(name=name, edge="lower"):
                self.assertIs(evaluate_readiness(accepted(), valid_observation(**{name: 1})).code, ReadinessCode.OK)
            for value in (0, -1, bound + 1):
                with self.subTest(name=name, value=value):
                    self.assertBlocked(ReadinessCode.TIMEOUT_OUT_OF_BOUNDS, **{name: value})

    def test_revision_rows(self) -> None:
        self.assertBlocked(ReadinessCode.REVISION_ROW_COUNT, alembic_revisions=())
        self.assertBlocked(ReadinessCode.REVISION_ROW_COUNT, alembic_revisions=(HEAD, HEAD))
        self.assertBlocked(ReadinessCode.REVISION_MISMATCH, alembic_revisions=("aaaaaaaaaaaa",))
        self.assertBlocked(ReadinessCode.REVISION_MISMATCH, alembic_revisions=(HEAD.upper(),))

    def test_same_input_same_result(self) -> None:
        static, obs = accepted(), valid_observation(in_recovery=True)
        self.assertEqual(evaluate_readiness(static, obs), evaluate_readiness(static, obs))

    def test_readiness_decision_invariant(self) -> None:
        for args in ((ReadinessStatus.READY, ReadinessCode.IN_RECOVERY), (ReadinessStatus.BLOCKED, ReadinessCode.OK), ("ready", ReadinessCode.OK)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    ReadinessDecision(*args)  # type: ignore[arg-type]


class ImmutabilityAndReprTests(unittest.TestCase):
    def test_frozen(self) -> None:
        static = accepted()
        objects = [static, static.target, valid_observation(), evaluate_readiness(static, valid_observation())]
        for obj in objects:
            with self.subTest(type=type(obj).__name__):
                field_name = dataclasses.fields(obj)[0].name
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(obj, field_name, None)

    def test_repr_redacts_target_and_observation_values(self) -> None:
        static = accepted()
        texts = [repr(static), str(static), repr(static.target), repr(valid_observation()), repr(evaluate_readiness(static, valid_observation()))]
        for text in texts:
            for secret in (HOST, DB, HEAD, ATTESTATION, "5432"):
                with self.subTest(text=text[:40], secret=secret):
                    self.assertNotIn(secret, text)

    def test_rejection_codes_carry_no_input_values(self) -> None:
        decision = evaluate_static(valid_facts(url_host="leaky-host-value"))
        self.assertNotIn("leaky", repr(decision))
        values = {code.value for code in StaticCode} | {code.value for code in ReadinessCode}
        for value in values:
            self.assertRegex(value, r"^[a-z_]+$")


_PURITY_PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
denied_roots = {"sqlalchemy", "fastapi", "starlette", "pydantic", "psycopg", "psycopg2", "socket", "ssl",
                "http", "urllib", "logging", "subprocess", "sqlite3", "asyncio", "threading", "app.core.config",
                "app.database", "app.main", "app.api", "app.services"}
before = set(sys.modules)
import app.core.import_pilot_guard as g
new = sorted(set(sys.modules) - before)
denied = [m for m in new if any(m == r or m.startswith(r + ".") for r in denied_roots)]

events = []
class TrapEnv(dict):
    def __getitem__(self, k): events.append("env"); raise KeyError(k)
    def get(self, k, d=None): events.append("env"); return d
    def __contains__(self, k): events.append("env"); return False
os.environ = TrapEnv()
def hook(name, args):
    if name in ("open", "import", "exec", "compile") or name.split(".")[0] in ("socket", "subprocess", "os", "shutil", "urllib", "http", "sqlite3", "ctypes"):
        events.append(name)
sys.addaudithook(hook)
facts = {
    "pilot_enabled": True, "test_enabled": False, "app_env": "production", "url_driver": "postgresql",
    "url_host": "h", "url_port": 5432, "url_database": "d", "expected_database_host": "h",
    "expected_database_name": "d", "pilot_host": "h", "pilot_port": 5432, "pilot_database": "d",
    "pilot_schema_revision": "r1", "code_head_revision": "r1", "attestation_reference": "ATT-X-01",
    "lock_timeout_ms": 1, "statement_timeout_ms": 1, "idle_in_transaction_timeout_ms": 1,
}
s = g.evaluate_static(facts)
obs = g.ReadinessObservation("d", False, False, False, "read committed", "read committed", 1, 1, 1, ("r1",))
r = g.evaluate_readiness(s, obs)
g.evaluate_static({}); g.evaluate_static({"pilot_enabled": "x"}); g.evaluate_readiness(g.evaluate_static({}), obs)
print(json.dumps({"denied": denied, "events": events, "static": s.code.value, "ready": r.code.value}))
"""


class PurityTests(unittest.TestCase):
    def test_isolated_import_and_evaluation_are_pure(self) -> None:
        env = {key: value for key, value in __import__("os").environ.items() if key.upper() in {"SYSTEMROOT", "PATH"}}
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _PURITY_PROBE, str(BACKEND_DIR)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["denied"], [])
        self.assertEqual(result["events"], [])
        self.assertEqual((result["static"], result["ready"]), ("ok", "ok"))


if __name__ == "__main__":
    unittest.main()
