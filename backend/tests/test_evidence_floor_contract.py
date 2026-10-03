"""
VALUE-3.4D-1 -- contrato OPERACIONAL estrito do activation floor e
preflight de ativacao (app/core/evidence_floor_contract.py +
scripts/evidence_floor_preflight.py).

O Settings (3.3B/3.4B) segue tolerante por decisao do PO; este contrato
e o que o PROCEDIMENTO de ativacao exige. Provas:

* matriz de formas validas/invalidas com codigo estavel;
* fuzz deterministico contra um oraculo independente (parser por posicao);
* todo valor aceito pelo contrato e aceito pelo Settings, com o mesmo
  instante (contrato estrito e subconjunto da camada tecnica);
* sensibilidade: mutantes do codigo-fonte do contrato sao detectados;
* preflight: exit codes, nenhum traceback, relogio do PostgreSQL
  (auneron_test) e somente leitura.

Nenhum teste aqui consulta o DEV: o relogio do banco e exercitado so em
`auneron_test` (APP_ENV=test e DATABASE_URL vindos do conftest).
"""

from __future__ import annotations

import ast
import random
import re
import subprocess
import sys
import types
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from sqlalchemy import event

import scripts.evidence_floor_preflight as preflight
from app.core import evidence_floor_contract as contract
from app.core.config import Settings
from app.database.database import engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
CONTRACT_SOURCE = BACKEND_DIR / "app/core/evidence_floor_contract.py"
PREFLIGHT_SOURCE = BACKEND_DIR / "scripts/evidence_floor_preflight.py"

TEST_URL = (
    "postgresql+psycopg://"
    "auneron:test_password"
    "@localhost:5432/auneron_test"
)

DB_NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)

VALID_FLOORS = {
    "utc_offset_zero": "2026-10-03T11:55:00+00:00",
    "z": "2026-10-03T11:55:00Z",
    "offset_minus_3": "2026-10-03T08:55:00-03:00",
    "offset_plus_530": "2026-10-03T17:25:00+05:30",
    "fraction_1_digit": "2026-10-03T11:55:00.1Z",
    "fraction_6_digits": "2026-10-03T11:55:00.123456+00:00",
    "exactly_30_minutes": "2026-10-03T11:30:00Z",
    "equal_to_db_clock": "2026-10-03T12:00:00Z",
}

# nome -> (valor, codigo esperado)
INVALID_FLOORS = {
    "epoch_zero": ("0", "format"),
    "epoch_seconds": ("1790000000", "format"),
    "epoch_milliseconds": ("1790000000000", "format"),
    "compact_number": ("20261003", "format"),
    "date_only": ("2026-10-03", "format"),
    "naive": ("2026-10-03T11:55:00", "format"),
    "naive_with_fraction": ("2026-10-03T11:55:00.123", "format"),
    "space_separator": ("2026-10-03 11:55:00+00:00", "format"),
    "lowercase_t": ("2026-10-03t11:55:00Z", "format"),
    "lowercase_z": ("2026-10-03T11:55:00z", "format"),
    "offset_without_colon": ("2026-10-03T11:55:00+0000", "format"),
    "offset_hour_only": ("2026-10-03T11:55:00+00", "format"),
    "no_seconds": ("2026-10-03T11:55Z", "format"),
    "fraction_7_digits": ("2026-10-03T11:55:00.1234567Z", "format"),
    "empty_fraction": ("2026-10-03T11:55:00.Z", "format"),
    "leading_space": (" 2026-10-03T11:55:00Z", "format"),
    "trailing_space": ("2026-10-03T11:55:00Z ", "format"),
    "trailing_newline": ("2026-10-03T11:55:00Z\n", "format"),
    "trailing_tab": ("2026-10-03T11:55:00Z\t", "format"),
    "fullwidth_digits": ("２０２６-10-03T11:55:00Z", "format"),
    "arabic_indic_digits": ("٢٠٢٦-10-03T11:55:00Z", "format"),
    "text": ("agora", "format"),
    "empty": ("", "format"),
    "whitespace_only": ("   ", "format"),
    "month_13": ("2026-13-03T11:55:00Z", "calendar"),
    "day_32": ("2026-10-32T11:55:00Z", "calendar"),
    "february_30": ("2026-02-30T11:55:00Z", "calendar"),
    "hour_24": ("2026-10-03T24:00:00Z", "calendar"),
    "second_60": ("2026-10-03T11:55:60Z", "calendar"),
    "offset_plus_24": ("2026-10-03T11:55:00+24:00", "calendar"),
    "future_1_minute": ("2026-10-03T12:01:00Z", "future"),
    "future_10_minutes": ("2026-10-03T12:10:00+00:00", "future"),
    "far_future_year": ("2200-01-01T00:00:00Z", "future"),
    "stale_31_minutes": ("2026-10-03T11:29:00Z", "stale"),
    "stale_2_hours": ("2026-10-03T10:00:00Z", "stale"),
    "epoch_as_iso": ("1970-01-01T00:00:00Z", "stale"),
}


