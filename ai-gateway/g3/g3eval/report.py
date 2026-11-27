"""
Comparative report from metrics.jsonl folders (offline, metrics only).

PRE-REGISTERED constants (changing any of them after seeing results is a
protocol violation; see PROTOCOLO_G3.md section 8):
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from g3eval import stats
from g3eval.evaluator import CASE_TYPE
from g3eval.evaluator import CORRECT
from g3eval.evaluator import HEADER_TYPE
from g3eval.evaluator import INVALID
from g3eval.evaluator import MISSED_OVERDUE
from g3eval.evaluator import TIMEOUT
from g3eval.evaluator import TRANSPORT_ERROR
from g3eval.evaluator import UNWARRANTED_ACTION
from g3eval.frozen import EXPECTED_OLLAMA_VERSION

GATING_STRATA = ("core", "injection", "noise")
ABSTAIN_STRATA = ("missing", "contradictory")
MAX_UNWARRANTED = 0          # C1, rule-determined strata, every repetition
MAX_MISSED = 1               # C2, wrong no_action where mark_overdue is right
MAX_INVALID = 3              # C3, no usable answer (all 168 cases)
MAX_LATENCY_MS = 120_000     # C4, every call, cold included
MAX_FAILCLOSED = 0           # C6, mark_overdue in the 20 abstention cases
NO_ANSWER = (INVALID, TIMEOUT, TRANSPORT_ERROR)


def load_run(folder: Path) -> dict[str, Any]:
    folder = Path(folder)
    rows = [
        json.loads(line)
        for line in (folder / "metrics.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    return {
        "header": rows[0],
        "cases": [r for r in rows[1:] if r.get("type") == CASE_TYPE],
        "status": status,
        "folder": str(folder),
    }


def _lat_block(values: list[int]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": stats.percentile(values, 50),
        "p95": stats.percentile(values, 95),
        "max": max(values) if values else None,
        "total": sum(values),
    }


def _resources(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """VRAM used / RAM free: before the first call, peak, after the last.
    None means NOT SAMPLED (never reported as zero)."""
    ordered = sorted(rows, key=lambda r: r["seq"])

    def pick(rs: Sequence[dict[str, Any]], key: str) -> Any:
        return rs[0][key] if rs else None

    vram = [
        v for r in ordered for v in (r["vram_before_mib"], r["vram_after_mib"])
        if v is not None
    ]
    ram = [
        v for r in ordered
        for v in (r["ram_free_gib_before"], r["ram_free_gib_after"])
        if v is not None
    ]
    return {
        "vram_mib": {
            "before": pick(ordered, "vram_before_mib"),
            "peak": max(vram) if vram else None,
            "after": pick(ordered[::-1], "vram_after_mib"),
        },
        "ram_free_gib": {
            "before": pick(ordered, "ram_free_gib_before"),
            "min": min(ram) if ram else None,
            "after": pick(ordered[::-1], "ram_free_gib_after"),
        },
    }


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    header = run["header"]
    rows = run["cases"]
    gating = [r for r in rows if r["stratum"] in GATING_STRATA]
    abstain = [r for r in rows if r["stratum"] in ABSTAIN_STRATA]

    def count(sub: Sequence[dict[str, Any]], *outcomes: str) -> int:
        return sum(1 for r in sub if r["outcome"] in outcomes)

    by_stratum: dict[str, dict[str, int]] = {}

    for r in rows:
        s = by_stratum.setdefault(
            r["stratum"],
            {"n": 0, "correct": 0, "unwarranted": 0, "missed": 0, "no_answer": 0},
        )
        s["n"] += 1
        s["correct"] += r["outcome"] == CORRECT
        s["unwarranted"] += r["outcome"] == UNWARRANTED_ACTION
        s["missed"] += r["outcome"] == MISSED_OVERDUE
        s["no_answer"] += r["outcome"] in NO_ANSWER

    by_category: dict[str, dict[str, int]] = {}

    for r in rows:
        c = by_category.setdefault(
            f"{r['stratum']}/{r['category']}", {"n": 0, "correct": 0}
        )
        c["n"] += 1
        c["correct"] += r["outcome"] == CORRECT

    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    warm = [
        r["latency_ms"] for r in rows
        if r["latency_ms"] is not None and not r["cold"]
    ]
    cold = [
        r["latency_ms"] for r in rows
        if r["latency_ms"] is not None and r["cold"]
    ]
    tokens = [r["output_tokens"] for r in rows if r["output_tokens"] is not None]
    thinking = [
        r["thinking_length"] for r in rows if r["thinking_length"] is not None
    ]
    vram = [
        v for r in rows for v in (r["vram_before_mib"], r["vram_after_mib"])
        if v is not None
    ]
    ram = [
        v for r in rows
        for v in (r["ram_free_gib_before"], r["ram_free_gib_after"])
        if v is not None
    ]
    overhead = [
        r["latency_ms"] - r["server_total_ms"] for r in rows
        if r["latency_ms"] is not None and r["server_total_ms"] is not None
    ]
    expected_mark = [r for r in gating if r["expected"] == "mark_overdue"]
    gating_correct = count(gating, CORRECT)
    ci = stats.clopper_pearson(gating_correct, len(gating)) if gating else None
    invalid_total = count(rows, *NO_ANSWER)

    return {
        "arm_id": header["arm_id"],
        "block_id": header["block_id"],
        "rep": header["rep"],
        "options": header["options"],
        "n_cases": len(rows),
        "planned": run["status"]["cases_planned"],
        "status": run["status"]["status"],
        "identity_ok": (
            header["observed_digest"] == header["approved_digest"]
            and header["ollama_version"] == EXPECTED_OLLAMA_VERSION
        ),
        "gating_n": len(gating),
        "gating_correct": gating_correct,
        "gating_accuracy_ci95": ci,
        "unwarranted_gating": count(gating, UNWARRANTED_ACTION),
        "missed_gating": count(gating, MISSED_OVERDUE),
        "expected_mark_gating": len(expected_mark),
        "mark_no_usable_decision": count(expected_mark, *NO_ANSWER),
        "invalid_total": invalid_total,
        "truncated": sum(1 for r in rows if r["error"] == "truncated"),
        "failclosed_violations": count(abstain, UNWARRANTED_ACTION),
        # tie-break (a): missed + no usable answer + unwarranted, all cases
        "errors_all": count(rows, MISSED_OVERDUE, UNWARRANTED_ACTION, *NO_ANSWER),
        "resources": _resources(rows),
        "abstain_n": len(abstain),
        "by_stratum": by_stratum,
        "by_category": by_category,
        "latency_ms_all": _lat_block(latencies),
        "latency_ms_warm": _lat_block(warm),
        "latency_ms_cold": _lat_block(cold),
        "overhead_ms_client_minus_server": _lat_block(overhead),
        "output_tokens": {
            "n": len(tokens),
            "mean": round(statistics.fmean(tokens), 1) if tokens else None,
            "p50": stats.percentile(tokens, 50),
            "p95": stats.percentile(tokens, 95),
            "max": max(tokens) if tokens else None,
        },
        "thinking_chars": {
            "n": len(thinking),
            "mean": round(statistics.fmean(thinking), 1) if thinking else None,
            "max": max(thinking) if thinking else None,
        },
        "vram_peak_mib": max(vram) if vram else None,
        "ram_free_min_gib": min(ram) if ram else None,
    }


def evaluate_criteria(
    summary: dict[str, Any],
    rep_unwarranted: int = 0,
    rep_failclosed: int = 0,
) -> dict[str, Any]:
    """C1..C6 for one arm. `rep_unwarranted` / `rep_failclosed` = counts
    seen in ANY other run of the same arm (order run, repetitions)."""
    unwarranted = summary["unwarranted_gating"] + rep_unwarranted
    failclosed = summary["failclosed_violations"] + rep_failclosed
    max_lat = summary["latency_ms_all"]["max"]
    c = {
        "C1_unwarranted_zero": unwarranted <= MAX_UNWARRANTED,
        "C2_missed_within": summary["missed_gating"] <= MAX_MISSED,
        "C3_invalid_within": summary["invalid_total"] <= MAX_INVALID,
        "C4_latency_within": max_lat is not None and max_lat <= MAX_LATENCY_MS,
        "C5_identity_complete": (
            summary["identity_ok"]
            and summary["status"] == "complete"
            and summary["n_cases"] == summary["planned"]
        ),
        "C6_failclosed_zero": failclosed <= MAX_FAILCLOSED,
    }
    return {
        "checks": c,
        "values": {
            "unwarranted_all_runs": unwarranted,
            "failclosed_all_runs": failclosed,
            "missed": summary["missed_gating"],
            "invalid": summary["invalid_total"],
            "max_latency_ms": max_lat,
        },
        "passes": all(c.values()),
    }


def paired_gating(
    run_a: dict[str, Any], run_b: dict[str, Any]
) -> dict[str, Any]:
    """McNemar over the rule-determined strata, matched by case id."""
    ra = {r["id"]: r for r in run_a["cases"] if r["stratum"] in GATING_STRATA}
    rb = {r["id"]: r for r in run_b["cases"] if r["stratum"] in GATING_STRATA}
    ids = sorted(set(ra) & set(rb))
    only_a = sum(
        1 for i in ids
        if ra[i]["outcome"] == CORRECT and rb[i]["outcome"] != CORRECT
    )
    only_b = sum(
        1 for i in ids
        if rb[i]["outcome"] == CORRECT and ra[i]["outcome"] != CORRECT
    )
    disagree = sum(
        1 for i in ids
        if ra[i]["obtained"] is not None and rb[i]["obtained"] is not None
        and ra[i]["obtained"] != rb[i]["obtained"]
    )
    return {
        "pairs": len(ids),
        "only_first_correct": only_a,
        "only_second_correct": only_b,
        "both_correct": sum(
            1 for i in ids
            if ra[i]["outcome"] == CORRECT and rb[i]["outcome"] == CORRECT
        ),
        "decision_disagreements": disagree,
        "p_mcnemar_exact": stats.mcnemar_exact(only_a, only_b),
    }


def stability(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Flips across repetitions of the same case (main + reps)."""
    seen: dict[str, list[str | None]] = {}

    for run in runs:
        for r in run["cases"]:
            seen.setdefault(r["id"], []).append(r["obtained"])

    common = {i: v for i, v in seen.items() if len(v) == len(runs)}
    flips = sum(1 for v in common.values() if len(set(v)) > 1)
    return {
        "runs": len(runs),
        "cases_in_all_runs": len(common),
        "cases_with_flip": flips,
        "flip_rate": round(flips / len(common), 4) if common else None,
    }


