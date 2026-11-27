"""
Offline G3 evaluator: one BLOCK = one arm x one case list x one seed x one
order, written to ONE NEW evidence folder as metrics only.

What is stored per call: ids, enums, counts, durations, lengths. NEVER the
model's free text (`reason`) nor its reasoning (only `thinking_length`).

Guarantees (each covered by a test):
  - sealed input: dataset sha256 must equal the expected one and every label
    is recomputed from the payload by the independent verifier;
  - pre-flight identity (digest, runtime version, free memory) before any
    prompt; refusal sends nothing;
  - concurrency 1 (re-entrancy raises), no retry, 1 attempt per case;
  - limits: max cases, wall-clock budget, consecutive-failure stop,
    blackout windows (paused, resumable);
  - resume: appended JSONL, header must match the config hash;
  - an unrecognised or truncated answer is never used as an answer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import threading
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any

from g3eval import frozen
from g3eval import verify_v3
from g3eval.transport import GenRequest
from g3eval.transport import Transport
from g3eval.transport import TransportError
from g3eval.transport import TransportNotSent
from g3eval.transport import TransportTimeout

EVALUATOR_VERSION = "g3-eval-1"
HEADER_TYPE = "header"
CASE_TYPE = "case"

CORRECT = "correct"
UNWARRANTED_ACTION = "unwarranted_action"
MISSED_OVERDUE = "missed_overdue"
INVALID = "invalid_response"
TIMEOUT = "timeout"
TRANSPORT_ERROR = "transport_error"
NOT_RUN = "not_run"
ANSWERED = (CORRECT, UNWARRANTED_ACTION, MISSED_OVERDUE)
OUTCOMES = (
    CORRECT, UNWARRANTED_ACTION, MISSED_OVERDUE, INVALID, TIMEOUT,
    TRANSPORT_ERROR, NOT_RUN,
)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_INCOMPLETE = 3

COLD_LOAD_MS = 5000  # Ollama load_duration at/above this = cold start

QWEN_OPTIONS = {
    "num_ctx": 8192, "num_predict": 4096, "seed": 0,
    "temperature": 0.6, "top_p": 0.95, "top_k": 20,
}


@dataclass(frozen=True)
class ArmConfig:
    arm_id: str
    model_id: str
    digest: str
    think: bool | None
    options: dict[str, Any]
    timeout_s: float = 300.0


ARM_A = ArmConfig(
    arm_id="A",
    model_id="qwen3:4b-instruct-2507-q4_K_M",
    digest="0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0",
    think=None,
    options=dict(QWEN_OPTIONS),
)
ARM_B = ArmConfig(
    arm_id="B",
    model_id="qwen3:4b",
    digest="359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
    think=True,
    options=dict(QWEN_OPTIONS),
)


class EvalRefused(Exception):
    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__(",".join(reasons))
        self.reasons = list(reasons)


class ConcurrencyError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# limits, blackout
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    max_cases: int | None = None
    max_wall_seconds: float | None = None
    max_consecutive_failures: int = 5
    min_free_memory_gib: float = 14.0


@dataclass(frozen=True)
class Blackout:
    """Half-open windows [start, end) in which NO call may be sent."""

    windows: tuple[tuple[datetime, datetime], ...] = ()

    def active(self, now: datetime) -> bool:
        return any(start <= now < end for start, end in self.windows)


def blackout_from_file(path: Path) -> Blackout:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return Blackout(
        tuple(
            (datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"]))
            for w in doc["windows"]
        )
    )


_RELEASE_FLAGS = (
    "h24_closure_1bea10d_satisfactory", "g1_concluded", "express_authorization",
)


def load_release(path: Path, now: datetime) -> dict[str, Any]:
    """Release of the blackout (protocol v2, item 5). The blackout hour is
    only a floor: running also needs this signed release. Raises EvalRefused."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        assert isinstance(doc, dict)
    except Exception:
        raise EvalRefused(["release_unreadable"]) from None

    who = doc.get("authorized_by")

    if not isinstance(who, str) or not who.strip():
        raise EvalRefused(["release_without_author"])

    for flag in _RELEASE_FLAGS:
        if doc.get(flag) is not True:
            raise EvalRefused([f"release_condition_not_met:{flag}"])

    try:
        issued = datetime.fromisoformat(doc["authorized_at"])
        expires = datetime.fromisoformat(doc["expires_at"])
    except Exception:
        raise EvalRefused(["release_bad_dates"]) from None

    if (issued.tzinfo is None) != (now.tzinfo is None) or (
        expires.tzinfo is None
    ) != (now.tzinfo is None):
        raise EvalRefused(["release_timezone_mismatch"])

    if not issued <= now < expires:
        raise EvalRefused(["release_not_in_force"])

    return doc