# ---------------------------------------------------------------------
# Verificador reutilizado pelo modulo real E pelos mutantes
# ---------------------------------------------------------------------


def _contract_violations(module: types.ModuleType) -> list[str]:
    problems: list[str] = []

    for name, raw in VALID_FLOORS.items():
        try:
            canonical = module.validate_for_activation(raw, DB_NOW)
            expected = module.parse_strict(raw).astimezone(timezone.utc)
            if not (
                canonical.endswith("Z")
                and re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
                    r"[0-9]{2}\.[0-9]{6}Z",
                    canonical,
                )
                and datetime.fromisoformat(
                    canonical.replace("Z", "+00:00")
                )
                == expected
            ):
                problems.append(f"canonicalizacao errada em {name}")
        except Exception as error:  # noqa: BLE001
            problems.append(f"valido rejeitado: {name}: {error!r}")

    for name, (raw, code) in INVALID_FLOORS.items():
        try:
            accepted = module.validate_for_activation(raw, DB_NOW)
            problems.append(f"invalido aceito: {name} -> {accepted!r}")
        except module.FloorContractError as error:
            if error.code != code:
                problems.append(
                    f"codigo errado em {name}: {error.code} != {code}"
                )
        except Exception as error:  # noqa: BLE001
            problems.append(
                f"excecao nao tratada em {name}: {type(error).__name__}"
            )

    return problems


def _load_mutated(old: str, new: str) -> types.ModuleType:
    source = CONTRACT_SOURCE.read_text(encoding="utf-8")
    assert old in source, f"trecho a mutar ausente: {old!r}"
    module = types.ModuleType("mutated_evidence_floor_contract")
    exec(  # noqa: S102 - mutacao controlada de codigo do proprio repo
        compile(source.replace(old, new), "mutated_contract", "exec"),
        module.__dict__,
    )
    return module


def test_contract_has_no_violations() -> None:
    assert _contract_violations(contract) == []


@pytest.mark.parametrize(
    "mutation_name,old,new",
    [
        ("unicode_digits", "[0-9]", r"\d"),
        (
            "match_instead_of_fullmatch",
            "_STRICT_FLOOR.fullmatch(raw)",
            "_STRICT_FLOOR.match(raw)",
        ),
        ("no_future_check", "if value > db_now:", "if False:"),
        ("no_stale_check", "if db_now - value > max_age:", "if False:"),
        (
            "timezone_optional",
            r'r"(Z|[+-][0-9]{2}:[0-9]{2})"',
            r'r"(Z|[+-][0-9]{2}:[0-9]{2})?"',
        ),
        (
            "fraction_up_to_9_digits",
            r'r"(\.[0-9]{1,6})?"',
            r'r"(\.[0-9]{1,9})?"',
        ),
        (
            "lowercase_t_and_space_allowed",
            r'r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"',
            r'r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ]"',
        ),
        (
            "calendar_error_not_wrapped",
            "except ValueError as error:",
            "except KeyError as error:",
        ),
        (
            "canonical_drops_microseconds",
            '"%Y-%m-%dT%H:%M:%S.%fZ"',
            '"%Y-%m-%dT%H:%M:%SZ"',
        ),
    ],
)
def test_contract_mutants_are_detected(
    mutation_name: str, old: str, new: str
) -> None:
    mutated = _load_mutated(old, new)

    assert _contract_violations(mutated) != [], (
        f"mutante {mutation_name} nao foi detectado"
    )