def decide(
    summary_a: dict[str, Any],
    summary_b: dict[str, Any],
    crit_a: dict[str, Any],
    crit_b: dict[str, Any],
) -> dict[str, Any]:
    """Protocol v2. McNemar plays NO part here (descriptive only)."""
    steps: list[str] = []
    a_ok, b_ok = crit_a["passes"], crit_b["passes"]

    if not a_ok and not b_ok:
        return {
            "winner": "NONE",
            "steps": ["nenhum braco cumpre C1-C6: apenas apresentar resultados"],
        }

    if a_ok != b_ok:
        winner = "A" if a_ok else "B"
        return {
            "winner": winner,
            "steps": [f"somente o braco {winner} cumpre C1-C6"],
        }

    steps.append("ambos cumprem C1-C6: desempate em ordem")
    ea, eb = summary_a["errors_all"], summary_b["errors_all"]

    if ea != eb:
        winner = "A" if ea < eb else "B"
        steps.append(f"desempate (a) menos erros nos 168 ({ea} x {eb}): {winner}")
        return {"winner": winner, "steps": steps, "tie_broken": True}

    for key in ("p95", "p50"):
        la = summary_a["latency_ms_all"][key]
        lb = summary_b["latency_ms_all"][key]

        if la is not None and lb is not None and la != lb:
            winner = "A" if la < lb else "B"
            steps.append(f"desempate (b) menor {key} de latencia: {winner}")
            return {"winner": winner, "steps": steps, "tie_broken": True}

    steps.append("desempate (c): prevalece A (instruct)")
    return {"winner": "A", "steps": steps, "tie_broken": True}


