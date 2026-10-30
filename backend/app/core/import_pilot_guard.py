"""Pure pilot-guard decisions for `/imports/confirm` (TASK-034, RESULT-033 §11).

Design-only decision functions over plain facts. This module performs no I/O,
reads no environment, opens no connection and is NOT wired into the app: a
decision returned here is not runtime authority. The attestation reference is
only a syntactic prerequisite, never proof of a human signature or topology.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class StaticStatus(Enum):
    DISABLED = "disabled"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class StaticCode(Enum):
    OK = "ok"
    DISABLED = "disabled"
    INVALID_INPUT = "invalid_input"
    INVALID_FLAG = "invalid_flag"
    FLAG_CHANGED = "flag_changed"
    UNKNOWN_FACT = "unknown_fact"
    MISSING_FACT = "missing_fact"
    INVALID_FACT_TYPE = "invalid_fact_type"
    INVALID_FACT_VALUE = "invalid_fact_value"
    APP_ENV_NOT_PRODUCTION = "app_env_not_production"
    TEST_FLAG_CONFLICT = "test_flag_conflict"
    DRIVER_NOT_ALLOWED = "driver_not_allowed"
    HOST_MISMATCH = "host_mismatch"
    INVALID_PORT = "invalid_port"
    PORT_MISMATCH = "port_mismatch"
    TEST_DATABASE_FORBIDDEN = "test_database_forbidden"
    DATABASE_MISMATCH = "database_mismatch"
    INVALID_REVISION = "invalid_revision"
    REVISION_MISMATCH = "revision_mismatch"
    INVALID_ATTESTATION = "invalid_attestation"
    TIMEOUT_OUT_OF_BOUNDS = "timeout_out_of_bounds"


class ReadinessStatus(Enum):
    READY = "ready"
    BLOCKED = "blocked"


class ReadinessCode(Enum):
    OK = "ok"
    STATIC_NOT_ACCEPTED = "static_not_accepted"
    INVALID_STATIC_DECISION = "invalid_static_decision"
    INVALID_OBSERVATION = "invalid_observation"
    DATABASE_MISMATCH = "database_mismatch"
    IN_RECOVERY = "in_recovery"
    TRANSACTION_READ_ONLY = "transaction_read_only"
    DEFAULT_READ_ONLY = "default_read_only"
    ISOLATION_NOT_READ_COMMITTED = "isolation_not_read_committed"
    DEFAULT_ISOLATION_NOT_READ_COMMITTED = "default_isolation_not_read_committed"
    TIMEOUT_OUT_OF_BOUNDS = "timeout_out_of_bounds"
    REVISION_ROW_COUNT = "revision_row_count"
    REVISION_MISMATCH = "revision_mismatch"


PILOT_FLAG = "pilot_enabled"
ALLOWED_DRIVERS = frozenset({"postgresql", "postgresql+psycopg"})
FORBIDDEN_DATABASE = "auneron_test"
READ_COMMITTED = "read committed"
TIMEOUT_BOUNDS_MS = {
    "lock_timeout_ms": 5000,
    "statement_timeout_ms": 30000,
    "idle_in_transaction_timeout_ms": 60000,
}

# Fact name -> exact required type (bool is not accepted where int is required).
STATIC_FACT_TYPES: Mapping[str, type] = {
    PILOT_FLAG: bool,
    "test_enabled": bool,
    "app_env": str,
    "url_driver": str,
    "url_host": str,
    "url_port": int,
    "url_database": str,
    "expected_database_host": str,
    "expected_database_name": str,
    "pilot_host": str,
    "pilot_port": int,
    "pilot_database": str,
    "pilot_schema_revision": str,
    "code_head_revision": str,
    "attestation_reference": str,
    **{name: int for name in TIMEOUT_BOUNDS_MS},
}

_REVISION_FORMAT = re.compile(r"[A-Za-z0-9_]{1,64}")
# Non-secret reference id only (e.g. ATT-IMPORT-PILOT-20261030-01): no URL, user, password or spaces.
_ATTESTATION_FORMAT = re.compile(r"ATT(?:-[A-Z0-9]{1,16}){1,6}")
_MAX_TEXT = 255


@dataclass(frozen=True)
class PilotTarget:
    host: str = field(repr=False)
    port: int = field(repr=False)
    database: str = field(repr=False)
    schema_revision: str = field(repr=False)
    attestation_reference: str = field(repr=False)


@dataclass(frozen=True)
class StaticDecision:
    status: StaticStatus
    code: StaticCode
    target: PilotTarget | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        accepted = self.status is StaticStatus.ACCEPTED
        consistent = (
            type(self.status) is StaticStatus
            and type(self.code) is StaticCode
            and accepted == (self.code is StaticCode.OK)
            and accepted == (type(self.target) is PilotTarget)
            and (self.status is not StaticStatus.DISABLED or self.code is StaticCode.DISABLED)
            and (self.code is not StaticCode.DISABLED or self.status is StaticStatus.DISABLED)
        )
        if not consistent:
            raise ValueError("inconsistent_static_decision")


@dataclass(frozen=True)
class ReadinessObservation:
    """Plain facts from a future external probe (not implemented here).

    The probe is expected to run SELECT-only statements in a normal (not
    read-only) transaction; `transaction_read_only` is the observed value.
    """

    current_database: str = field(repr=False)
    in_recovery: bool = field(repr=False)
    transaction_read_only: bool = field(repr=False)
    default_transaction_read_only: bool = field(repr=False)
    transaction_isolation: str = field(repr=False)
    default_transaction_isolation: str = field(repr=False)
    lock_timeout_ms: int = field(repr=False)
    statement_timeout_ms: int = field(repr=False)
    idle_in_transaction_timeout_ms: int = field(repr=False)
    alembic_revisions: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class ReadinessDecision:
    status: ReadinessStatus
    code: ReadinessCode

    def __post_init__(self) -> None:
        if not (
            type(self.status) is ReadinessStatus
            and type(self.code) is ReadinessCode
            and (self.status is ReadinessStatus.READY) == (self.code is ReadinessCode.OK)
        ):
            raise ValueError("inconsistent_readiness_decision")


_DISABLED = StaticDecision(StaticStatus.DISABLED, StaticCode.DISABLED)


def _rejected(code: StaticCode) -> StaticDecision:
    return StaticDecision(StaticStatus.REJECTED, code)


def _blocked(code: ReadinessCode) -> ReadinessDecision:
    return ReadinessDecision(ReadinessStatus.BLOCKED, code)


def _valid_text(value: str) -> bool:
    return 0 < len(value) <= _MAX_TEXT and value.isprintable() and not any(c.isspace() for c in value)


def _valid_revision(value: str) -> bool:
    return _REVISION_FORMAT.fullmatch(value) is not None and value.lower() != "unknown"


def _valid_port(value: int) -> bool:
    return 0 < value <= 65535


def _valid_attestation(value: str) -> bool:
    return len(value) <= 64 and _ATTESTATION_FORMAT.fullmatch(value) is not None


def _timeout_in_bounds(name: str, value: int) -> bool:
    return 0 < value <= TIMEOUT_BOUNDS_MS[name]


def evaluate_static(facts: object) -> StaticDecision:
    """Decide the pilot static configuration from plain facts. Never raises.

    The flag is read first; when it is absent or False the result is DISABLED
    and no other fact is accessed. When it is True, all later decisions use only
    an internal snapshot, whose flag must again be exactly True (else
    `flag_changed`). This does not make arbitrary concurrent mutation of the
    input safe; it only stops a re-read from disagreeing with the first read.
    """
    if not isinstance(facts, Mapping):
        return _rejected(StaticCode.INVALID_INPUT)
    try:
        if PILOT_FLAG not in facts:
            return _DISABLED
        flag = facts[PILOT_FLAG]
        if type(flag) is not bool:
            return _rejected(StaticCode.INVALID_FLAG)
        if flag is False:
            return _DISABLED
        snapshot = {key: facts[key] for key in list(facts.keys())}
    except Exception:
        return _rejected(StaticCode.INVALID_INPUT)

    if snapshot.get(PILOT_FLAG) is not True:
        return _rejected(StaticCode.FLAG_CHANGED)
    if any(key not in STATIC_FACT_TYPES for key in snapshot):
        return _rejected(StaticCode.UNKNOWN_FACT)
    if any(key not in snapshot for key in STATIC_FACT_TYPES):
        return _rejected(StaticCode.MISSING_FACT)
    if any(type(snapshot[key]) is not kind for key, kind in STATIC_FACT_TYPES.items()):
        return _rejected(StaticCode.INVALID_FACT_TYPE)
    if any(type(value) is str and not _valid_text(value) for value in snapshot.values()):
        return _rejected(StaticCode.INVALID_FACT_VALUE)

    f = snapshot
    if f["app_env"] != "production":
        return _rejected(StaticCode.APP_ENV_NOT_PRODUCTION)
    if f["test_enabled"] is not False:
        return _rejected(StaticCode.TEST_FLAG_CONFLICT)
    if f["url_driver"] not in ALLOWED_DRIVERS:
        return _rejected(StaticCode.DRIVER_NOT_ALLOWED)
    if not (f["url_host"] == f["pilot_host"] == f["expected_database_host"]):
        return _rejected(StaticCode.HOST_MISMATCH)
    if not (_valid_port(f["url_port"]) and _valid_port(f["pilot_port"])):
        return _rejected(StaticCode.INVALID_PORT)
    if f["url_port"] != f["pilot_port"]:
        return _rejected(StaticCode.PORT_MISMATCH)
    names = (f["url_database"], f["pilot_database"], f["expected_database_name"])
    if any(name.lower() == FORBIDDEN_DATABASE for name in names):
        return _rejected(StaticCode.TEST_DATABASE_FORBIDDEN)
    if not (names[0] == names[1] == names[2]):
        return _rejected(StaticCode.DATABASE_MISMATCH)
    if not (_valid_revision(f["pilot_schema_revision"]) and _valid_revision(f["code_head_revision"])):
        return _rejected(StaticCode.INVALID_REVISION)
    if f["pilot_schema_revision"] != f["code_head_revision"]:
        return _rejected(StaticCode.REVISION_MISMATCH)
    if not _valid_attestation(f["attestation_reference"]):
        return _rejected(StaticCode.INVALID_ATTESTATION)
    if not all(_timeout_in_bounds(name, f[name]) for name in TIMEOUT_BOUNDS_MS):
        return _rejected(StaticCode.TIMEOUT_OUT_OF_BOUNDS)

    target = PilotTarget(
        host=f["pilot_host"],
        port=f["pilot_port"],
        database=f["pilot_database"],
        schema_revision=f["pilot_schema_revision"],
        attestation_reference=f["attestation_reference"],
    )
    return StaticDecision(StaticStatus.ACCEPTED, StaticCode.OK, target)


def _target_is_valid(target: PilotTarget) -> bool:
    return (
        type(target.host) is str
        and _valid_text(target.host)
        and type(target.port) is int
        and _valid_port(target.port)
        and type(target.database) is str
        and _valid_text(target.database)
        and target.database.lower() != FORBIDDEN_DATABASE
        and type(target.schema_revision) is str
        and _valid_revision(target.schema_revision)
        and type(target.attestation_reference) is str
        and _valid_attestation(target.attestation_reference)
    )


_OBSERVATION_TYPES: Mapping[str, type] = {
    "current_database": str,
    "in_recovery": bool,
    "transaction_read_only": bool,
    "default_transaction_read_only": bool,
    "transaction_isolation": str,
    "default_transaction_isolation": str,
    **{name: int for name in TIMEOUT_BOUNDS_MS},
    "alembic_revisions": tuple,
}


def evaluate_readiness(static: object, observation: object) -> ReadinessDecision:
    """Decide readiness from an ACCEPTED static decision plus probe facts. Never raises.

    A DISABLED or REJECTED static decision can never yield READY.
    """
    if type(static) is not StaticDecision:
        return _blocked(ReadinessCode.INVALID_STATIC_DECISION)
    if static.status is not StaticStatus.ACCEPTED or static.code is not StaticCode.OK:
        return _blocked(ReadinessCode.STATIC_NOT_ACCEPTED)
    target = static.target
    if type(target) is not PilotTarget or not _target_is_valid(target):
        return _blocked(ReadinessCode.INVALID_STATIC_DECISION)
    if type(observation) is not ReadinessObservation:
        return _blocked(ReadinessCode.INVALID_OBSERVATION)
    obs = observation
    if any(type(getattr(obs, name)) is not kind for name, kind in _OBSERVATION_TYPES.items()):
        return _blocked(ReadinessCode.INVALID_OBSERVATION)
    if any(type(rev) is not str for rev in obs.alembic_revisions):
        return _blocked(ReadinessCode.INVALID_OBSERVATION)

    if obs.current_database != target.database:
        return _blocked(ReadinessCode.DATABASE_MISMATCH)
    if obs.in_recovery is not False:
        return _blocked(ReadinessCode.IN_RECOVERY)
    if obs.transaction_read_only is not False:
        return _blocked(ReadinessCode.TRANSACTION_READ_ONLY)
    if obs.default_transaction_read_only is not False:
        return _blocked(ReadinessCode.DEFAULT_READ_ONLY)
    if obs.transaction_isolation != READ_COMMITTED:
        return _blocked(ReadinessCode.ISOLATION_NOT_READ_COMMITTED)
    if obs.default_transaction_isolation != READ_COMMITTED:
        return _blocked(ReadinessCode.DEFAULT_ISOLATION_NOT_READ_COMMITTED)
    if not all(_timeout_in_bounds(name, getattr(obs, name)) for name in TIMEOUT_BOUNDS_MS):
        return _blocked(ReadinessCode.TIMEOUT_OUT_OF_BOUNDS)
    if len(obs.alembic_revisions) != 1:
        return _blocked(ReadinessCode.REVISION_ROW_COUNT)
    if obs.alembic_revisions[0] != target.schema_revision:
        return _blocked(ReadinessCode.REVISION_MISMATCH)
    return ReadinessDecision(ReadinessStatus.READY, ReadinessCode.OK)
