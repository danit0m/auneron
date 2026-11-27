import json
import unittest
from datetime import datetime

from g3eval import evaluator as ev
from g3eval import transport as tr

from helpers import ROOT, SEALED_SHA, TempDirCase


class ConfigFileTests(unittest.TestCase):
    def test_blackout_covers_now_until_26_nov_2200_and_ends_exclusive(self):
        b = ev.blackout_from_file(ROOT / "config" / "blackout.json")
        self.assertTrue(b.active(datetime(2026, 11, 24, 16, 0, 0)))
        self.assertTrue(b.active(datetime(2026, 11, 25, 12, 0, 0)))
        self.assertTrue(b.active(datetime(2026, 11, 26, 0, 0, 0)))
        self.assertTrue(b.active(datetime(2026, 11, 26, 21, 59, 59)))
        self.assertFalse(b.active(datetime(2026, 11, 26, 22, 0, 0)))

    def test_authorization_template_is_invalid_on_purpose(self):
        with self.assertRaises(tr.TransportDisabled) as cm:
            tr.load_authorization(
                ROOT / "config" / "authorization.TEMPLATE.json",
                now=datetime(2026, 11, 26, 9, 0), arm_id="A",
                dataset_sha256=SEALED_SHA)
        self.assertEqual(str(cm.exception), "authorization_without_author")

    def test_release_template_is_invalid_on_purpose(self):
        with self.assertRaises(ev.EvalRefused) as cm:
            ev.load_release(ROOT / "config" / "release.TEMPLATE.json",
                            datetime(2026, 11, 27, 9, 0))
        self.assertEqual(cm.exception.reasons, ["release_without_author"])


class ReleaseTests(TempDirCase):
    NOW = datetime(2026, 11, 27, 9, 0)

    def write(self, **over):
        doc = {"authorized_by": "Daniel",
               "authorized_at": "2026-11-27T08:00:00",
               "expires_at": "2026-11-27T20:00:00",
               "h24_closure_1bea10d_satisfactory": True,
               "g1_concluded": True, "express_authorization": True}
        doc.update(over)
        p = self.tmp / "release.json"
        p.write_text(json.dumps(doc))
        return p

    def reasons(self, path, now=None):
        with self.assertRaises(ev.EvalRefused) as cm:
            ev.load_release(path, now or self.NOW)
        return cm.exception.reasons

    def test_valid_release_loads(self):
        doc = ev.load_release(self.write(), self.NOW)
        self.assertEqual(doc["authorized_by"], "Daniel")

    def test_each_condition_is_required(self):
        for flag in ("h24_closure_1bea10d_satisfactory", "g1_concluded",
                     "express_authorization"):
            self.assertEqual(self.reasons(self.write(**{flag: False})),
                             [f"release_condition_not_met:{flag}"])
            self.assertEqual(self.reasons(self.write(**{flag: "yes"})),
                             [f"release_condition_not_met:{flag}"])

    def test_author_dates_and_window(self):
        self.assertEqual(self.reasons(self.write(authorized_by=" ")),
                         ["release_without_author"])
        self.assertEqual(self.reasons(self.write(expires_at="x")),
                         ["release_bad_dates"])
        self.assertEqual(self.reasons(self.write(), datetime(2026, 11, 27, 20, 0)),
                         ["release_not_in_force"])
        self.assertEqual(self.reasons(self.write(), datetime(2026, 11, 27, 7, 0)),
                         ["release_not_in_force"])
        self.assertEqual(
            self.reasons(self.write(expires_at="2026-11-27T20:00:00+00:00")),
            ["release_timezone_mismatch"])
        bad = self.tmp / "bad.json"
        bad.write_text("nope")
        self.assertEqual(self.reasons(bad), ["release_unreadable"])

    def test_hour_alone_is_not_enough_to_run(self):
        # past the blackout hour, but no release: refused, nothing sent
        from helpers import Clock
        clock = Clock(datetime(2026, 11, 27, 9, 0))
        code, status, t = self.run_arm("r", clock=clock, release=None)
        self.assertEqual(code, ev.EXIT_REFUSED)
        self.assertEqual(status["reasons"], ["release_authorization_absent"])
        self.assertEqual(t.requests, [])

    def test_expired_release_refused_and_midrun_expiry_pauses(self):
        from helpers import Clock
        clock = Clock(datetime(2026, 11, 27, 21, 0))
        rel = {"authorized_at": "2026-11-27T08:00:00",
               "expires_at": "2026-11-27T20:00:00"}
        code, status, t = self.run_arm("e", clock=clock, release=rel)
        self.assertEqual(status["reasons"], ["release_not_in_force"])
        clock = Clock(datetime(2026, 11, 27, 19, 59, 0))
        code, status, t = self.run_arm(
            "m", clock=clock, release=rel,
            behaviour=lambda i, r, o: {"latency_s": 30.0})
        self.assertEqual(status["stop_reason"], "release_expired")
        self.assertEqual(len(t.requests), 2)


if __name__ == "__main__":
    unittest.main()