# ----------------------------------------------------------------------
# sealed cases
# ----------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_sealed_cases(
    path: Path, expected_sha256: str
) -> tuple[list[dict[str, Any]], str]:
    data = Path(path).read_bytes()
    digest = sha256_hex(data)

    if digest != expected_sha256.strip().lower():
        raise EvalRefused(["dataset_hash_mismatch"])

    try:
        cases = json.loads(data.decode("utf-8"))["cases"]
        assert isinstance(cases, list) and cases
    except Exception:
        raise EvalRefused(["dataset_unreadable"]) from None

    ids: set[str] = set()

    for case in cases:
        try:
            payload = (
                case["payload"]
                if "payload" in case
                else json.loads(frozen.input_text_for(case))
            )
            truth, _ = verify_v3.truth_from_payload(payload)
            ok = (
                isinstance(case["id"], str)
                and case["id"] not in ids
                and case["expected"] == truth
            )
        except Exception:
            ok = False

        if not ok:
            raise EvalRefused(["dataset_label_inconsistent"])

        ids.add(case["id"])

    return cases, digest


def order_cases(
    cases: Sequence[dict[str, Any]], order_seed: int | None
) -> list[dict[str, Any]]:
    ordered = list(cases)

    if order_seed is not None:
        random.Random(order_seed).shuffle(ordered)

    return ordered