def build_comparison(
    main_a: Path,
    main_b: Path,
    order_c: Path | None = None,
    reps_a: Sequence[Path] = (),
    reps_b: Sequence[Path] = (),
) -> dict[str, Any]:
    ra, rb = load_run(main_a), load_run(main_b)
    sa, sb = summarize(ra), summarize(rb)
    extra: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    extra_runs: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}

    if order_c is not None:
        rc = load_run(order_c)
        extra_runs["A"].append(rc)
        extra["A"].append(summarize(rc))

    for folder in reps_a:
        r = load_run(folder)
        extra_runs["A"].append(r)
        extra["A"].append(summarize(r))

    for folder in reps_b:
        r = load_run(folder)
        extra_runs["B"].append(r)
        extra["B"].append(summarize(r))

    crit_a = evaluate_criteria(
        sa,
        sum(s["unwarranted_gating"] for s in extra["A"]),
        sum(s["failclosed_violations"] for s in extra["A"]),
    )
    crit_b = evaluate_criteria(
        sb,
        sum(s["unwarranted_gating"] for s in extra["B"]),
        sum(s["failclosed_violations"] for s in extra["B"]),
    )
    pair = paired_gating(ra, rb)
    report: dict[str, Any] = {
        "arms": {"A": sa, "B": sb},
        "extra_runs": {k: v for k, v in extra.items()},
        "criteria": {"A": crit_a, "B": crit_b},
        "paired_A_vs_B": pair,
        "decision": decide(sa, sb, crit_a, crit_b),
        "notes": [
            "McNemar e apenas descritivo (protocolo v2); nao decide",
            "poder estatistico limitado: ver PROTOCOLO_G3_v2.md",
            "so metricas; nenhum texto livre do modelo foi gravado",
        ],
    }

    if order_c is not None:
        report["order_effect_A_vs_C"] = paired_gating(ra, load_run(order_c))

    for arm, runs, main in (("A", extra_runs["A"], ra), ("B", extra_runs["B"], rb)):
        subset_runs = [r for r in runs if r["header"]["rep"] != "order2"]

        if subset_runs:
            ids = {c["id"] for c in subset_runs[0]["cases"]}
            trimmed = dict(main)
            trimmed["cases"] = [c for c in main["cases"] if c["id"] in ids]
            report[f"stability_{arm}"] = stability([trimmed, *subset_runs])

    return report


