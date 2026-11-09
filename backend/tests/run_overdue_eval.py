"""Area 3: DRIVER of the overdue evaluation round. Not collected by pytest (no ``test_`` prefix).

It wires the dataset, the manifest, ``ask_model``, ``model_info``, the identity probe, a monotonic
clock, the free-memory probe (``GlobalMemoryStatusEx.ullAvailPhys`` through ``ctypes``), the real
policy and the commit into ``run_round``. It does nothing by itself that the runner does not
already govern.

Safety gates, all before any generation call:

* it starts only with ``--confirm`` equal to the ``manifest_hash`` (a resume needs the flag
  ``--resume`` AND the same confirmation);
* the round folder must be NEW (or empty) for a start, and must hold the results for a resume;
* free memory below 14 GiB, a refused preflight, a host that is not loopback: zero generation
  calls;
* an existing results file or ``wire/`` capture is never overwritten (exclusive create).

When the round ended (any reason, Ctrl+C included) AFTER it had started, the model is unloaded
with ``keep_alive: 0`` and no prompt. Nothing here has been run against Ollama; the short proof
mode does not exist yet (a later step of its own).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

TESTS_DIR: Final[Path] = Path(__file__).resolve().parent
for _path in (TESTS_DIR, TESTS_DIR.parent):  # `python tests/run_overdue_eval.py` from backend/
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import intelligence_overdue_eval_http as http_layer  # noqa: E402
import intelligence_overdue_eval_runner as rn  # noqa: E402

DEFAULT_BASE_URL: Final[str] = "http://127.0.0.1:11434"
DEFAULT_PROTOCOL: Final[Path] = TESTS_DIR.parent / "docs" / "intelligence" / "OVERDUE_EVAL_PROTOCOL.md"
RESULTS_NAME: Final[str] = "results.jsonl"
STOP_FILE_NAME: Final[str] = "STOP"

EXIT_COMPLETED: Final[int] = 0
EXIT_REFUSED: Final[int] = 2
EXIT_STOPPED: Final[int] = 3
# The round itself ended well (completed) but the anchor or the unload failed: NOT a success, and
# distinct from a refusal (2) and a stop (3). A stopped or interrupted round keeps its own code; the
# JSON line printed at the end carries `anchor_error` / `unload_error` in every case.
EXIT_HOUSEKEEPING: Final[int] = 4
EXIT_INTERRUPTED: Final[int] = 130


@dataclasses.dataclass(frozen=True)
class DriverResult:
    code: int
    status: str  # completed | stopped | refused | interrupted
    reason: str | None = None
    records: int = 0
    unloaded: bool = False
    anchor_error: str | None = None  # the external anchor could not be written: reported, not silent
    unload_error: str | None = None


def free_memory_gib_windows() -> float:
    """Available physical memory (``ullAvailPhys``) in GiB, from ``GlobalMemoryStatusEx``."""
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        raise OSError("GlobalMemoryStatusEx failed")
    return status.ullAvailPhys / 2**30


def build_manifest_for(dataset_sha256: str, protocol_sha256: str, params: dict) -> dict:
    """The manifest of a round from the current files. ``params`` is the director's decision."""
    entries = {"dataset": dataset_sha256, "protocol": protocol_sha256,
               "schema": rn.schema_sha256(), "prompt": rn.prompt_sha256(), **rn.source_hashes()}
    return rn.build_manifest(entries, params)


def default_validator() -> rn.Validator:
    """The real ``validate_plan`` with the real policy (assembled from the real modules)."""
    from test_intelligence_plan_validator import ALL_PERMISSIONS, build_real_policy_reference

    return rn.make_real_validator(build_real_policy_reference(), ALL_PERMISSIONS)


def _record_count(results: Path) -> int:
    try:
        return len(results.read_bytes().splitlines())
    except OSError:
        return 0


def _round_started(results: Path, before: int) -> bool:
    """A `start` or `resume` event after the first ``before`` records: the round really began.

    A refused preflight on a resume also appends a record, but that is not a start.
    """
    try:
        lines = results.read_bytes().splitlines()[before:]
    except OSError:
        return False
    for line in lines:
        try:
            if json.loads(line).get("event") in ("start", "resume"):
                return True
        except ValueError:
            continue
    return False


