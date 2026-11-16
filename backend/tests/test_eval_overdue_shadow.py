"""
Avaliacao independente do shadow de vencidos: gerador selado e executor.

Tudo offline: transporte roteirizado, nenhum modelo, nenhum banco (o
modulo do shadow so e importado). Cobre o gerador deterministico e a
regra de rotulo, a selagem, o pre-voo, cada categoria de metrica, a
fidelidade ao prompt/entrada do shadow e a ausencia do texto `reason`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services import overdue_detection_shadow as shadow
from evals.overdue_shadow import generate_cases as gen
from scripts import eval_overdue_shadow as ev

BACKEND = Path(__file__).resolve().parents[1]
SERVICE_FILE = BACKEND / "app" / "services" / "overdue_detection_service.py"
DIGEST = ev.APPROVED_DIGEST
VERSION = ev.EXPECTED_OLLAMA_VERSION
SECRET_REASON = "SEGREDO-NAO-GRAVAR-123"


# ----------------------------------------------------------------------
# generator and label rule
# ----------------------------------------------------------------------


def independent_label(case: dict) -> str:
    """Second, literal transcription of the documented rule."""
    due = date.fromisoformat(case["due_date"])
    business = date.fromisoformat(case["business_date"])
    return (
        "mark_overdue"
        if case["status"] == "aberto" and due < business
        else "no_action"
    )


def test_same_seed_same_bytes_and_hash_and_matches_committed_file():
    first = gen.render(gen.build_dataset(gen.SEED))
    second = gen.render(gen.build_dataset(gen.SEED))

    assert first == second
    assert gen.sha256_hex(first) == gen.sha256_hex(second)
    assert gen.CASES_PATH.read_bytes() == first
    assert b"\r" not in first and first.endswith(b"\n")


def test_other_seed_changes_the_dataset():
    assert gen.render(gen.build_dataset(1)) != gen.render(
        gen.build_dataset(gen.SEED)
    )


def test_labels_match_the_rule_for_every_committed_case():
    document = json.loads(gen.CASES_PATH.read_text(encoding="utf-8"))

    assert document["dataset_version"] == gen.DATASET_VERSION
    assert document["seed"] == gen.SEED

    for case in document["cases"]:
        assert case["expected"] == independent_label(case), case["id"]
        assert case["days_after_due"] == (
            date.fromisoformat(case["business_date"])
            - date.fromisoformat(case["due_date"])
        ).days


def test_rule_is_the_one_in_the_deterministic_service():
    source = SERVICE_FILE.read_text(encoding="utf-8")

    assert 'Account.status == "aberto"' in source
    assert "Account.vencimento < effective_today" in source


def test_dataset_is_balanced_unique_and_covers_the_required_edges():
    document = json.loads(gen.CASES_PATH.read_text(encoding="utf-8"))
    cases = document["cases"]
    categories = {case["category"] for case in cases}

    assert len(cases) == 80
    assert document["counts"] == {"mark_overdue": 40, "no_action": 40}
    assert len({case["id"] for case in cases}) == 80
    assert len(
        {(c["status"], c["due_date"], c["business_date"]) for c in cases}
    ) == 80
    assert {
        "not_due_future", "due_today", "paid_past_due", "paid_today",
        "paid_future", "already_overdue_past", "already_overdue_today",
        "already_overdue_future", "one_day", "few_days", "many_days",
        "extreme",
    } <= categories
    assert {c["status"] for c in cases} == {"aberto", "atrasado", "pago"}
    # leap day, year turn and month turn are present
    assert any("02-29" in c["due_date"] for c in cases)
    assert any(
        c["due_date"] == "2023-12-31" and c["business_date"] == "2024-01-01"
        for c in cases
    )
    # a 1-day late and a due-today case exist and are labelled correctly
    assert any(
        c["days_after_due"] == 1 and c["expected"] == "mark_overdue"
        for c in cases
    )
    assert any(
        c["days_after_due"] == 0 and c["expected"] == "no_action"
        for c in cases
    )


def test_each_origin_of_label_each_branch_of_the_rule():
    due, today = date(2024, 2, 28), date(2024, 2, 29)

    assert gen.expected_decision("aberto", due, today) == "mark_overdue"
    assert gen.expected_decision("aberto", today, today) == "no_action"
    assert gen.expected_decision("pago", due, today) == "no_action"
    assert gen.expected_decision("atrasado", due, today) == "no_action"
    assert gen.expected_decision("aberto", today, due) == "no_action"


def test_dataset_carries_no_personal_data_fields():
    document = json.loads(gen.CASES_PATH.read_text(encoding="utf-8"))

    for case in document["cases"]:
        assert set(case) == {
            "id", "category", "status", "due_date", "business_date",
            "days_after_due", "expected",
        }


# ----------------------------------------------------------------------
# scripted runtime
# ----------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedRuntime:
    """Scripted transport. `behaviour(index, payload)` decides each chat."""

    def __init__(
        self,
        behaviour,
        *,
        clock: FakeClock | None = None,
        digest: str = DIGEST,
        version: str = VERSION,
        step_s: float = 0.1,
    ) -> None:
        self.behaviour = behaviour
        self.clock = clock
        self.digest = digest
        self.version = version
        self.step_s = step_s
        self.calls: list[tuple[str, str]] = []
        self.chats: list[dict] = []

    def __call__(self, method, path, body, timeout_s):
        self.calls.append((method, path))

        if path == "/api/version":
            return 200, json.dumps({"version": self.version}).encode()

        if path == "/api/tags":
            return 200, json.dumps(
                {"models": [{"name": ev.MODEL_ID, "digest": self.digest}]}
            ).encode()

        sent = json.loads(body.decode("utf-8"))
        self.chats.append(sent)
        payload = json.loads(sent["messages"][1]["content"])
        index = len(self.chats) - 1

        if self.clock is not None:
            self.clock.now += self.step_s * (index + 1)

        return self.behaviour(index, payload)

    @property
    def chat_calls(self) -> int:
        return sum(1 for _, path in self.calls if path == "/api/chat")


def answer(decision: str, reason: str = SECRET_REASON, **extra):
    document = {
        "model": ev.MODEL_ID,
        "done": True,
        "message": {
            "content": json.dumps({"decision": decision, "reason": reason})
        },
        "prompt_eval_count": 40,
        "eval_count": 9,
    }
    document.update(extra)
    return 200, json.dumps(document).encode()


def raw_answer(content: str, **extra):
    document = {
        "model": ev.MODEL_ID,
        "done": True,
        "message": {"content": content},
        "prompt_eval_count": 40,
        "eval_count": 9,
    }
    document.update(extra)
    return 200, json.dumps(document).encode()


def rule(payload) -> str:
    return (
        "mark_overdue"
        if payload["status"] == "aberto" and payload["days_after_due"] > 0
        else "no_action"
    )


def small_dataset(tmp_path: Path, rows) -> tuple[Path, str]:
    """rows: (status, due, business). Labels from the rule."""
    cases = []

    for index, (status, due, business) in enumerate(rows, start=1):
        d, b = date.fromisoformat(due), date.fromisoformat(business)
        cases.append(
            {
                "id": f"T-{index:02d}",
                "category": "t",
                "status": status,
                "due_date": due,
                "business_date": business,
                "days_after_due": (b - d).days,
                "expected": gen.expected_decision(status, d, b),
            }
        )

    data = gen.render({"cases": cases})
    path = tmp_path / "cases.json"
    path.write_bytes(data)
    return path, gen.sha256_hex(data)


ROWS = [
    ("aberto", "2024-01-01", "2024-01-05"),   # mark_overdue
    ("aberto", "2024-01-05", "2024-01-05"),   # no_action
    ("pago", "2024-01-01", "2024-01-05"),     # no_action
    ("atrasado", "2024-01-01", "2024-01-05"), # no_action
    ("aberto", "2024-02-01", "2024-01-05"),   # no_action
    ("aberto", "2023-12-31", "2024-01-01"),   # mark_overdue
]


def run(tmp_path, runtime, *, rows=ROWS, memory=16.0, clock=None, sha=None):
    path, digest = small_dataset(tmp_path, rows)
    out = tmp_path / "out"
    code, report = ev.evaluate(
        cases_path=path,
        expected_sha256=sha or digest,
        out_dir=out,
        transport=runtime,
        free_memory_gib=lambda: memory,
        clock=clock or FakeClock(),
    )
    return code, report, out


# ----------------------------------------------------------------------
# metrics: each category counted on its own
# ----------------------------------------------------------------------


def test_every_metric_category_is_counted_separately(tmp_path):
    clock = FakeClock()

    def behaviour(index, payload):
        if index == 0:
            return answer("no_action")          # missed_overdue
        if index == 1:
            return answer("mark_overdue")       # unwarranted_action
        if index == 2:
            return raw_answer("not json at all")  # invalid
        if index == 3:
            raise TimeoutError("deadline")      # timeout
        if index == 4:
            raise ConnectionResetError("reset")  # transport error
        return answer("mark_overdue")           # idx 5: correct

    runtime = ScriptedRuntime(behaviour, clock=clock)
    code, report, out = run(tmp_path, runtime, clock=clock)
    metrics = report["metrics"]

    assert code == ev.EXIT_OK
    assert metrics["total_cases"] == 6
    assert metrics["missed_overdue"] == 1
    assert metrics["unwarranted_actions"] == 1
    assert metrics["invalid_responses"] == 1
    assert metrics["timeouts"] == 1
    assert metrics["transport_errors"] == 1
    assert metrics["correct"] == 1
    assert metrics["not_run"] == 0
    assert metrics["expected_no_action_cases"] == 4
    assert metrics["expected_mark_overdue_cases"] == 2
    assert metrics["unwarranted_action_rate"] == 0.25
    assert metrics["missed_overdue_rate"] == 0.5
    assert metrics["accuracy"] == round(1 / 6, 4)

    by_id = {c["id"]: c for c in report["cases"]}
    assert by_id["T-01"]["obtained"] == "no_action"
    assert by_id["T-01"]["valid"] is True
    assert by_id["T-03"]["valid"] is False
    assert by_id["T-03"]["obtained"] is None
    assert by_id["T-04"]["outcome"] == ev.TIMEOUT
    assert by_id["T-05"]["outcome"] == ev.TRANSPORT_ERROR
    assert (out / "results.json").exists() and (out / "summary.md").exists()


def test_invalid_variants_are_not_valid_answers(tmp_path):
    variants = [
        raw_answer(json.dumps({"decision": "maybe", "reason": "x"})),
        raw_answer(json.dumps({"decision": "no_action"})),
        raw_answer(
            json.dumps({"decision": "no_action", "reason": "x", "z": 1})
        ),
        raw_answer('{"decision": "no_action", "decision": "mark_overdue",'
                   ' "reason": "x"}'),
        answer("no_action", done_reason="length"),
        raw_answer("```json\n{}\n```"),
    ]

    runtime = ScriptedRuntime(lambda i, p: variants[i])
    rows = [
        ("aberto", f"2024-01-0{n}", "2024-02-01") for n in range(1, 7)
    ]
    code, report, _ = run(tmp_path, runtime, rows=rows)

    assert report["metrics"]["invalid_responses"] == 6
    assert report["metrics"]["correct"] == 0
    assert all(case["valid"] is False for case in report["cases"])


def test_latency_percentiles_max_and_total(tmp_path):
    clock = FakeClock()
    runtime = ScriptedRuntime(
        lambda i, p: answer(rule(p)), clock=clock, step_s=1.0
    )
    code, report, out = run(tmp_path, runtime, clock=clock)
    latency = report["metrics"]["latency_ms"]

    # the fake clock advances 1 s * (index + 1) inside each chat
    assert [c["latency_ms"] for c in report["cases"]] == [
        1000, 2000, 3000, 4000, 5000, 6000
    ]
    assert latency == {
        "calls_measured": 6, "p50": 3000, "p95": 6000, "max": 6000,
        "total": 21000, "first_call_ms": 1000,
        "first_call_includes_cold_load": True,
        "excluding_first": {
            "calls_measured": 5, "p50": 4000, "p95": 6000, "max": 6000,
            "total": 20000,
        },
    }
    assert ev.CALL_TIMEOUT_SECONDS == 120
    assert report["config"]["timeout_seconds"] == 120
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "inclui carregamento a frio" in summary
    assert "sem o 1o caso" in summary
    assert report["metrics"]["correct"] == 6


def test_run_aborts_after_consecutive_transport_failures(tmp_path):
    def behaviour(index, payload):
        raise TimeoutError("deadline")

    runtime = ScriptedRuntime(behaviour)
    rows = [
        ("aberto", f"2024-01-0{n}", "2024-02-01") for n in range(1, 9)
    ]
    code, report, _ = run(tmp_path, runtime, rows=rows)

    assert code == ev.EXIT_INCOMPLETE
    assert report["status"] == "incomplete"
    assert report["metrics"]["timeouts"] == ev.MAX_CONSECUTIVE_FAILURES
    assert report["metrics"]["not_run"] == 8 - ev.MAX_CONSECUTIVE_FAILURES
    assert runtime.chat_calls == ev.MAX_CONSECUTIVE_FAILURES


# ----------------------------------------------------------------------
# fidelity to the shadow
# ----------------------------------------------------------------------


def test_prompt_schema_input_and_options_are_the_shadows(tmp_path):
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))
    code, report, _ = run(tmp_path, runtime)
    first = runtime.chats[0]

    assert first["messages"][0]["content"] == shadow.INSTRUCTIONS
    assert first["format"] == shadow.OUTPUT_SCHEMA
    assert first["options"]["num_predict"] == shadow.MAX_OUTPUT_TOKENS
    assert first["options"]["temperature"] == 0
    assert first["options"]["seed"] == 0
    assert first["model"] == "qwen3:4b"
    assert first["think"] is False and first["stream"] is False

    for sent, (status, due, business) in zip(runtime.chats, ROWS):
        expected_input = shadow.build_model_input(
            type("O", (), {
                "due_date": due, "status": status, "business_date": business,
            })()
        )
        assert sent["messages"][1]["content"] == expected_input

    config = report["config"]
    assert config["prompt_schema_sha256"] == ev.prompt_schema_hash()
    assert config["options"] == first["options"]
    assert config["approved_digest"] == DIGEST
    assert config["observed_ollama_versions"] == [VERSION]
    assert config["attempts_per_case"] == 1
    assert config["max_output_tokens"] == shadow.MAX_OUTPUT_TOKENS


# ----------------------------------------------------------------------
# seal and pre-flight
# ----------------------------------------------------------------------


def test_altered_dataset_is_refused_before_any_prompt(tmp_path, capsys):
    path, digest = small_dataset(tmp_path, ROWS)
    path.write_bytes(path.read_bytes().replace(b"2024-01-05", b"2024-01-06", 1))
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))
    out = tmp_path / "out"

    code, report = ev.evaluate(
        cases_path=path, expected_sha256=digest, out_dir=out,
        transport=runtime, free_memory_gib=lambda: 16.0,
    )

    assert code == ev.EXIT_REFUSED and report is None
    assert "dataset_hash_mismatch" in capsys.readouterr().out
    assert runtime.calls == [] and not out.exists()


def test_wrong_label_with_matching_hash_is_refused(tmp_path, capsys):
    path, _ = small_dataset(tmp_path, ROWS)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["cases"][0]["expected"] = "no_action"
    data = gen.render(document)
    path.write_bytes(data)
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))

    code, _ = ev.evaluate(
        cases_path=path, expected_sha256=gen.sha256_hex(data),
        out_dir=tmp_path / "out", transport=runtime,
        free_memory_gib=lambda: 16.0,
    )

    assert code == ev.EXIT_REFUSED
    assert "dataset_label_inconsistent" in capsys.readouterr().out
    assert runtime.calls == []


def test_committed_dataset_passes_the_seal_with_its_recorded_hash():
    data = gen.CASES_PATH.read_bytes()
    cases, digest = ev.load_sealed_cases(
        gen.CASES_PATH, gen.sha256_hex(data)
    )

    assert len(cases) == 80 and digest == gen.sha256_hex(data)

    with pytest.raises(ev.EvalRefused):
        ev.load_sealed_cases(gen.CASES_PATH, "0" * 64)


@pytest.mark.parametrize("memory", [13.99, 0.0, float("nan"), None])
def test_low_memory_is_refused_without_sending_a_prompt(
    tmp_path, memory, capsys
):
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))
    code, report, out = run(tmp_path, runtime, memory=memory)

    assert code == ev.EXIT_REFUSED and report is None
    assert "low_memory" in capsys.readouterr().out
    assert runtime.chat_calls == 0 and not out.exists()


def test_exactly_14_gib_is_accepted(tmp_path):
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))
    code, report, _ = run(tmp_path, runtime, memory=14.0)

    assert code == ev.EXIT_OK and report["status"] == "complete"


def test_different_digest_is_refused_without_sending_a_prompt(
    tmp_path, capsys
):
    runtime = ScriptedRuntime(
        lambda i, p: answer(rule(p)), digest="ab" * 32
    )
    code, report, out = run(tmp_path, runtime)

    assert code == ev.EXIT_REFUSED
    assert "digest_mismatch" in capsys.readouterr().out
    assert runtime.chat_calls == 0 and not out.exists()


def test_different_ollama_version_is_refused(tmp_path, capsys):
    runtime = ScriptedRuntime(
        lambda i, p: answer(rule(p)), version="0.99.0"
    )
    code, report, _ = run(tmp_path, runtime)

    assert code == ev.EXIT_REFUSED
    assert "ollama_version_mismatch" in capsys.readouterr().out
    assert runtime.chat_calls == 0


def test_digest_change_mid_run_stops_the_run(tmp_path):
    state = {"runtime": None}

    def behaviour(index, payload):
        if index == 1:
            state["runtime"].digest = "cd" * 32
        return answer(rule(payload))

    runtime = ScriptedRuntime(behaviour)
    state["runtime"] = runtime
    code, report, _ = run(tmp_path, runtime)

    assert code == ev.EXIT_INCOMPLETE
    assert report["stop_reason"] == "model_identity_changed"
    assert report["metrics"]["not_run"] == 4
    assert report["metrics"]["correct"] == 2


def test_non_empty_output_directory_is_refused(tmp_path, capsys):
    path, digest = small_dataset(tmp_path, ROWS)
    out = tmp_path / "out"
    out.mkdir()
    (out / "previous.txt").write_text("old")
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))

    code, _ = ev.evaluate(
        cases_path=path, expected_sha256=digest, out_dir=out,
        transport=runtime, free_memory_gib=lambda: 16.0,
    )

    assert code == ev.EXIT_REFUSED
    assert "out_dir_not_empty" in capsys.readouterr().out
    assert runtime.calls == [] and (out / "previous.txt").read_text() == "old"


# ----------------------------------------------------------------------
# reason text never persisted
# ----------------------------------------------------------------------


def test_free_text_reason_is_never_written(tmp_path):
    runtime = ScriptedRuntime(lambda i, p: answer(rule(p)))
    code, report, out = run(tmp_path, runtime)

    for name in ("results.json", "summary.md"):
        assert SECRET_REASON not in (out / name).read_text(encoding="utf-8")

    assert SECRET_REASON not in json.dumps(report)
    assert all(
        case["reason_length"] == len(SECRET_REASON)
        for case in report["cases"]
    )
    assert all("reason" not in case for case in report["cases"])


# ----------------------------------------------------------------------
# offline end-to-end with the committed set
# ----------------------------------------------------------------------


def test_full_committed_set_with_scripted_oracle(tmp_path):
    data = gen.CASES_PATH.read_bytes()
    code, report = ev.evaluate(
        cases_path=gen.CASES_PATH,
        expected_sha256=gen.sha256_hex(data),
        out_dir=tmp_path / "out",
        transport=ev.ScriptedOracleTransport(),
        free_memory_gib=lambda: 14.0,
        mode="scripted_offline",
    )

    assert code == ev.EXIT_OK
    assert report["mode"] == "scripted_offline"
    assert report["metrics"]["correct"] == 80
    assert report["config"]["dataset_sha256"] == gen.sha256_hex(data)


def test_evaluator_is_not_part_of_app():
    assert not (BACKEND / "app" / "scripts").exists()
    assert "eval_overdue_shadow" not in "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (BACKEND / "app").rglob("*.py")
    )
