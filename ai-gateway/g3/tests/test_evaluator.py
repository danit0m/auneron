import json
import shutil
import unittest
from datetime import datetime

from g3eval import evaluator as ev
from g3eval import frozen
from g3eval import transport as tr

from helpers import (
    Clock, TempDirCase, SEALED, SEALED_SHA, load_all, small_cases, spec_for,
    make_fake, V1, V1_SHA, RELEASE,
)

SECRET = "SECRET_REASON_TEXT_42"


def wrong(index, request, oracle):
    return {"decision": "no_action" if oracle == "mark_overdue" else "mark_overdue"}


class ClassifyTests(unittest.TestCase):
    def c(self, expected, done, content):
        return ev.classify(expected, done, content)[0::3]

    def ok(self, decision, reason="r"):
        return json.dumps({"decision": decision, "reason": reason})

    def test_three_answer_outcomes(self):
        self.assertEqual(self.c("no_action", "stop", self.ok("no_action")),
                         (ev.CORRECT, None))
        self.assertEqual(self.c("no_action", "stop", self.ok("mark_overdue")),
                         (ev.UNWARRANTED_ACTION, None))
        self.assertEqual(self.c("mark_overdue", "stop", self.ok("no_action")),
                         (ev.MISSED_OVERDUE, None))

    def test_truncated_is_never_an_answer_even_with_valid_content(self):
        self.assertEqual(
            self.c("no_action", "length", self.ok("no_action")),
            (ev.INVALID, "truncated"))

    def test_invalid_shapes(self):
        cases = {
            "": "empty_content",
            "   ": "empty_content",
            "not json": "invalid_json",
            '{"decision": "no_action", "decision": "mark_overdue", '
            '"reason": "x"}': "invalid_json",
            '{"decision": NaN, "reason": "x"}': "invalid_json",
            '{"decision": "no_action"}': "schema_violation",
            '{"decision": "no_action", "reason": "x", "extra": 1}':
                "schema_violation",
            '{"decision": "no_action", "reason": 5}': "schema_violation",
            '[1]': "schema_violation",
            json.dumps({"decision": "no_action", "reason": "x" * 201}):
                "schema_violation",
            self.ok("escalate"): "decision_outside_set",
        }

        for content, code in cases.items():
            self.assertEqual(
                self.c("no_action", "stop", content), (ev.INVALID, code),
                msg=content)

        self.assertEqual(self.c("no_action", "stop", None),
                         (ev.INVALID, "empty_content"))
        self.assertEqual(self.c("no_action", "weird", self.ok("no_action")),
                         (ev.INVALID, "unexpected_done_reason"))

    def test_reason_at_limit_is_valid(self):
        self.assertEqual(
            self.c("no_action", "stop", self.ok("no_action", "x" * 200)),
            (ev.CORRECT, None))


