"""The DRIVER of the overdue evaluation, run against a scripted server on 127.0.0.1.

The driver is `run_overdue_eval.py` (not collected by pytest). Here it runs `execute(...)` with the
real HTTP layer pointed at the scripted server of `test_intelligence_overdue_eval_http.py`. No
Ollama, no model, no inference, no external network. The 12 synthetic scenarios only.
"""

import _thread
import ast
import fnmatch
import json
import sys
import threading
import time
from pathlib import Path

import pytest

import intelligence_overdue_eval_http as hl
import intelligence_overdue_eval_runner as rn
import run_overdue_eval as drv
from test_intelligence_overdue_eval_http import NAME
from test_intelligence_overdue_eval_http import RUNTIME_SERVER
from test_intelligence_overdue_eval_http import assert_one_request_each
from test_intelligence_overdue_eval_http import server  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import COMMIT
from test_intelligence_overdue_eval_runner import DIGEST
from test_intelligence_overdue_eval_runner import OTHER_DIGEST
from test_intelligence_overdue_eval_runner import dataset  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import legacy_json
from test_intelligence_overdue_eval_runner import make_manifest
from test_intelligence_overdue_eval_runner import real_120  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import twelve_case_protocol  # noqa: F401  (autouse)
from test_intelligence_overdue_eval_runner import validator  # noqa: F401  (fixture)
from test_intelligence_overdue_eval_runner import write_dataset


class Setup:
    def __init__(self, tmp_path, dataset_):
        self.root = tmp_path
        self.dataset_path = write_dataset(tmp_path / "dataset.json", legacy_json())
        self.protocol = tmp_path / "protocol.md"
        self.protocol.write_text("DRAFT protocol text\n", encoding="utf-8")
        self.manifest = make_manifest(dataset_, k=1, protocol=rn.file_sha256(self.protocol),
                                      params={"runtime": dict(RUNTIME_SERVER)})
        self.manifest_path = tmp_path / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.hash = self.manifest["manifest_hash"]
        self.round = tmp_path / "round-01"
        self.memory = 32.0

    def execute(self, server_, validator_, *, confirm="default", resume=False, base_url=None, **kw):
        return drv.execute(
            manifest_path=self.manifest_path, dataset_path=self.dataset_path, results_dir=self.round,
            confirm=self.hash if confirm == "default" else confirm, commit=COMMIT, resume=resume,
            base_url=base_url or server_.url, protocol_path=self.protocol,
            memory=lambda: self.memory, validator=validator_, **kw)

    @property
    def results(self):
        return self.round / "results.jsonl"


@pytest.fixture
def setup(tmp_path, dataset):
    return Setup(tmp_path, dataset)


def nothing_was_asked(server_, setup_):
    assert server_.requests == []  # not even the metadata calls
    assert not setup_.round.exists() or list(setup_.round.iterdir()) == []


# --- the gates: zero generation calls --------------------------------------------------------

@pytest.mark.parametrize("confirm", [None, "", "wrong", "0" * 64, "  ", 5])
def test_without_the_exact_confirmation_nothing_is_called_and_nothing_is_created(
        server, setup, validator, confirm) -> None:
    result = setup.execute(server, validator, confirm=confirm)

    assert (result.code, result.status, result.reason) == (2, "refused", "confirmation_missing_or_wrong")
    nothing_was_asked(server, setup)
    assert server.unloads == 0


def test_the_confirmation_must_be_the_exact_hash_not_a_case_variant(server, setup, validator) -> None:
    result = setup.execute(server, validator, confirm=setup.hash.upper())

    assert result.reason == "confirmation_missing_or_wrong" and server.requests == []


def test_a_tampered_manifest_file_is_refused_before_anything(server, setup, validator) -> None:
    manifest = json.loads(setup.manifest_path.read_text(encoding="utf-8"))
    manifest["params"]["seed_base"] = 7
    setup.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = setup.execute(server, validator)

    assert result.reason == "manifest_invalid" and server.requests == []


def test_memory_below_14_gib_means_zero_calls(server, setup, validator) -> None:
    setup.memory = 13.99
    result = setup.execute(server, validator)

    assert (result.code, result.reason) == (2, "low_memory")
    nothing_was_asked(server, setup)