def stability_subset(
    cases: Sequence[dict[str, Any]], size: int = 40
) -> list[dict[str, Any]]:
    """Deterministic, category-round-robin subset (edges stay represented)."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for case in sorted(cases, key=lambda c: c["id"]):
        groups.setdefault((case["stratum"], case["category"]), []).append(case)

    keys = sorted(groups)
    picked: list[dict[str, Any]] = []
    depth = 0

    while len(picked) < size and any(depth < len(groups[k]) for k in keys):
        for key in keys:
            if depth < len(groups[key]) and len(picked) < size:
                picked.append(groups[key][depth])

        depth += 1

    return sorted(picked, key=lambda c: c["id"])


# ----------------------------------------------------------------------
# classification (no free text survives this function)
# ----------------------------------------------------------------------


def strict_json_loads(text: str) -> Any:
    def no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}

        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate key")

            out[key] = value

        return out

    def bad_constant(name: str) -> Any:
        raise ValueError("non-finite constant")

    return json.loads(
        text, object_pairs_hook=no_dupes, parse_constant=bad_constant
    )


def classify(
    expected: str, done_reason: str | None, content: str | None
) -> tuple[str, str | None, int | None, str | None]:
    """-> (outcome, obtained, reason_length, error_code)."""
    if done_reason == "length":
        return INVALID, None, None, "truncated"

    if done_reason not in (None, "stop"):
        return INVALID, None, None, "unexpected_done_reason"

    if content is None or not content.strip():
        return INVALID, None, None, "empty_content"

    try:
        parsed = strict_json_loads(content)
    except Exception:
        return INVALID, None, None, "invalid_json"

    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"decision", "reason"}
        or not isinstance(parsed["reason"], str)
        or len(parsed["reason"]) > 200
    ):
        return INVALID, None, None, "schema_violation"

    decision = parsed["decision"]

    if decision not in frozen.DECISIONS:
        return INVALID, None, None, "decision_outside_set"

    length = len(parsed["reason"])

    if decision == expected:
        return CORRECT, decision, length, None

    if decision == "mark_overdue":
        return UNWARRANTED_ACTION, decision, length, None

    return MISSED_OVERDUE, decision, length, None


# ----------------------------------------------------------------------
# resources
# ----------------------------------------------------------------------


def parse_nvidia_smi(text: str) -> int | None:
    """`nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits`
    -> highest used MiB over the listed GPUs, None if unreadable."""
    values = []

    for line in text.strip().splitlines():
        line = line.strip()

        if line.isdigit():
            values.append(int(line))
        else:
            return None

    return max(values) if values else None


class NvidiaSmiSampler:
    """Not executed in this delivery. `run` is injectable for tests."""

    COMMAND = (
        "nvidia-smi", "--query-gpu=memory.used",
        "--format=csv,noheader,nounits",
    )

    def __init__(
        self,
        run: Callable[[Sequence[str]], str],
        free_ram_gib: Callable[[], float | None],
    ) -> None:
        self._run = run
        self._ram = free_ram_gib

    def __call__(self) -> dict[str, Any]:
        try:
            vram = parse_nvidia_smi(self._run(self.COMMAND))
        except Exception:
            vram = None

        try:
            ram = self._ram()
        except Exception:
            ram = None

        return {"vram_used_mib": vram, "ram_free_gib": ram}


def null_sampler() -> dict[str, Any]:
    return {"vram_used_mib": None, "ram_free_gib": None}


# ----------------------------------------------------------------------
# run
# ----------------------------------------------------------------------


@dataclass
class RunSpec:
    block_id: str
    arm: ArmConfig
    dataset_label: str
    dataset_sha256: str
    seed: int | None = None  # None: keep the arm's seed (0)
    order_seed: int | None = None
    case_ids: tuple[str, ...] | None = None
    rep_label: str = "main"
    extra_options: dict[str, Any] = field(default_factory=dict)

    def effective_options(self) -> dict[str, Any]:
        options = dict(self.arm.options)

        if self.seed is not None:
            options["seed"] = self.seed

        options.update(self.extra_options)
        return options


def config_hash(spec: RunSpec, limits: Limits) -> str:
    material = {
        "evaluator": EVALUATOR_VERSION,
        "prompt": frozen.prompt_hash(),
        "arm": asdict(spec.arm),
        "options": spec.effective_options(),
        "dataset_sha256": spec.dataset_sha256,
        "dataset_label": spec.dataset_label,
        "order_seed": spec.order_seed,
        "case_ids": list(spec.case_ids) if spec.case_ids else None,
        "rep": spec.rep_label,
        "block": spec.block_id,
        "min_free_memory_gib": limits.min_free_memory_gib,
    }
    return sha256_hex(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    )


def memory_ok(free: object, floor: float) -> bool:
    return (
        type(free) in (int, float)
        and math.isfinite(free)  # type: ignore[arg-type]
        and free >= floor  # type: ignore[operator]
    )


def preflight(
    transport: Transport,
    spec: RunSpec,
    limits: Limits,
    free_memory_gib: Callable[[], float],
) -> dict[str, Any]:
    reasons: list[str] = []

    try:
        free = free_memory_gib()
    except Exception:
        free = None

    if not memory_ok(free, limits.min_free_memory_gib):
        reasons.append("low_memory")

    version = None
    digest = None

    try:
        identity = transport.identity()
        version = identity.get("version")
        digest = identity.get("digests", {}).get(spec.arm.model_id)
    except Exception:
        reasons.append("runtime_unreadable")

    if "runtime_unreadable" not in reasons:
        if digest is None:
            reasons.append("model_not_installed")
        elif digest != spec.arm.digest:
            reasons.append("digest_mismatch")

        if version != frozen.EXPECTED_OLLAMA_VERSION:
            reasons.append("ollama_version_mismatch")

    if reasons:
        raise EvalRefused(reasons)

    return {
        "free_memory_gib": round(float(free), 2),
        "digest": digest,
        "ollama_version": version,
    }


class _Exclusive:
    """Concurrency = 1: entering twice raises instead of queueing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self) -> "_Exclusive":
        if not self._lock.acquire(blocking=False):
            raise ConcurrencyError("concurrency is fixed at 1")

        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    return rows


