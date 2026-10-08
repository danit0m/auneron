"""
Processo do host: trava do runner (`RUN.LOCK`), maquina acordada (SetThreadExecutionState no Windows) e parada pelo
operador. Tudo injetavel: os testes nao tocam no sistema operacional.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from sim.runner.errors import LockError

LOCK_NAME = "RUN.LOCK"
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32                                          # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))                      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


class RunLock:
    """Trava exclusiva do runner. Lock orfao (processo morto) NAO e reassumido: o run e selado como UNCLEAN_EXIT."""

    def __init__(self, run_dir, *, pid=os.getpid, alive=pid_alive) -> None:
        self.path = Path(run_dir) / LOCK_NAME
        self.pid = pid
        self.alive = alive
        self.held = False

    def orphan(self) -> bool:
        if not self.path.exists():
            return False
        try:
            other = json.loads(self.path.read_text(encoding="utf-8"))["pid"]
        except (ValueError, KeyError, OSError):
            return True
        return not self.alive(other)

    def acquire(self) -> None:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            kind = "orfao (processo anterior morreu)" if self.orphan() else "de outro runner vivo"
            raise LockError(f"RUN.LOCK {kind}") from error
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": self.pid()}))
        self.held = True

    def release(self) -> None:
        if self.held:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self.held = False


class HeartbeatWriter:
    """Escreve `heartbeat.json` (atomico) a cada `interval_s`. O monitor le a idade para detectar saida inesperada/travamento."""

    def __init__(self, path, status, *, interval_s: int = 30, clock=time.time, pid=os.getpid) -> None:
        self.path = Path(path)
        self.status = status
        self.interval_s = interval_s
        self.clock = clock
        self.pid = pid
        self._stop = threading.Event()
        self._thread = None

    def beat(self) -> dict:
        now = int(self.clock())
        doc = {"pid": self.pid(), "t_real": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "t_epoch": now,
               "status": dict(self.status())}
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
        return doc

    def start(self) -> None:
        self.beat()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.beat()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.beat()


def keep_awake(set_state=None) -> bool:
    """Pede ao Windows para nao suspender enquanto este thread viver. Falha/nao-Windows => False (o preflight reprova)."""
    if set_state is None:
        if sys.platform != "win32":
            return False
        import ctypes

        set_state = ctypes.windll.kernel32.SetThreadExecutionState                  # type: ignore[attr-defined]
    try:
        return bool(set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    except Exception:                                                             # noqa: BLE001
        return False


def release_awake(set_state=None) -> None:
    if set_state is None and sys.platform == "win32":
        import ctypes

        set_state = ctypes.windll.kernel32.SetThreadExecutionState                  # type: ignore[attr-defined]
    if set_state is not None:
        try:
            set_state(ES_CONTINUOUS)
        except Exception:                                                         # noqa: BLE001
            pass


class StopFlag:
    """Parada do operador: so pede; quem decide e o runner, em um ponto seguro (fronteira de hook).
    Duas origens: o sinal do processo (`request`) e o ARQUIVO `STOP` da pasta de processo (parada controlada de fora)."""

    def __init__(self, path=None) -> None:
        self._requested = False
        self.reason = None
        self.path = Path(path) if path is not None else None

    @property
    def requested(self) -> bool:
        if not self._requested and self.path is not None and self.path.exists():
            try:
                self.reason = "STOP_FILE:" + self.path.read_text(encoding="utf-8", errors="replace")[:120].strip()
            except OSError:
                self.reason = "STOP_FILE"
            self._requested = True
        return self._requested

    def request(self, reason: str = "OPERATOR_STOP") -> None:
        self._requested = True
        self.reason = reason

    def install(self) -> None:
        def handler(signum, frame):                                                # noqa: ARG001
            self.request(f"signal:{signum}")

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), handler)