class RunTests(TempDirCase):
    def test_perfect_run_and_no_free_text_stored(self):
        def behaviour(i, r, o):
            return {"reason": SECRET, "thinking_length": 99}

        code, status, t = self.run_arm("a", behaviour=behaviour,
                                       arm=ev.ARM_B)
        self.assertEqual(code, ev.EXIT_OK)
        self.assertEqual(status["status"], "complete")
        text = (self.out("a") / "metrics.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        rows = self.rows("a")
        self.assertEqual(rows[0]["type"], "header")
        self.assertTrue(all(r["outcome"] == ev.CORRECT for r in rows[1:]))
        self.assertTrue(all(r["reason_length"] == len(SECRET) for r in rows[1:]))
        self.assertEqual(rows[1]["thinking_length"], 99)

    def test_record_keys_are_a_closed_whitelist(self):
        self.run_arm("a")
        allowed = {
            "type", "id", "stratum", "category", "seq", "queue_remaining",
            "expected", "obtained", "outcome", "error", "latency_ms",
            "server_total_ms", "load_ms", "cold", "prompt_tokens",
            "output_tokens", "thinking_length", "reason_length",
            "vram_before_mib", "vram_after_mib", "ram_free_gib_before",
            "ram_free_gib_after",
        }

        for r in self.rows("a")[1:]:
            self.assertEqual(set(r), allowed)

    def test_request_is_identical_across_arms_except_model_and_think(self):
        _, _, ta = self.run_arm("a", arm=ev.ARM_A)
        _, _, tb = self.run_arm("b", arm=ev.ARM_B)
        self.assertEqual(len(ta.requests), len(tb.requests))

        for ra, rb in zip(ta.requests, tb.requests):
            self.assertEqual(ra.instructions, frozen.INSTRUCTIONS)
            self.assertEqual(rb.instructions, frozen.INSTRUCTIONS)
            self.assertEqual(ra.output_schema, frozen.OUTPUT_SCHEMA)
            self.assertEqual(ra.input_text, rb.input_text)
            self.assertEqual(ra.options, rb.options)
            self.assertIsNone(ra.think)
            self.assertIs(rb.think, True)

        body_a = tr.build_chat_body(ta.requests[0])
        self.assertNotIn("think", body_a)
        self.assertEqual(body_a["model"], ev.ARM_A.model_id)
        self.assertEqual(body_a["options"]["num_predict"], 4096)
        self.assertEqual(body_a["options"]["temperature"], 0.6)
        self.assertEqual(body_a["options"]["seed"], 0)

    def test_seed_and_order_overrides(self):
        _, _, t = self.run_arm("s", spec_kw={"seed": 2})
        self.assertTrue(all(r.options["seed"] == 2 for r in t.requests))
        base = small_cases(20)
        _, _, t1 = self.run_arm("o1", cases=base)
        _, _, t2 = self.run_arm("o2", cases=base, spec_kw={"order_seed": 2})
        _, _, t3 = self.run_arm("o3", cases=base, spec_kw={"order_seed": 2})
        i1 = [r.input_text for r in t1.requests]
        i2 = [r.input_text for r in t2.requests]
        i3 = [r.input_text for r in t3.requests]
        _, _, t4 = self.run_arm("o4", cases=base, spec_kw={"order_seed": 3})
        self.assertEqual(i2, i3)
        self.assertNotEqual(i2, [r.input_text for r in t4.requests])
        self.assertNotEqual(i1, i2)
        self.assertEqual(sorted(i1), sorted(i2))

    def test_outcomes_unwarranted_and_missed_counted(self):
        code, _, _ = self.run_arm("w", behaviour=wrong,
                                  cases=small_cases(30))
        self.assertEqual(code, ev.EXIT_OK)
        outcomes = {r["outcome"] for r in self.rows("w")[1:]}
        self.assertEqual(outcomes, {ev.UNWARRANTED_ACTION, ev.MISSED_OVERDUE})

    def test_truncation_recorded_as_invalid(self):
        def b(i, r, o):
            return {"done_reason": "length", "tokens": 4096, "content": None}
        self.run_arm("t", arm=ev.ARM_B, behaviour=b,
                     cases=small_cases(4))
        for r in self.rows("t")[1:]:
            self.assertEqual((r["outcome"], r["error"]),
                             (ev.INVALID, "truncated"))
            self.assertEqual(r["output_tokens"], 4096)

    def test_consecutive_failures_stop_the_run(self):
        def b(i, r, o):
            return {"raise_": "timeout"}
        code, status, t = self.run_arm("f", behaviour=b,
                                       cases=small_cases(12))
        self.assertEqual(code, ev.EXIT_INCOMPLETE)
        self.assertEqual(status["stop_reason"],
                         "consecutive_transport_failures")
        self.assertEqual(len(t.requests), 5)
        self.assertTrue(all(r["outcome"] == ev.TIMEOUT
                            for r in self.rows("f")[1:]))

    def test_success_resets_failure_counter(self):
        def b(i, r, o):
            return {"raise_": "not_sent"} if i % 5 != 4 else {}
        code, _, t = self.run_arm("r", behaviour=b, cases=small_cases(12))
        self.assertEqual(code, ev.EXIT_OK)
        self.assertEqual(len(t.requests), 12)

    def test_no_retry_one_attempt_per_case(self):
        def b(i, r, o):
            return {"raise_": "not_sent"} if i == 0 else {}
        _, _, t = self.run_arm("n", behaviour=b, cases=small_cases(6))
        self.assertEqual(len(t.requests), 6)

    def test_max_cases_and_wall_budget(self):
        code, status, t = self.run_arm(
            "m", limits=ev.Limits(max_cases=3), cases=small_cases(10))
        self.assertEqual((code, status["stop_reason"], len(t.requests)),
                         (ev.EXIT_INCOMPLETE, "max_cases", 3))
        code, status, t = self.run_arm(
            "w", limits=ev.Limits(max_wall_seconds=5),
            behaviour=lambda i, r, o: {"latency_s": 2.0},
            cases=small_cases(10))
        self.assertEqual(status["stop_reason"], "wall_budget")
        self.assertEqual(len(t.requests), 3)

    def test_identity_change_midrun_stops_and_discards(self):
        def b(i, r, o):
            return {}
        clock = Clock()
        t = make_fake(ev.ARM_A, clock, b)
        original = t.generate

        def swapped(req):
            resp = original(req)
            if len(t.requests) == 3:
                return resp.__class__(**{**resp.__dict__,
                                         "model_digest": "f" * 64})
            return resp
        t.generate = swapped
        code, status = ev.run_block(
            spec=spec_for(), cases=small_cases(8), out_dir=self.out("i"),
            transport=t, free_memory_gib=lambda: 20.0,
            wall_clock=clock.wall, clock=clock.mono, release=RELEASE)
        self.assertEqual(status["stop_reason"], "model_identity_changed")
        self.assertEqual(len(self.rows("i")) - 1, 2)

    def test_cold_flag_first_call_and_load_time(self):
        def b(i, r, o):
            return {"load_ms": 9000 if i == 3 else 10}
        self.run_arm("c", behaviour=b, cases=small_cases(6))
        cold = [r["cold"] for r in self.rows("c")[1:]]
        self.assertEqual(cold, [True, False, False, True, False, False])

    def test_samplers_and_overhead_recorded(self):
        vals = iter(range(1000, 1100))
        sampler = lambda: {"vram_used_mib": next(vals), "ram_free_gib": 15.5}
        self.run_arm("v", sampler=sampler, cases=small_cases(3),
                     behaviour=lambda i, r, o: {"total_ms": 500,
                                                "latency_s": 2.0})
        r = self.rows("v")[1]
        self.assertEqual((r["vram_before_mib"], r["vram_after_mib"]),
                         (1000, 1001))
        self.assertEqual(r["ram_free_gib_before"], 15.5)
        self.assertEqual(r["server_total_ms"], 500)
        self.assertEqual(r["latency_ms"], 2000)
        self.assertEqual(r["queue_remaining"], 2)

    def test_nvidia_smi_parser_and_sampler(self):
        self.assertEqual(ev.parse_nvidia_smi("1234\n"), 1234)
        self.assertEqual(ev.parse_nvidia_smi("100\n2048\n"), 2048)
        self.assertIsNone(ev.parse_nvidia_smi("N/A"))
        self.assertIsNone(ev.parse_nvidia_smi(""))
        s = ev.NvidiaSmiSampler(lambda cmd: "777\n", lambda: 12.0)
        self.assertEqual(s(), {"vram_used_mib": 777, "ram_free_gib": 12.0})
        bad = ev.NvidiaSmiSampler(
            lambda cmd: (_ for _ in ()).throw(OSError()), lambda: None)
        self.assertEqual(bad(), {"vram_used_mib": None, "ram_free_gib": None})
        self.assertEqual(ev.NvidiaSmiSampler.COMMAND[0], "nvidia-smi")

    def test_concurrency_is_fixed_at_one(self):
        guard = ev._Exclusive()
        with guard:
            with self.assertRaises(ev.ConcurrencyError):
                with guard:
                    pass
        with guard:
            pass

        live = {"now": 0, "max": 0}

        def b(i, r, o):
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            live["now"] -= 1
            return {}
        self.run_arm("k", behaviour=b)
        self.assertEqual(live["max"], 1)


class RefusalTests(TempDirCase):
    def assertRefused(self, name, reason, **kw):
        code, status, t = self.run_arm(name, **kw)
        self.assertEqual(code, ev.EXIT_REFUSED)
        self.assertIn(reason, status["reasons"])
        self.assertEqual(t.requests, [])
        return t

    def test_low_memory(self):
        self.assertRefused("a", "low_memory", free_memory_gib=lambda: 13.99)
        self.assertRefused("b", "low_memory",
                           free_memory_gib=lambda: float("nan"))

        def boom():
            raise OSError()
        self.assertRefused("c", "low_memory", free_memory_gib=boom)
        code, _, _ = self.run_arm("d", free_memory_gib=lambda: 14.0)
        self.assertEqual(code, ev.EXIT_OK)

    def test_digest_version_and_missing_model(self):
        clock = Clock()
        for name, kw, reason in (
            ("a", {"digests": {ev.ARM_A.model_id: "0" * 64}},
             "digest_mismatch"),
            ("b", {"digests": {ev.ARM_A.model_id: ev.ARM_A.digest},
                   "version": "0.40.17"}, "ollama_version_mismatch"),
            ("c", {"digests": {}}, "model_not_installed"),
        ):
            from g3eval import fake
            t = fake.FakeTransport(advance=clock.advance, **kw)
            code, status = ev.run_block(
                spec=spec_for(), cases=small_cases(3),
                out_dir=self.out(name), transport=t,
                free_memory_gib=lambda: 20.0, wall_clock=clock.wall,
                clock=clock.mono, release=RELEASE)
            self.assertEqual(code, ev.EXIT_REFUSED)
            self.assertIn(reason, status["reasons"])
            self.assertEqual(t.requests, [])
            self.assertFalse(self.out(name).exists())

    def test_arm_b_uses_its_own_digest(self):
        clock = Clock()
        from g3eval import fake
        t = fake.FakeTransport(
            digests={ev.ARM_B.model_id: ev.ARM_A.digest},
            advance=clock.advance)
        code, status = ev.run_block(
            spec=spec_for(ev.ARM_B), cases=small_cases(3),
            out_dir=self.out("x"), transport=t,
            free_memory_gib=lambda: 20.0, wall_clock=clock.wall,
            clock=clock.mono, release=RELEASE)
        self.assertEqual(code, ev.EXIT_REFUSED)
        self.assertIn("digest_mismatch", status["reasons"])
        code, _, _ = self.run_arm("b", arm=ev.ARM_B)
        self.assertEqual(code, ev.EXIT_OK)

    def test_out_dir_must_be_new(self):
        self.out("d").mkdir()
        (self.out("d") / "old.txt").write_text("x")
        self.assertRefused("d", "out_dir_not_empty")

    def test_dataset_hash_and_label_checks(self):
        with self.assertRaises(ev.EvalRefused) as cm:
            ev.load_sealed_cases(SEALED, "0" * 64)
        self.assertEqual(cm.exception.reasons, ["dataset_hash_mismatch"])
        doc = json.loads(SEALED.read_text(encoding="utf-8"))
        doc["cases"][0]["expected"] = (
            "no_action" if doc["cases"][0]["expected"] == "mark_overdue"
            else "mark_overdue")
        bad = self.tmp / "bad.json"
        data = (json.dumps(doc, sort_keys=True, indent=2) + "\n").encode()
        bad.write_bytes(data)
        with self.assertRaises(ev.EvalRefused) as cm:
            ev.load_sealed_cases(bad, ev.sha256_hex(data))
        self.assertEqual(cm.exception.reasons, ["dataset_label_inconsistent"])
        cases, sha = ev.load_sealed_cases(V1, V1_SHA)
        self.assertEqual(len(cases), 80)

    def test_unknown_case_ids_refused(self):
        self.assertRefused("u", "unknown_case_ids",
                           spec_kw={"case_ids": ("G3-001", "NOPE")})


class ResumeAndBlackoutTests(TempDirCase):
    def test_resume_continues_without_resending(self):
        cases = small_cases(10)
        code, status, t1 = self.run_arm(
            "r", cases=cases, limits=ev.Limits(max_cases=4))
        self.assertEqual(code, ev.EXIT_INCOMPLETE)
        code, status, t2 = self.run_arm("r", cases=cases, resume=True)
        self.assertEqual((code, status["status"]), (ev.EXIT_OK, "complete"))
        self.assertEqual(len(t1.requests), 4)
        self.assertEqual(len(t2.requests), 6)
        rows = self.rows("r")
        self.assertEqual([r["type"] for r in rows].count("header"), 1)
        ids = [r["id"] for r in rows[1:]]
        self.assertEqual(sorted(ids), sorted(c["id"] for c in cases))
        self.assertEqual(len(ids), len(set(ids)))

    def test_resume_refuses_changed_config_or_missing_run(self):
        cases = small_cases(6)
        self.run_arm("r", cases=cases, limits=ev.Limits(max_cases=2))
        code, status, t = self.run_arm(
            "r", cases=cases, resume=True, spec_kw={"seed": 7})
        self.assertEqual(code, ev.EXIT_REFUSED)
        self.assertEqual(status["reasons"], ["resume_config_mismatch"])
        self.assertEqual(t.requests, [])
        code, status, _ = self.run_arm("never", cases=cases, resume=True)
        self.assertEqual(status["reasons"], ["resume_nothing_to_resume"])

    def test_resume_of_a_header_only_run_writes_one_header(self):
        cases = small_cases(4)
        self.run_arm("h", cases=cases, limits=ev.Limits(max_cases=0))
        self.assertEqual(len(self.rows("h")), 1)
        self.run_arm("h", cases=cases, resume=True)
        rows = self.rows("h")
        self.assertEqual([r["type"] for r in rows].count("header"), 1)
        self.assertEqual(len(rows), 5)

    def test_blackout_window_boundaries(self):
        start = datetime(2026, 11, 25, 0, 0)
        end = datetime(2026, 11, 26, 0, 0)
        b = ev.Blackout(((start, end),))
        self.assertFalse(b.active(datetime(2026, 11, 24, 23, 59, 59)))
        self.assertTrue(b.active(start))
        self.assertTrue(b.active(datetime(2026, 11, 25, 18, 0)))
        self.assertFalse(b.active(end))

    def test_blackout_at_start_refuses_and_sends_nothing(self):
        clock = Clock(datetime(2026, 11, 25, 19, 0))
        b = ev.Blackout(((datetime(2026, 11, 24, 18, 0),
                          datetime(2026, 11, 26, 0, 0)),))
        code, status, t = self.run_arm("b", clock=clock, blackout=b)
        self.assertEqual(code, ev.EXIT_REFUSED)
        self.assertEqual(status["reasons"], ["blackout_window"])
        self.assertEqual(t.requests, [])

    def test_blackout_midrun_pauses_then_resumes_after_window(self):
        clock = Clock(datetime(2026, 11, 24, 17, 59, 0))
        b = ev.Blackout(((datetime(2026, 11, 24, 18, 0),
                          datetime(2026, 11, 26, 0, 0)),))
        cases = small_cases(10)
        code, status, t1 = self.run_arm(
            "p", cases=cases, clock=clock, blackout=b,
            behaviour=lambda i, r, o: {"latency_s": 30.0})
        self.assertEqual((code, status["stop_reason"]),
                         (ev.EXIT_INCOMPLETE, "blackout_window"))
        self.assertEqual(len(t1.requests), 2)
        later = Clock(datetime(2026, 11, 26, 0, 0))
        code, status, t2 = self.run_arm(
            "p", cases=cases, clock=later, blackout=b, resume=True)
        self.assertEqual(code, ev.EXIT_OK)
        self.assertEqual(len(t2.requests), 8)

    def test_blackout_file_loader(self):
        p = self.tmp / "b.json"
        p.write_text(json.dumps({"windows": [
            {"start": "2026-11-24T18:00:00", "end": "2026-11-26T00:00:00"}]}))
        b = ev.blackout_from_file(p)
        self.assertTrue(b.active(datetime(2026, 11, 25, 12, 0)))


class SubsetTests(unittest.TestCase):
    def test_stability_subset_is_deterministic_spread_and_sized(self):
        cases = load_all()
        s1 = ev.stability_subset(cases)
        s2 = ev.stability_subset(list(reversed(cases)))
        self.assertEqual([c["id"] for c in s1], [c["id"] for c in s2])
        self.assertEqual(len(s1), 40)
        self.assertEqual(len({c["id"] for c in s1}), 40)
        self.assertEqual({c["category"] for c in s1},
                         {c["category"] for c in cases})
        self.assertEqual({c["stratum"] for c in s1},
                         {"core", "injection", "noise", "missing",
                          "contradictory"})

    def test_config_hash_depends_on_everything_that_matters(self):
        base = spec_for()
        h = ev.config_hash(base, ev.Limits())
        self.assertNotEqual(h, ev.config_hash(spec_for(seed=1), ev.Limits()))
        self.assertNotEqual(h, ev.config_hash(spec_for(order_seed=2),
                                              ev.Limits()))
        self.assertNotEqual(h, ev.config_hash(spec_for(ev.ARM_B),
                                              ev.Limits()))
        self.assertEqual(h, ev.config_hash(spec_for(), ev.Limits()))


if __name__ == "__main__":
    unittest.main()
