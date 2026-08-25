import importlib
import io
import json
import sys
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from typing import Any

from skill_runtime_context import MAX_WORK_LEARNING_CONTEXT_BYTES
from skill_runtime_context import WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
from skill_runtime_context import normalize_work_learning_runtime_context


MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_CONTEXT_WIRE_BYTES = (
    MAX_INPUT_BYTES
    + MAX_WORK_LEARNING_CONTEXT_BYTES
    + 1024
)


def _emit(payload: dict[str, Any]) -> None:
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _error(code: str) -> int:
    _emit({"error": code, "ok": False})
    return 0


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def main() -> int:
    if len(sys.argv) not in {3, 4}:
        return _error("worker_arguments_invalid")

    entrypoint = sys.argv[1]
    try:
        max_output_bytes = int(sys.argv[2])
    except ValueError:
        return _error("worker_arguments_invalid")

    if (
        max_output_bytes < 1024
        or max_output_bytes > MAX_OUTPUT_BYTES
    ):
        return _error("worker_arguments_invalid")

    runtime_context_protocol = (
        sys.argv[3]
        if len(sys.argv) == 4
        else None
    )
    if (
        runtime_context_protocol is not None
        and runtime_context_protocol
        != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
    ):
        return _error("worker_arguments_invalid")

    input_ceiling = (
        MAX_CONTEXT_WIRE_BYTES
        if runtime_context_protocol is not None
        else MAX_INPUT_BYTES
    )
    input_bytes = sys.stdin.buffer.read(input_ceiling + 1)
    if len(input_bytes) > input_ceiling:
        return _error("input_too_large")

    try:
        wire_value = json.loads(input_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("input_invalid")

    runtime_context = None
    if runtime_context_protocol is None:
        payload = wire_value
    else:
        if (
            not isinstance(wire_value, dict)
            or set(wire_value.keys())
            != {"payload", "protocol", "runtime_context"}
            or wire_value.get("protocol")
            != runtime_context_protocol
        ):
            return _error("runtime_context_invalid")

        payload = wire_value["payload"]
        try:
            if len(_canonical_bytes(payload)) > MAX_INPUT_BYTES:
                return _error("input_too_large")
            runtime_context = (
                normalize_work_learning_runtime_context(
                    wire_value["runtime_context"]
                )
            )
        except Exception:
            return _error("runtime_context_invalid")

        if runtime_context.protocol != runtime_context_protocol:
            return _error("runtime_context_invalid")

    try:
        module_name, callable_name = entrypoint.split(":", 1)
        sink = io.StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            module = importlib.import_module(module_name)
            handler = getattr(module, callable_name)
            if not callable(handler):
                raise TypeError("entrypoint is not callable")
            if runtime_context is None:
                result = handler(payload)
            else:
                result = handler(
                    payload,
                    runtime_context.payload,
                )

        result_bytes = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        return _error("handler_failed")

    if len(result_bytes) > max_output_bytes:
        return _error("output_too_large")

    _emit({"ok": True, "value": json.loads(result_bytes)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
