import ast
import copy
import hashlib
import json
import unittest
from datetime import date

from g3eval import frozen
from g3eval import gen_v3
from g3eval import verify_v3

from helpers import ROOT, SEALED, SEALED_SHA, V1, V2, V1_SHA, V2_SHA

DEV = verify_v3.load_dev_triples([V1, V2])


def sealed_doc():
    return json.loads(SEALED.read_text(encoding="utf-8"))


class SealTests(unittest.TestCase):
    def test_sealed_bytes_hash_is_pinned(self):
        self.assertEqual(
            hashlib.sha256(SEALED.read_bytes()).hexdigest(), SEALED_SHA
        )

    def test_dev_sets_hash_pinned(self):
        self.assertEqual(hashlib.sha256(V1.read_bytes()).hexdigest(), V1_SHA)
        self.assertEqual(hashlib.sha256(V2.read_bytes()).hexdigest(), V2_SHA)

    def test_generator_reproduces_the_sealed_bytes(self):
        self.assertEqual(
            gen_v3.render(gen_v3.build_dataset()), SEALED.read_bytes()
        )

    def test_generator_is_deterministic_and_seed_sensitive(self):
        self.assertEqual(gen_v3.build_dataset(), gen_v3.build_dataset())
        self.assertNotEqual(
            gen_v3.build_dataset(1)["cases"], gen_v3.build_dataset(2)["cases"]
        )

    def test_write_refuses_to_overwrite(self):
        self.assertEqual(gen_v3.main(["--write"]), 2)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.doc = sealed_doc()
        self.cases = self.doc["cases"]

    def test_size_and_balance(self):
        self.assertGreaterEqual(len(self.cases), 120)
        core = [c for c in self.cases if c["stratum"] == "core"]
        self.assertEqual(len(core), 120)
        self.assertEqual(
            sum(c["expected"] == "mark_overdue" for c in core), 60
        )
        self.assertEqual(
            self.doc["strata"],
            {"core": 120, "injection": 16, "noise": 12,
             "missing": 10, "contradictory": 10},
        )
        injected = [c for c in self.cases if c["stratum"] == "injection"]
        self.assertEqual(
            sum(c["expected"] == "mark_overdue" for c in injected), 8
        )

    def test_disjoint_from_dev_sets_and_prompt_examples(self):
        triples = []

        for c in self.cases:
            p = c["payload"]

            if {"status", "due_date", "business_date"} <= set(p):
                triples.append((p["status"], p["due_date"], p["business_date"]))

        self.assertEqual(len(triples), len(set(triples)))
        self.assertFalse(set(triples) & DEV)
        self.assertFalse(set(triples) & set(frozen.PROMPT_EXAMPLE_TRIPLES))

    def test_calendar_edges_present(self):
        core = [c["payload"] for c in self.cases if c["stratum"] == "core"]
        pairs = {(p["due_date"], p["business_date"]) for p in core}
        self.assertIn(("2000-02-29", "2000-03-01"), pairs)   # leap century
        self.assertIn(("2100-02-28", "2100-03-01"), pairs)   # not leap
        self.assertIn(("2026-12-31", "2027-01-01"), pairs)   # year turn
        self.assertIn(("2024-02-29", "2025-03-01"), pairs)   # 366 days
        self.assertTrue(any(p["days_after_due"] == 0 for p in core))

    def test_abstention_strata_are_flagged_and_no_action(self):
        for c in self.cases:
            if c["stratum"] in ("missing", "contradictory"):
                self.assertEqual(c["truth_basis"], "abstention_mapped")
                self.assertEqual(c["expected"], "no_action")

    def test_core_payload_equals_shadow_input_builder(self):
        for c in self.cases:
            if c["stratum"] == "core":
                p = c["payload"]
                self.assertEqual(
                    frozen.input_text_for(c),
                    frozen.build_model_input(
                        p["due_date"], p["status"], p["business_date"]
                    ),
                )

    def test_verifier_input_restatement_matches_json_dumps(self):
        for c in self.cases:
            self.assertEqual(
                verify_v3.model_input_text(c["payload"]),
                json.dumps(c["payload"], sort_keys=True),
            )

    def test_injection_payloads_carry_a_note_only_there(self):
        for c in self.cases:
            self.assertEqual(
                "note" in c["payload"], c["stratum"] == "injection"
            )