@pytest.mark.parametrize("raw", list(VALID_FLOORS.values()), ids=list(VALID_FLOORS))
def test_valid_floor_is_accepted_and_canonicalized(raw: str) -> None:
    canonical = contract.validate_for_activation(raw, DB_NOW)

    assert canonical.endswith("Z")
    assert contract.parse_strict(canonical) == contract.parse_strict(raw)


@pytest.mark.parametrize(
    "raw,code",
    list(INVALID_FLOORS.values()),
    ids=list(INVALID_FLOORS),
)
def test_forbidden_floor_is_rejected_with_stable_code(
    raw: str, code: str
) -> None:
    with pytest.raises(contract.FloorContractError) as excinfo:
        contract.validate_for_activation(raw, DB_NOW)

    assert excinfo.value.code == code


def test_non_string_input_is_rejected() -> None:
    for value in (None, 0, 1790000000, 1.5, b"2026-10-03T11:55:00Z"):
        with pytest.raises(contract.FloorContractError) as excinfo:
            contract.parse_strict(value)  # type: ignore[arg-type]

        assert excinfo.value.code == "not_a_string"


def test_naive_reference_clock_is_rejected() -> None:
    with pytest.raises(contract.FloorContractError) as excinfo:
        contract.validate_for_activation(
            "2026-10-03T11:55:00Z", datetime(2026, 10, 3, 12, 0, 0)
        )

    assert excinfo.value.code == "db_now_naive"


def test_contract_has_no_calendar_year_range() -> None:
    # PO (3.4D): 2026-2100 nao e propriedade arquitetural; frescor e
    # "nao futuro" bastam. Sem faixa de anos no contrato.
    source = CONTRACT_SOURCE.read_text(encoding="utf-8")

    assert "year_out_of_range" not in source
    assert "YEAR_MIN" not in source and "YEAR_MAX" not in source


def test_age_limit_is_exactly_30_minutes() -> None:
    assert contract.MAX_FLOOR_AGE == timedelta(minutes=30)
    boundary = DB_NOW - timedelta(minutes=30)
    over = DB_NOW - timedelta(minutes=30, seconds=1)

    assert contract.validate_for_activation(
        boundary.isoformat(), DB_NOW
    )
    with pytest.raises(contract.FloorContractError) as excinfo:
        contract.validate_for_activation(over.isoformat(), DB_NOW)
    assert excinfo.value.code == "stale"


def test_floor_state_and_age() -> None:
    assert contract.floor_state(None, DB_NOW) == "unset"
    assert contract.floor_age_seconds(None, DB_NOW) is None

    past = DB_NOW - timedelta(minutes=5)
    assert contract.floor_state(past, DB_NOW) == "armed"
    assert contract.floor_age_seconds(past, DB_NOW) == 300

    future = DB_NOW + timedelta(minutes=5)
    assert contract.floor_state(future, DB_NOW) == "future"
    assert contract.floor_age_seconds(future, DB_NOW) == -300

    assert contract.floor_state(DB_NOW, DB_NOW) == "armed"


# ---------------------------------------------------------------------
# Subconjunto do Settings + fuzz contra oraculo independente
# ---------------------------------------------------------------------


@pytest.mark.parametrize("raw", list(VALID_FLOORS.values()), ids=list(VALID_FLOORS))
def test_strict_contract_is_a_subset_of_settings(raw: str) -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
        ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=raw,
    )

    assert (
        settings.escalation_payment_observation_activation_floor
        == contract.parse_strict(raw)
    )


