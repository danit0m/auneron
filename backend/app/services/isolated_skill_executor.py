import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from app.core.skill_errors import SkillExecutionError
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.skill_errors import SkillOutputLimitError
from app.core.skill_errors import SkillRuntimeBusyError
from app.core.skill_errors import SkillValidationError
from app.services.skill_runtime_context import MAX_WORK_LEARNING_CONTEXT_BYTES
from app.services.skill_runtime_context import WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
from app.services.skill_runtime_context import normalize_work_learning_runtime_context


ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$"
)
MAX_AUTONOMY_WORKERS = 8
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_CONTEXT_WIRE_BYTES = (
    MAX_INPUT_BYTES
    + MAX_WORK_LEARNING_CONTEXT_BYTES
    + 1024
)
_SAFE_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)


class IsolatedSkillExecutor:
    """
    Killable process boundary for trusted autonomous Python handlers.

    This is process isolation for timeout/cancellation, not a security
    sandbox. It intentionally does not forward Auneron application secrets.
    """

    def __init__(
        self,
        *,
        max_workers: int = 2,
        kill_grace_seconds: int = 2,
        python_executable: str | None = None,
        python_path_entries: tuple[str, ...] = (),
    ) -> None:
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > MAX_AUTONOMY_WORKERS
        ):
            raise SkillValidationError(
                "max_workers isolado deve estar entre "
                f"1 e {MAX_AUTONOMY_WORKERS}."
            )
        if (
            isinstance(kill_grace_seconds, bool)
            or not isinstance(kill_grace_seconds, int)
            or kill_grace_seconds < 1
            or kill_grace_seconds > 10
        ):
            raise SkillValidationError(
                "kill_grace_seconds deve estar entre 1 e 10."
            )
        if python_executable is not None and (
            not isinstance(python_executable, str)
            or not python_executable.strip()
        ):
            raise SkillValidationError(
                "python_executable isolado é inválido."
            )
        if not isinstance(python_path_entries, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in python_path_entries
        ):
            raise SkillValidationError(
                "python_path_entries isolado é inválido."
            )

        self.max_workers = max_workers
        self.kill_grace_seconds = kill_grace_seconds
        self.python_executable = (
            python_executable.strip()
            if python_executable is not None
            else sys.executable
        )
        self.python_path_entries = tuple(
            str(Path(item).resolve())
            for item in python_path_entries
        )
        self._semaphore = BoundedSemaphore(max_workers)
        self._backend_dir = Path(__file__).resolve().parents[2]

    def execute(
        self,
        entrypoint: str,
        payload: Any,
        *,
        timeout_seconds: int,
        max_output_bytes: int,
        runtime_context_protocol: str | None = None,
        runtime_context: Any | None = None,
    ) -> Any:
        normalized_entrypoint = self._validate_entrypoint(entrypoint)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds < 1
            or timeout_seconds > 300
        ):
            raise SkillValidationError(
                "timeout_seconds isolado deve estar entre 1 e 300."
            )
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < 1024
            or max_output_bytes > MAX_OUTPUT_BYTES
        ):
            raise SkillValidationError(
                "max_output_bytes isolado é inválido."
            )

        try:
            payload_bytes = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise SkillValidationError(
                "Payload isolado não é JSON válido."
            ) from error

        if len(payload_bytes) > MAX_INPUT_BYTES:
            raise SkillValidationError(
                "Payload isolado excede 65536 bytes."
            )

        if (runtime_context_protocol is None) != (runtime_context is None):
            raise SkillValidationError(
                "runtime context isolado está incompleto."
            )

        input_bytes = payload_bytes
        normalized_context = None
        if runtime_context_protocol is not None:
            if (
                runtime_context_protocol
                != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
            ):
                raise SkillValidationError(
                    "runtime_context_protocol isolado inválido."
                )

            normalized_context = (
                normalize_work_learning_runtime_context(
                    runtime_context
                )
            )
            try:
                input_bytes = json.dumps(
                    {
                        "payload": json.loads(
                            payload_bytes.decode("utf-8")
                        ),
                        "protocol": normalized_context.protocol,
                        "runtime_context": normalized_context.payload,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise SkillValidationError(
                    "Envelope de runtime context isolado inválido."
                ) from error

            if len(input_bytes) > MAX_CONTEXT_WIRE_BYTES:
                raise SkillValidationError(
                    "Envelope de runtime context isolado excede o limite."
                )

        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            raise SkillRuntimeBusyError(
                "Runtime autônomo isolado sem capacidade disponível."
            )

        process: subprocess.Popen[bytes] | None = None
        try:
            process = self._start_process(
                normalized_entrypoint,
                max_output_bytes=max_output_bytes,
                runtime_context_protocol=(
                    normalized_context.protocol
                    if normalized_context is not None
                    else None
                ),
            )
            try:
                stdout, _ = process.communicate(
                    input=input_bytes,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                self._terminate_process_tree(process)
                self._wait_after_kill(process)
                raise SkillExecutionTimeoutError(
                    "Skill autônoma excedeu o timeout e o processo foi encerrado."
                ) from error

            if process.returncode != 0:
                raise SkillExecutionError(
                    "Worker isolado terminou de forma inesperada."
                )

            return self._decode_result(
                stdout,
                max_output_bytes=max_output_bytes,
            )
        finally:
            if process is not None and process.poll() is None:
                self._terminate_process_tree(process)
                self._wait_after_kill(process)
            self._semaphore.release()

    def shutdown(self) -> None:
        """Compatibility hook. No persistent worker processes are retained."""

    @staticmethod
    def _validate_entrypoint(entrypoint: str) -> str:
        if not isinstance(entrypoint, str):
            raise SkillValidationError(
                "autonomy_entrypoint deve ser texto."
            )
        normalized = entrypoint.strip()
        if (
            len(normalized) > 320
            or ENTRYPOINT_PATTERN.fullmatch(normalized) is None
        ):
            raise SkillValidationError(
                "autonomy_entrypoint isolado é inválido."
            )
        return normalized

    def _safe_environment(self) -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in _SAFE_ENV_KEYS
            if key in os.environ
        }
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"

        paths = [str(self._backend_dir), *self.python_path_entries]
        environment["PYTHONPATH"] = os.pathsep.join(paths)
        return environment

    def _start_process(
        self,
        entrypoint: str,
        *,
        max_output_bytes: int,
        runtime_context_protocol: str | None = None,
    ) -> subprocess.Popen[bytes]:
        worker_script = Path(__file__).with_name(
            "skill_process_worker.py"
        )
        command = [
            self.python_executable,
            str(worker_script),
            entrypoint,
            str(max_output_bytes),
        ]
        if runtime_context_protocol is not None:
            command.append(runtime_context_protocol)

        kwargs: dict[str, Any] = {
            "cwd": str(self._backend_dir),
            "env": self._safe_environment(),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
        }

        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True

        try:
            return subprocess.Popen(command, **kwargs)
        except Exception as error:
            raise SkillExecutionError(
                "Runtime autônomo não conseguiu iniciar o worker isolado."
            ) from error

    def _terminate_process_tree(
        self,
        process: subprocess.Popen[bytes],
    ) -> None:
        if process.poll() is not None:
            return

        if os.name == "nt":
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.kill_grace_seconds,
                    check=False,
                )
            except Exception:
                process.kill()
            return

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            if process.poll() is None:
                process.kill()

    def _wait_after_kill(
        self,
        process: subprocess.Popen[bytes],
    ) -> None:
        try:
            process.communicate(timeout=self.kill_grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    @staticmethod
    def _decode_result(
        stdout: bytes,
        *,
        max_output_bytes: int,
    ) -> Any:
        envelope_ceiling = max_output_bytes + 4096
        if len(stdout) > envelope_ceiling:
            raise SkillOutputLimitError(
                "Resposta do worker isolado excedeu o limite."
            )
        try:
            envelope = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SkillExecutionError(
                "Worker isolado retornou protocolo inválido."
            ) from error

        if not isinstance(envelope, dict):
            raise SkillExecutionError(
                "Worker isolado retornou protocolo inválido."
            )
        if envelope.get("ok") is True and "value" in envelope:
            return envelope["value"]

        error_code = envelope.get("error")
        if error_code == "output_too_large":
            raise SkillOutputLimitError(
                "Saída isolada excedeu o limite publicado."
            )
        raise SkillExecutionError(
            "Handler isolado falhou durante a execução."
        )
