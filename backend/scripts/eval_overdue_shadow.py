"""
Avaliacao independente do modelo local no agente de vencidos (shadow).

Script de avaliacao, FORA de app/. Roda manualmente a partir de backend/:

    python -m scripts.eval_overdue_shadow ^
        --cases evals/overdue_shadow/cases_v1.json ^
        --expected-sha256 <hash registrado na geracao> ^
        --out-dir <pasta NOVA da rodada>

cases_v1.json e o conjunto de AJUSTE; cases_v2.json e a PROVA (inedito,
nao ajustar prompt com ele). Ver evals/overdue_shadow/README.md.

Usa EXATAMENTE o prompt, o schema, o limite de saida e a montagem de
entrada do shadow (app.services.overdue_detection_shadow), e o
OllamaProvider (loopback, digest aprovado). Sem banco, sem ledger, sem
gateway: e avaliacao, nao produto; cada chamada e uma tentativa unica, sem
retry. O texto livre `reason` NUNCA e gravado (so o tamanho).

Pre-voo (recusa sem enviar nenhum prompt): conjunto selado (hash dos bytes
igual ao informado e rotulos recomputados pela regra), memoria livre >= 14
GiB, digest do modelo igual ao aprovado, versao do Ollama igual a fixada.

Codigos de saida:
    0  rodada completa (as metricas dizem se o modelo e bom ou nao)
    2  recusado no pre-voo (nada enviado ao modelo)
    3  rodada incompleta (abortada); casos restantes ficam como not_run
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
import time
from collections.abc import Callable
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import Final

from jsonschema import Draft202012Validator

from app.intelligence.gateway.adapters.ollama import DEFAULT_PORT
from app.intelligence.gateway.adapters.ollama import LoopbackHttpTransport
from app.intelligence.gateway.adapters.ollama import OllamaProvider
from app.intelligence.gateway.adapters.ollama import Transport
from app.intelligence.gateway.contracts import ProviderRequest
from app.intelligence.gateway.contracts import ProviderStatus
from app.intelligence.gateway.errors import ProviderError
from app.intelligence.gateway.errors import ProviderHTTPError
from app.intelligence.gateway.errors import ProviderInvalidResponseError
from app.intelligence.gateway.errors import ProviderModelNotApprovedError
from app.intelligence.gateway.errors import ProviderNotSentError
from app.intelligence.gateway.errors import ProviderTimeoutError
from app.intelligence.gateway.gateway import strict_json_loads
from app.intelligence.gateway.profiles import build_local_ollama_profile
from app.services import overdue_detection_shadow as shadow

EVAL_VERSION: Final[str] = "overdue-shadow-eval-v2-think"
MODEL_ID: Final[str] = "qwen3:4b"
APPROVED_DIGEST: Final[str] = (
    "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
)
EXPECTED_OLLAMA_VERSION: Final[str] = "0.40.16"
MIN_FREE_MEMORY_GIB: Final[float] = 14.0
# = INTELLIGENCE_CALL_TIMEOUT_SECONDS da S5 com raciocinio (300 s): carga a frio
# (~33 s) + ate 4096 tokens a ~19 tok/s (~215 s) + leitura do prompt. 120 nao basta.
CALL_TIMEOUT_SECONDS: Final[int] = 300
MAX_CONSECUTIVE_FAILURES: Final[int] = 5
DECISIONS: Final[tuple[str, str]] = shadow.DECISIONS

# Mesmo perfil do shadow (raciocinio ligado, amostragem, teto de saida).
_PROFILE: Final = build_local_ollama_profile(model_id=MODEL_ID)

EXIT_OK: Final[int] = 0
EXIT_REFUSED: Final[int] = 2
EXIT_INCOMPLETE: Final[int] = 3

# Case outcomes (disjoint, one per case).
CORRECT: Final[str] = "correct"
UNWARRANTED_ACTION: Final[str] = "unwarranted_action"
MISSED_OVERDUE: Final[str] = "missed_overdue"
INVALID: Final[str] = "invalid_response"
TIMEOUT: Final[str] = "timeout"
TRANSPORT_ERROR: Final[str] = "transport_error"
NOT_RUN: Final[str] = "not_run"
OUTCOMES: Final[tuple[str, ...]] = (
    CORRECT, UNWARRANTED_ACTION, MISSED_OVERDUE, INVALID, TIMEOUT,
    TRANSPORT_ERROR, NOT_RUN,
)


class EvalRefused(Exception):
    """Pre-flight refusal; carries the list of reason codes."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__(",".join(reasons))
        self.reasons = list(reasons)


