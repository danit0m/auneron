"""
Prompt v2 do shadow de vencidos e conjunto de PROVA v2 (selado).

Tudo offline: nenhum modelo, nenhum banco. O v1 e o conjunto de AJUSTE;
o v2 e a PROVA. O prompt e o do shadow, usado identico pelo avaliador.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services import overdue_detection_shadow as shadow
from evals.overdue_shadow import generate_cases as gen
from evals.overdue_shadow import generate_cases_v2 as gen2
from scripts import eval_overdue_shadow as ev

V1_SHA256 = (
    "80bd6f7c53b1c6ffc8cfcc1276bc3d392d896a26c9b210b8d14baa1618e16a01"
)
V2_SHA256 = (
    "609c8186d2836df69760d211f036c30c15feb86f8fde11bcb012753fcd1b10ea"
)


def independent_label(status: str, due_text: str, business_text: str) -> str:
    """Literal transcription of the rule (strict)."""
    due = date.fromisoformat(due_text)
    business = date.fromisoformat(business_text)
    return (
        "mark_overdue" if status == "aberto" and due < business
        else "no_action"
    )


def _triples(path: Path) -> set[tuple[str, str, str]]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return {(c["status"], c["due_date"], c["business_date"]) for c in cases}


def _prompt_examples() -> list[tuple[dict, str]]:
    found = []

    for line in shadow.INSTRUCTIONS.splitlines():
        if line.startswith("{") and " => " in line:
            raw, decision = line.rsplit(" => ", 1)
            found.append((json.loads(raw), decision))

    return found


# ---------------------------------------------------------------- data


def test_v1_remains_the_unchanged_tuning_set():
    assert gen.sha256_hex(gen.CASES_PATH.read_bytes()) == V1_SHA256


def test_v2_is_deterministic_sealed_and_matches_committed_file():
    first = gen2.render(gen2.build_dataset())

    assert first == gen2.render(gen2.build_dataset())
    assert gen2.CASES_PATH.read_bytes() == first
    assert gen2.sha256_hex(first) == V2_SHA256
    assert b"\r" not in first and first.endswith(b"\n")
    assert gen2.SEED != gen.SEED
    assert gen2.CASES_PATH != gen.CASES_PATH


def test_v2_labels_follow_the_rule_and_set_is_balanced():
    document = json.loads(gen2.CASES_PATH.read_text(encoding="utf-8"))
    cases = document["cases"]

    assert document["dataset_version"] == gen2.DATASET_VERSION
    assert document["seed"] == gen2.SEED
    assert len(cases) == 80
    assert document["counts"] == {"mark_overdue": 40, "no_action": 40}
    assert len({c["id"] for c in cases}) == 80
    assert len(_triples(gen2.CASES_PATH)) == 80

    for case in cases:
        assert case["expected"] == independent_label(
            case["status"], case["due_date"], case["business_date"]
        ), case["id"]
        assert case["days_after_due"] == (
            date.fromisoformat(case["business_date"])
            - date.fromisoformat(case["due_date"])
        ).days
        assert set(case) == {
            "id", "category", "status", "due_date", "business_date",
            "days_after_due", "expected",
        }


def test_v2_reinforces_the_boundaries():
    cases = json.loads(gen2.CASES_PATH.read_text(encoding="utf-8"))["cases"]
    open_by_days: dict[int, list[dict]] = {}

    for case in cases:
        if case["status"] == "aberto":
            open_by_days.setdefault(case["days_after_due"], []).append(case)

    for days, minimum in ((0, 10), (-1, 5), (-2, 5), (1, 10), (2, 8)):
        assert len(open_by_days[days]) >= minimum, days

    for days in (0, -1, -2):
        assert all(c["expected"] == "no_action" for c in open_by_days[days])

    for days in (1, 2):
        assert all(
            c["expected"] == "mark_overdue" for c in open_by_days[days]
        )

    assert {c["status"] for c in cases} == {"aberto", "atrasado", "pago"}


def test_v1_and_v2_do_not_overlap():
    v1_set = _triples(gen.CASES_PATH)
    v2_set = _triples(gen2.CASES_PATH)

    assert v1_set.isdisjoint(v2_set)
    assert gen2.v1_keys() == {
        (s, date.fromisoformat(d), date.fromisoformat(b))
        for s, d, b in v1_set
    }


def test_v2_passes_the_evaluator_seal_and_scripted_oracle(tmp_path):
    cases, digest = ev.load_sealed_cases(gen2.CASES_PATH, V2_SHA256)

    assert len(cases) == 80 and digest == V2_SHA256

    with pytest.raises(ev.EvalRefused):
        ev.load_sealed_cases(gen2.CASES_PATH, V1_SHA256)

    code, report = ev.evaluate(
        cases_path=gen2.CASES_PATH,
        expected_sha256=V2_SHA256,
        out_dir=tmp_path / "out",
        transport=ev.ScriptedOracleTransport(),
        free_memory_gib=lambda: 14.0,
        mode="scripted_offline",
    )

    assert code == ev.EXIT_OK
    assert report["metrics"]["correct"] == 80
    assert report["config"]["dataset_sha256"] == V2_SHA256


# -------------------------------------------------------------- prompt


def test_report_records_prompt_version_and_hash(tmp_path):
    code, report = ev.evaluate(
        cases_path=gen2.CASES_PATH,
        expected_sha256=V2_SHA256,
        out_dir=tmp_path / "out",
        transport=ev.ScriptedOracleTransport(),
        free_memory_gib=lambda: 14.0,
        mode="scripted_offline",
    )
    config = report["config"]

    assert config["prompt_version"] == shadow.PROMPT_VERSION
    assert shadow.PROMPT_VERSION == "overdue_shadow_v2"
    assert shadow.SHADOW_PURPOSE == shadow.PROMPT_VERSION
    assert config["prompt_template_id"] == "overdue-shadow-prompt-v2-think"
    assert config["prompt_schema_sha256"] == ev.prompt_schema_hash()

    summary = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "overdue_shadow_v2" in summary
    assert ev.prompt_schema_hash() in summary


def test_prompt_hash_changes_when_the_instructions_change(monkeypatch):
    base = ev.prompt_schema_hash()
    monkeypatch.setattr(shadow, "INSTRUCTIONS", shadow.INSTRUCTIONS + " ")

    assert ev.prompt_schema_hash() != base


def test_prompt_v2_states_the_date_rule_explicitly():
    text = shadow.INSTRUCTIONS

    assert "days_after_due" in text
    assert "1 or more" in text
    assert "due today" in text and "NOT late" in text
    assert "negative" in text
    assert "'aberto'" in text and "'pago'" in text and "'atrasado'" in text
    assert shadow.OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(shadow.OUTPUT_SCHEMA["properties"]) == {"decision", "reason"}
    assert shadow.OUTPUT_SCHEMA["required"] == ["decision", "reason"]


def test_prompt_and_input_carry_no_precomputed_answer_field():
    payload = json.loads(
        shadow.build_model_input(
            type("O", (), {
                "due_date": "2024-01-01", "status": "aberto",
                "business_date": "2024-01-10",
            })()
        )
    )

    assert set(payload) == {
        "due_date", "status", "business_date", "days_after_due",
    }

    for key in payload:
        assert not any(
            word in key
            for word in ("overdue", "late", "expected", "decision",
                         "answer", "label")
        ), key

    examples = _prompt_examples()

    assert len(examples) >= 4

    for example_input, _ in examples:
        assert set(example_input) == set(payload)


def test_prompt_examples_are_correct_balanced_and_not_dataset_cases():
    examples = _prompt_examples()
    seen = _triples(gen.CASES_PATH) | _triples(gen2.CASES_PATH)

    assert {d for _, d in examples} == {"mark_overdue", "no_action"}

    for payload, decision in examples:
        due = date.fromisoformat(payload["due_date"])
        business = date.fromisoformat(payload["business_date"])

        assert payload["days_after_due"] == (business - due).days
        assert decision == independent_label(
            payload["status"], payload["due_date"], payload["business_date"]
        )
        assert (
            payload["status"], payload["due_date"], payload["business_date"]
        ) not in seen
