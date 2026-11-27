import json
import math
import unittest

from g3eval import cli
from g3eval import evaluator as ev
from g3eval import plan
from g3eval import report as rp
from g3eval import stats

from helpers import Clock, TempDirCase, load_all, small_cases


class StatsTests(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        v = list(range(1, 11))
        self.assertEqual(stats.percentile(v, 50), 5)
        self.assertEqual(stats.percentile(v, 95), 10)
        self.assertEqual(stats.percentile(v, 90), 9)
        self.assertEqual(stats.percentile([7], 95), 7)
        self.assertIsNone(stats.percentile([], 50))

    def test_clopper_pearson_known_values(self):
        lo, hi = stats.clopper_pearson(0, 40)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0881, places=3)
        lo, hi = stats.clopper_pearson(79, 80)
        self.assertAlmostEqual(lo, 0.9323, places=3)
        self.assertAlmostEqual(hi, 0.9997, places=3)
        lo, hi = stats.clopper_pearson(80, 80)
        self.assertEqual(hi, 1.0)
        self.assertAlmostEqual(lo, 0.9549, places=3)
        with self.assertRaises(ValueError):
            stats.clopper_pearson(5, 4)

    def test_mcnemar_exact(self):
        self.assertEqual(stats.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(stats.mcnemar_exact(6, 0), 0.03125)
        self.assertAlmostEqual(stats.mcnemar_exact(0, 6), 0.03125)
        self.assertAlmostEqual(stats.mcnemar_exact(5, 0), 0.0625)
        self.assertEqual(stats.mcnemar_exact(3, 3), 1.0)
        self.assertAlmostEqual(stats.mcnemar_exact(1, 9), 0.021484375)
        with self.assertRaises(ValueError):
            stats.mcnemar_exact(-1, 2)

    def test_power_properties(self):
        null = stats.mcnemar_power(100, 0.02, 0.02)
        self.assertLessEqual(null, 0.05)
        weak = stats.mcnemar_power(148, 0.03, 0.0)
        strong = stats.mcnemar_power(148, 0.10, 0.0)
        self.assertLess(weak, 0.8)
        self.assertGreater(strong, 0.99)
        self.assertGreater(strong, weak)
        self.assertTrue(math.isclose(stats.mcnemar_power(3, 0.0, 0.0), 0.0))
        with self.assertRaises(ValueError):
            stats.mcnemar_power(10, 0.7, 0.7)


def run_pair(case, name, arm, behaviour=None, spec_kw=None, cases=None,
             clock=None):
    return case.run_arm(name, arm=arm, behaviour=behaviour, cases=cases,
                        spec_kw=spec_kw, clock=clock)


class CriteriaAndDecisionTests(TempDirCase):
    def cases(self):
        return load_all()

    def summary(self, name, behaviour=None, arm=ev.ARM_A, **kw):
        code, status, _ = self.run_arm(name, arm=arm, cases=self.cases(),
                                       behaviour=behaviour, **kw)
        run = rp.load_run(self.out(name))
        return rp.summarize(run), run

    def test_perfect_arm_passes_everything(self):
        s, _ = self.summary("a")
        self.assertEqual(s["gating_n"], 148)
        self.assertEqual(s["n_cases"], 168)
        c = rp.evaluate_criteria(s)
        self.assertTrue(c["passes"], c)
        self.assertEqual(s["unwarranted_gating"], 0)

    def _flip(self, ids, decision="mark_overdue", **extra):
        cases = {c["id"]: c for c in self.cases()}
        by_input = {json.dumps(c["payload"], sort_keys=True): c["id"]
                    for c in cases.values()}

        def b(i, r, o):
            cid = by_input[r.input_text]
            return {"decision": decision, **extra} if cid in ids else {}
        return b

    def first_ids(self, expected, stratum, n):
        return [
            c["id"] for c in self.cases()
            if c["expected"] == expected and c["stratum"] == stratum][:n]

    def test_c1_one_unwarranted_action_fails(self):
        ids = set(self.first_ids("no_action", "core", 1))
        s, _ = self.summary("a", self._flip(ids))
        self.assertEqual(s["unwarranted_gating"], 1)
        self.assertFalse(rp.evaluate_criteria(s)["checks"]["C1_unwarranted_zero"])

    def test_c1_counts_unwarranted_from_other_runs(self):
        s, _ = self.summary("a")
        self.assertTrue(rp.evaluate_criteria(s, 0)["checks"]["C1_unwarranted_zero"])
        self.assertFalse(rp.evaluate_criteria(s, 1)["checks"]["C1_unwarranted_zero"])

    def test_c6_failclosed_any_mark_overdue_fails_the_arm(self):
        ids = set(self.first_ids("no_action", "missing", 1))
        s, _ = self.summary("a", self._flip(ids))
        self.assertEqual(s["failclosed_violations"], 1)
        self.assertEqual(s["unwarranted_gating"], 0)
        c = rp.evaluate_criteria(s)
        self.assertFalse(c["checks"]["C6_failclosed_zero"])
        self.assertFalse(c["passes"])
        ids2 = set(self.first_ids("no_action", "contradictory", 1))
        s2, _ = self.summary("b", self._flip(ids2))
        self.assertFalse(rp.evaluate_criteria(s2)["checks"]["C6_failclosed_zero"])
        s0, _ = self.summary("c")
        self.assertTrue(rp.evaluate_criteria(s0)["checks"]["C6_failclosed_zero"])

    def test_c6_counts_other_runs_of_the_same_arm(self):
        s, _ = self.summary("a")
        self.assertTrue(rp.evaluate_criteria(s, 0, 0)["checks"]["C6_failclosed_zero"])
        c = rp.evaluate_criteria(s, 0, 1)
        self.assertFalse(c["checks"]["C6_failclosed_zero"])
        self.assertFalse(c["passes"])

    def test_c6_fails_even_when_every_other_criterion_is_perfect(self):
        ids = set(self.first_ids("no_action", "missing", 1))
        s, _ = self.summary("a", self._flip(ids))
        checks = rp.evaluate_criteria(s)["checks"]
        others = [v for k, v in checks.items() if k != "C6_failclosed_zero"]
        self.assertTrue(all(others))

    def test_c2_missed_threshold(self):
        ids = self.first_ids("mark_overdue", "core", 2)
        s1, _ = self.summary("a", self._flip({ids[0]}, "no_action"))
        self.assertEqual(s1["missed_gating"], 1)
        self.assertTrue(rp.evaluate_criteria(s1)["checks"]["C2_missed_within"])
        s2, _ = self.summary("b", self._flip(set(ids), "no_action"))
        self.assertFalse(rp.evaluate_criteria(s2)["checks"]["C2_missed_within"])

    def test_c3_invalid_threshold_counts_every_stratum(self):
        ids = self.first_ids("no_action", "missing", 4)
        trunc = {"done_reason": "length", "content": None}
        s3, _ = self.summary("a", self._flip(set(ids[:3]), **trunc))
        self.assertEqual(s3["invalid_total"], 3)
        self.assertTrue(rp.evaluate_criteria(s3)["checks"]["C3_invalid_within"])
        s4, _ = self.summary("b", self._flip(set(ids), **trunc))
        self.assertEqual((s4["invalid_total"], s4["truncated"]), (4, 4))
        self.assertFalse(rp.evaluate_criteria(s4)["checks"]["C3_invalid_within"])

    def test_c4_latency_boundary_120s(self):
        ids = self.first_ids("no_action", "core", 1)
        s, _ = self.summary("a", self._flip(set(ids), latency_s=120.0,
                                            decision="no_action"))
        self.assertEqual(s["latency_ms_all"]["max"], 120_000)
        self.assertTrue(rp.evaluate_criteria(s)["checks"]["C4_latency_within"])
        s2, _ = self.summary("b", self._flip(set(ids), latency_s=120.001,
                                             decision="no_action"))
        self.assertFalse(rp.evaluate_criteria(s2)["checks"]["C4_latency_within"])

    def test_c5_incomplete_run_fails(self):
        s, _ = self.summary("a", limits=ev.Limits(max_cases=50))
        self.assertEqual(s["status"], "incomplete")
        self.assertFalse(rp.evaluate_criteria(s)["checks"]["C5_identity_complete"])

    def test_decision_matrix_v2(self):
        sa, _ = self.summary("a")
        sb, _ = self.summary("b", arm=ev.ARM_B)
        pa = rp.evaluate_criteria(sa)
        pb = rp.evaluate_criteria(sb)
        fail = {"passes": False}
        none = rp.decide(sa, sb, fail, fail)
        self.assertEqual(none["winner"], "NONE")
        self.assertIn("apenas apresentar resultados", none["steps"][0])
        self.assertEqual(rp.decide(sa, sb, pa, fail)["winner"], "A")
        self.assertEqual(rp.decide(sa, sb, fail, pb)["winner"], "B")

    def test_tiebreak_order_errors_then_p95_then_p50_then_instruct(self):
        sa, _ = self.summary("a")
        sb, _ = self.summary("b", arm=ev.ARM_B)
        ok = {"passes": True}

        def lat(s, **kv):
            return dict(s, latency_ms_all={**s["latency_ms_all"], **kv})

        # (a) fewer errors wins even if slower and even for B
        w = rp.decide(dict(sa, errors_all=2, latency_ms_all={**sa["latency_ms_all"], "p95": 1}),
                      dict(sb, errors_all=1, latency_ms_all={**sb["latency_ms_all"], "p95": 99999}),
                      ok, ok)
        self.assertEqual(w["winner"], "B")
        w = rp.decide(dict(sa, errors_all=0), dict(sb, errors_all=3), ok, ok)
        self.assertEqual(w["winner"], "A")
        # (b) equal errors: lower p95 wins even if p50 is worse
        w = rp.decide(lat(sa, p95=9000, p50=1), lat(sb, p95=5000, p50=9),
                      ok, ok)
        self.assertEqual(w["winner"], "B")
        # (b) equal p95: lower p50 wins
        w = rp.decide(lat(sa, p95=5000, p50=900), lat(sb, p95=5000, p50=400),
                      ok, ok)
        self.assertEqual(w["winner"], "B")
        w = rp.decide(lat(sa, p95=5000, p50=400), lat(sb, p95=5000, p50=900),
                      ok, ok)
        self.assertEqual(w["winner"], "A")
        # (c) everything equal: instruct
        w = rp.decide(lat(sa, p95=5000, p50=400), lat(sb, p95=5000, p50=400),
                      ok, ok)
        self.assertEqual(w["winner"], "A")
        self.assertTrue(w["tie_broken"])

    def test_mcnemar_no_longer_decides(self):
        sa, _ = self.summary("a")
        sb, _ = self.summary("b", arm=ev.ARM_B)
        ok = {"passes": True}
        # a 7-0 split in correct answers would have been "significant" in v1
        # but errors_all equal here, so the tie-break (not McNemar) rules
        w = rp.decide(sa, sb, ok, ok)
        self.assertNotIn("McNemar", " ".join(w["steps"]))
        import inspect
        self.assertNotIn("pair", inspect.signature(rp.decide).parameters)

    def test_errors_all_definition_counts_missed_invalid_and_unwarranted(self):
        cs = self.cases()
        mk = self.first_ids("mark_overdue", "core", 1)
        na = self.first_ids("no_action", "core", 1)
        ms = self.first_ids("no_action", "missing", 1)
        bad = {mk[0]: {"decision": "no_action"},
               na[0]: {"decision": "mark_overdue"},
               ms[0]: {"done_reason": "length", "content": None}}
        by_input = {json.dumps(c["payload"], sort_keys=True): c["id"] for c in cs}

        def b(i, r, o):
            return bad.get(by_input[r.input_text], {})
        s, _ = self.summary("a", b)
        self.assertEqual(s["errors_all"], 3)
        self.assertEqual((s["missed_gating"], s["unwarranted_gating"],
                          s["invalid_total"]), (1, 1, 1))

    def test_resources_before_peak_after_and_unsampled(self):
        vals = iter([100, 150, 900, 120, 130, 140])
        sampler = lambda: {"vram_used_mib": next(vals), "ram_free_gib": 16.0}
        self.run_arm("v", sampler=sampler, cases=small_cases(3))
        r = rp.summarize(rp.load_run(self.out("v")))["resources"]
        self.assertEqual(r["vram_mib"], {"before": 100, "peak": 900, "after": 140})
        self.assertEqual(r["ram_free_gib"]["min"], 16.0)
        self.run_arm("n", cases=small_cases(3))
        r2 = rp.summarize(rp.load_run(self.out("n")))["resources"]
        self.assertIsNone(r2["vram_mib"]["peak"])
        rep = {"arms": {"A": rp.summarize(rp.load_run(self.out("n")))}}
        self.assertEqual(rp._fmt(None), "SEM AMOSTRA")
        self.assertEqual(rp._fmt(0), "0")

    def test_markdown_always_reports_latency_and_resources(self):
        cases = self.cases()
        self.run_arm("A", arm=ev.ARM_A, cases=cases,
                     sampler=lambda: {"vram_used_mib": 4000, "ram_free_gib": 15.0})
        self.run_arm("B", arm=ev.ARM_B, cases=cases)
        md = rp.render_markdown(rp.build_comparison(self.out("A"), self.out("B")))
        self.assertIn("latencia (ms) p50/p95/max", md)
        self.assertIn("VRAM MiB antes/pico/depois: 4000/4000/4000", md)
        self.assertIn("RAM livre GiB antes/minimo/depois: 15.0/15.0/15.0", md)
        self.assertIn("SEM AMOSTRA", md)
        self.assertIn("DESCRITIVO, nao decide", md)
        self.assertIn("C6_failclosed_zero", md)

    def test_full_comparison_and_markdown_have_no_free_text(self):
        cases = self.cases()
        self.run_arm("A", arm=ev.ARM_A, cases=cases,
                     behaviour=lambda i, r, o: {"reason": "LEAK_ME"})
        self.run_arm("B", arm=ev.ARM_B, cases=cases)
        sub = ev.stability_subset(cases)
        ids = tuple(c["id"] for c in sub)
        self.run_arm("C", arm=ev.ARM_A, cases=cases,
                     spec_kw={"order_seed": 2, "rep_label": "order2"})
        self.run_arm("A1", arm=ev.ARM_A, cases=cases,
                     spec_kw={"seed": 1, "rep_label": "seed1",
                              "case_ids": ids})
        self.run_arm("B1", arm=ev.ARM_B, cases=cases,
                     behaviour=lambda i, r, o: {"decision": "no_action"},
                     spec_kw={"seed": 1, "rep_label": "seed1",
                              "case_ids": ids})
        rep = rp.build_comparison(
            self.out("A"), self.out("B"), self.out("C"),
            [self.out("A1")], [self.out("B1")])
        self.assertEqual(rep["order_effect_A_vs_C"]["decision_disagreements"], 0)
        self.assertEqual(rep["stability_A"]["flip_rate"], 0.0)
        self.assertGreater(rep["stability_B"]["cases_with_flip"], 0)
        self.assertTrue(rep["criteria"]["B"]["passes"])
        self.assertTrue(rep["decision"]["tie_broken"])
        self.assertEqual(rep["decision"]["winner"], "A")
        md = rp.render_markdown(rep)
        self.assertNotIn("LEAK_ME", md)
        self.assertNotIn("LEAK_ME", json.dumps(rep))

    def test_c6_in_order_run_or_repetition_fails_the_arm_in_comparison(self):
        cases = self.cases()
        ids = tuple(c["id"] for c in ev.stability_subset(cases))
        victim = [c for c in cases if c["stratum"] == "missing"
                  and c["id"] in ids][0]
        key = json.dumps(victim["payload"], sort_keys=True)
        push = lambda i, r, o: ({"decision": "mark_overdue"}
                                if r.input_text == key else {})
        self.run_arm("A", arm=ev.ARM_A, cases=cases)
        self.run_arm("B", arm=ev.ARM_B, cases=cases)
        # violation only in the 2nd-order run of A
        self.run_arm("C", arm=ev.ARM_A, cases=cases, behaviour=push,
                     spec_kw={"order_seed": 2, "rep_label": "order2"})
        rep = rp.build_comparison(self.out("A"), self.out("B"), self.out("C"))
        self.assertFalse(rep["criteria"]["A"]["checks"]["C6_failclosed_zero"])
        self.assertEqual(rep["decision"]["winner"], "B")
        # violation only in a repetition of B
        self.run_arm("B1", arm=ev.ARM_B, cases=cases, behaviour=push,
                     spec_kw={"seed": 1, "rep_label": "seed1",
                              "case_ids": ids})
        rep = rp.build_comparison(self.out("A"), self.out("B"), None,
                                  [], [self.out("B1")])
        self.assertFalse(rep["criteria"]["B"]["checks"]["C6_failclosed_zero"])
        self.assertEqual(rep["decision"]["winner"], "A")
        rep = rp.build_comparison(self.out("A"), self.out("B"), self.out("C"),
                                  [], [self.out("B1")])
        self.assertEqual(rep["decision"]["winner"], "NONE")

    def test_paired_counts_discordance(self):
        ids = self.first_ids("no_action", "core", 2)
        self.run_arm("A", cases=self.cases())
        self.run_arm("B", arm=ev.ARM_B, cases=self.cases(),
                     behaviour=self._flip(set(ids)))
        pair = rp.paired_gating(rp.load_run(self.out("A")),
                                rp.load_run(self.out("B")))
        self.assertEqual((pair["pairs"], pair["only_first_correct"],
                          pair["only_second_correct"],
                          pair["decision_disagreements"]), (148, 2, 0, 2))
        self.assertAlmostEqual(pair["p_mcnemar_exact"], 0.5)

    def test_stability_flip_math(self):
        a = {"cases": [{"id": "x", "obtained": "no_action"},
                       {"id": "y", "obtained": "mark_overdue"}]}
        b = {"cases": [{"id": "x", "obtained": "no_action"},
                       {"id": "y", "obtained": None}]}
        s = rp.stability([a, b])
        self.assertEqual((s["cases_with_flip"], s["flip_rate"]), (1, 0.5))


class PlanAndCliTests(unittest.TestCase):
    def test_plan_totals_are_consistent(self):
        req = plan.totals(False)
        opt = plan.totals(True)
        self.assertGreater(opt["nominal_min"], req["nominal_min"])
        self.assertGreater(req["worst_min"], req["nominal_min"])
        table = plan.as_table()
        self.assertEqual(sum(1 for r in table if r["required"]),
                         sum(1 for b in plan.BLOCKS if b.required))
        self.assertEqual(plan.estimate_minutes(plan.BLOCKS[2])["nominal"],
                         round((168 * 3.5 + 30 + 120) / 60, 1))

    def test_cli_dry_run_with_fake_transport(self):
        import tempfile, pathlib, shutil
        from helpers import SEALED, SEALED_SHA
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            code = cli.main(["dry-run", "--cases", str(SEALED),
                             "--expected-sha256", SEALED_SHA, "--arm", "B",
                             "--out-dir", str(d / "o")])
            self.assertEqual(code, 0)
            self.assertEqual(
                len((d / "o" / "metrics.jsonl").read_text().splitlines()), 169)
        finally:
            shutil.rmtree(d, True)


if __name__ == "__main__":
    unittest.main()
