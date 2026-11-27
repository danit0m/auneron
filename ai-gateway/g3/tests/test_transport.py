import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from g3eval import cli
from g3eval import transport as tr

from helpers import SEALED_SHA, TempDirCase

NOW = datetime(2026, 11, 26, 9, 0, 0)


def request(think=True):
    return tr.GenRequest(
        model_id="m", instructions="I", input_text="X",
        output_schema={"type": "object"}, options={"seed": 0},
        think=think, timeout_s=300.0)


class PureTests(unittest.TestCase):
    def test_body_omits_think_when_none(self):
        b = tr.build_chat_body(request(None))
        self.assertNotIn("think", b)
        self.assertIs(tr.build_chat_body(request(True))["think"], True)
        self.assertFalse(b["stream"])
        self.assertEqual(b["messages"][0], {"role": "system", "content": "I"})
        self.assertEqual(b["messages"][1], {"role": "user", "content": "X"})
        self.assertEqual(b["format"], {"type": "object"})

    def test_parse_response_keeps_only_lengths_and_numbers(self):
        raw = json.dumps({
            "message": {"content": '{"decision": "no_action", "reason": "r"}',
                        "thinking": "abcdef"},
            "done_reason": "stop", "eval_count": 77, "prompt_eval_count": 650,
            "load_duration": 5_000_000_000, "total_duration": 9_000_000_000,
        }).encode()
        r = tr.parse_chat_response(raw, digest="d", version="v")
        self.assertEqual((r.thinking_length, r.output_tokens,
                          r.prompt_tokens), (6, 77, 650))
        self.assertEqual((r.load_duration_ms, r.total_duration_ms),
                         (5000, 9000))
        self.assertEqual(r.done_reason, "stop")
        self.assertFalse(hasattr(r, "thinking"))

    def test_parse_rejects_garbage_and_bad_counts(self):
        with self.assertRaises(tr.TransportInvalidResponse):
            tr.parse_chat_response(b"nope", digest=None, version=None)
        with self.assertRaises(tr.TransportInvalidResponse):
            tr.parse_chat_response(b'{"x": 1}', digest=None, version=None)
        raw = json.dumps({"message": {"content": "x"}, "eval_count": True,
                          "load_duration": -1}).encode()
        r = tr.parse_chat_response(raw, digest=None, version=None)
        self.assertIsNone(r.output_tokens)
        self.assertIsNone(r.load_duration_ms)


class LockTests(TempDirCase):
    def auth(self, **over):
        doc = {"authorized_by": "Daniel", "authorized_at": "2026-11-26T08:00:00",
               "expires_at": "2026-11-26T20:00:00", "arms": ["A", "B"],
               "dataset_sha256": SEALED_SHA, "max_calls": 3}
        doc.update(over)
        p = self.tmp / "auth.json"
        p.write_text(json.dumps(doc))
        return p

    def build(self, path, **kw):
        args = dict(enabled=True, authorization_path=path, arm_id="A",
                    dataset_sha256=SEALED_SHA, now=lambda: NOW,
                    http=lambda *a: (200, b"{}"))
        args.update(kw)
        return tr.RealOllamaTransport(**args)

    def test_shipped_build_lock_is_closed(self):
        self.assertFalse(tr.REAL_TRANSPORT_BUILD_ENABLED)
        with self.assertRaises(tr.TransportDisabled) as cm:
            self.build(self.auth())
        self.assertEqual(str(cm.exception), "build_lock_closed")

    def test_each_lock_refuses_when_build_lock_is_opened_for_the_test(self):
        with mock.patch.object(tr, "REAL_TRANSPORT_BUILD_ENABLED", True):
            ok = self.auth()
            self.build(ok)  # all three locks open: builds, no network used

            def refused(code, **kw):
                with self.assertRaises(tr.TransportDisabled) as cm:
                    self.build(kw.pop("path", ok), **kw)
                self.assertEqual(str(cm.exception), code)

            refused("flag_not_set", enabled=False)
            refused("authorization_file_absent", path=None)
            refused("authorization_file_absent", path=self.tmp / "no.json")
            refused("host_not_loopback", host="10.0.0.5")
            refused("host_not_loopback", host="example.com")
            refused("no_http_client_injected", http=None)
            refused("authorization_arm_not_listed", arm_id="Z")
            refused("authorization_dataset_mismatch", dataset_sha256="0" * 64)
            refused("authorization_not_in_force",
                    now=lambda: datetime(2026, 11, 26, 20, 0, 0))
            refused("authorization_not_in_force",
                    now=lambda: datetime(2026, 11, 26, 7, 0, 0))
            refused("authorization_without_author",
                    path=self.auth(authorized_by="  "))
            refused("authorization_bad_ceiling",
                    path=self.auth(max_calls=0))
            refused("authorization_bad_ceiling",
                    path=self.auth(max_calls=True))
            refused("authorization_missing:arms", path=self._without("arms"))
            refused("authorization_bad_dates",
                    path=self.auth(expires_at="soon"))
            refused("authorization_timezone_mismatch",
                    path=self.auth(expires_at="2026-11-26T20:00:00+00:00"))

            bad = self.tmp / "junk.json"
            bad.write_text("not json")
            refused("authorization_unreadable", path=bad)

    def _without(self, key):
        p = self.auth()
        doc = json.loads(p.read_text())
        del doc[key]
        p.write_text(json.dumps(doc))
        return p

    def test_call_ceiling_and_identity_with_injected_http(self):
        calls = []

        def http(method, path, body, timeout):
            calls.append((method, path))
            if path == "/api/version":
                return 200, b'{"version": "0.40.16"}'
            if path == "/api/tags":
                return 200, json.dumps({"models": [
                    {"name": "m", "digest": "sha256:ABC"}]}).encode()
            return 200, json.dumps({"message": {"content": "{}"},
                                    "done_reason": "stop"}).encode()

        with mock.patch.object(tr, "REAL_TRANSPORT_BUILD_ENABLED", True):
            t = self.build(self.auth(max_calls=2), http=http)
            ident = t.identity()
            self.assertEqual(ident["digests"], {"m": "abc"})
            t.generate(request())
            t.generate(request())
            with self.assertRaises(tr.TransportDisabled):
                t.generate(request())

        self.assertEqual([c for c in calls if c[1] == "/api/chat"],
                         [("POST", "/api/chat")] * 2)

    def test_cli_run_is_a_refusal_stub(self):
        code = cli.main(["run", "--arm", "A", "--expected-sha256", SEALED_SHA])
        self.assertEqual(code, 2)
        code = cli.main(["run", "--real", "--arm", "A", "--expected-sha256",
                         SEALED_SHA, "--authorization",
                         str(self.auth())])
        self.assertEqual(code, 2)

    def test_module_has_no_network_imports(self):
        src = Path(tr.__file__).read_text(encoding="utf-8")
        for word in ("socket", "urllib", "http.client", "requests"):
            self.assertNotIn("import " + word, src)


if __name__ == "__main__":
    unittest.main()