@pytest.mark.parametrize("label, change, reason", [
    ("digest", lambda s: setattr(s, "tags", [(NAME, OTHER_DIGEST)]), "model_mismatch"),
    ("exclusive", lambda s: setattr(s, "ps_models", [
        {"name": "llama3:8b", "digest": OTHER_DIGEST, "size": 1, "size_vram": 1}]), "model_not_exclusive"),
    ("version", lambda s: setattr(s, "version", "0.99.0"), "ollama_version_mismatch"),
    ("template", lambda s: setattr(s, "show", {"template": "OTHER", "parameters": "P"}), "template_mismatch"),
])
def test_a_refused_preflight_means_zero_generation_calls_and_no_unload(
        server, setup, validator, label, change, reason) -> None:
    change(server)
    result = setup.execute(server, validator)

    assert result.code == 2 and result.status == "refused" and reason in result.reason
    assert server.generates() == 0 and server.unloads == 0
    assert ("POST", "/api/generate") not in server.requests  # no inference, no unload
    assert not setup.results.exists()


def test_a_host_outside_the_loopback_is_refused_without_a_single_request(server, setup, validator) -> None:
    result = setup.execute(server, validator, base_url="http://192.0.2.9:11434")

    assert result.code == 2 and result.reason == "setup_failed:NotLoopback"
    assert server.requests == []