def _refused(reason: str) -> DriverResult:
    return DriverResult(EXIT_REFUSED, "refused", reason)


def execute(
    *,
    manifest_path: Path,
    dataset_path: Path,
    results_dir: Path,
    confirm: str | None,
    commit: str,
    resume: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    protocol_path: Path = DEFAULT_PROTOCOL,
    memory: Callable[[], float] = free_memory_gib_windows,
    clock: Callable[[], float] = time.monotonic,
    validator: rn.Validator | None = None,
    client_factory: Callable[..., http_layer.LoopbackClient] = http_layer.LoopbackClient,
) -> DriverResult:
    results_dir = Path(results_dir)
    results = results_dir / RESULTS_NAME
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        manifest_hash = rn.verify_manifest(manifest)
    except (OSError, ValueError, rn.ManifestTampered):
        return _refused("manifest_invalid")
    if not (type(confirm) is str and confirm == manifest_hash):
        return _refused("confirmation_missing_or_wrong")  # the first gate: nothing else is touched
    if not rn.memory_ok(memory()):
        return _refused("low_memory")
    if resume:
        if not results.exists():
            return _refused("resume_without_results")
    elif results_dir.exists() and (not results_dir.is_dir() or any(results_dir.iterdir())):
        return _refused("results_dir_not_new")

    try:
        client = client_factory(base_url, wire_dir=results_dir / http_layer.WIRE_DIR_NAME)
        dataset = rn.load_dataset(Path(dataset_path))
    except (http_layer.NotLoopback, rn.DatasetError, OSError) as exc:
        return _refused(f"setup_failed:{type(exc).__name__}")
    results_dir.mkdir(parents=True, exist_ok=True)

    before = _record_count(results)
    result: DriverResult
    try:
        outcome = rn.run_round(
            dataset=dataset, manifest=manifest, results_path=results,
            ask_model=client.ask_model, validator=validator or default_validator(),
            free_memory_gib=memory, clock=clock, model_info=client.model_info(),
            protocol_sha256=rn.file_sha256(Path(protocol_path)), commit=commit,
            resume=resume, runtime_probe=client.runtime_probe(),
            should_stop=lambda: (results_dir / STOP_FILE_NAME).exists(),
        )
        result = DriverResult(
            EXIT_COMPLETED if outcome.status == "completed" else EXIT_STOPPED,
            outcome.status, outcome.stop_reason, outcome.records_total)
    except rn.PreflightRefused as exc:
        result = _refused("preflight:" + ",".join(exc.reasons))
    except rn.ResumeRefused as exc:
        result = _refused(f"resume_refused:{exc}")
    except KeyboardInterrupt:
        result = DriverResult(EXIT_INTERRUPTED, "interrupted", "keyboard_interrupt")
    finally:
        started = _round_started(results, before)  # a round began: unload what it loaded
        unloaded = False
        anchor_error = unload_error = None
        if started:
            try:  # the external anchor, for the operator to copy out; never overwritten
                anchor = rn.external_anchor(results, manifest)
                with open(results_dir / f"anchor.{anchor['records']}.json", "xb") as handle:
                    handle.write(rn.canonical(anchor) + b"\n")
            except (OSError, rn.RunnerError) as exc:
                anchor_error = f"{type(exc).__name__}: {exc}"[:200]
            try:
                unloaded = client.unload_model() == 200
                if not unloaded:
                    unload_error = "unload_status_not_200"
            except Exception as exc:  # best effort: the model also expires with keep_alive
                unload_error = f"{type(exc).__name__}: {exc}"[:200]
    code = result.code
    if code == EXIT_COMPLETED and (anchor_error or unload_error):
        code = EXIT_HOUSEKEEPING
    return dataclasses.replace(result, code=code, records=_record_count(results), unloaded=unloaded,
                               anchor_error=anchor_error, unload_error=unload_error)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overdue evaluation round driver")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--confirm", default=None, help="must equal the manifest_hash")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = execute(
        manifest_path=args.manifest, dataset_path=args.dataset, results_dir=args.results_dir,
        confirm=args.confirm, commit=args.commit, resume=args.resume, base_url=args.base_url)
    print(json.dumps(dataclasses.asdict(result), sort_keys=True))
    return result.code


if __name__ == "__main__":
    raise SystemExit(main())