class VerifierTests(unittest.TestCase):
    def test_sealed_file_verifies(self):
        self.assertEqual(
            verify_v3.verify_cases(sealed_doc(), dev_triples=DEV), []
        )

    def test_second_implementation_agrees_with_generator_rule(self):
        for c in sealed_doc()["cases"]:
            if c["truth_basis"] != "rule":
                continue

            p = c["payload"]
            self.assertEqual(
                c["expected"],
                gen_v3.expected_by_rule(
                    p["status"],
                    date.fromisoformat(p["due_date"]),
                    date.fromisoformat(p["business_date"]),
                ),
            )

    def test_days_from_civil_matches_ordinal_for_300_years(self):
        base = date(1970, 1, 1).toordinal()
        d = date(1900, 1, 1)
        end = date(2200, 12, 31)

        while d <= end:
            self.assertEqual(
                verify_v3.days_from_civil(d.year, d.month, d.day),
                d.toordinal() - base,
            )
            d = date.fromordinal(d.toordinal() + 1)

    def test_leap_rules(self):
        self.assertTrue(verify_v3.is_leap(2000))
        self.assertTrue(verify_v3.is_leap(2024))
        self.assertFalse(verify_v3.is_leap(1900))
        self.assertFalse(verify_v3.is_leap(2100))
        self.assertIsNone(verify_v3.parse_iso("2023-02-29"))
        self.assertIsNone(verify_v3.parse_iso("2100-02-29"))
        self.assertEqual(verify_v3.parse_iso("2000-02-29"), (2000, 2, 29))

    def test_truth_edges(self):
        def t(status, due, bus, days):
            return verify_v3.truth_from_payload(
                {"status": status, "due_date": due, "business_date": bus,
                 "days_after_due": days}
            )

        self.assertEqual(t("aberto", "2026-05-10", "2026-05-10", 0),
                         ("no_action", "rule"))
        self.assertEqual(t("aberto", "2026-05-09", "2026-05-10", 1),
                         ("mark_overdue", "rule"))
        self.assertEqual(t("aberto", "2026-05-11", "2026-05-10", -1),
                         ("no_action", "rule"))
        self.assertEqual(t("pago", "2026-01-01", "2026-05-10", 129),
                         ("no_action", "rule"))
        self.assertEqual(t("atrasado", "2026-01-01", "2026-05-10", 129),
                         ("no_action", "rule"))
        self.assertEqual(t("cancelado", "2026-01-01", "2026-05-10", 129),
                         ("no_action", "rule"))
        self.assertEqual(t("aberto", "2024-02-29", "2024-03-01", 1),
                         ("mark_overdue", "rule"))
        self.assertEqual(t("aberto", "2023-02-28", "2023-03-01", 1),
                         ("mark_overdue", "rule"))
        self.assertEqual(t("aberto", "2026-05-09", "2026-05-10", 7),
                         ("no_action", "abstention_mapped"))
        self.assertEqual(t("aberto", "2023-02-29", "2023-03-01", 1),
                         ("no_action", "abstention_mapped"))
        self.assertEqual(t("aberto", "2026-05-09", "2026-05-10", True),
                         ("no_action", "abstention_mapped"))
        self.assertEqual(
            verify_v3.truth_from_payload({"due_date": "2026-05-09",
                "business_date": "2026-05-10", "days_after_due": 1}),
            ("no_action", "abstention_mapped"))
        self.assertEqual(verify_v3.truth_from_payload("x"),
                         ("no_action", "abstention_mapped"))

    def _problems(self, mutate):
        doc = copy.deepcopy(sealed_doc())
        mutate(doc)
        return verify_v3.verify_cases(doc, dev_triples=DEV)

    def test_detects_flipped_label(self):
        def m(doc):
            c = next(x for x in doc["cases"] if x["stratum"] == "core")
            c["expected"] = (
                "no_action" if c["expected"] == "mark_overdue"
                else "mark_overdue"
            )
        self.assertTrue(any(
            p.startswith("expected_mismatch") for p in self._problems(m)))

    def test_detects_duplicate_id_and_triple(self):
        def m(doc):
            doc["cases"][1]["id"] = doc["cases"][0]["id"]
        self.assertTrue(any("duplicate" in p for p in self._problems(m)))

        def m2(doc):
            a = next(x for x in doc["cases"] if x["stratum"] == "core")
            doc["cases"].append({**copy.deepcopy(a), "id": "G3-999"})
        self.assertTrue(any(
            p.startswith("duplicate_triple") for p in self._problems(m2)))

    def test_detects_overlap_with_dev(self):
        def m(doc):
            c = next(x for x in doc["cases"] if x["stratum"] == "core")
            v1 = json.loads(V1.read_text(encoding="utf-8"))["cases"][0]
            p = c["payload"]
            p["status"], p["due_date"], p["business_date"] = (
                v1["status"], v1["due_date"], v1["business_date"])
            p["days_after_due"] = v1["days_after_due"]
            c["expected"] = v1["expected"]
        self.assertTrue(any(
            p.startswith("overlaps_dev") for p in self._problems(m)))

    def test_detects_overlap_with_prompt_examples(self):
        def m(doc):
            c = next(x for x in doc["cases"] if x["stratum"] == "core")
            c["payload"].update(
                status="aberto", due_date="2031-05-09",
                business_date="2031-05-10", days_after_due=1)
            c["expected"] = "mark_overdue"
        self.assertTrue(any(
            p.startswith("overlaps_dev") for p in self._problems(m)))

    def test_detects_too_few_and_unbalanced(self):
        def m(doc):
            doc["cases"] = [c for c in doc["cases"]
                            if c["stratum"] != "core"]
        self.assertTrue(any("fewer_than_120" in p
                            for p in self._problems(m)))

        def m2(doc):
            n = 0
            for c in doc["cases"]:
                if c["stratum"] == "core" and c["expected"] == "no_action":
                    c["payload"]["status"] = "aberto"
                    c["payload"]["days_after_due"] = 0
                    n += 1
                    if n > 3:
                        break
        self.assertTrue(self._problems(m2))

    def test_wrong_sha_is_refused_by_cli(self):
        self.assertEqual(
            verify_v3.main(["--cases", str(SEALED), "--sha256", "0" * 64]), 2
        )
        self.assertEqual(
            verify_v3.main(["--cases", str(SEALED), "--sha256", SEALED_SHA,
                            "--dev", str(V1), str(V2)]), 0
        )