def test_a_start_over_an_existing_round_folder_is_refused_and_nothing_is_overwritten(
        server, setup, validator) -> None:
    setup.round.mkdir()
    (setup.round / "results.jsonl").write_bytes(b"precious\n")
    (setup.round / "notes.txt").write_text("keep", encoding="utf-8")
    result = setup.execute(server, validator)

    assert result.reason == "results_dir_not_new" and server.requests == []
    assert (setup.round / "results.jsonl").read_bytes() == b"precious\n"
    assert (setup.round / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_an_existing_but_empty_folder_is_a_valid_new_round(server, setup, validator) -> None:
    setup.round.mkdir()

    assert setup.execute(server, validator).status == "completed"


# --- a whole round -----------------------------------------------------------------------------

def test_a_whole_round_through_the_driver_unloads_the_model_at_the_end_and_never_overwrites(
        server, setup, validator) -> None:
    result = setup.execute(server, validator)

    assert (result.code, result.status, result.unloaded) == (0, "completed", True)
    assert server.generates() == 15 and server.unloads == 1
    assert server.last_unload == {"model": NAME, "keep_alive": 0}  # no prompt at all
    assert server.requests[-1] == ("POST", "/api/generate")  # the unload is the very last request
    assert_one_request_each(server)
    report = rn.report_from_file(setup.results, rn.load_dataset(setup.dataset_path), setup.manifest)
    assert report["complete"] is True and rn.evaluate_criteria(report)["overall"] == "pass"
    anchors = list(setup.round.glob("anchor.*.json"))
    assert len(anchors) == 1 and json.loads(anchors[0].read_text())["manifest_hash"] == setup.hash
    assert len(list((setup.round / "wire").iterdir())) == 30
    before = setup.results.read_bytes()
    requests_before = len(server.requests)

    again = setup.execute(server, validator)  # the same folder again
    assert again.reason == "results_dir_not_new"
    assert setup.results.read_bytes() == before and len(server.requests) == requests_before


def test_a_resume_needs_its_own_flag_the_same_confirmation_and_a_results_file(
        server, setup, validator) -> None:
    assert setup.execute(server, validator, resume=True).reason == "resume_without_results"
    stop = setup.round / "STOP"
    server.hooks[("c002", 0, "abstention")] = lambda: (setup.round).mkdir(exist_ok=True) or stop.write_text("")
    first = setup.execute(server, validator)
    assert (first.code, first.status, first.reason) == (3, "stopped", "operator_stop")

    stop.unlink()
    requests_before = len(server.requests)
    assert setup.execute(server, validator).reason == "results_dir_not_new"  # no flag: a start
    assert setup.execute(server, validator, resume=True, confirm=None).reason == "confirmation_missing_or_wrong"
    assert setup.execute(server, validator, resume=True, confirm="1" * 64).reason == "confirmation_missing_or_wrong"
    assert len(server.requests) == requests_before  # all three refused before a single request

    done = setup.execute(server, validator, resume=True)
    assert (done.code, done.status) == (0, "completed")
    assert server.generates() == 15  # unloads carry no prompt and are not generate calls
    assert server.unloads == 2  # the round ended twice (stop, then finish): unloaded each time
    assert_one_request_each(server)


def test_a_stop_file_present_at_resume_stops_again_at_once_without_a_call(server, setup, validator) -> None:
    stop = setup.round / "STOP"
    server.hooks[("c001", 0, "abstention")] = lambda: (setup.round).mkdir(exist_ok=True) or stop.write_text("")
    assert setup.execute(server, validator).reason == "operator_stop"
    generated = server.generates()
    again = setup.execute(server, validator, resume=True)  # STOP is still there

    assert again.status == "stopped" and again.reason == "operator_stop"
    assert server.generates() == generated


def test_ctrl_c_ends_the_round_unloads_the_model_and_keeps_what_was_received(
        server, setup, validator) -> None:
    def stall(handler, key, data):
        handler.begin()
        handler.line({"model": NAME, "response": "part", "done": False})
        handler.wait_closed(5.0)

    server.behaviors[("c001", 0, "abstention")] = stall
    server.hooks[("c001", 0, "abstention")] = lambda: threading.Timer(0.3, _thread.interrupt_main).start()
    result = setup.execute(server, validator)

    assert (result.code, result.status) == (130, "interrupted")
    assert server.unloads == 1 and result.unloaded is True
    end = time.monotonic() + 2.0
    while ("c001", 0, "abstention") not in server.cut and time.monotonic() < end:
        time.sleep(0.02)
    assert ("c001", 0, "abstention") in server.cut  # the connection was really cut
    calls = rn.merged_calls([json.loads(line) for line in setup.results.read_bytes().splitlines()])
    assert calls[0]["call_status"] == "interrupted" and calls[0]["raw_text"] == "part"


def test_a_preflight_refusal_on_resume_is_recorded_and_makes_no_call(server, setup, validator) -> None:
    stop = setup.round / "STOP"
    server.hooks[("c002", 0, "abstention")] = lambda: (setup.round).mkdir(exist_ok=True) or stop.write_text("")
    setup.execute(server, validator)
    stop.unlink()
    server.tags = [(NAME, OTHER_DIGEST)]
    generated, unloads = server.generates(), server.unloads
    result = setup.execute(server, validator, resume=True)

    assert result.code == 2 and "model_mismatch" in result.reason
    assert server.generates() == generated
    assert server.unloads == unloads  # a refused preflight is not a start: nothing to unload
    assert result.unloaded is False
    assert [r["event"] for r in map(json.loads, setup.results.read_bytes().splitlines())
            if r["kind"] == "event"][-1] == "preflight_refused"


# --- the pieces ---------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="GlobalMemoryStatusEx is a Windows call")
def test_the_memory_probe_reads_available_physical_memory_in_gib() -> None:
    value = drv.free_memory_gib_windows()

    assert isinstance(value, float) and 0.1 < value < 4096


def test_the_driver_is_not_collected_by_pytest_and_writes_only_by_exclusive_create() -> None:
    path = Path(drv.__file__)

    assert not fnmatch.fnmatch(path.name, "test_*.py") and not fnmatch.fnmatch(path.name, "*_test.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modes = [n.args[1].value for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
    assert modes == ["xb"]
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not {"write_text", "write_bytes", "unlink", "remove", "rename", "truncate", "rmtree"} & attributes


def test_the_driver_and_the_http_module_are_pinned_by_the_manifest(dataset) -> None:
    manifest = make_manifest(dataset)
    entries = {e["name"]: e["sha256"] for e in manifest["entries"]}

    assert entries["driver"] == rn.file_sha256(Path(drv.__file__)) == rn.source_hashes()["driver"]
    assert entries["http"] == rn.file_sha256(Path(hl.__file__)) == rn.source_hashes()["http"]
    assert {"http", "driver"} <= set(rn.REQUIRED_ENTRIES)


def test_the_command_line_without_a_confirmation_refuses(server, setup, capsys) -> None:
    code = drv.main(["--manifest", str(setup.manifest_path), "--dataset", str(setup.dataset_path),
                     "--results-dir", str(setup.round), "--commit", COMMIT, "--base-url", server.url])
    out = json.loads(capsys.readouterr().out)

    assert code == 2 and out["reason"] == "confirmation_missing_or_wrong"
    assert server.requests == []
    with pytest.raises(SystemExit):
        drv.parse_args(["--dataset", "x"])  # required arguments


def test_building_a_manifest_from_the_current_files_gives_a_valid_hash(dataset) -> None:
    manifest = drv.build_manifest_for(dataset.file_sha256, "11" * 32, make_manifest(dataset)["params"])

    assert rn.verify_manifest(manifest) == manifest["manifest_hash"]


# --- round 02: nothing silent --------------------------------------------------------------------------

def test_a_failure_to_write_the_anchor_is_reported_not_silenced(server, setup, validator, monkeypatch) -> None:
    def failing(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(rn, "external_anchor", failing)
    result = setup.execute(server, validator)

    assert result.status == "completed" and result.anchor_error.startswith("OSError: disk full")
    assert not list(setup.round.glob("anchor.*.json"))


def test_a_failure_to_unload_is_reported_not_silenced(server, setup, validator, monkeypatch) -> None:
    def failing(self):
        raise ConnectionError("gone")

    monkeypatch.setattr(hl.LoopbackClient, "unload_model", failing)
    result = setup.execute(server, validator)

    assert result.status == "completed" and result.unloaded is False
    assert result.unload_error.startswith("ConnectionError: gone") and result.anchor_error is None


def test_a_clean_run_reports_no_anchor_or_unload_error(server, setup, validator) -> None:
    result = setup.execute(server, validator)

    assert result.anchor_error is None and result.unload_error is None and result.unloaded is True


def test_the_manifest_the_driver_runs_pins_the_validator_files(setup) -> None:
    names = {e["name"] for e in setup.manifest["entries"]}

    assert set(rn.VALIDATOR_FILES) <= names and "planning_validator" in names


# --- round 04: exit codes ----------------------------------------------------------------------------

def test_a_completed_round_whose_anchor_failed_exits_with_the_distinct_housekeeping_code(
        server, setup, validator, monkeypatch) -> None:
    def failing(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(rn, "external_anchor", failing)
    result = setup.execute(server, validator)

    assert result.status == "completed" and result.anchor_error
    assert result.code == drv.EXIT_HOUSEKEEPING == 4
    assert len({drv.EXIT_COMPLETED, drv.EXIT_REFUSED, drv.EXIT_STOPPED, drv.EXIT_INTERRUPTED,
                drv.EXIT_HOUSEKEEPING}) == 5


def test_a_completed_round_whose_unload_failed_exits_with_the_housekeeping_code(
        server, setup, validator, monkeypatch) -> None:
    monkeypatch.setattr(hl.LoopbackClient, "unload_model", lambda self: (_ for _ in ()).throw(ConnectionError("gone")))
    result = setup.execute(server, validator)

    assert result.status == "completed" and result.unload_error and result.code == 4


def test_an_unload_that_answers_something_other_than_200_is_also_housekeeping(
        server, setup, validator, monkeypatch) -> None:
    monkeypatch.setattr(hl.LoopbackClient, "unload_model", lambda self: 500)
    result = setup.execute(server, validator)

    assert result.unload_error == "unload_status_not_200" and result.code == 4 and result.unloaded is False


def test_a_clean_round_still_exits_zero_and_a_stopped_one_keeps_its_own_code(server, setup, validator) -> None:
    stop = setup.round / "STOP"
    server.hooks[("c001", 0, "abstention")] = lambda: stop.write_text("")
    stopped = setup.execute(server, validator)
    stop.unlink()
    done = setup.execute(server, validator, resume=True)

    assert (stopped.code, done.code) == (3, 0) and stopped.anchor_error is None and done.unload_error is None


def test_a_stopped_round_with_a_failing_anchor_keeps_the_stop_code_and_reports_the_error(
        server, setup, validator, monkeypatch) -> None:
    stop = setup.round / "STOP"
    server.hooks[("c001", 0, "abstention")] = lambda: stop.write_text("")
    monkeypatch.setattr(rn, "external_anchor", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    result = setup.execute(server, validator)

    assert result.code == 3 and result.anchor_error


def test_the_command_line_returns_the_code_of_the_result(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(drv, "execute", lambda **kw: drv.DriverResult(4, "completed", None, 9, True, "OSError: x"))
    code = drv.main(["--manifest", "m", "--dataset", "d", "--results-dir", str(tmp_path / "r"), "--commit", "c"])

    assert code == 4 and json.loads(capsys.readouterr().out)["anchor_error"] == "OSError: x"