# ----------------------------------------------------------------------
# hashing, sealing
# ----------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_hex(
        json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def prompt_schema_hash() -> str:
    """Hash of everything the model is told, taken from the shadow module."""
    return canonical_hash(
        {
            "instructions": shadow.INSTRUCTIONS,
            "output_schema": shadow.OUTPUT_SCHEMA,
            "prompt_version": shadow.PROMPT_VERSION,
            "prompt_template_id": shadow.PROMPT_TEMPLATE_ID,
            "output_schema_id": shadow.OUTPUT_SCHEMA_ID,
            "max_output_tokens": shadow.MAX_OUTPUT_TOKENS,
            "reasoning_mode": shadow.REASONING_MODE,
            "think": _PROFILE.think,
            "sampling": dataclasses.asdict(_PROFILE.sampling),
        }
    )


def label_for(status: str, due: date, business: date) -> str:
    """The documented label rule (see evals/overdue_shadow/generate_cases.py)."""
    if status == "aberto" and due < business:
        return "mark_overdue"

    return "no_action"


def load_sealed_cases(
    path: Path, expected_sha256: str
) -> tuple[list[dict[str, Any]], str]:
    """Refuses a changed file or labels that disagree with the rule."""
    data = Path(path).read_bytes()
    digest = sha256_hex(data)

    if digest != expected_sha256.strip().lower():
        raise EvalRefused(["dataset_hash_mismatch"])

    try:
        document = json.loads(data.decode("utf-8"))
        cases = document["cases"]
        assert isinstance(cases, list) and cases
    except Exception:
        raise EvalRefused(["dataset_unreadable"]) from None

    ids: set[str] = set()

    for case in cases:
        try:
            due = date.fromisoformat(case["due_date"])
            business = date.fromisoformat(case["business_date"])
            ok = (
                isinstance(case["id"], str)
                and case["id"] not in ids
                and case["status"] in ("aberto", "atrasado", "pago")
                and case["days_after_due"] == (business - due).days
                and case["expected"] in DECISIONS
                and case["expected"]
                == label_for(case["status"], due, business)
            )
        except Exception:
            ok = False

        if not ok:
            raise EvalRefused(["dataset_label_inconsistent"])

        ids.add(case["id"])

    return cases, digest


# ----------------------------------------------------------------------
# pre-flight
# ----------------------------------------------------------------------


def free_memory_gib_windows() -> float:
    """Available physical memory (ullAvailPhys) in GiB (Windows)."""
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)

    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
        ctypes.byref(status)
    ):
        raise OSError("GlobalMemoryStatusEx failed")

    return status.ullAvailPhys / (1024**3)


def memory_ok(free_gib: object) -> bool:
    return (
        type(free_gib) in (int, float)
        and math.isfinite(free_gib)
        and free_gib >= MIN_FREE_MEMORY_GIB
    )


def _get(transport: Transport, path: str) -> dict[str, Any]:
    status, body = transport("GET", path, None, 10.0)
    document = json.loads(body.decode("utf-8"))

    if status != 200 or not isinstance(document, dict):
        raise ValueError("unreadable")

    return document