def _oracle_accepts(raw: str) -> bool:
    """Parser por posicao, independente da regex do contrato."""
    if not raw.isascii() or len(raw) < 20:
        return False

    def digits(start: int, end: int) -> bool:
        return all("0" <= char <= "9" for char in raw[start:end])

    if not (
        digits(0, 4)
        and raw[4] == "-"
        and digits(5, 7)
        and raw[7] == "-"
        and digits(8, 10)
        and raw[10] == "T"
        and digits(11, 13)
        and raw[13] == ":"
        and digits(14, 16)
        and raw[16] == ":"
        and digits(17, 19)
    ):
        return False

    index = 19
    if index < len(raw) and raw[index] == ".":
        end = index + 1
        while end < len(raw) and "0" <= raw[end] <= "9":
            end += 1
        if not 1 <= end - (index + 1) <= 6:
            return False
        index = end

    rest = raw[index:]
    if rest == "Z":
        pass
    elif (
        len(rest) == 6
        and rest[0] in "+-"
        and rest[1:3].isdigit()
        and rest[3] == ":"
        and rest[4:6].isdigit()
    ):
        pass
    else:
        return False

    try:
        value = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError:
        return False

    return value.tzinfo is not None


def test_parser_agrees_with_independent_oracle_under_fuzz() -> None:
    generator = random.Random(34)
    seeds = list(VALID_FLOORS.values()) + [
        raw for raw, _ in INVALID_FLOORS.values()
    ]
    alphabet = "0123456789-:.TZz+ \t\n٢２abc"
    disagreements: list[str] = []

    for _ in range(5000):
        characters = list(generator.choice(seeds))
        for _mutation in range(generator.randint(1, 3)):
            operation = generator.random()
            position = generator.randrange(len(characters) + 1)
            if operation < 0.4 and characters:
                characters[min(position, len(characters) - 1)] = (
                    generator.choice(alphabet)
                )
            elif operation < 0.7:
                characters.insert(position, generator.choice(alphabet))
            elif characters:
                del characters[min(position, len(characters) - 1)]
        raw = "".join(characters)

        try:
            contract.parse_strict(raw)
            accepted = True
        except contract.FloorContractError:
            accepted = False

        if accepted != _oracle_accepts(raw):
            disagreements.append(raw)

    assert disagreements == []


# ---------------------------------------------------------------------
# Preflight (CLI)
# ---------------------------------------------------------------------

NOW_ARGUMENT = "2026-10-03T12:00:00Z"


