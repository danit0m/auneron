"""
SIM-1.5G.3 -- identidade de processo (PID + data de criacao), descoberta do CLI iniciador, vigilia duravel (`watch.jsonl`),
versao verificavel da evidencia e replay historico.

Parte usa a API REAL do Windows apenas para LER processos do proprio teste (o proprio PID, um filho curto criado e encerrado
aqui, um PID inexistente e o processo System, cuja legibilidade DEPENDE DO PRIVILEGIO do usuario: o teste real so exige a
coerencia de cada estado, nunca um estado especifico); nenhum processo alheio e encerrado. A traducao dos erros do Win32 por
`query_native` e verificada de forma DETERMINISTICA com um `kernel32` falso (sem handle, processo ou privilegio reais). O resto usa
`FakeOS`.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys

import pytest

from sim.runner import cli
from sim.runner import detach
from sim.runner import detach_probe
from sim.runner import sched
from sim.runner import watch
from sim.tests.sched_support import FakeOS
from sim.tests.sched_support import probe_os
from sim.tests.test_runner_sched import good_facts
from sim.tests.test_runner_sched import legacy_monitor
from sim.tests.test_runner_sched import probe_world
from sim.tests.test_runner_sched import run_monitor_and_finish

windows = pytest.mark.skipif(sys.platform != "win32", reason="GetProcessTimes so existe no Windows")


# ------------------------------------------------------------------------------------------------------ API real (somente leitura)
@windows
def test_real_identity_of_this_process_is_alive_with_a_stable_creation_time():
    first, second = watch.query_native(os.getpid()), watch.query_native(os.getpid())
    assert first["state"] == watch.ALIVE and isinstance(first["created"], int) and first["created"] == second["created"]
    ident = watch.identity_of(os.getpid())
    assert ident["pid"] == os.getpid() and ident["iso"].startswith("20") and ident["iso"].endswith("Z")


def assert_coherent_query(found: dict) -> None:
    """Coerencia de UM resultado de `query_native` para um processo que pode ou nao ser legivel conforme o privilegio do usuario (PID 4,
    o System). Contrato: NUNCA ABSENT (ABSENT exige prova de inexistencia ou de termino); ALIVE exige data de criacao inteira positiva e
    nenhum erro; UNKNOWN exige criacao None e o motivo de uma falha de CONSULTA (nao o de PID invalido)."""
    state = found["state"]
    assert state in (watch.ALIVE, watch.UNKNOWN), found
    if state == watch.ALIVE:
        assert isinstance(found["created"], int) and not isinstance(found["created"], bool) and found["created"] > 0, found
        assert "error" not in found, found
    else:
        assert found["created"] is None, found
        assert isinstance(found.get("error"), str) and found["error"].startswith(("OpenProcess falhou (", "GetProcessTimes falhou (")), found


@pytest.mark.parametrize("found", [
    {"state": "ALIVE", "created": 133_000_000_000_000_000},                                           # lido com privilegio (administrador)
    {"state": "UNKNOWN", "created": None, "error": "OpenProcess falhou (5)"},                         # acesso negado (usuario comum)
    {"state": "UNKNOWN", "created": None, "error": "GetProcessTimes falhou (6)"},
])
def test_the_coherence_check_accepts_what_each_privilege_level_may_legitimately_observe(found):
    assert_coherent_query(found)


@pytest.mark.parametrize("found", [
    {"state": "ABSENT", "created": None, "error": "OpenProcess: PID inexistente"},                    # um processo que existe nunca e ABSENT
    {"state": "ABSENT", "created": 5, "error": "processo encerrado (handle ainda aberto)"},
    {"state": "ABSENT", "created": None, "error": "OpenProcess falhou (5)"},                         # nem com um motivo de falha de consulta
    {"state": "ALIVE", "created": None},
    {"state": "ALIVE", "created": True},
    {"state": "ALIVE", "created": 0},
    {"state": "ALIVE", "created": 5, "error": "x"},
    {"state": "UNKNOWN", "created": 5, "error": "OpenProcess falhou (5)"},
    {"state": "UNKNOWN", "created": None},
    {"state": "UNKNOWN", "created": None, "error": "pid invalido"},
    {"state": "???", "created": None},
])
def test_the_coherence_check_rejects_every_incoherent_result(found):
    with pytest.raises(AssertionError):
        assert_coherent_query(found)


@windows
def test_real_absent_unreadable_and_invalid_pids_are_never_confused():
    missing = watch.query_native(0x7FFFFFF0)
    assert missing["state"] == watch.ABSENT and missing["created"] is None and "inexistente" in missing["error"]    # ERROR_INVALID_PARAMETER
    # PID 4 (System): legivel so com privilegio suficiente. UNKNOWN (acesso negado, usuario comum) e ALIVE (administrador, runner hospedado)
    # sao ambos validos; o contrato exige apenas a coerencia de cada estado e que NUNCA seja ABSENT.
    first = watch.query_native(4)
    assert_coherent_query(first)
    ident = watch.identity_of(4)                                                      # consulta propria: pode, em tese, ver outro estado
    second = watch.query_native(4)
    assert_coherent_query(second)
    if first["state"] == second["state"]:                                             # so se compara quando o estado nao mudou entre as leituras
        if first["state"] == watch.ALIVE:
            assert ident is not None and ident["pid"] == 4 and ident["created"] == first["created"] == second["created"]
            assert ident["iso"] == watch.filetime_iso(ident["created"])
        else:
            assert ident is None
    for bad in (0, -1, None, True, "12", 1.5):
        assert watch.query_native(bad)["state"] == watch.UNKNOWN
    assert watch.identity_of(0x7FFFFFF0) is None


@windows
def test_real_child_is_alive_then_absent_and_a_target_for_it_reports_the_end():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        target = watch.make_target("helper", child.pid)
        assert watch.evaluate(target) == (watch.ALIVE, None, "")
    finally:
        child.terminate()
        child.wait(timeout=30)
    verdict, how, _ = watch.evaluate(target)
    assert verdict == "ENDED" and how == "absent"
    assert watch.query_native(child.pid)["state"] == watch.ABSENT                    # tempo de saida gravado ou PID liberado


@windows
def test_real_snapshot_and_ancestors_follow_the_parent_chain_with_decreasing_creation_times():
    table = watch.snapshot_native()
    assert table[os.getpid()]["ppid"] == os.getppid() and table[os.getpid()]["name"].lower().startswith("python")
    chain = watch.ancestors(os.getpid())
    assert chain[0]["pid"] == os.getpid() and len(chain) >= 2 and chain[1]["pid"] == os.getppid()
    assert all(older["created"] <= newer["created"] for newer, older in zip(chain, chain[1:]))
    lineage = watch.self_lineage()
    assert lineage["self"]["pid"] == os.getpid() and lineage["parent"]["pid"] == os.getppid()
    assert watch.lineage_unchanged(lineage) is True


# ------------------------------------------------------------------------------------------------------ contrato Win32 (deterministico)
class Win32Function:
    """Chamavel que aceita `restype`/`argtypes` como as funcoes de `ctypes.WinDLL` e delega a um metodo do kernel32 falso."""

    def __init__(self, action) -> None:
        self.action, self.restype, self.argtypes = action, None, None

    def __call__(self, *args):
        return self.action(*args)


class FakeKernel32:
    """`kernel32` falso: nenhum handle, processo ou privilegio reais; registra cada chamada em `calls`."""

    HANDLE = 0x5EED

    def __init__(self, *, open_ok=True, error=0, times_ok=True, created=(0, 0), exited=(0, 0), raise_in_times=None) -> None:
        self.calls, self.last_error = [], error
        self.open_ok, self.times_ok, self.created, self.exited, self.raise_in_times = open_ok, times_ok, created, exited, raise_in_times
        self.OpenProcess = Win32Function(self._open)
        self.GetProcessTimes = Win32Function(self._times)
        self.CloseHandle = Win32Function(self._close)

    def _open(self, access, inherit, pid):
        self.calls.append(("OpenProcess", access, inherit, pid))
        return self.HANDLE if self.open_ok else 0

    def _times(self, handle, created, exited, kernel, user):
        self.calls.append(("GetProcessTimes", handle))
        if self.raise_in_times is not None:
            raise self.raise_in_times
        if not self.times_ok:
            return 0
        for reference, (high, low) in ((created, self.created), (exited, self.exited), (kernel, (0, 0)), (user, (0, 0))):
            reference._obj.dwHighDateTime, reference._obj.dwLowDateTime = high, low         # `byref(FILETIME)` -> a estrutura em `_obj`
        return 1

    def _close(self, handle):
        self.calls.append(("CloseHandle", handle))
        return 1

    def names(self) -> list:
        return [call[0] for call in self.calls]


@pytest.fixture
def win32(monkeypatch):
    """Instala um `kernel32` falso no lugar de `ctypes.WinDLL` e finge a plataforma Windows (as importacoes de `query_native` sao feitas
    na chamada, entao o fake vale ate o teste terminar). Pode ser chamada de novo no mesmo teste para trocar o comportamento."""

    def install(**kwargs):
        fake = FakeKernel32(**kwargs)

        def fake_windll(name, use_last_error=False):
            assert name == "kernel32" and use_last_error is True, (name, use_last_error)
            return fake

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: fake.last_error, raising=False)
        return fake

    return install


def forbid_the_os(monkeypatch, platform: str) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("kernel32 nao pode ser carregado neste caminho")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)


HIGH, LOW = 0x01DC1234, 0x89ABCDEF
MADE = (HIGH << 32) | LOW


@pytest.mark.parametrize("error,state,fragment", [
    (5, watch.UNKNOWN, "OpenProcess falhou (5)"),                  # ERROR_ACCESS_DENIED: indeterminado, nunca "ausente"
    (87, watch.ABSENT, "PID inexistente"),                         # ERROR_INVALID_PARAMETER: a unica prova de inexistencia
    (2, watch.UNKNOWN, "OpenProcess falhou (2)"),                  # ERROR_FILE_NOT_FOUND
    (1314, watch.UNKNOWN, "OpenProcess falhou (1314)"),            # ERROR_PRIVILEGE_NOT_HELD
    (998, watch.UNKNOWN, "OpenProcess falhou (998)"),
    (0, watch.UNKNOWN, "OpenProcess falhou (0)"),                  # falha sem codigo: tambem indeterminada
])
def test_open_process_errors_are_translated_and_no_handle_is_ever_closed(win32, error, state, fragment):
    fake = win32(open_ok=False, error=error)
    found = watch.query_native(1234)
    assert found["state"] == state and found["created"] is None and fragment in found["error"]
    assert fake.calls == [("OpenProcess", 0x1000, False, 1234)]            # QUERY_LIMITED_INFORMATION, sem herdar; nada para fechar


def test_a_failing_get_process_times_is_unknown_and_the_handle_is_closed_exactly_once(win32):
    fake = win32(times_ok=False, error=6)
    found = watch.query_native(77)
    assert found == {"state": watch.UNKNOWN, "created": None, "error": "GetProcessTimes falhou (6)"}
    assert fake.calls == [("OpenProcess", 0x1000, False, 77), ("GetProcessTimes", fake.HANDLE), ("CloseHandle", fake.HANDLE)]


@pytest.mark.parametrize("exited", [(0, 1), (1, 0), (HIGH, LOW)])
def test_a_recorded_exit_time_means_absent_with_the_creation_time_and_one_close(win32, exited):
    fake = win32(created=(HIGH, LOW), exited=exited)
    found = watch.query_native(77)
    assert found == {"state": watch.ABSENT, "created": MADE, "error": "processo encerrado (handle ainda aberto)"}
    assert fake.names() == ["OpenProcess", "GetProcessTimes", "CloseHandle"] and fake.calls[-1] == ("CloseHandle", fake.HANDLE)


@pytest.mark.parametrize("high,low", [(0, 1), (1, 0), (0xFFFFFFFF, 0xFFFFFFFF), (HIGH, LOW)])
def test_a_live_process_is_alive_with_the_exact_filetime_and_one_close(win32, high, low):
    fake = win32(created=(high, low))
    found = watch.query_native(77)
    assert found == {"state": watch.ALIVE, "created": (high << 32) | low}                    # as duas palavras de 32 bits, na ordem certa
    assert fake.names() == ["OpenProcess", "GetProcessTimes", "CloseHandle"] and fake.calls[-1] == ("CloseHandle", fake.HANDLE)


def test_the_handle_is_closed_even_if_reading_the_times_raises(win32):
    fake = win32(raise_in_times=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        watch.query_native(77)
    assert fake.names() == ["OpenProcess", "GetProcessTimes", "CloseHandle"]


@pytest.mark.parametrize("bad", [0, -1, -5, None, True, False, "12", 1.5, 4.0])
def test_invalid_pids_are_unknown_without_touching_the_os(monkeypatch, bad):
    forbid_the_os(monkeypatch, "win32")
    assert watch.query_native(bad) == {"state": watch.UNKNOWN, "created": None, "error": "pid invalido"}


def test_other_platforms_are_unknown_without_touching_the_os(monkeypatch):
    forbid_the_os(monkeypatch, "linux")
    assert watch.query_native(1234) == {"state": watch.UNKNOWN, "created": None, "error": "plataforma sem GetProcessTimes"}


def test_identity_and_evaluation_follow_the_translated_states(win32):
    win32(created=(HIGH, LOW))
    assert watch.identity_of(77) == {"pid": 77, "created": MADE, "iso": watch.filetime_iso(MADE)}
    target = watch.make_target("helper", 77)
    assert watch.evaluate(target) == (watch.ALIVE, None, "")
    win32(open_ok=False, error=5)
    assert watch.identity_of(77) is None
    verdict, how, detail = watch.evaluate(target)
    assert (verdict, how) == (watch.UNKNOWN, None) and "OpenProcess falhou (5)" in detail          # acesso negado nunca e termino
    win32(open_ok=False, error=87)
    assert watch.evaluate(target)[:2] == ("ENDED", "absent")


# ------------------------------------------------------------------------------------------------------ evaluate / make_target
def test_a_target_is_alive_ended_by_absence_or_by_pid_reuse_and_unknown_is_never_an_end():
    fake = FakeOS().add(10, created=100)
    target = watch.make_target("initiator", 10, fake.query)
    assert watch.evaluate(target, fake.query) == (watch.ALIVE, None, "")
    fake.deny(10)
    verdict, how, detail = watch.evaluate(target, fake.query)
    assert verdict == watch.UNKNOWN and how is None and "negado" in detail                 # UNKNOWN nunca vira termino
    fake.denied.clear()
    fake.reuse(10, created=555)
    verdict, how, _ = watch.evaluate(target, fake.query)
    assert (verdict, how) == ("ENDED", "pid_reused")
    fake.end(10)
    assert watch.evaluate(target, fake.query)[:2] == ("ENDED", "absent")
    assert watch.evaluate(target, lambda pid: {"state": "ALIVE", "created": None})[0] == watch.UNKNOWN       # sem data de criacao
    assert watch.evaluate(target, lambda pid: {"state": "???", "created": 100})[0] == watch.UNKNOWN


def test_make_target_refuses_what_it_cannot_identify_or_unknown_roles():
    fake = FakeOS().add(10, created=100)
    assert watch.make_target("launcher", 10, fake.query, exe="python.exe")["created"] == 100
    for pid in (11, 12):
        with pytest.raises(watch.WatchError, match="identidade"):
            watch.make_target("launcher", pid, fake.query)
    fake.deny(10)
    with pytest.raises(watch.WatchError, match="identidade"):
        watch.make_target("launcher", 10, fake.query)
    with pytest.raises(watch.WatchError, match="papel"):
        watch.make_target("shared_app", 10, FakeOS().add(10).query)


# ------------------------------------------------------------------------------------------------------ ancestrais
def test_ancestors_stop_where_the_chain_cannot_be_trusted():
    fake = FakeOS().add(1, created=10, ppid=0, name="root").add(2, created=20, ppid=1).add(3, created=30, ppid=2).add(4, created=40, ppid=3)
    assert [c["pid"] for c in watch.ancestors(4, query=fake.query, snapshot=fake.snapshot)] == [4, 3, 2, 1]
    assert [c["pid"] for c in watch.ancestors(4, query=fake.query, snapshot=fake.snapshot, max_depth=2)] == [4, 3]
    fake.reuse(2, created=999)                                    # PID do pai reutilizado: o pai agora e MAIS NOVO que o filho
    assert [c["pid"] for c in watch.ancestors(4, query=fake.query, snapshot=fake.snapshot)] == [4, 3]
    fake.reuse(2, created=20)
    fake.end(3)                                                   # pai ausente da tabela
    assert [c["pid"] for c in watch.ancestors(4, query=fake.query, snapshot=fake.snapshot)] == [4]
    fake.add(3, created=30, ppid=2)
    fake.deny(2)                                                  # pai nao identificavel
    assert [c["pid"] for c in watch.ancestors(4, query=fake.query, snapshot=fake.snapshot)] == [4, 3]
    cyc = FakeOS().add(1, created=10, ppid=2).add(2, created=10, ppid=1)
    assert [c["pid"] for c in watch.ancestors(1, query=cyc.query, snapshot=cyc.snapshot)] == [1, 2]       # a prova de ciclos


# ------------------------------------------------------------------------------------------------------ descoberta do iniciador
CLI_CMD = r'"C:\Claude\claude.exe" --output-format stream-json --input-format stream-json --resume=a9c8e6c4-4f7c-4573-8d45-3882b04ca7e3'
APP_CMD = r'"C:\Claude\claude.exe"'


REAL_ANCESTORS = watch.ancestors


def chain_of(fake, pid):
    return REAL_ANCESTORS(pid, query=fake.query, snapshot=fake.snapshot)


def test_the_initiator_is_the_one_cli_claude_and_the_shared_app_is_excluded_and_recorded():
    fake = probe_os()
    cmds = {7777: CLI_CMD, 7000: APP_CMD}
    target, excluded = watch.discover_initiator(chain_of(fake, os.getpid()), cmds.get, fake.query)
    assert target["role"] == "initiator" and target["pid"] == 7777 and target["created"] == 200
    assert target["resume_id"] == "a9c8e6c4-4f7c-4573-8d45-3882b04ca7e3" and target["cmdline_sha256"] == hashlib.sha256(CLI_CMD.encode()).hexdigest()
    assert [e["pid"] for e in excluded] == [7000] and excluded[0]["role"] == "shared_app"


@pytest.mark.parametrize("cmds,why", [
    ({7777: APP_CMD, 7000: APP_CMD}, "0 CLI"),                                    # so o app: nenhuma sessao identificavel
    ({7777: CLI_CMD, 7000: CLI_CMD}, "2 CLI"),                                    # dois CLIs: ambiguo
    ({7777: None, 7000: APP_CMD}, "ilegivel"),
    ({7777: "", 7000: APP_CMD}, "ilegivel"),
    ({7777: r'"claude.exe" --output-format json', 7000: APP_CMD}, "0 CLI"),       # falta stream-json
    ({7777: r'"claude.exe" stream-json', 7000: APP_CMD}, "0 CLI"),                # falta --output-format
])
def test_ambiguous_or_missing_initiators_block_everything(cmds, why):
    fake = probe_os()
    with pytest.raises(watch.WatchError, match="ambigua|ilegivel"):
        watch.discover_initiator(chain_of(fake, os.getpid()), cmds.get, fake.query)


def test_no_claude_ancestor_at_all_and_a_dead_or_unreadable_cli_identity_fail_closed():
    fake = FakeOS().add(50, created=10, ppid=1, name="explorer.exe").add(60, created=20, ppid=50, name="python.exe")
    with pytest.raises(watch.WatchError, match="0 CLI"):
        watch.discover_initiator(chain_of(fake, 60), lambda pid: CLI_CMD, fake.query)
    fake2 = probe_os()
    fake2.deny(7777)                                             # a identidade do CLI nao pode ser lida => nada e armado
    with pytest.raises(watch.WatchError):
        watch.discover_initiator(chain_of(fake2, os.getpid()), {7777: CLI_CMD, 7000: APP_CMD}.get, fake2.query)


def test_the_name_match_ignores_case_and_a_cli_without_resume_has_no_resume_id():
    fake = probe_os()
    fake.procs[7777]["name"] = "Claude.EXE"
    target, _ = watch.discover_initiator(chain_of(fake, os.getpid()), {7777: r'"c" --output-format stream-json', 7000: APP_CMD}.get, fake.query)
    assert target["pid"] == 7777 and target["resume_id"] is None


# ------------------------------------------------------------------------------------------------------ watch.json
def test_build_watch_is_deterministic_and_validates_roles_targets_and_the_launcher():
    fake = probe_os()
    launcher = watch.make_target("launcher", os.getpid(), fake.query)
    initiator = watch.make_target("initiator", 7777, fake.query)
    data, sha = watch.build_watch([launcher, initiator], [{"role": "shared_app", "pid": 7000}], armed_t=5)
    assert (data, sha) == watch.build_watch([launcher, initiator], [{"role": "shared_app", "pid": 7000}], armed_t=5)
    assert sha == hashlib.sha256(data).hexdigest() and json.loads(data)["schema"] == 2
    for targets in ([initiator], [launcher, launcher], [{**launcher, "role": "boss"}], [{**launcher, "pid": "x"}], [{**launcher, "created": None}], []):
        with pytest.raises(watch.WatchError):
            watch.build_watch(targets, [], armed_t=5)


# ------------------------------------------------------------------------------------------------------ vigilia duravel
class Clock:
    def __init__(self, t=1000):
        self.t = t

    def __call__(self):
        return self.t


def make_watcher(tmp_path, fake, targets, clock, **kwargs):
    data, _ = watch.build_watch(targets, [], armed_t=int(clock()))
    (tmp_path / watch.WATCH_JSON).write_bytes(data)
    return watch.Watcher.open(tmp_path, query=fake.query, clock=clock, **kwargs)


def records(tmp_path):
    return [json.loads(line) for line in (tmp_path / watch.WATCH_LOG).read_text().splitlines()]


def test_the_watcher_records_the_interval_between_the_last_alive_and_the_first_ended_observation(tmp_path):
    fake, clock = probe_os(), Clock()
    targets = [watch.make_target("launcher", os.getpid(), fake.query), watch.make_target("initiator", 7777, fake.query)]
    watcher = make_watcher(tmp_path, fake, targets, clock)
    for _ in range(3):
        watcher.step()
        clock.t += 5
    fake.end(7777)
    clock.t += 3                                                  # o fim ocorreu em algum ponto de (1010, 1018]; so se sabe o intervalo
    watcher.step()
    ended = [r for r in records(tmp_path) if r["event"] == "ENDED"]
    assert len(ended) == 1
    assert ended[0]["role"] == "initiator" and ended[0]["last_seen_alive_t"] == 1010 and ended[0]["first_seen_ended_t"] == 1018
    assert ended[0]["how"] == "absent" and ended[0]["period_s"] == 5 and ended[0]["created"] == 200 and ended[0]["unknown_observations"] == 0
    clock.t += 5
    watcher.step()
    assert len([r for r in records(tmp_path) if r["event"] == "ENDED"]) == 1          # nao repete depois de encerrado
    assert records(tmp_path)[0]["event"] == "OBSERVING"


def test_unknown_observations_never_end_a_target_and_are_counted_in_the_interval(tmp_path):
    fake, clock = probe_os(), Clock()
    watcher = make_watcher(tmp_path, fake, [watch.make_target("launcher", os.getpid(), fake.query), watch.make_target("initiator", 7777, fake.query)], clock)
    watcher.step()
    fake.deny(7777)
    for _ in range(3):
        clock.t += 5
        watcher.step()
    assert not [r for r in records(tmp_path) if r["event"] == "ENDED"]
    assert len([r for r in records(tmp_path) if r["event"] == "UNKNOWN"]) == 1        # so a transicao e gravada
    fake.denied.clear()
    clock.t += 5
    watcher.step()
    fake.end(7777)
    clock.t += 5
    watcher.step()
    ended = [r for r in records(tmp_path) if r["event"] == "ENDED"][0]
    assert ended["unknown_observations"] == 3 and ended["last_seen_alive_t"] == 1020 and ended["first_seen_ended_t"] == 1025


def test_pid_reuse_is_recorded_as_the_end_of_the_original_process(tmp_path):
    fake, clock = probe_os(), Clock()
    watcher = make_watcher(tmp_path, fake, [watch.make_target("launcher", os.getpid(), fake.query)], clock)
    watcher.step()
    fake.reuse(os.getpid(), created=77_777)
    clock.t += 5
    watcher.step()
    assert [r["how"] for r in records(tmp_path) if r["event"] == "ENDED"] == ["pid_reused"]


def test_a_target_already_gone_at_the_first_observation_has_no_last_alive_time(tmp_path):
    fake, clock = probe_os(), Clock()
    targets = [watch.make_target("launcher", os.getpid(), fake.query), watch.make_target("initiator", 7777, fake.query)]
    fake.end(7777)
    watcher = make_watcher(tmp_path, fake, targets, clock)
    watcher.step()
    ended = [r for r in records(tmp_path) if r["event"] == "ENDED"][0]
    assert ended["last_seen_alive_t"] is None and ended["first_seen_ended_t"] == 1000
    summary = watch.summarize(tmp_path)
    assert summary["integrity_ok"] is True and summary["ended"]["initiator"]["last_seen_alive_t"] is None


def test_observed_records_are_periodic_and_every_record_is_fsynced(tmp_path, monkeypatch):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
    fake, clock = probe_os(), Clock()
    watcher = make_watcher(tmp_path, fake, [watch.make_target("launcher", os.getpid(), fake.query)], clock, observed_every_s=60)
    for _ in range(30):
        watcher.step()
        clock.t += 5
    events = [r["event"] for r in records(tmp_path)]
    assert events[0] == "OBSERVING" and events.count("OBSERVED") == 2 and len(synced) == len(events)
    assert [r["t"] for r in records(tmp_path) if r["event"] == "OBSERVED"] == [1060, 1120]               # exatamente a cada 60 s


def test_a_failing_write_is_counted_and_never_stops_the_probe(tmp_path):
    fake, clock = probe_os(), Clock()
    boom = lambda path, doc: (_ for _ in ()).throw(OSError("disco cheio"))                                # noqa: E731
    watcher = make_watcher(tmp_path, fake, [watch.make_target("launcher", os.getpid(), fake.query)], clock, write=boom)
    watcher.step()
    fake.end(os.getpid())
    clock.t += 5
    watcher.step()
    assert watcher.write_errors == 2


def test_open_returns_nothing_without_a_valid_watch_json(tmp_path):
    assert watch.Watcher.open(tmp_path) is None
    (tmp_path / watch.WATCH_JSON).write_text("{", encoding="utf-8")
    assert watch.Watcher.open(tmp_path) is None
    (tmp_path / watch.WATCH_JSON).write_text(json.dumps({"schema": 1, "targets": [{"role": "launcher", "pid": 1, "created": 1}]}), encoding="utf-8")
    assert watch.Watcher.open(tmp_path) is None
    (tmp_path / watch.WATCH_JSON).write_text(json.dumps({"schema": 2, "targets": []}), encoding="utf-8")
    assert watch.Watcher.open(tmp_path) is None


# ------------------------------------------------------------------------------------------------------ resumo e integridade
def seeded(tmp_path, *, ended_extra=None):
    fake, clock = probe_os(), Clock()
    targets = [watch.make_target("launcher", os.getpid(), fake.query), watch.make_target("initiator", 7777, fake.query)]
    watcher = make_watcher(tmp_path, fake, targets, clock)
    watcher.step()
    fake.end(7777)
    clock.t += 5
    watcher.step()
    return tmp_path


def test_the_summary_reports_the_interval_and_trusts_only_consistent_records(tmp_path):
    summary = watch.summarize(seeded(tmp_path))
    assert summary["armed"] and summary["integrity_ok"] and summary["observing"] and summary["period_s"] == 5
    assert summary["ended"]["initiator"] == {"pid": 7777, "created": 200, "last_seen_alive_t": 1000, "first_seen_ended_t": 1005,
                                             "how": "absent", "unknown_observations": 0, "period_s": 5}
    assert "launcher" not in summary["ended"]


@pytest.mark.parametrize("mutate", [
    lambda lines: lines + ["{nao-json"],                                                           # linha corrompida
    lambda lines: [l.replace('"created":200', '"created":201') for l in lines],                  # criacao diferente da declarada
    lambda lines: [l.replace('"pid":7777', '"pid":7778') for l in lines],                        # alvo nao declarado
    lambda lines: [l.replace('"how":"absent"', '"how":"vanished"') for l in lines],
    lambda lines: lines[1:],                                                                       # sem OBSERVING
])
def test_tampered_or_inconsistent_watch_logs_lose_integrity(tmp_path, mutate):
    seeded(tmp_path)
    lines = (tmp_path / watch.WATCH_LOG).read_text().splitlines()
    (tmp_path / watch.WATCH_LOG).write_text("\n".join(mutate(lines)) + "\n", encoding="utf-8")
    assert watch.summarize(tmp_path)["integrity_ok"] is False


def test_the_summary_without_files_is_not_armed_or_not_sound(tmp_path):
    assert watch.summarize(tmp_path) == {"armed": False, "integrity_ok": False, "records": 0, "corrupt_lines": 0, "observing": False,
                                         "period_s": None, "targets": {}, "ended": {}}
    seeded(tmp_path)
    (tmp_path / watch.WATCH_LOG).unlink()
    assert watch.summarize(tmp_path)["armed"] is True and watch.summarize(tmp_path)["integrity_ok"] is False
    assert watch.watch_sha256(tmp_path / "nada") is None


# ------------------------------------------------------------------------------------------------------ linhagem e solicitante
def test_lineage_is_unchanged_only_when_every_level_is_alive_with_the_recorded_creation():
    fake = probe_os()
    lineage = watch.self_lineage(fake.query, fake.snapshot, pid=901)
    assert [lineage[k]["pid"] for k in ("self", "parent", "grandparent")] == [901, 902, 900]
    assert watch.lineage_unchanged(lineage, fake.query) is True
    fake.reuse(902, created=12_345)
    assert watch.lineage_unchanged(lineage, fake.query) is False
    fake.reuse(902, created=510)
    fake.end(900)
    assert watch.lineage_unchanged(lineage, fake.query) is False
    fake.add(900, created=500, ppid=555, name="cmd.exe")
    fake.deny(901)
    assert watch.lineage_unchanged(lineage, fake.query) is None                    # indeterminado nunca e "inalterado"
    fake.denied.clear()
    fake.end(901)
    fake.deny(902)
    assert watch.lineage_unchanged(lineage, fake.query) is False                   # um nivel terminou, outro indeterminado: falso vence
    for nothing in (None, {}, {"self": None, "parent": None, "grandparent": None}):
        assert watch.lineage_unchanged(nothing, fake.query) is None
    healthy = probe_os()                                             # SO sao: sem o nivel "self" a linhagem nunca e provada, mesmo com pais vivos
    assert watch.lineage_unchanged({"parent": lineage["parent"], "grandparent": lineage["grandparent"]}, healthy.query) is None
    assert watch.lineage_unchanged(lineage, healthy.query) is True


def test_self_lineage_with_a_short_or_unreadable_chain_fills_the_missing_levels_with_none():
    fake = FakeOS().add(5, created=10, ppid=1, name="solo.exe")
    lineage = watch.self_lineage(fake.query, fake.snapshot, pid=5)
    assert lineage["self"]["pid"] == 5 and lineage["parent"] is None and lineage["grandparent"] is None
    broken = lambda: (_ for _ in ()).throw(watch.WatchError("sem tabela"))                           # noqa: E731
    assert watch.self_lineage(fake.query, broken, pid=5) == {"self": None, "parent": None, "grandparent": None}


def test_outside_means_no_link_of_the_chain_is_the_target_by_pid_and_creation():
    chain = [{"pid": 1, "created": 10}, {"pid": 7777, "created": 200}]
    assert watch.outside(chain, {"pid": 7777, "created": 200}) is False
    assert watch.outside(chain, {"pid": 7777, "created": 201}) is True                              # mesmo PID, outra criacao: outro processo
    assert watch.outside(chain, {"pid": 7778, "created": 200}) is True and watch.outside([], {"pid": 1, "created": 1}) is True


# ------------------------------------------------------------------------------------------------------ versao da evidencia
def v2_world(tmp_path, level="L1"):
    host, name, doc, proc = probe_world(tmp_path, level)
    return host, name, doc, proc


def test_new_evidence_is_v2_only_when_launch_watch_and_audit_agree(tmp_path):
    host, name, doc, proc = v2_world(tmp_path)
    assert doc["evidence_schema"] == 2 and detach_probe.evidence_version(proc) == "v2"
    original = (proc / watch.WATCH_JSON).read_bytes()
    (proc / watch.WATCH_JSON).write_bytes(original.replace(b'"schema":2', b'"schema":2 '))
    assert detach_probe.evidence_version(proc) is None                                               # watch.json adulterado
    (proc / watch.WATCH_JSON).write_bytes(original)
    assert detach_probe.evidence_version(proc) == "v2"
    (proc / watch.WATCH_JSON).unlink()
    assert detach_probe.evidence_version(proc) is None
    (proc / watch.WATCH_JSON).write_bytes(original)
    launch = json.loads((proc / detach.LAUNCH).read_text())
    for edit in (lambda d: d.update(evidence_schema=3), lambda d: d.update(watch_sha256=None), lambda d: d.update(watch_sha256="0" * 64)):
        broken = json.loads(json.dumps(launch))
        edit(broken)
        (proc / detach.LAUNCH).write_text(json.dumps(broken))
        assert detach_probe.evidence_version(proc) is None
    (proc / detach.LAUNCH).write_text(json.dumps(launch))
    assert detach_probe.evidence_version(proc) == "v2"


def test_removing_the_new_evidence_marker_does_not_select_the_legacy_path(tmp_path):
    host, name, doc, proc = v2_world(tmp_path)
    launch = json.loads((proc / detach.LAUNCH).read_text())
    launch.pop("evidence_schema")
    launch.pop("watch_sha256")
    (proc / detach.LAUNCH).write_text(json.dumps(launch))
    assert detach_probe.evidence_version(proc) is None                                               # run novo: fora da lista historica


def test_the_audit_must_carry_exactly_one_watch_armed_with_the_same_hash(tmp_path):
    host, name, doc, proc = v2_world(tmp_path)
    audit = sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, name)
    assert [r["event"] for r in audit.records()].count("WATCH_ARMED") == 1
    audit.append("WATCH_ARMED", {"sha256": doc["watch_sha256"]})                                      # segundo WATCH_ARMED
    assert detach_probe.evidence_version(proc) is None
    host2, name2, doc2, proc2 = v2_world(tmp_path / "b")
    lines = [l for l in (proc2 / sched.AUDIT_NAME).read_text().splitlines() if '"WATCH_ARMED"' not in l]
    (proc2 / sched.AUDIT_NAME).write_text("\n".join(lines) + "\n")
    assert detach_probe.evidence_version(proc2) is None                                               # sem WATCH_ARMED
    host3, name3, doc3, proc3 = v2_world(tmp_path / "c")
    audit3 = sched.Audit(proc3, tmp_path / "c" / sched.ANCHOR_DIR, name3)
    audit3.path.write_bytes(audit3.path.read_bytes().replace(doc3["watch_sha256"].encode(), b"0" * 64))
    assert detach_probe.evidence_version(proc3) is None                                               # hash da trilha diferente


def test_legacy_evidence_is_recognised_only_by_run_id_plus_the_exact_launch_json_bytes(tmp_path, monkeypatch):
    proc = tmp_path / "DETACH2-PROBE-L1-20250101T000000Z.proc"
    proc.mkdir()
    raw = json.dumps({"run_id": "DETACH2-PROBE-L1-20250101T000000Z", "task": "\\AuneronSim\\X"}).encode()
    (proc / detach.LAUNCH).write_bytes(raw)
    assert detach_probe.evidence_version(proc) is None                                               # fora da lista
    monkeypatch.setitem(detach_probe.LEGACY_EVIDENCE, "DETACH2-PROBE-L1-20250101T000000Z", hashlib.sha256(raw).hexdigest())
    assert detach_probe.evidence_version(proc) == "legacy-1"
    (proc / detach.LAUNCH).write_bytes(raw + b" ")
    assert detach_probe.evidence_version(proc) is None                                               # um byte a mais
    (proc / detach.LAUNCH).write_bytes(raw.replace(b"DETACH2-PROBE-L1-20250101T000000Z", b"DETACH2-PROBE-L1-20250101T000001Z"))
    assert detach_probe.evidence_version(proc) is None
    (proc / detach.LAUNCH).write_text("[]")
    assert detach_probe.evidence_version(proc) is None
    (proc / detach.LAUNCH).unlink()
    assert detach_probe.evidence_version(proc) is None


def test_the_historic_evidence_registry_lists_exactly_the_two_verified_l1_attempts():
    assert sorted(detach_probe.LEGACY_EVIDENCE) == ["DETACH2-PROBE-L1-20261008T191935Z", "DETACH2-PROBE-L1-20261008T204909Z"]
    assert all(len(v) == 64 and set(v) <= set("0123456789abcdef") for v in detach_probe.LEGACY_EVIDENCE.values())


# ------------------------------------------------------------------------------------------------------ lancamento: armar antes de registrar
def test_launch_arms_the_watch_before_registering_and_the_audit_orders_the_events(tmp_path):
    host, name, doc, proc = v2_world(tmp_path, "L2")
    events = [r["event"] for r in sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, name).records()]
    assert events == ["REGISTERED", "EXPORT_VERIFIED", "WATCH_ARMED", "STARTED", "HEARTBEAT_SEEN"]
    armed = watch.read_watch(proc)
    assert [t["role"] for t in armed["targets"]] == ["launcher", "initiator"] and armed["excluded"] == [{"role": "shared_app", "pid": 7000}]
    assert armed["targets"][0]["pid"] == os.getpid() and doc["watch_sha256"] == watch.watch_sha256(proc)
    assert host.labels().index("register") < host.labels().index("start")


def test_l2_without_the_initiator_registers_nothing(tmp_path):
    host = __import__("sim.tests.sched_support", fromlist=["FakeHost"]).FakeHost(tmp_path)
    fake = probe_os()
    with pytest.raises(detach.DetachError, match="L2 exige"):
        detach_probe.probe_launch(tmp_path, "DETACH2-PROBE-L2-20261008T120000Z", level="L2", python="C:\\v\\python.exe", repo="C:\\repo",
                                  interactive_session=1, run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000, query=fake.query)
    assert host.calls == [] and not host.tasks


def test_an_unidentifiable_launcher_or_helper_registers_nothing(tmp_path):
    from sim.tests.sched_support import FakeHost

    host, fake = FakeHost(tmp_path), probe_os()
    fake.deny(os.getpid())
    with pytest.raises(watch.WatchError):
        detach_probe.probe_launch(tmp_path, "DETACH2-PROBE-L1-20261008T120000Z", level="L1", python="C:\\v\\python.exe", repo="C:\\repo",
                                  interactive_session=1, run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000, query=fake.query)
    fake.denied.clear()
    with pytest.raises(watch.WatchError):
        detach_probe.probe_launch(tmp_path, "DETACH2-PROBE-L1-20261008T120000Z", level="L1", python="C:\\v\\python.exe", repo="C:\\repo",
                                  interactive_session=1, run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000, query=fake.query,
                                  helpers=[424242])
    assert host.calls == []


def test_helpers_are_watched_but_never_judged(tmp_path):
    from sim.tests.sched_support import FakeHost

    host, fake = FakeHost(tmp_path), probe_os()
    fake.add(31337, created=900, name="helper.exe")
    detach_probe.probe_launch(tmp_path, "DETACH2-PROBE-L1-20261008T120000Z", level="L1", python="C:\\v\\python.exe", repo="C:\\repo",
                              interactive_session=1, run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000, query=fake.query,
                              helpers=[31337])
    proc = detach.proc_dir(tmp_path, "DETACH2-PROBE-L1-20261008T120000Z")
    assert [(t["role"], t["pid"]) for t in watch.read_watch(proc)["targets"]] == [("launcher", os.getpid()), ("helper", 31337)]


# ------------------------------------------------------------------------------------------------------ CLI do lancamento
def test_the_cli_discovers_the_initiator_for_l2_only_and_stops_on_ambiguity(tmp_path, monkeypatch, capsys):
    seen = {}
    fake = probe_os()
    monkeypatch.setattr(detach_probe, "probe_launch", lambda root, name, **kw: seen.update(kw, name=name) or {
        "task": "\\AuneronSim\\" + name, "heartbeat_pid": 1, "engine_pids": [2]})
    monkeypatch.setattr(watch, "ancestors", lambda pid, **kw: chain_of(fake, os.getpid()))
    monkeypatch.setattr(sched, "process_chain", lambda run, pid: [{"pid": pid, "cmd": {7777: CLI_CMD, 7000: APP_CMD}.get(pid)}])
    monkeypatch.setattr(watch, "make_target", lambda role, pid, query=None, **extra: {"role": role, "pid": pid, "created": fake.procs[pid]["created"],
                                                                                  "iso": "x", **extra})
    args = ["sched-probe", "--runs-root", str(tmp_path), "--phase", "launch", "--python", "C:\\v\\python.exe", "--repo", "C:\\repo", "--name",
            "DETACH2-PROBE-L2-20261008T120000Z"]
    assert cli.main([*args, "--level", "L2"]) == 0
    assert seen["initiator"]["pid"] == 7777 and seen["excluded"][0]["pid"] == 7000 and seen["helpers"] == ()
    out = json.loads(capsys.readouterr().out)
    assert out["initiator"]["pid"] == 7777 and out["excluded"][0]["role"] == "shared_app"
    seen.clear()
    assert cli.main([*args, "--level", "L1", "--watch-pid", "31337"]) == 0
    assert seen["initiator"] is None and seen["excluded"] == [] and seen["helpers"] == [31337]
    capsys.readouterr()
    monkeypatch.setattr(sched, "process_chain", lambda run, pid: [{"pid": pid, "cmd": CLI_CMD}])           # dois CLIs: ambiguo
    seen.clear()
    assert cli.main([*args, "--level", "L2"]) == 3 and "ambigua" in capsys.readouterr().out and not seen


# ------------------------------------------------------------------------------------------------------ replay
def finished_world(tmp_path, level="L1"):
    host, name, doc, proc = probe_world(tmp_path, level)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, level)
    return host, name, proc, monitor, final


OBSERVED = {"orphan_processes": 0, "folder_clean": True}


def test_replay_of_new_evidence_gives_the_same_verdict_and_never_invents_unmeasured_items(tmp_path):
    host, name, proc, monitor, final = finished_world(tmp_path)
    assert final["verdict"]["verdict"] == "PASS"
    replay = detach_probe.judge_evidence_dir(proc, "L1", OBSERVED)
    assert replay["verdict"] == "PASS" and replay["passed"] == final["verdict"]["passed"]
    unmeasured = detach_probe.judge_evidence_dir(proc, "L1")                                           # orfaos/pasta nao medidos: D2-11 reprova
    assert unmeasured["failed"] == ["D2-11_removed_clean_evidence"]


def test_replay_of_tampered_new_evidence_fails_instead_of_falling_back(tmp_path):
    host, name, proc, monitor, final = finished_world(tmp_path)
    tampered = [dict(json.loads(l), how="x") if '"ENDED"' in l else json.loads(l) for l in (proc / watch.WATCH_LOG).read_text().splitlines()]
    (proc / watch.WATCH_LOG).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in tampered))
    result = detach_probe.judge_evidence_dir(proc, "L1", OBSERVED)
    assert result["verdict"] == "FAIL" and "D2-08_survives_launcher" in result["failed"]
    with pytest.raises(detach.DetachError, match="monitor ausente"):
        detach_probe.judge_evidence_dir(tmp_path / "nada.proc", "L1")


def test_replay_of_verified_historic_evidence_uses_the_legacy_path_and_still_enforces_the_thresholds(tmp_path, monkeypatch):
    host, name, proc, monitor, final = finished_world(tmp_path)
    for extra in ("watch.json", "watch.jsonl"):
        (proc / extra).unlink()
    launch = json.loads((proc / detach.LAUNCH).read_text())
    launch.pop("evidence_schema")
    launch.pop("watch_sha256")
    (proc / detach.LAUNCH).write_text(json.dumps(launch, sort_keys=True))
    lines = [l for l in (proc / sched.AUDIT_NAME).read_text().splitlines() if '"WATCH_ARMED"' not in l]
    (proc / sched.AUDIT_NAME).write_text("\n".join(lines) + "\n")                                      # trilha de uma versao anterior (sem WATCH_ARMED)
    stored = json.loads((proc / detach_probe.MONITOR_JSON).read_text())
    legacy = legacy_monitor("L1")
    stored.update({k: legacy[k] for k in ("beats", "session_end")})
    for key in ("termination", "watch", "launcher_watch", "audit_write_failures", "evidence_version"):
        stored.pop(key, None)
    (proc / detach_probe.MONITOR_JSON).write_text(json.dumps(stored))
    unlisted = detach_probe.judge_evidence_dir(proc, "L1", OBSERVED)
    assert {"D2-08_survives_launcher", "D2-09_stop_exit_clean"} <= set(unlisted["failed"])            # historica NAO listada: nao julgavel
    monkeypatch.setitem(detach_probe.LEGACY_EVIDENCE, launch["run_id"], hashlib.sha256((proc / detach.LAUNCH).read_bytes()).hexdigest())
    # a trilha precisa continuar integra: o AUDIT_TAMPERED derruba D2-11, nao o caminho legado
    ok = detach_probe.judge_evidence_dir(proc, "L1", {**OBSERVED})
    assert "D2-08_survives_launcher" not in ok["failed"] and "D2-09_stop_exit_clean" not in ok["failed"]
    stored["beats"]["survived_after_s"] = 839
    (proc / detach_probe.MONITOR_JSON).write_text(json.dumps(stored))
    assert "D2-08_survives_launcher" in detach_probe.judge_evidence_dir(proc, "L1", OBSERVED)["failed"]   # limiar preservado
    assert good_facts()["watch_armed"] is True
