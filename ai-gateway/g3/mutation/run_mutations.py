"""
Mutation check: every mutant is a one-line source change applied to a COPY of
the tree; the unittest suite must FAIL (mutant killed). A mutant whose
`old` text is not found exactly once is reported INVALID, never "killed".

    python mutation/run_mutations.py            (from g3-prep)
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (id, file, old, new, what it breaks)
MUTANTS = [
    ("M01", "g3eval/gen_v3.py",
     'if status == "aberto" and due < business:',
     'if status == "aberto" and due <= business:',
     "generator rule: strict < becomes <="),
    ("M02", "g3eval/verify_v3.py",
     'if status == "aberto" and delta >= 1:',
     'if status == "aberto" and delta >= 0:',
     "verifier: due today counted as overdue"),
    ("M03", "g3eval/verify_v3.py",
     "y = year - (1 if month <= 2 else 0)",
     "y = year - (1 if month <= 1 else 0)",
     "days_from_civil: leap-day month handling"),
    ("M04", "g3eval/verify_v3.py",
     "return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0",
     "return year % 4 == 0",
     "leap-year rule ignores centuries"),
    ("M05", "g3eval/verify_v3.py",
     "if delta != stated:", "if False:",
     "contradictory days_after_due no longer abstains"),
    ("M06", "g3eval/verify_v3.py",
     "dev_triples and triple in dev_triples", "False",
     "overlap with v1/v2 not detected"),
    ("M07", "g3eval/verify_v3.py",
     "if triple in PROMPT_TRIPLES or (", "if (",
     "overlap with prompt examples not detected"),
    ("M08", "g3eval/evaluator.py",
     "return UNWARRANTED_ACTION, decision, length, None",
     "return MISSED_OVERDUE, decision, length, None",
     "unwarranted action counted as missed"),
    ("M09", "g3eval/evaluator.py",
     'if done_reason == "length":', "if False:",
     "truncated answer accepted as an answer"),
    ("M10", "g3eval/evaluator.py",
     'done = {r["id"] for r in rows[1:] if r.get("type") == CASE_TYPE}',
     "done = set()",
     "resume re-sends finished cases"),
    ("M11", "g3eval/evaluator.py",
     "return any(start <= now < end for start, end in self.windows)",
     "return any(start < now < end for start, end in self.windows)",
     "blackout start boundary exclusive"),
    ("M12", "g3eval/evaluator.py",
     "return any(start <= now < end for start, end in self.windows)",
     "return any(start <= now <= end for start, end in self.windows)",
     "blackout end boundary inclusive"),
    ("M13", "g3eval/evaluator.py",
     "and free >= floor  # type: ignore[operator]",
     "and free > floor  # type: ignore[operator]",
     "memory floor off by one"),
    ("M14", "g3eval/evaluator.py",
     "elif digest != spec.arm.digest:", "elif False:",
     "model digest not checked"),
    ("M15", "g3eval/evaluator.py",
     "if version != frozen.EXPECTED_OLLAMA_VERSION:", "if False:",
     "runtime version not checked"),
    ("M16", "g3eval/evaluator.py",
     "consecutive = consecutive + 1 if failed else 0",
     "consecutive = consecutive + 1 if failed else consecutive",
     "failure counter never resets"),
    ("M17", "g3eval/evaluator.py",
     "if digest != expected_sha256.strip().lower():", "if False:",
     "sealed dataset hash not enforced"),
    ("M18", "g3eval/evaluator.py",
     "reason_length=rlen,",
     "reason_length=rlen, reason_text=response.content,",
     "free text of the model leaks into metrics"),
    ("M19", "g3eval/evaluator.py",
     "think=spec.arm.think,", "think=True,",
     "non-thinking arm receives think"),
    ("M20", "g3eval/evaluator.py",
     "random.Random(order_seed).shuffle(ordered)",
     "random.Random(0).shuffle(ordered)",
     "order seed ignored"),
    ("M21", "g3eval/evaluator.py",
     'raise ConcurrencyError("concurrency is fixed at 1")', "pass",
     "concurrency guard removed"),
    ("M22", "g3eval/evaluator.py",
     "(calls_this_session == 0 and not done)",
     "(False and not done)",
     "first call no longer flagged cold"),
    ("M23", "g3eval/evaluator.py",
     'if depth < len(groups[key]) and len(picked) < size:',
     'if depth < len(groups[key]):',
     "stability subset ignores its size"),
    ("M24", "g3eval/evaluator.py",
     '"temperature": 0.6,', '"temperature": 0.7,',
     "sampling changed"),
    ("M25", "g3eval/transport.py",
     "if not REAL_TRANSPORT_BUILD_ENABLED:", "if False:",
     "build lock of the real transport removed"),
    ("M26", "g3eval/transport.py",
     "if enabled is not True:", "if False:",
     "explicit flag no longer required"),
    ("M27", "g3eval/transport.py",
     'if arm_id not in doc["arms"]:', "if False:",
     "authorization not tied to the arm"),
    ("M28", "g3eval/transport.py",
     "if not issued <= now < expires:", "if not issued <= now <= expires:",
     "authorization valid at its expiry instant"),
    ("M29", "g3eval/stats.py",
     "return min(1.0, 2 * tail)", "return min(1.0, tail)",
     "McNemar p-value one-sided"),
    ("M30", "g3eval/stats.py",
     "rank = max(1, math.ceil(pct / 100 * len(ordered)))",
     "rank = max(1, math.floor(pct / 100 * len(ordered)))",
     "percentile rank rounds down"),
    ("M31", "g3eval/report.py",
     "MAX_LATENCY_MS = 120_000", "MAX_LATENCY_MS = 180_000",
     "latency limit loosened"),
    ("M32", "g3eval/report.py",
     "MAX_UNWARRANTED = 0 ", "MAX_UNWARRANTED = 1 ",
     "one unwarranted action tolerated"),
    ("M33", "g3eval/report.py",
     'failclosed = summary["failclosed_violations"] + rep_failclosed',
     'failclosed = summary["failclosed_violations"]',
     "C6 ignores the 2nd-order run and repetitions"),
    ("M34", "g3eval/report.py",
     "MAX_FAILCLOSED = 0 ", "MAX_FAILCLOSED = 1 ",
     "C6 tolerates one mark_overdue in the abstention cases"),
    ("M37", "g3eval/report.py",
     '"C6_failclosed_zero": failclosed <= MAX_FAILCLOSED,',
     '"C6_failclosed_zero": True,',
     "C6 removed from the criteria"),
    ("M38", "g3eval/report.py",
     'winner = "A" if ea < eb else "B"', 'winner = "A" if ea > eb else "B"',
     "tie-break (a) prefers MORE errors"),
    ("M39", "g3eval/report.py",
     'for key in ("p95", "p50"):', 'for key in ("p50", "p95"):',
     "tie-break (b) compares p50 before p95"),
    ("M40", "g3eval/report.py",
     'return {"winner": "A", "steps": steps, "tie_broken": True}',
     'return {"winner": "B", "steps": steps, "tie_broken": True}',
     "tie-break (c) favours the thinking arm"),
    ("M41", "g3eval/report.py",
     "count(rows, MISSED_OVERDUE, UNWARRANTED_ACTION, *NO_ANSWER)",
     "count(rows, MISSED_OVERDUE, UNWARRANTED_ACTION)",
     "errors_all forgets answers that are not usable"),
    ("M42", "g3eval/report.py",
     'winner = "A" if a_ok else "B"', 'winner = "B" if a_ok else "A"',
     "single passing arm swapped"),
    ("M43", "g3eval/report.py",
     "apenas apresentar resultados", "escolher o melhor",
     "no-pass outcome stops being 'only present results'"),
    ("M44", "g3eval/report.py",
     '"peak": max(vram) if vram else None,',
     '"peak": min(vram) if vram else None,',
     "VRAM peak wrong"),
    ("M45", "g3eval/evaluator.py",
     "if release is None:", "if False:",
     "run allowed without release authorization"),
    ("M46", "g3eval/evaluator.py",
     "for flag in _RELEASE_FLAGS:", "for flag in ():",
     "release conditions (24 h, G1, express) not required"),
    ("M47", "g3eval/evaluator.py",
     "if not issued <= now < expires:", "if not issued <= now <= expires:",
     "release valid at its expiry instant"),
    ("M35", "g3eval/frozen.py",
     '"Rule: answer mark_overdue only when status is \'aberto\' AND "',
     '"Rule: answer mark_overdue when status is \'aberto\' AND "',
     "frozen prompt edited"),
    ("M36", "g3eval/plan.py",
     '"A": {"nominal": 3.5, "worst": 10.0},',
     '"A": {"nominal": 4.5, "worst": 10.0},',
     "time estimate changed"),
]


def _writable(func, path, _exc):  # Windows read-only files
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _copy_tree(dst: Path) -> None:
    for name in ("g3eval", "tests", "data", "sealed", "config"):
        shutil.copytree(
            ROOT / name, dst / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )


def _run_tests(tree: Path) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=f"{tree}{os.pathsep}{tree / 'tests'}",
               PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=tree, env=env, capture_output=True, text=True, timeout=300,
    )
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:]
    return proc.returncode, " ".join(tail)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="g3mut-"))
    lines = []
    survived = invalid = killed = 0

    try:
        base = work / "base"
        base.mkdir()
        _copy_tree(base)
        code, tail = _run_tests(base)
        lines.append(f"BASELINE exit={code} {tail}")

        if code != 0:
            print("\n".join(lines))
            return 2

        for mid, rel, old, new, what in MUTANTS:
            tree = work / mid
            tree.mkdir()
            _copy_tree(tree)
            target = tree / rel
            text = target.read_text(encoding="utf-8")

            if text.count(old) != 1:
                invalid += 1
                lines.append(f"{mid} INVALID (found {text.count(old)}x) {what}")
                continue

            os.chmod(target, stat.S_IWRITE)
            target.write_text(text.replace(old, new), encoding="utf-8")

            try:
                code, tail = _run_tests(tree)
            except subprocess.TimeoutExpired:
                code, tail = 1, "timeout (counts as killed: suite hangs)"

            if code != 0:
                killed += 1
                lines.append(f"{mid} KILLED   exit={code} {what} | {tail}")
            else:
                survived += 1
                lines.append(f"{mid} SURVIVED exit=0 {what}")

            shutil.rmtree(tree, onerror=_writable)

        lines.append(
            f"TOTAL mutants={len(MUTANTS)} killed={killed} "
            f"survived={survived} invalid={invalid}"
        )
    finally:
        shutil.rmtree(work, onerror=_writable)

    out = "\n".join(lines)
    print(out)
    (ROOT / "mutation" / "RESULTS.txt").write_text(out + "\n", encoding="utf-8")
    return 0 if survived == 0 and invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