def test_preflight_check_accepts_a_strict_fresh_floor(capsys) -> None:
    exit_code = preflight.main(
        ["check", "2026-10-03T08:55:00-03:00", "--now", NOW_ARGUMENT]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "OK T0=2026-10-03T11:55:00.000000Z" in output
    assert "clock_source=argument" in output


@pytest.mark.parametrize(
    "raw,code",
    list(INVALID_FLOORS.values()),
    ids=list(INVALID_FLOORS),
)
def test_preflight_check_rejects_every_forbidden_form(
    capsys, raw: str, code: str
) -> None:
    exit_code = preflight.main(
        ["check", raw, "--now", NOW_ARGUMENT]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert f"ACTIVATION PREFLIGHT FAIL: {code}" in output
    assert "Traceback" not in output


def test_preflight_rejects_a_malformed_reference_clock(capsys) -> None:
    exit_code = preflight.main(
        ["check", "2026-10-03T11:55:00Z", "--now", "agora"]
    )

    assert exit_code == 2
    assert (
        "ACTIVATION PREFLIGHT FAIL: now_format"
        in capsys.readouterr().out
    )


def _run_script(*arguments: str, env_overrides=None):
    import os

    environment = dict(os.environ)
    environment.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, str(PREFLIGHT_SOURCE), *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_preflight_script_never_prints_a_traceback_for_bad_input() -> None:
    result = _run_script("check", "0", "--now", NOW_ARGUMENT)

    assert result.returncode == 2
    assert "ACTIVATION PREFLIGHT FAIL: format" in result.stdout
    assert "Traceback" not in result.stderr + result.stdout


def test_preflight_script_fails_closed_when_database_is_unreachable() -> (
    None
):
    result = _run_script(
        "capture",
        env_overrides={
            "APP_ENV": "test",
            "DATABASE_URL": (
                "postgresql+psycopg://auneron:x@127.0.0.1:1/auneron_test"
            ),
        },
    )

    assert result.returncode == 3
    assert "ACTIVATION PREFLIGHT FAIL: db_unavailable" in result.stdout
    assert "Traceback" not in result.stderr + result.stdout


# ---------------------------------------------------------------------
# Relogio do PostgreSQL (somente auneron_test) e somente leitura
# ---------------------------------------------------------------------


def _capture_statements(run) -> list[str]:
    statements: list[str] = []

    def listener(
        conn, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return statements


def test_preflight_script_has_no_host_clock_calls() -> None:
    # O relogio so pode vir do PostgreSQL (clock_timestamp()).
    tree = ast.parse(PREFLIGHT_SOURCE.read_bytes())
    clock_calls = [
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "utcnow", "time", "today"}
    ]

    assert clock_calls == []


def test_capture_reads_the_postgresql_clock_read_only(capsys) -> None:
    exit_codes: list[int] = []

    statements = _capture_statements(
        lambda: exit_codes.append(preflight.main(["capture"]))
    )

    output = capsys.readouterr().out
    assert exit_codes == [0]

    t0_line = next(
        line for line in output.splitlines() if line.startswith("T0=")
    )
    t0 = t0_line.removeprefix("T0=")
    assert contract.parse_strict(t0).astimezone(timezone.utc)
    assert t0.endswith("Z")
    assert "clock_source=postgresql database=auneron_test" in output

    normalized = [" ".join(s.split()).upper() for s in statements]
    assert "SET TRANSACTION READ ONLY" in normalized
    assert any("CLOCK_TIMESTAMP()" in s for s in normalized)
    assert not any(
        re.match(r"^(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP)\b", s)
        for s in normalized
    )


def test_capture_value_brackets_the_database_clock(capsys) -> None:
    from sqlalchemy import text

    with engine.connect() as connection:
        before = connection.execute(
            text("SELECT clock_timestamp()")
        ).scalar_one()
    assert preflight.main(["capture"]) == 0
    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT clock_timestamp()")
        ).scalar_one()

    t0 = contract.parse_strict(
        next(
            line.removeprefix("T0=")
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("T0=")
        )
    )
    assert before <= t0 <= after


def test_check_without_now_uses_the_postgresql_clock(capsys) -> None:
    assert preflight.main(["capture"]) == 0
    t0 = next(
        line.removeprefix("T0=")
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("T0=")
    )
    exit_codes: list[int] = []

    statements = _capture_statements(
        lambda: exit_codes.append(preflight.main(["check", t0]))
    )

    output = capsys.readouterr().out
    assert exit_codes == [0]
    assert f"OK T0={t0}" in output
    assert "clock_source=postgresql" in output
    assert any(
        "CLOCK_TIMESTAMP()" in " ".join(s.split()).upper()
        for s in statements
    )


def test_check_judges_future_and_stale_against_the_database_clock(
    capsys,
) -> None:
    assert preflight.main(["capture"]) == 0
    t0 = contract.parse_strict(
        next(
            line.removeprefix("T0=")
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("T0=")
        )
    )

    future = contract.canonical_utc(t0 + timedelta(minutes=10))
    stale = contract.canonical_utc(t0 - timedelta(minutes=31))

    assert preflight.main(["check", future]) == 2
    assert "FAIL: future" in capsys.readouterr().out
    assert preflight.main(["check", stale]) == 2
    assert "FAIL: stale" in capsys.readouterr().out


def test_database_failure_is_fail_closed_in_process(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def broken() -> None:
        raise preflight.DatabaseUnavailableError("OperationalError")

    monkeypatch.setattr(preflight, "_db_clock_now", broken)

    assert preflight.main(["capture"]) == 3
    assert preflight.main(["check", "2026-10-03T11:55:00Z"]) == 3
    assert "db_unavailable" in capsys.readouterr().out
