"""
SIM-1.5G.2 / DEF-5 -- captura do codigo de saida pelo comando compartilhado (`detach.build_cmd_arguments`).

O L1-B01 mostrou `exit_code.txt` com 0 bytes. Estes testes EXECUTAM o `cmd.exe` de verdade (so um processo local curto, em
`tmp_path`; nada de Task Scheduler, WMI ou Docker) para provar que o codigo e gravado DEPOIS de o Python terminar, que o
arquivo contem um inteiro valido e que codigos nao zero sao preservados. Inclui o controle positivo da construcao ANTIGA.

SIM-1.5G.3: tambem o STATUS DE SAIDA DO PROPRIO `cmd` (o que o Task Scheduler registra em `LastTaskResult`) e o codigo do
Python. A cauda anterior (`& >arq echo !errorlevel!`) saia sempre 0 porque o `echo` era o ultimo comando.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from sim.runner import detach

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe so existe no Windows")
REPO = Path(__file__).resolve().parents[2]


def run_cmd(arguments: str) -> int:
    """Como o Task Scheduler: `cmd.exe` + Arguments, sem shell intermediario. Devolve o STATUS DE SAIDA do `cmd`."""
    result = subprocess.run("cmd.exe " + arguments, cwd=REPO, capture_output=True, timeout=120)
    assert result.returncode in range(-2**31, 2**32), result
    return result.returncode


def stub_arguments(proc: Path, code: int, marker: Path | None = None) -> str:
    cli_args = f"{code}" + (f' "{marker}"' if marker else "")
    return detach.build_cmd_arguments(sys.executable, cli_args, proc, module="sim.tests.exit_stub")


@pytest.mark.parametrize("code", [0, 1, 2, 3, 4, 5, 7, 255, -1])
def test_the_exit_code_is_written_after_python_ends_and_is_a_valid_integer(tmp_path, code):
    proc = tmp_path / "p"
    proc.mkdir()
    marker = tmp_path / "ran.txt"
    status = run_cmd(stub_arguments(proc, code, marker))
    assert status in {code, code % 2**32}                                                    # o `cmd` PROPAGA o codigo do Python
    path = proc / detach.EXIT_CODE
    text = path.read_text(encoding="utf-8")
    assert re.fullmatch(r"-?[0-9]+\s*", text), repr(text)                                  # inteiro valido, nunca vazio
    expected = {code, code % 2**32}                                                         # -1 pode ser lido como 4294967295
    assert detach.read_exit_code(proc) in expected
    assert marker.read_text(encoding="utf-8") == "done"
    assert marker.stat().st_mtime_ns <= path.stat().st_mtime_ns                              # gravado DEPOIS de o Python terminar
    assert "stub-out" in (proc / detach.STDOUT).read_text(encoding="utf-8", errors="replace")
    assert (proc / detach.STDERR).read_text(encoding="utf-8") == ""


def test_non_zero_codes_are_never_reported_as_zero(tmp_path):
    seen, statuses = set(), set()
    for code in (1, 3, 42):
        proc = tmp_path / f"p{code}"
        proc.mkdir()
        statuses.add(run_cmd(stub_arguments(proc, code)))
        seen.add(detach.read_exit_code(proc))
    assert seen == {1, 3, 42} and statuses == {1, 3, 42}


def test_positive_control_the_previous_tail_always_exited_zero_and_hid_python_failures(tmp_path):
    """A cauda ate 1.5G.2 (`& >arq echo !errorlevel!`): o arquivo tinha o codigo, mas o `cmd` saia 0 => `LastTaskResult` cego."""
    proc = tmp_path / "prev"
    proc.mkdir()
    inner = f'"{sys.executable}" -m sim.tests.exit_stub 7 1>>"{proc / detach.STDOUT}" 2>>"{proc / detach.STDERR}"'
    status = run_cmd(f'/d /v:on /s /c "{inner} & >"{proc / detach.EXIT_CODE}" echo !errorlevel!"')
    assert detach.read_exit_code(proc) == 7 and status == 0
    proc2 = tmp_path / "new"
    proc2.mkdir()
    assert run_cmd(stub_arguments(proc2, 7)) == 7 and detach.read_exit_code(proc2) == 7


def test_the_python_code_survives_a_failure_to_write_exit_code_txt(tmp_path):
    """Se o arquivo nao puder ser gravado (aqui: `exit_code.txt` e um DIRETORIO), o codigo do Python nao se perde no `cmd`."""
    proc = tmp_path / "p"
    (proc / detach.EXIT_CODE).mkdir(parents=True)
    assert run_cmd(stub_arguments(proc, 4)) == 4
    assert detach.read_exit_code(proc) is None                                               # arquivo ilegivel nunca vira codigo


def test_every_semantic_code_of_the_table_round_trips_through_the_real_cmd(tmp_path):
    from sim.runner import termination

    for reason, code in termination.EXIT_CODES.items():
        proc = tmp_path / reason
        proc.mkdir()
        assert run_cmd(stub_arguments(proc, code)) == code and detach.read_exit_code(proc) == code, reason


def test_positive_control_the_old_construction_loses_or_corrupts_the_code(tmp_path):
    proc = tmp_path / "old"
    proc.mkdir()
    inner = f'"{sys.executable}" -m sim.tests.exit_stub 7 1>>"{proc / detach.STDOUT}" 2>>"{proc / detach.STDERR}"'
    run_cmd(f'/d /s /c "{inner} & echo %errorlevel%> "{proc / detach.EXIT_CODE}""')
    assert detach.read_exit_code(proc) != 7                                                 # vazio (None) ou obsoleto (0)


@pytest.mark.parametrize("content,expected", [
    (None, None), ("", None), ("  \r\n", None), ("abc", None), ("1_0", None), ("1.0", None), ("+3", None), ("0x10", None),
    ("٣", None), ("12 34", None), ("99999999999", None), ("4294967296", None), ("-2147483649", None),
    ("0", 0), ("0\r\n", 0), ("  7 \r\n", 7), ("1", 1), ("255", 255), ("-1", -1), ("4294967295", 4294967295), ("-2147483648", -2147483648),
])
def test_read_exit_code_accepts_only_a_valid_integer_and_never_infers_it(tmp_path, content, expected):
    proc = tmp_path / "p"
    proc.mkdir()
    if content is not None:
        (proc / detach.EXIT_CODE).write_text(content, encoding="utf-8")
    (proc / detach.EXIT).write_text('{"code": 0, "reason": "STOP", "t_epoch": 1}', encoding="utf-8")   # exit.json NAO substitui
    assert detach.read_exit_code(proc) == expected


def test_a_binary_garbage_file_is_not_an_exit_code(tmp_path):
    proc = tmp_path / "p"
    proc.mkdir()
    (proc / detach.EXIT_CODE).write_bytes(b"\xff\xfe\x00\x01")
    assert detach.read_exit_code(proc) is None


def test_the_command_uses_delayed_expansion_and_redirects_before_echo(tmp_path):
    arguments = detach.build_cmd_arguments("C:\\v\\python.exe", "run --run-id X", Path("C:\\p"))
    assert arguments.startswith("/d /v:on /s /c ") and arguments.endswith('echo !RC! & exit !RC!"')
    assert ' & set "RC=!errorlevel!" & >"C:\\p\\exit_code.txt" echo !RC! & exit !RC!"' in arguments and "%errorlevel%" not in arguments
    assert arguments.index('set "RC=!errorlevel!"') < arguments.index("echo !RC!") < arguments.index("exit !RC!")   # capturado ANTES do echo
    assert 'echo !RC!>' not in arguments and arguments.count("!") == 6 and "%RC%" not in arguments
    assert detach.build_cmdline("C:\\v\\python.exe", "x", Path("C:\\p")).startswith("cmd.exe /d /v:on /s /c ")


@pytest.mark.parametrize("bad", ["%", "!", "^", "&", "|", "<", ">", "\n", "\r"])
@pytest.mark.parametrize("where", ["python", "cli_args", "module", "proc"])
def test_cmd_metacharacters_never_reach_the_command_line(bad, where):
    values = {"python": "C:\\v\\python.exe", "cli_args": "run --run-id X", "module": "sim.runner.cli", "proc": "C:\\p"}
    values[where] = values[where] + bad
    with pytest.raises(detach.DetachError, match="caractere especial"):
        detach.build_cmd_arguments(values["python"], values["cli_args"], Path(values["proc"]), values["module"])
