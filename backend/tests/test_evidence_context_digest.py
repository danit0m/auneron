"""
VALUE-3.4D-2b -- algoritmo `epc1` (context_digest): contrato congelado.

12 campos obrigatorios, JSON canonico restrito (chaves em ordem
lexicografica, sem espacos, ASCII; somente string/inteiro/booleano; nunca
`null`/float/`repr()` Python), SHA-256, floor UTC com 6 microssegundos. Os
vetores de ouro sao parte do contrato de regressao: NAO os altere para
acomodar uma implementacao.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from app.core.evidence_floor_contract import canonical_utc
from app.services.evidence_provenance_service import CONTEXT_DIGEST_ALGORITHM
from app.services.evidence_provenance_service import CONTEXT_FIELD_NAMES
from app.services.evidence_provenance_service import canonical_json
from app.services.evidence_provenance_service import compute_context_digest


BASE = {
    "activation_floor": "2026-10-06T12:00:00.000000Z",
    "actual_database_revision": "7432a1c2dd66",
    "context_digest_algorithm": "epc1",
    "expected_schema_revision": "7432a1c2dd66",
    "git_dirty": False,
    "git_sha": "72928738358cd842c6e165c81b9ceff9271f4be8",
    "producer_fingerprint": (
        "d0b9910dfc1a0a893e578c3c64c759b376ab97723bc6022c07fe5ab7a9474a24"
    ),
    "producer_fingerprint_algorithm": "pf1",
    "producer_spec": "escalation_payment_observation:v1",
    "source_digest": (
        "9809692d6d6ab524830152e4a75338f917d98e1cc2e0a38283d52143a7577863"
    ),
    "source_digest_algorithm": "sd1",
    "source_file_count": 216,
}

V1_JSON = (
    '{"activation_floor":"2026-10-06T12:00:00.000000Z",'
    '"actual_database_revision":"7432a1c2dd66",'
    '"context_digest_algorithm":"epc1",'
    '"expected_schema_revision":"7432a1c2dd66",'
    '"git_dirty":false,'
    '"git_sha":"72928738358cd842c6e165c81b9ceff9271f4be8",'
    '"producer_fingerprint":"d0b9910dfc1a0a893e578c3c64c759b376ab97723bc6022c07fe5ab7a9474a24",'
    '"producer_fingerprint_algorithm":"pf1",'
    '"producer_spec":"escalation_payment_observation:v1",'
    '"source_digest":"9809692d6d6ab524830152e4a75338f917d98e1cc2e0a38283d52143a7577863",'
    '"source_digest_algorithm":"sd1",'
    '"source_file_count":216}'
)
V1_DIGEST = (
    "be834b8e13fd10dcaa28e0021e8eed3a557e2295d4bc6048b9baecbae83fbbe5"
)

V2_FIELDS = {
    **BASE,
    "activation_floor": canonical_utc(
        datetime(
            2026,
            10,
            6,
            9,
            0,
            0,
            123456,
            tzinfo=timezone(timedelta(hours=-3)),
        )
    ),
}
V2_DIGEST = (
    "76f07b3a8bac48542326be0b72397b0148057decb9aa9c314889ba3065eff201"
)

V3_FIELDS = {
    **BASE,
    "source_file_count": 1,
    "expected_schema_revision": "a1b2c3d4e5f6",
    "actual_database_revision": "a1b2c3d4e5f6",
    "git_sha": "0" * 40,
}
V3_DIGEST = (
    "2df672c577a97bf551c79f3f9f1291f33ba6976acec8f5cb516c4270756638d5"
)


def manual_canonical_json(fields: dict) -> str:
    """Reimplementacao INDEPENDENTE (nao usa o modulo)."""
    parts = []
    for key in sorted(fields):
        value = fields[key]
        if value is True:
            encoded = "true"
        elif value is False:
            encoded = "false"
        elif isinstance(value, int):
            encoded = str(value)
        else:
            encoded = '"' + value + '"'
        parts.append('"' + key + '":' + encoded)
    return "{" + ",".join(parts) + "}"


# ---------------------------------------------------------------------
# 1. contrato congelado e vetores de ouro
# ---------------------------------------------------------------------


def test_frozen_contract() -> None:
    assert CONTEXT_DIGEST_ALGORITHM == "epc1"
    assert len(CONTEXT_FIELD_NAMES) == 12
    assert CONTEXT_FIELD_NAMES == tuple(sorted(CONTEXT_FIELD_NAMES))
    assert set(CONTEXT_FIELD_NAMES) == set(BASE)


def test_golden_vector_v1_canonical_json_and_digest() -> None:
    assert canonical_json(BASE) == V1_JSON
    assert manual_canonical_json(BASE) == V1_JSON
    assert compute_context_digest(BASE) == V1_DIGEST
    assert hashlib.sha256(V1_JSON.encode("utf-8")).hexdigest() == V1_DIGEST


def test_golden_vector_v2_floor_is_normalized_to_utc_microseconds() -> None:
    assert V2_FIELDS["activation_floor"] == "2026-10-06T12:00:00.123456Z"
    assert compute_context_digest(V2_FIELDS) == V2_DIGEST


def test_golden_vector_v3_distinct_revisions_one_file_zero_sha() -> None:
    assert compute_context_digest(V3_FIELDS) == V3_DIGEST
    assert canonical_json(V3_FIELDS) == manual_canonical_json(V3_FIELDS)


def test_canonical_json_equals_the_json_module_for_this_value_domain() -> None:
    for fields in (BASE, V2_FIELDS, V3_FIELDS):
        assert canonical_json(fields) == json.dumps(
            fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


def test_the_serialization_is_valid_json_with_sorted_keys() -> None:
    parsed = json.loads(canonical_json(BASE))

    assert list(parsed) == sorted(parsed)
    assert parsed == BASE


# ---------------------------------------------------------------------
# 2. cada campo participa do digest
# ---------------------------------------------------------------------


@pytest.mark.parametrize("field", CONTEXT_FIELD_NAMES)
def test_changing_any_single_field_changes_the_digest(field: str) -> None:
    changed = dict(BASE)
    value = changed[field]
    if isinstance(value, bool):
        changed[field] = not value
    elif isinstance(value, int):
        changed[field] = value + 1
    else:
        changed[field] = value[:-1] + ("0" if value[-1] != "0" else "1")

    assert compute_context_digest(changed) != V1_DIGEST


def test_key_insertion_order_does_not_matter() -> None:
    reordered = dict(reversed(list(BASE.items())))

    assert compute_context_digest(reordered) == V1_DIGEST


def test_execution_data_is_not_part_of_the_context() -> None:
    forbidden_names = {
        "id",
        "created_at",
        "context_digest",
        "pass_id",
        "producer_pass_id",
        "hostname",
        "pid",
        "interval_seconds",
        "batch_size",
        "business_timezone",
        "maintenance_enabled",
    }

    assert not forbidden_names & set(CONTEXT_FIELD_NAMES)


# ---------------------------------------------------------------------
# 3. tipos restritos e rejeicoes
# ---------------------------------------------------------------------


def test_missing_field_is_rejected() -> None:
    fields = dict(BASE)
    del fields["source_digest"]

    with pytest.raises(ValueError):
        canonical_json(fields)


def test_extra_field_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({**BASE, "pass_id": "x"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_digest", None),
        ("git_sha", None),
        ("source_file_count", None),
        ("source_file_count", 216.0),
        ("git_dirty", None),
        ("producer_spec", b"bytes"),
        ("producer_spec", ["list"]),
        ("activation_floor", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ],
)
def test_forbidden_types_are_rejected(field: str, value: object) -> None:
    # `null`, float e tipos nao-JSON (bytes, lista, datetime) sao proibidos.
    with pytest.raises(ValueError):
        canonical_json({**BASE, field: value})


@pytest.mark.parametrize(
    "field,value",
    [("git_dirty", "false"), ("source_file_count", "216")],
)
def test_a_string_is_never_confused_with_a_boolean_or_integer(
    field: str, value: str
) -> None:
    # tipado pelo chamador: a string "false"/"216" NAO produz o mesmo
    # digest que o booleano `false` / o inteiro 216.
    assert compute_context_digest({**BASE, field: value}) != V1_DIGEST


@pytest.mark.parametrize(
    "bad_value",
    ["não-ascii", 'with"quote', "back\\slash", "ctl\x01char", "line\nbreak"],
)
def test_non_canonical_string_characters_are_rejected(bad_value: str) -> None:
    with pytest.raises(ValueError):
        canonical_json({**BASE, "producer_spec": bad_value})


def test_bool_is_not_confused_with_int() -> None:
    as_true = {**BASE, "git_dirty": True}
    as_one = {**BASE, "git_dirty": 1}

    assert canonical_json(as_true) != canonical_json(as_one)
    assert '"git_dirty":true' in canonical_json(as_true)
    assert '"git_dirty":false' in canonical_json(BASE)


# ---------------------------------------------------------------------
# 4. floor canonico
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "floor,expected",
    [
        (
            datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc),
            "2026-10-06T12:00:00.000000Z",
        ),
        (
            datetime(2026, 10, 6, 12, 0, 0, 1, tzinfo=timezone.utc),
            "2026-10-06T12:00:00.000001Z",
        ),
        (
            datetime(
                2026,
                10,
                6,
                9,
                0,
                0,
                123456,
                tzinfo=timezone(timedelta(hours=-3)),
            ),
            "2026-10-06T12:00:00.123456Z",
        ),
        (
            datetime(
                2026,
                10,
                6,
                15,
                30,
                0,
                tzinfo=timezone(timedelta(hours=5, minutes=30)),
            ),
            "2026-10-06T10:00:00.000000Z",
        ),
    ],
)
def test_floor_is_utc_with_six_microsecond_digits(
    floor: datetime, expected: str
) -> None:
    assert canonical_utc(floor) == expected


def test_same_instant_different_offsets_give_the_same_digest() -> None:
    utc = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    brt = datetime(
        2026, 10, 6, 9, 0, 0, tzinfo=timezone(timedelta(hours=-3))
    )

    first = {**BASE, "activation_floor": canonical_utc(utc)}
    second = {**BASE, "activation_floor": canonical_utc(brt)}

    assert compute_context_digest(first) == compute_context_digest(second)
