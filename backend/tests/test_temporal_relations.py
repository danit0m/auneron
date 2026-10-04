"""
OPS-CI-1.2 -- regressao temporal independente do horario do CI.

Contexto: o `Backend + Frontend CI` falhou (run 37082218299) porque testes
construiam datas com `date.today()` (relogio LOCAL do host; UTC no runner)
para codigo cujo contrato de "hoje" e `business_today()` (fuso de negocio,
`BUSINESS_TIMEZONE`, padrao America/Sao_Paulo). Entre 00:00Z e 03:00Z as
duas datas diferem em um dia (`days_overdue` 9 em vez de 10).

Este modulo prova, em subprocessos isolados e SEM depender da hora em que
o CI roda (nem alterar o relogio do host/PostgreSQL), as tres relacoes:

* SAME   (D)    -- data do host == data de negocio;
* BEHIND (D-1)  -- data de negocio = data do host - 1 dia (o caso do CI);
* AHEAD  (D+1)  -- data de negocio = data do host + 1 dia.

A defasagem e exata (24 h entre os dois fusos fixos) em qualquer instante:
`TZ` (fuso do processo do host) + `BUSINESS_TIMEZONE`.

Inclui controle de sensibilidade: um teste sintetico que usa `date.today()`
contra `business_today()` DEVE falhar em BEHIND/AHEAD e passar em SAME --
sem isso um "passou" nao provaria que o metodo detecta a falha.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]

# nome -> (TZ do processo, BUSINESS_TIMEZONE, delta esperado negocio - host em dias)
RELATIONS = {
    "SAME": ("UTC0", "UTC", 0),
    "BEHIND": ("XXX-12", "Etc/GMT+12", -1),
    "AHEAD": ("XXX12", "Etc/GMT-12", 1),
}

# Testes que eram temporalmente vulneraveis (Discovery OPS-CI-1.2).
VULNERABLE_TESTS = (
    "tests/test_human_escalation_api.py::"
    "test_human_escalation_returns_200_with_frozen_shape",
    "tests/test_nba_policy_api.py::"
    "test_nba_returns_200_with_frozen_shape",
    "tests/test_human_escalation_eligibility.py::"
    "test_overdue_open_account_without_work_item_is_eligible",
    "tests/test_nba_policy.py::test_r3a_high_exposure_early_trigger_alone",
    "tests/test_nba_policy.py::test_r3b_prolonged_overdue_trigger_alone",
    "tests/test_nba_policy.py::test_prolonged_boundary_46_days_500_01_is_true",
    "tests/test_nba_policy.py::"
    "test_high_exposure_boundary_5_days_15000_is_true",
    "tests/test_nba_policy.py::"
    "test_prolonged_boundary_exactly_45_days_500_amount_is_false",
    "tests/test_nba_policy.py::"
    "test_high_exposure_boundary_4_days_15000_is_false",
)

# Arquivos corrigidos: `date.today()` so e permitido onde o valor e inerte
# (conta inexistente => 404, sem calculo de dias de atraso).
CORRECTED_FILES = {
    "tests/test_human_escalation_api.py": {
        "test_human_escalation_returns_404_for_nonexistent_account"
    },
    "tests/test_nba_policy_api.py": {
        "test_nba_returns_404_for_nonexistent_account"
    },
    "tests/test_human_escalation_eligibility.py": set(),
    "tests/test_nba_policy.py": set(),
}


def _child_environment(relation: str) -> dict[str, str]:
    host_timezone, business_timezone, _ = RELATIONS[relation]
    environment = dict(os.environ)
    environment["TZ"] = host_timezone
    environment["BUSINESS_TIMEZONE"] = business_timezone
    return environment


def _run_pytest(
    arguments: list[str], relation: str, *, cwd: Path = BACKEND_DIR
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *arguments,
        ],
        cwd=cwd,
        env=_child_environment(relation),
        capture_output=True,
        text=True,
        timeout=600,
    )


# ---------------------------------------------------------------------
# 1. As relacoes realmente produzem a defasagem pretendida
# ---------------------------------------------------------------------

_DATES_SNIPPET = (
    "from datetime import date, datetime\n"
    "from zoneinfo import ZoneInfo\n"
    "import os\n"
    "host = date.today()\n"
    "business = datetime.now(ZoneInfo(os.environ['BUSINESS_TIMEZONE'])).date()\n"
    "print((business - host).days)\n"
)


@pytest.mark.parametrize("relation", list(RELATIONS))
def test_relation_produces_the_intended_one_day_offset(
    relation: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _DATES_SNIPPET],
        env=_child_environment(relation),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) == RELATIONS[relation][2]


# ---------------------------------------------------------------------
# 2. Controle de sensibilidade: o metodo DETECTA o desvio
# ---------------------------------------------------------------------

_SYNTHETIC_VULNERABLE_TEST = '''
from datetime import date

from app.core.receivable_lifecycle import business_today


def test_host_date_equals_business_date() -> None:
    # exatamente o padrao que quebrou o CI: data do host contra o
    # consumidor que usa a data de negocio.
    assert date.today() == business_today()
'''


@pytest.mark.parametrize(
    "relation,should_fail",
    [("SAME", False), ("BEHIND", True), ("AHEAD", True)],
)
def test_synthetic_host_date_pattern_is_detected(
    tmp_path: Path, relation: str, should_fail: bool
) -> None:
    synthetic = tmp_path / "test_synthetic_host_date_pattern.py"
    synthetic.write_text(_SYNTHETIC_VULNERABLE_TEST, encoding="utf-8")

    empty_ini = tmp_path / "pytest.ini"
    empty_ini.write_text("[pytest]\n", encoding="utf-8")

    result = _run_pytest(
        [str(synthetic), "--rootdir", str(tmp_path), "-c", str(empty_ini)],
        relation,
    )

    if should_fail:
        assert result.returncode != 0, (
            f"o padrao vulneravel passou em {relation}: o metodo nao "
            "detectaria a regressao"
        )
        assert "1 failed" in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed" in result.stdout


# ---------------------------------------------------------------------
# 3. Os testes corrigidos passam nas tres relacoes (D-1, D, D+1)
# ---------------------------------------------------------------------


def test_vulnerable_set_is_the_nine_found_by_the_discovery() -> None:
    # A matriz usa len(VULNERABLE_TESTS) dinamicamente; sem este pino,
    # remover um id da lista a deixaria "passar" exercitando menos.
    assert len(VULNERABLE_TESTS) == 9
    assert len(set(VULNERABLE_TESTS)) == 9


def test_vulnerable_test_ids_still_exist() -> None:
    # evita que um rename faca a matriz abaixo "passar" sem exercitar nada
    # `_run_pytest` ja passa `-q`; um segundo `-q` (-qq) suprime a linha
    # "N tests collected".
    result = _run_pytest(["--collect-only", *VULNERABLE_TESTS], "SAME")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{len(VULNERABLE_TESTS)} tests collected" in result.stdout


@pytest.mark.parametrize("relation", list(RELATIONS))
def test_formerly_vulnerable_tests_pass_in_every_date_relation(
    relation: str,
) -> None:
    result = _run_pytest(list(VULNERABLE_TESTS), relation)

    assert result.returncode == 0, (
        f"relacao {relation}:\n{result.stdout[-3000:]}{result.stderr[-500:]}"
    )
    assert f"{len(VULNERABLE_TESTS)} passed" in result.stdout


# ---------------------------------------------------------------------
# 4. Guarda estatica: o produtor da data nao volta a ser o relogio do host
# ---------------------------------------------------------------------


def _host_date_calls(source: bytes) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    inside_function: set[int] = set()
    findings: list[tuple[int, str]] = []

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "date.today"
            ):
                findings.append((node.lineno, function.name))
                inside_function.add(node.lineno)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "date.today"
            and node.lineno not in inside_function
        ):
            findings.append((node.lineno, "<modulo>"))

    return findings


def _offenders(source: bytes, allowed: set[str]) -> list[tuple[int, str]]:
    return [
        (lineno, name)
        for lineno, name in _host_date_calls(source)
        if name not in allowed
    ]


@pytest.mark.parametrize("relative_path", list(CORRECTED_FILES))
def test_corrected_files_do_not_build_dates_from_the_host_clock(
    relative_path: str,
) -> None:
    source = (BACKEND_DIR / relative_path).read_bytes()

    assert _offenders(source, CORRECTED_FILES[relative_path]) == [], (
        f"{relative_path}: date.today() fora dos testes inertes permitidos"
    )


@pytest.mark.parametrize(
    "relative_path,original,mutated",
    [
        (
            "tests/test_nba_policy.py",
            "business_today() - timedelta(days=days_overdue)",
            "date.today() - timedelta(days=days_overdue)",
        ),
        (
            "tests/test_human_escalation_eligibility.py",
            "return business_today() - timedelta(days=10)",
            "return date.today() - timedelta(days=10)",
        ),
        (
            "tests/test_human_escalation_api.py",
            "due_date = business_today() - timedelta(days=10)",
            "due_date = date.today() - timedelta(days=10)",
        ),
        (
            "tests/test_nba_policy_api.py",
            "due_date = business_today() - timedelta(days=10)",
            "due_date = date.today() - timedelta(days=10)",
        ),
    ],
)
def test_static_guard_detects_a_reintroduced_host_date(
    relative_path: str, original: str, mutated: str
) -> None:
    source = (BACKEND_DIR / relative_path).read_text(encoding="utf-8")
    assert original in source

    mutated_source = source.replace(original, mutated, 1).encode("utf-8")

    assert _offenders(
        mutated_source, CORRECTED_FILES[relative_path]
    ) != []
