import importlib
import io
import json
import sys
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from typing import Any


MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


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


def main() -> int:
    if len(sys.argv) != 3:
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

    input_bytes = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(input_bytes) > MAX_INPUT_BYTES:
        return _error("input_too_large")

    try:
        payload = json.loads(input_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("input_invalid")

    try:
        module_name, callable_name = entrypoint.split(":", 1)
        sink = io.StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            module = importlib.import_module(module_name)
            handler = getattr(module, callable_name)
            if not callable(handler):
                raise TypeError("entrypoint is not callable")
            result = handler(payload)

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