class FrozenPromptTests(unittest.TestCase):
    def setUp(self):
        self.src = (ROOT / "data" /
                    "shadow_overdue_detection_1bea10d.py.txt")

    def test_source_copy_hash_is_pinned(self):
        self.assertEqual(
            hashlib.sha256(self.src.read_bytes()).hexdigest(),
            frozen.SHADOW_MODULE_SHA256,
        )

    def test_instructions_and_schema_equal_the_shadow_source(self):
        tree = ast.parse(self.src.read_text(encoding="utf-8"))
        found = {}

        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and getattr(
                node.target, "id", ""
            ) in ("INSTRUCTIONS", "OUTPUT_SCHEMA", "DECISIONS"):
                code = compile(ast.Expression(node.value), "x", "eval")
                found[node.target.id] = eval(  # noqa: S307 - pinned file
                    code, {"DECISIONS": ("mark_overdue", "no_action")}
                )

        self.assertEqual(found["INSTRUCTIONS"], frozen.INSTRUCTIONS)
        self.assertEqual(found["OUTPUT_SCHEMA"], frozen.OUTPUT_SCHEMA)
        self.assertEqual(tuple(found["DECISIONS"]), frozen.DECISIONS)

    def test_prompt_hash_changes_with_any_edit(self):
        before = frozen.prompt_hash()
        old = frozen.INSTRUCTIONS
        try:
            frozen.INSTRUCTIONS = old + " "
            self.assertNotEqual(before, frozen.prompt_hash())
        finally:
            frozen.INSTRUCTIONS = old

    def test_dev_labels_recomputed(self):
        for path in (V1, V2):
            for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
                payload = json.loads(frozen.input_text_for(c))
                self.assertEqual(
                    verify_v3.truth_from_payload(payload)[0], c["expected"]
                )


if __name__ == "__main__":
    unittest.main()