def preflight(
    transport: Transport,
    *,
    free_memory_gib: Callable[[], float],
    approved_digest: str,
    expected_version: str,
) -> dict[str, Any]:
    """Reads identity; raises EvalRefused. No prompt is sent here."""
    reasons: list[str] = []

    try:
        free = free_memory_gib()
    except Exception:
        free = None

    if not memory_ok(free):
        reasons.append("low_memory")

    version = None
    digest = None

    try:
        version = _get(transport, "/api/version").get("version")
        models = _get(transport, "/api/tags").get("models")

        for entry in models if isinstance(models, list) else []:
            if isinstance(entry, dict) and MODEL_ID in (
                entry.get("name"), entry.get("model")
            ):
                raw = str(entry.get("digest", "")).strip().lower()
                digest = raw[len("sha256:"):] if raw.startswith(
                    "sha256:"
                ) else raw
                break
    except Exception:
        reasons.append("runtime_unreadable")

    if "runtime_unreadable" not in reasons:
        if digest is None:
            reasons.append("model_not_installed")
        elif digest != approved_digest:
            reasons.append("digest_mismatch")

        if version != expected_version:
            reasons.append("ollama_version_mismatch")

    if reasons:
        raise EvalRefused(reasons)

    return {
        "free_memory_gib": round(float(free), 2),
        "model_digest": digest,
        "ollama_version": version,
    }


# ----------------------------------------------------------------------
# execution
# ----------------------------------------------------------------------


class _RecordingTransport:
    """Wraps a transport; keeps the options/hash of each chat body."""

    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self.chat_bodies: list[dict[str, Any]] = []

    def __call__(
        self, method: str, path: str, body: bytes | None, timeout_s: float
    ) -> tuple[int, bytes]:
        if method == "POST" and body is not None:
            sent = json.loads(body.decode("utf-8"))
            self.chat_bodies.append(
                {
                    "path": path,
                    "sha256": sha256_hex(body),
                    "options": sent.get("options"),
                    "think": sent.get("think"),
                    "stream": sent.get("stream"),
                }
            )

        return self._inner(method, path, body, timeout_s)


def _model_input(case: dict[str, Any]) -> str:
    text = shadow.build_model_input(
        SimpleNamespace(
            due_date=case["due_date"],
            status=case["status"],
            business_date=case["business_date"],
        )
    )

    if text is None:
        raise EvalRefused(["unusable_input"])

    return text


def _classify_answer(
    case: dict[str, Any], status: ProviderStatus, output_text: str | None
) -> tuple[str, str | None, int | None, str | None]:
    """-> (outcome, obtained, reason_length, error)."""
    if status is not ProviderStatus.COMPLETED:
        # TRUNCATED (done_reason=length: the budget ended inside the
        # reasoning or the JSON) is never used as an answer.
        return INVALID, None, None, status.value

    if output_text is None or not output_text.strip():
        return INVALID, None, None, "empty_content"

    try:
        parsed = strict_json_loads(output_text)
        Draft202012Validator(dict(shadow.OUTPUT_SCHEMA)).validate(parsed)
    except Exception:
        return INVALID, None, None, "invalid_schema_response"

    decision = parsed.get("decision")
    reason = parsed.get("reason")

    if decision not in DECISIONS:
        return INVALID, None, None, "decision_outside_set"

    reason_length = len(reason) if isinstance(reason, str) else None

    if decision == case["expected"]:
        return CORRECT, decision, reason_length, None

    if decision == "mark_overdue":
        return UNWARRANTED_ACTION, decision, reason_length, None

    return MISSED_OVERDUE, decision, reason_length, None


