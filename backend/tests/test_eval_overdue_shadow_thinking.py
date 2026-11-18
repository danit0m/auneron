"""Evaluator with reasoning on: only lengths are kept, never the text.

Offline (scripted transport). Reuses the helpers of test_eval_overdue_shadow.
"""

import json

from scripts import eval_overdue_shadow as ev
from tests.test_eval_overdue_shadow import ScriptedRuntime
from tests.test_eval_overdue_shadow import rule
from tests.test_eval_overdue_shadow import run


SECRET_THINKING = "RACIOCINIO-NAO-GRAVAR-987 mark_overdue"


def chat(content, *, thinking=SECRET_THINKING, done_reason="stop", eval_count=700):
    message = {"content": content}

    if thinking is not None:
        message["thinking"] = thinking

    return (
        200,
        json.dumps(
            {
                "model": ev.MODEL_ID,
                "done": True,
                "done_reason": done_reason,
                "message": message,
                "prompt_eval_count": 40,
                "eval_count": eval_count,
            }
        ).encode(),
    )


def good(decision, **extra):
    return chat(json.dumps({"decision": decision, "reason": "r"}), **extra)


def test_reasoning_text_is_never_written_only_its_length(tmp_path):
    runtime = ScriptedRuntime(lambda i, p: good(rule(p)))
    code, report, out = run(tmp_path, runtime)

    assert code == ev.EXIT_OK

    for name in ("results.json", "summary.md"):
        assert "RACIOCINIO-NAO-GRAVAR" not in (out / name).read_text(
            encoding="utf-8"
        )

    assert "RACIOCINIO-NAO-GRAVAR" not in json.dumps(report)
    assert all(
        c["thinking_length"] == len(SECRET_THINKING) for c in report["cases"]
    )
    assert all(c["output_tokens"] == 700 for c in report["cases"])
    assert report["metrics"]["thinking_chars"]["max"] == len(SECRET_THINKING)
    assert report["config"]["reasoning_text_stored"] is False
    assert report["config"]["think"] is True


def test_empty_content_and_truncation_are_invalid_and_classified(tmp_path):
    def behaviour(index, payload):
        if index == 0:
            return chat("", eval_count=300)

        if index == 1:
            # complete-looking JSON, but cut by the limit: never used
            return good("mark_overdue", done_reason="length", eval_count=2048)

        return good(rule(payload))

    code, report, _ = run(tmp_path, ScriptedRuntime(behaviour))
    first, second = report["cases"][0], report["cases"][1]

    assert first["outcome"] == ev.INVALID
    assert first["error"] == "empty_content"
    assert second["outcome"] == ev.INVALID
    assert second["error"] == "truncated"
    assert second["obtained"] is None
    assert report["metrics"]["truncated_responses"] == 1
    assert report["metrics"]["empty_content_responses"] == 1


def test_thinking_that_looks_like_the_answer_is_not_used(tmp_path):
    answer_like = json.dumps({"decision": "mark_overdue", "reason": "x"})
    runtime = ScriptedRuntime(
        lambda i, p: chat("", thinking=answer_like, eval_count=100)
    )
    code, report, _ = run(tmp_path, runtime)

    assert report["metrics"]["correct"] == 0
    assert all(c["obtained"] is None for c in report["cases"])
    assert all(c["error"] == "empty_content" for c in report["cases"])