def _append(handle: Any, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def run_block(
    *,
    spec: RunSpec,
    cases: Sequence[dict[str, Any]],
    out_dir: Path,
    transport: Transport,
    free_memory_gib: Callable[[], float],
    limits: Limits = Limits(),
    blackout: Blackout = Blackout(),
    sampler: Callable[[], dict[str, Any]] = null_sampler,
    wall_clock: Callable[[], datetime] = datetime.now,
    clock: Callable[[], float] = time.monotonic,
    resume: bool = False,
    release: dict[str, Any] | None = None,
    require_release: bool = True,
) -> tuple[int, dict[str, Any]]:
    """Returns (exit_code, status dict). Writes metrics.jsonl + status.json."""
    out_dir = Path(out_dir)
    chash = config_hash(spec, limits)
    metrics_path = out_dir / "metrics.jsonl"
    done: set[str] = set()

    try:
        if require_release:
            if release is None:
                raise EvalRefused(["release_authorization_absent"])

            if not (
                datetime.fromisoformat(release["authorized_at"])
                <= wall_clock()
                < datetime.fromisoformat(release["expires_at"])
            ):
                raise EvalRefused(["release_not_in_force"])

        if blackout.active(wall_clock()):
            raise EvalRefused(["blackout_window"])

        if out_dir.exists() and any(out_dir.iterdir()):
            if not resume:
                raise EvalRefused(["out_dir_not_empty"])

            rows = _read_jsonl(metrics_path)

            if not rows or rows[0].get("type") != HEADER_TYPE or rows[0].get(
                "config_hash"
            ) != chash:
                raise EvalRefused(["resume_config_mismatch"])

            done = {r["id"] for r in rows[1:] if r.get("type") == CASE_TYPE}
        elif resume:
            raise EvalRefused(["resume_nothing_to_resume"])

        selected = [
            c for c in cases
            if spec.case_ids is None or c["id"] in spec.case_ids
        ]

        if spec.case_ids is not None and len(selected) != len(
            set(spec.case_ids)
        ):
            raise EvalRefused(["unknown_case_ids"])

        queue = order_cases(selected, spec.order_seed)
        info = preflight(transport, spec, limits, free_memory_gib)
    except EvalRefused as refusal:
        return EXIT_REFUSED, {"status": "refused", "reasons": refusal.reasons}

    out_dir.mkdir(parents=True, exist_ok=True)
    options = spec.effective_options()
    guard = _Exclusive()
    started = clock()
    consecutive = 0
    calls_this_session = 0
    stop_reason: str | None = None

    with open(metrics_path, "a", encoding="utf-8", newline="\n") as handle:
        if handle.tell() == 0:
            _append(
                handle,
                {
                    "type": HEADER_TYPE,
                    "evaluator_version": EVALUATOR_VERSION,
                    "config_hash": chash,
                    "block_id": spec.block_id,
                    "arm_id": spec.arm.arm_id,
                    "model_id": spec.arm.model_id,
                    "approved_digest": spec.arm.digest,
                    "observed_digest": info["digest"],
                    "ollama_version": info["ollama_version"],
                    "dataset_label": spec.dataset_label,
                    "dataset_sha256": spec.dataset_sha256,
                    "prompt_sha256": frozen.prompt_hash(),
                    "think": spec.arm.think,
                    "options": options,
                    "order_seed": spec.order_seed,
                    "rep": spec.rep_label,
                    "concurrency": 1,
                    "attempts_per_case": 1,
                    "timeout_s": spec.arm.timeout_s,
                    "free_memory_gib_preflight": info["free_memory_gib"],
                    "free_text_stored": False,
                },
            )

        remaining = [c for c in queue if c["id"] not in done]
        sent = 0

        for position, case in enumerate(remaining):
            if limits.max_cases is not None and sent >= limits.max_cases:
                stop_reason = "max_cases"
                break

            if (
                limits.max_wall_seconds is not None
                and clock() - started >= limits.max_wall_seconds
            ):
                stop_reason = "wall_budget"
                break

            if blackout.active(wall_clock()):
                stop_reason = "blackout_window"
                break

            if require_release and not (
                datetime.fromisoformat(release["authorized_at"])
                <= wall_clock()
                < datetime.fromisoformat(release["expires_at"])
            ):
                stop_reason = "release_expired"
                break

            record: dict[str, Any] = {
                "type": CASE_TYPE,
                "id": case["id"],
                "stratum": case.get("stratum"),
                "category": case.get("category"),
                "seq": len(done) + position,
                "queue_remaining": len(remaining) - position - 1,
                "expected": case["expected"],
                "obtained": None,
                "outcome": NOT_RUN,
                "error": None,
                "latency_ms": None,
                "server_total_ms": None,
                "load_ms": None,
                "cold": False,
                "prompt_tokens": None,
                "output_tokens": None,
                "thinking_length": None,
                "reason_length": None,
                "vram_before_mib": None,
                "vram_after_mib": None,
                "ram_free_gib_before": None,
                "ram_free_gib_after": None,
            }
            before = sampler()
            record["vram_before_mib"] = before.get("vram_used_mib")
            record["ram_free_gib_before"] = before.get("ram_free_gib")
            request = GenRequest(
                model_id=spec.arm.model_id,
                instructions=frozen.INSTRUCTIONS,
                input_text=frozen.input_text_for(case),
                output_schema=frozen.OUTPUT_SCHEMA,
                options=options,
                think=spec.arm.think,
                timeout_s=spec.arm.timeout_s,
            )
            t0 = clock()
            failed = False

            try:
                with guard:
                    response = transport.generate(request)
            except TransportTimeout:
                record["outcome"] = TIMEOUT
                record["error"] = "TransportTimeout"
                failed = True
            except TransportNotSent as exc:
                record["outcome"] = TRANSPORT_ERROR
                record["error"] = type(exc).__name__
                failed = True
            except TransportError as exc:
                record["outcome"] = TRANSPORT_ERROR
                record["error"] = type(exc).__name__
                failed = True
            else:
                outcome, obtained, rlen, error = classify(
                    case["expected"], response.done_reason, response.content
                )
                record.update(
                    outcome=outcome, obtained=obtained, error=error,
                    reason_length=rlen,
                    prompt_tokens=response.prompt_tokens,
                    output_tokens=response.output_tokens,
                    thinking_length=response.thinking_length,
                    server_total_ms=response.total_duration_ms,
                    load_ms=response.load_duration_ms,
                )

                if (
                    response.model_digest not in (None, spec.arm.digest)
                    or response.runtime_version not in (
                        None, frozen.EXPECTED_OLLAMA_VERSION
                    )
                ):
                    record["outcome"] = NOT_RUN
                    record["error"] = "identity_changed"
                    stop_reason = "model_identity_changed"

            record["latency_ms"] = int((clock() - t0) * 1000)
            record["cold"] = (
                record["load_ms"] is not None
                and record["load_ms"] >= COLD_LOAD_MS
            ) or (calls_this_session == 0 and not done)
            after = sampler()
            record["vram_after_mib"] = after.get("vram_used_mib")
            record["ram_free_gib_after"] = after.get("ram_free_gib")
            calls_this_session += 1
            sent += 1

            if record["outcome"] != NOT_RUN:
                _append(handle, record)

            consecutive = consecutive + 1 if failed else 0

            if stop_reason is None and consecutive >= (
                limits.max_consecutive_failures
            ):
                stop_reason = "consecutive_transport_failures"

            if stop_reason is not None:
                break

    rows = _read_jsonl(metrics_path)
    finished = {r["id"] for r in rows[1:] if r.get("type") == CASE_TYPE}
    complete = stop_reason is None and finished >= {
        c["id"] for c in queue
    }
    status = {
        "status": "complete" if complete else "incomplete",
        "stop_reason": stop_reason,
        "cases_done": len(finished),
        "cases_planned": len(queue),
        "config_hash": chash,
    }
    (out_dir / "status.json").write_text(
        json.dumps(status, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return (EXIT_OK if complete else EXIT_INCOMPLETE), status