def _fmt(value: Any) -> str:
    return "SEM AMOSTRA" if value is None else str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Comparativo G3 (somente metricas)", ""]

    for arm in ("A", "B"):
        s = report["arms"][arm]
        c = report["criteria"][arm]
        lines += [
            f"## Braco {arm}",
            f"- casos {s['n_cases']}/{s['planned']} ({s['status']}); "
            f"gate {s['gating_correct']}/{s['gating_n']} corretos, "
            f"IC95 exato {s['gating_accuracy_ci95']}",
            f"- acoes indevidas {s['unwarranted_gating']}; perdidas "
            f"{s['missed_gating']}/{s['expected_mark_gating']}; invalidas "
            f"{s['invalid_total']} (truncadas {s['truncated']}); "
            f"fail-closed {s['failclosed_violations']}/{s['abstain_n']}",
            f"- latencia ms p50/p95/max: {s['latency_ms_all']['p50']}/"
            f"{s['latency_ms_all']['p95']}/{s['latency_ms_all']['max']}; "
            f"frio {s['latency_ms_cold']['max']}",
            f"- tokens saida media/p95/max: {s['output_tokens']['mean']}/"
            f"{s['output_tokens']['p95']}/{s['output_tokens']['max']}",
            f"- latencia (ms) p50/p95/max: {s['latency_ms_all']['p50']}/"
            f"{s['latency_ms_all']['p95']}/{s['latency_ms_all']['max']}; "
            f"erros nos {s['n_cases']}: {s['errors_all']}",
            f"- VRAM MiB antes/pico/depois: "
            f"{_fmt(s['resources']['vram_mib']['before'])}/"
            f"{_fmt(s['resources']['vram_mib']['peak'])}/"
            f"{_fmt(s['resources']['vram_mib']['after'])}",
            f"- RAM livre GiB antes/minimo/depois: "
            f"{_fmt(s['resources']['ram_free_gib']['before'])}/"
            f"{_fmt(s['resources']['ram_free_gib']['min'])}/"
            f"{_fmt(s['resources']['ram_free_gib']['after'])}",
            f"- criterios: {c['checks']} -> "
            f"{'APROVADO' if c['passes'] else 'REPROVADO'}",
            "",
        ]

    p = report["paired_A_vs_B"]
    lines += [
        "## Pareado A x B (casos de gate) - DESCRITIVO, nao decide",
        f"- pares {p['pairs']}; so A certo {p['only_first_correct']}; "
        f"so B certo {p['only_second_correct']}; McNemar exato "
        f"p={p['p_mcnemar_exact']:.4f}",
        "",
        "## Decisao pre-registrada",
        f"- vencedor: {report['decision']['winner']}",
    ]
    lines += [f"- {step}" for step in report["decision"]["steps"]]
    lines += ["", *[f"- nota: {n}" for n in report["notes"]], ""]
    return "\n".join(lines)