def run_cases(
    cases: Sequence[dict[str, Any]],
    provider: OllamaProvider,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    identity: dict[str, set[str]] = {"digests": set(), "versions": set()}
    consecutive = 0
    stop_reason: str | None = None

    for case in cases:
        record: dict[str, Any] = {
            "id": case["id"],
            "expected": case["expected"],
            "obtained": None,
            "valid": False,
            "outcome": NOT_RUN,
            "latency_ms": None,
            "reason_length": None,
            "thinking_length": None,
            "output_tokens": None,
            "error": None,
        }
        results.append(record)

        if stop_reason is not None:
            record["error"] = "run_aborted"
            continue

        request = ProviderRequest(
            model_id=MODEL_ID,
            instructions=shadow.INSTRUCTIONS,
            input_text=_model_input(case),
            output_schema=shadow.OUTPUT_SCHEMA,
            max_output_tokens=shadow.MAX_OUTPUT_TOKENS,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
            think=_PROFILE.think,
            sampling=_PROFILE.sampling,
        )
        started = clock()

        try:
            response = provider.invoke(request)
        except ProviderModelNotApprovedError:
            record["outcome"] = NOT_RUN
            record["error"] = "ModelNotApprovedError"
            stop_reason = "model_identity_changed"
            continue
        except ProviderNotSentError as exc:
            record["latency_ms"] = int((clock() - started) * 1000)
            record["outcome"] = TRANSPORT_ERROR
            record["error"] = type(exc).__name__
            consecutive += 1
        except ProviderTimeoutError:
            record["latency_ms"] = int((clock() - started) * 1000)
            record["outcome"] = TIMEOUT
            record["error"] = "ProviderTimeoutError"
            consecutive += 1
        except ProviderInvalidResponseError as exc:
            # The runtime answered but not with a usable envelope.
            record["latency_ms"] = int((clock() - started) * 1000)
            record["outcome"] = INVALID
            record["error"] = type(exc).__name__
            consecutive = 0
        except ProviderHTTPError as exc:
            record["latency_ms"] = int((clock() - started) * 1000)
            record["outcome"] = TRANSPORT_ERROR
            record["error"] = type(exc).__name__
            consecutive += 1
        except ProviderError as exc:
            record["latency_ms"] = int((clock() - started) * 1000)
            record["outcome"] = TRANSPORT_ERROR
            record["error"] = type(exc).__name__
            consecutive += 1
        else:
            record["latency_ms"] = int((clock() - started) * 1000)
            consecutive = 0

            if response.model_digest:
                identity["digests"].add(response.model_digest)

            if response.runtime_version:
                identity["versions"].add(response.runtime_version)

            outcome, obtained, reason_length, error = _classify_answer(
                case, response.status, response.output_text
            )
            record["outcome"] = outcome
            record["obtained"] = obtained
            record["valid"] = outcome in (
                CORRECT, UNWARRANTED_ACTION, MISSED_OVERDUE
            )
            record["reason_length"] = reason_length
            record["error"] = error
            # Only the LENGTH of the reasoning; the text is never kept.
            record["thinking_length"] = response.thinking_length
            record["output_tokens"] = response.output_tokens

        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            stop_reason = "consecutive_transport_failures"

    return results, {
        "stop_reason": stop_reason,
        "observed_digests": sorted(identity["digests"]),
        "observed_versions": sorted(identity["versions"]),
    }


# ----------------------------------------------------------------------
# metrics and report
# ----------------------------------------------------------------------


def _percentile(sorted_values: list[int], pct: int) -> int | None:
    if not sorted_values:
        return None

    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def compute_metrics(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counts = {name: 0 for name in OUTCOMES}

    for record in results:
        counts[record["outcome"]] += 1

    total = len(results)
    ordered = [
        r["latency_ms"] for r in results if r["latency_ms"] is not None
    ]
    latencies = sorted(ordered)
    # The first call includes the cold model load; reported apart.
    warm = sorted(ordered[1:])
    expected_no_action = sum(
        1 for r in results if r["expected"] == "no_action"
    )
    expected_overdue = total - expected_no_action

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    thinking = [
        r["thinking_length"] for r in results
        if r.get("thinking_length") is not None
    ]
    out_tokens = [
        r["output_tokens"] for r in results
        if r.get("output_tokens") is not None
    ]
    truncated = sum(1 for r in results if r["error"] == "truncated")
    empty = sum(1 for r in results if r["error"] == "empty_content")

    return {
        "truncated_responses": truncated,
        "empty_content_responses": empty,
        "thinking_chars": {
            "calls_with_thinking": len(thinking),
            "mean": round(sum(thinking) / len(thinking), 1) if thinking else None,
            "max": max(thinking) if thinking else None,
        },
        "output_tokens": {
            "calls": len(out_tokens),
            "mean": round(sum(out_tokens) / len(out_tokens), 1) if out_tokens else None,
            "max": max(out_tokens) if out_tokens else None,
        },
        "total_cases": total,
        "correct": counts[CORRECT],
        "unwarranted_actions": counts[UNWARRANTED_ACTION],
        "missed_overdue": counts[MISSED_OVERDUE],
        "invalid_responses": counts[INVALID],
        "timeouts": counts[TIMEOUT],
        "transport_errors": counts[TRANSPORT_ERROR],
        "not_run": counts[NOT_RUN],
        "expected_no_action_cases": expected_no_action,
        "expected_mark_overdue_cases": expected_overdue,
        # Failures and non-answers count against the model: denominators
        # are the full class sizes.
        "accuracy": rate(counts[CORRECT], total),
        "unwarranted_action_rate": rate(
            counts[UNWARRANTED_ACTION], expected_no_action
        ),
        "missed_overdue_rate": rate(
            counts[MISSED_OVERDUE], expected_overdue
        ),
        "latency_ms": {
            "calls_measured": len(latencies),
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": latencies[-1] if latencies else None,
            "total": sum(latencies),
            "first_call_ms": ordered[0] if ordered else None,
            "first_call_includes_cold_load": True,
            "excluding_first": {
                "calls_measured": len(warm),
                "p50": _percentile(warm, 50),
                "p95": _percentile(warm, 95),
                "max": warm[-1] if warm else None,
                "total": sum(warm),
            },
        },
    }


def build_report(
    *,
    mode: str,
    dataset_sha256: str,
    dataset_path: str,
    preflight_info: dict[str, Any],
    results: list[dict[str, Any]],
    run_info: dict[str, Any],
    recorder: _RecordingTransport,
    approved_digest: str,
    expected_version: str,
) -> dict[str, Any]:
    options = recorder.chat_bodies[0]["options"] if recorder.chat_bodies else None
    identity_ok = (
        run_info["observed_digests"] in ([], [approved_digest])
        and run_info["observed_versions"] in ([], [expected_version])
    )
    complete = run_info["stop_reason"] is None

    return {
        "eval_version": EVAL_VERSION,
        "mode": mode,
        "status": "complete" if complete else "incomplete",
        "stop_reason": run_info["stop_reason"],
        "config": {
            "model_id": MODEL_ID,
            "approved_digest": approved_digest,
            "observed_digests": run_info["observed_digests"],
            "expected_ollama_version": expected_version,
            "observed_ollama_versions": run_info["observed_versions"],
            "identity_consistent": identity_ok,
            "options": options,
            "think": (
                recorder.chat_bodies[0]["think"]
                if recorder.chat_bodies else None
            ),
            "reasoning_mode": shadow.REASONING_MODE,
            "sampling_choice": (
                "recomendacao Qwen3 para raciocinio (temperature 0.6, "
                "top_p 0.95, top_k 20) com seed fixa 0; temperature 0 "
                "(guloso) causa repeticao sem fim no raciocinio. "
                "Repetibilidade so no mesmo build/hardware do runtime."
            ),
            "reasoning_text_stored": False,
            "max_output_tokens": shadow.MAX_OUTPUT_TOKENS,
            "timeout_seconds": CALL_TIMEOUT_SECONDS,
            "attempts_per_case": 1,
            "prompt_version": shadow.PROMPT_VERSION,
            "prompt_template_id": shadow.PROMPT_TEMPLATE_ID,
            "output_schema_id": shadow.OUTPUT_SCHEMA_ID,
            "prompt_schema_sha256": prompt_schema_hash(),
            "dataset_path": dataset_path,
            "dataset_sha256": dataset_sha256,
            "shadow_module_sha256": sha256_hex(
                Path(shadow.__file__).read_bytes()
            ),
            "evaluator_sha256": sha256_hex(Path(__file__).read_bytes()),
            "min_free_memory_gib": MIN_FREE_MEMORY_GIB,
            "free_memory_gib_at_preflight": preflight_info.get(
                "free_memory_gib"
            ),
        },
        "metrics": compute_metrics(results),
        "cases": results,
    }


def render_summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    config = report["config"]
    latency = metrics["latency_ms"]
    lines = [
        f"# Avaliacao independente - shadow de vencidos ({report['mode']})",
        "",
        f"- Status: {report['status']}"
        + (f" ({report['stop_reason']})" if report["stop_reason"] else ""),
        f"- Modelo: {config['model_id']} digest {config['approved_digest']}",
        f"- Ollama: {', '.join(config['observed_ollama_versions']) or 'n/d'}",
        f"- Conjunto: sha256 {config['dataset_sha256']}",
        f"- Prompt: {config['prompt_version']} "
        f"({config['prompt_template_id']}), "
        f"sha256 {config['prompt_schema_sha256']}",
        f"- Opcoes: {json.dumps(config['options'], sort_keys=True)}",
        f"- Raciocinio: think={config['think']} ({config['reasoning_mode']}); "
        f"amostragem: {config['sampling_choice']}",
        f"- Texto do raciocinio gravado: {config['reasoning_text_stored']} "
        "(so o tamanho em caracteres)",
        f"- max_output_tokens {config['max_output_tokens']}, "
        f"timeout {config['timeout_seconds']} s, 1 tentativa por caso",
        "",
        "| Metrica | Valor |",
        "|---|---|",
        f"| Casos | {metrics['total_cases']} "
        f"({metrics['expected_no_action_cases']} no_action, "
        f"{metrics['expected_mark_overdue_cases']} mark_overdue) |",
        f"| Acertos | {metrics['correct']} |",
        f"| Acoes indevidas (mark_overdue onde o certo e no_action) | "
        f"{metrics['unwarranted_actions']} "
        f"(taxa {metrics['unwarranted_action_rate']}) |",
        f"| Atrasos nao identificados (no_action onde o certo e "
        f"mark_overdue) | {metrics['missed_overdue']} "
        f"(taxa {metrics['missed_overdue_rate']}) |",
        f"| Respostas invalidas | {metrics['invalid_responses']} "
        f"(truncadas {metrics['truncated_responses']}, "
        f"conteudo vazio {metrics['empty_content_responses']}) |",
        f"| Raciocinio (caracteres): media / max | "
        f"{metrics['thinking_chars']['mean']} / "
        f"{metrics['thinking_chars']['max']} |",
        f"| Tokens de saida: media / max | "
        f"{metrics['output_tokens']['mean']} / "
        f"{metrics['output_tokens']['max']} |",
        f"| Timeouts | {metrics['timeouts']} |",
        f"| Erros de transporte | {metrics['transport_errors']} |",
        f"| Nao executados | {metrics['not_run']} |",
        f"| Acuracia (sobre todos os casos) | {metrics['accuracy']} |",
        f"| Latencia p50 / p95 / max / total (ms) | {latency['p50']} / "
        f"{latency['p95']} / {latency['max']} / {latency['total']} |",
        f"| 1o caso (inclui carregamento a frio) (ms) | "
        f"{latency['first_call_ms']} |",
        f"| Latencia sem o 1o caso: p50 / p95 / max / total (ms) | "
        f"{latency['excluding_first']['p50']} / "
        f"{latency['excluding_first']['p95']} / "
        f"{latency['excluding_first']['max']} / "
        f"{latency['excluding_first']['total']} |",
        "",
        "Texto livre `reason` nao e gravado (so o tamanho, por caso).",
        "Detalhe por caso: results.json.",
        "",
    ]
    return "\n".join(lines)


def evaluate(
    *,
    cases_path: Path,
    expected_sha256: str,
    out_dir: Path,
    transport: Transport,
    free_memory_gib: Callable[[], float],
    mode: str = "real",
    approved_digest: str = APPROVED_DIGEST,
    expected_version: str = EXPECTED_OLLAMA_VERSION,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any] | None]:
    """Whole run. Returns (exit_code, report or None when refused)."""
    out_dir = Path(out_dir)

    try:
        if out_dir.exists() and any(out_dir.iterdir()):
            raise EvalRefused(["out_dir_not_empty"])

        cases, dataset_sha256 = load_sealed_cases(
            cases_path, expected_sha256
        )
        recorder = _RecordingTransport(transport)
        info = preflight(
            recorder,
            free_memory_gib=free_memory_gib,
            approved_digest=approved_digest,
            expected_version=expected_version,
        )
    except EvalRefused as refusal:
        print("RECUSADO: " + ", ".join(refusal.reasons))
        return EXIT_REFUSED, None

    provider = OllamaProvider(
        approved_digests={MODEL_ID: approved_digest},
        transport=recorder,
    )
    results, run_info = run_cases(cases, provider, clock=clock)
    report = build_report(
        mode=mode,
        dataset_sha256=dataset_sha256,
        dataset_path=Path(cases_path).as_posix(),
        preflight_info=info,
        results=results,
        run_info=run_info,
        recorder=recorder,
        approved_digest=approved_digest,
        expected_version=expected_version,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "summary.md").write_text(
        render_summary(report), encoding="utf-8"
    )

    return (
        EXIT_OK if report["status"] == "complete" else EXIT_INCOMPLETE
    ), report


# ----------------------------------------------------------------------
# offline scripted mode (proves the pipeline without any model)
# ----------------------------------------------------------------------


class ScriptedOracleTransport:
    """
    Offline stand-in for the runtime: reports the approved identity and
    answers every chat with the documented rule, so the pipeline and the
    metrics can be exercised without a model. Never used in real mode.
    """

    def __init__(
        self,
        *,
        digest: str = APPROVED_DIGEST,
        version: str = EXPECTED_OLLAMA_VERSION,
    ) -> None:
        self._digest = digest
        self._version = version

    def __call__(
        self, method: str, path: str, body: bytes | None, timeout_s: float
    ) -> tuple[int, bytes]:
        if path == "/api/version":
            return 200, json.dumps({"version": self._version}).encode()

        if path == "/api/tags":
            return 200, json.dumps(
                {"models": [{"name": MODEL_ID, "digest": self._digest}]}
            ).encode()

        sent = json.loads((body or b"{}").decode("utf-8"))
        payload = json.loads(sent["messages"][1]["content"])
        decision = (
            "mark_overdue"
            if payload["status"] == "aberto" and payload["days_after_due"] > 0
            else "no_action"
        )
        return 200, json.dumps(
            {
                "model": MODEL_ID,
                "done": True,
                "message": {
                    "thinking": "offline reasoning",
                    "content": json.dumps(
                        {"decision": decision, "reason": "offline"}
                    ),
                },
                "done_reason": "stop",
                "prompt_eval_count": 50,
                "eval_count": 10,
            }
        ).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avaliacao do shadow de vencidos")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--offline-scripted",
        action="store_true",
        help="usa o transporte roteirizado (sem modelo); so prova o pipeline",
    )
    args = parser.parse_args(argv)

    if args.offline_scripted:
        code, _ = evaluate(
            cases_path=args.cases,
            expected_sha256=args.expected_sha256,
            out_dir=args.out_dir,
            transport=ScriptedOracleTransport(),
            free_memory_gib=lambda: MIN_FREE_MEMORY_GIB,
            mode="scripted_offline",
        )
        return code

    # Loopback is enforced by LoopbackHttpTransport (raises otherwise).
    code, report = evaluate(
        cases_path=args.cases,
        expected_sha256=args.expected_sha256,
        out_dir=args.out_dir,
        transport=LoopbackHttpTransport(args.host, args.port),
        free_memory_gib=free_memory_gib_windows,
    )

    if report is not None:
        print(render_summary(report))

    return code


if __name__ == "__main__":
    sys.exit(main())
