"""
SIM-1.5G -- adaptador do Task Scheduler (DETACH-2): XML deterministico e semantica, falha fechada (privilegio, divergencia,
remocao), trilha de auditoria encadeada (adulteracao, perda de registros, ancoras), reconciliacao, P15, `kill`/`assess`,
sonda incremental e juiz de 12 criterios, CLI. TUDO com duplos: nenhuma tarefa real e criada, nenhum processo e iniciado.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from sim.runner import cli
from sim.runner import detach
from sim.runner import detach_probe
from sim.runner import preflight
from sim.runner import sched
from sim.tests.runner_support import good_preflight_facts
from sim.tests.runner_support import make_rim
from sim.tests.sched_support import THIRD_PARTY
from sim.tests.sched_support import FakeHost

RUN = "RUN-aaaaaaaaaaaa-A01"
USER = "PC-X\\tomaz"
ARGS = '/d /v:on /s /c ""C:\\v\\python.exe" -m sim.runner.cli run --run-id X 1>>"C:\\p\\stdout.log" & >"C:\\p\\e.txt" echo !errorlevel!"'
REPO = "C:\\repo"


@pytest.fixture(autouse=True)
def no_real_process(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"processo/tarefa real proibido nos testes: {args[:1]}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


def good_xml(name=RUN, limit="PT30M", arguments=ARGS):
    return sched.build_task_xml(name, USER, arguments, REPO, limit)


def sem(xml, **over):
    kwargs = dict(user_aliases=[USER], arguments=ARGS, workdir=REPO, limit="PT30M", name=RUN)
    kwargs.update(over)
    return sched.validate_semantics(xml, **kwargs)


def launch(tmp_path, host, run_id=RUN, **over):
    kwargs = dict(python="C:\\v\\python.exe", repo=REPO, cli_args="run --run-id X", env={"PYTHONUTF8": "1"}, ledger_state="PREFLIGHTED",
                  python_info={"v": "3.11.9"}, profile="PROBE_L1", run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000)
    kwargs.update(over)
    return detach.launch_detached(tmp_path, run_id, **kwargs)


# ------------------------------------------------------------------------------------------------------ XML
def test_xml_is_deterministic_and_has_no_credentials_triggers_or_restart():
    xml = good_xml()
    assert xml == good_xml() and sched.xml_sha256(xml) == sched.xml_sha256(good_xml())
    assert good_xml(limit="PT2H") != xml
    doc = sched.parse_task_xml(xml)
    assert doc["triggers"] == 0 and len(doc["principals"]) == 1
    principal = doc["principals"][0]
    assert principal == {"UserId": USER, "LogonType": "InteractiveToken", "RunLevel": "LeastPrivilege"}
    assert not {"Password", "RestartOnFailure", "S4U"} & set(doc["tags"]) and "S4U" not in xml and "Highest" not in xml
    assert doc["settings"]["MultipleInstancesPolicy"] == "IgnoreNew" and doc["settings"]["ExecutionTimeLimit"] == "PT30M"
    assert sem(xml) == []


@pytest.mark.parametrize("name", ["", "a b", "..x", "a..b", "x/../y", "x\"y", "a" * 65, "\\Microsoft\\x", "-start", "x;y", "x\ny"])
def test_invalid_task_names_are_refused(name):
    with pytest.raises(sched.SchedError):
        sched.validate_name(name)
    with pytest.raises(sched.SchedError):
        sched.build_task_xml(name, USER, ARGS, REPO, "PT30M")


@pytest.mark.parametrize("limit", ["", None, "PT0S", "PT0H0M", "P1D", "PT", "PT100H", "pt30m", "PT30S", "PT1.5H"])
def test_execution_limit_must_be_explicit_and_finite(limit):
    with pytest.raises(sched.SchedError):
        sched.validate_limit(limit)


def test_xml_escapes_special_characters_and_rejects_control_characters():
    arguments = '/d /s /c "a & b < c > d" "x"'
    xml = sched.build_task_xml(RUN, USER, arguments, REPO, "PT30M")
    assert sched.parse_task_xml(xml)["execs"][0]["Arguments"] == arguments
    with pytest.raises(sched.SchedError):
        sched.build_task_xml(RUN, USER, "a\nb", REPO, "PT30M")
    with pytest.raises(sched.SchedError):
        sched.build_task_xml(RUN, "tomaz", ARGS, REPO, "PT30M")                         # usuario sem DOMINIO\\


@pytest.mark.parametrize("old,new,needle", [
    ("<RunLevel>LeastPrivilege</RunLevel>", "<RunLevel>HighestAvailable</RunLevel>", "RunLevel"),
    ("<LogonType>InteractiveToken</LogonType>", "<LogonType>S4U</LogonType>", "LogonType"),
    ("<LogonType>InteractiveToken</LogonType>", "<LogonType>Password</LogonType><Password>x</Password>", "proibido"),
    ("<Triggers />", "<Triggers><TimeTrigger><StartBoundary>2026-10-09T10:00:00</StartBoundary></TimeTrigger></Triggers>", "gatilho"),
    ("<Priority>7</Priority>", "<Priority>7</Priority><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>",
     "proibido"),
    ("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", "<MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>",
     "MultipleInstancesPolicy"),
    ("<DisallowStartIfOnBatteries>false", "<DisallowStartIfOnBatteries>true", "DisallowStartIfOnBatteries"),
    ("<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>", "", "StopIfGoingOnBatteries"),
    ("<Hidden>true</Hidden>", "<Hidden>false</Hidden>", "Hidden"),
    ("<WakeToRun>false", "<WakeToRun>true", "WakeToRun"),
    ("<StartWhenAvailable>false", "<StartWhenAvailable>true", "StartWhenAvailable"),
    ("<ExecutionTimeLimit>PT30M", "<ExecutionTimeLimit>PT72H", "ExecutionTimeLimit"),
    ("<ExecutionTimeLimit>PT30M</ExecutionTimeLimit>", "", "ExecutionTimeLimit"),
    ("<UserId>PC-X\\tomaz</UserId>", "<UserId>PC-X\\other</UserId>", "principal"),
    ("<Command>C:\\Windows\\System32\\cmd.exe</Command>", "<Command>C:\\x\\evil.exe</Command>", "comando"),
    ("<WorkingDirectory>C:\\repo", "<WorkingDirectory>C:\\other", "diretorio"),
    ("</Exec>", "</Exec><ComHandler><ClassId>{x}</ClassId></ComHandler>", "proibido"),
    ("<URI>\\AuneronSim\\", "<URI>\\Microsoft\\", "URI"),
    ("run --run-id X", "run --run-id Y", "argumentos"),
    ("</Exec>", "</Exec><Exec><Command>C:\\Windows\\System32\\cmd.exe</Command></Exec>", "exatamente 1"),
    ("</Exec>", "</Exec><ShowMessage><Title>t</Title></ShowMessage>", "exatamente 1"),
])
def test_semantic_validation_catches_every_deviation_from_the_design(old, new, needle):
    xml = good_xml()
    assert old in xml
    problems = sem(xml.replace(old, new, 1))
    assert problems and any(needle.lower() in p.lower() for p in problems), problems


def test_semantics_accept_the_windows_normalised_export_form_and_sid_alias():
    exported = good_xml().replace("<RegistrationInfo>", "<RegistrationInfo><Author>PC-X\\tomaz</Author><Date>2026-10-08T12:00:00</Date>", 1)
    exported = exported.replace("<UserId>" + USER + "</UserId>", "<UserId>S-1-5-21-1-1000</UserId>", 1)
    assert sem(exported, user_aliases=[USER, "S-1-5-21-1-1000"]) == []
    assert sem(exported, user_aliases=[USER])                                           # SID desconhecido => reprova


def test_scheduler_scripts_are_constants_without_forbidden_options():
    for script in sched.SCRIPTS:
        low = script.lower()
        assert "-force" not in low and "-password" not in low and "/sc " not in low and "highest" not in low and "s4u" not in low
    assert "-Xml $env:SIM_TASK_XML" in sched.PS_REGISTER and "-User" not in sched.PS_REGISTER
    for script in (sched.PS_REGISTER, sched.PS_START, sched.PS_END, sched.PS_UNREGISTER):
        assert "$env:SIM_TASK_NAME" in script and "$env:SIM_TASK_PATH" in script and "SIM_COM_PATH" not in script
    for script in sched.SCRIPTS:
        assert script.startswith(sched._PRELUDE)                                      # DEF-3: saida UTF-8 em todos os scripts


# ------------------------------------------------------------------------------------------------------ SIM-1.5G.1 (DEF-1/2/3)
def test_def1_integrity_comes_from_the_token_api_not_from_the_group_list():
    script = sched.PS_IDENTITY
    assert script.count("GetTokenInformation(t, 25") == 2 and "SimTokenIntegrity" in script and "integrity_rid=$rid" in script
    assert "S-1-16" not in script and ".Groups" not in script and "medium=" not in script and "high=" not in script


def test_def1_unknown_or_non_medium_tokens_make_the_scan_report_elevation(tmp_path):
    for rid in (None, 0x3000, 0x1000, -1):
        host = FakeHost(tmp_path, integrity=rid)
        assert sched.scan_unreconciled(tmp_path, host, alive=host.alive)["elevated"] is True
    host = FakeHost(tmp_path)
    assert sched.scan_unreconciled(tmp_path, host, alive=host.alive)["elevated"] is False
    host = FakeHost(tmp_path, admin=True)
    assert sched.scan_unreconciled(tmp_path, host, alive=host.alive)["elevated"] is True


def test_def2_com_paths_have_no_trailing_backslash_and_cmdlet_paths_do():
    assert sched.COM_FOLDER == "\\AuneronSim" and sched.FOLDER == "\\AuneronSim\\"
    assert sched.validate_com_path("\\AuneronSim") == "\\AuneronSim"
    for bad in ("\\AuneronSim\\", "AuneronSim", "\\", "\\Microsoft", ""):
        with pytest.raises(sched.SchedError):
            sched.validate_com_path(bad)
    env = sched._task_env(RUN)
    assert env["SIM_COM_PATH"] == "\\AuneronSim" and env["SIM_TASK_PATH"] == "\\AuneronSim\\"
    for script in (sched.PS_INFO, sched.PS_LIST, sched.PS_DELETE_FOLDER):
        assert "$svc.GetFolder($env:SIM_COM_PATH)" in script and "SIM_TASK_PATH" not in script
    assert sched.PS_DELETE_FOLDER.count("GetFolder(") == 2 and "GetFolder('\\')" in sched.PS_DELETE_FOLDER


def test_def2_the_windows_contract_is_enforced_by_the_fake_for_both_path_forms(tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    assert sched.list_folder(host) == {"folder_exists": False, "tasks": [], "folders": []}
    assert sched.info(host, RUN) == {"found": False, "folder_exists": False}
    monkeypatch.setattr(sched, "COM_FOLDER", "\\AuneronSim\\")                       # o defeito original
    with pytest.raises(sched.SchedError, match="0x8007007B"):
        sched.list_folder(host)
    with pytest.raises(sched.SchedError, match="0x8007007B"):
        sched.info(host, RUN)
    monkeypatch.setattr(sched, "COM_FOLDER", "\\AuneronSim")
    monkeypatch.setattr(sched, "FOLDER", "\\AuneronSim")                               # cmdlet sem barra final
    with pytest.raises(sched.SchedError, match="TaskPath"):
        sched.register(host, RUN, good_xml())


def test_def2_only_the_two_genuine_not_found_hresults_mean_absent():
    assert sched._NOT_FOUND == "(-2147024894, -2147024893)"
    assert str(-2147024773) not in sched._NOT_FOUND                                    # 0x8007007B (nome invalido) NAO e "ausente"
    for script in (sched.PS_INFO, sched.PS_LIST):
        assert script.count("-in " + sched._NOT_FOUND) == (2 if script is sched.PS_INFO else 1) and "else { throw }" in script


def test_def3_python_decodes_utf8_explicitly_and_keeps_the_diagnostic(tmp_path):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(kwargs)
        return type("R", (), {"stdout": "", "stderr": "A sintaxe do nome do diretório está incorreta (0x8007007B)", "returncode": 1})()

    with pytest.raises(sched.SchedError, match="0x8007007B") as error:
        sched.list_folder(run)
    assert "diretório" in str(error.value) and seen["encoding"] == "utf-8" and seen["errors"] == "replace" and "text" not in seen


def test_def3_diagnostic_falls_back_to_stdout_and_unreadable_output_is_never_a_success():
    def make(stdout, stderr, rc):
        return lambda cmd, **kw: type("R", (), {"stdout": stdout, "stderr": stderr, "returncode": rc})()

    with pytest.raises(sched.SchedError, match="so no stdout"):
        sched.list_folder(make("so no stdout", "", 1))
    with pytest.raises(sched.SchedError, match="bytes nao decodificaveis"):
        sched.list_folder(make('{"folder_exists": true, "tasks": [], "folders": []}�', "", 0))             # falso sucesso proibido
    with pytest.raises(sched.SchedError, match="nao e JSON"):
        sched.list_folder(make("", "", 0))
    assert sched.list_folder(make('{"folder_exists": false, "tasks": [], "folders": []}', "aviso", 0))["folder_exists"] is False
    with pytest.raises(sched.SchedError, match="sem confirmacao"):
        sched.start(make("", "", 0), RUN)


# ------------------------------------------------------------------------------------------------------ privilegio
@pytest.mark.parametrize("field,value", [("admin_role", True), ("admin_role", None), ("integrity_rid", 0x3000), ("integrity_rid", 0x4000),
                                         ("integrity_rid", 0x1000), ("integrity_rid", 0), ("integrity_rid", -1), ("integrity_rid", None),
                                         ("integrity_rid", True), ("integrity_rid", "8192"), ("integrity_rid", 8192.0), ("integrity_rid", 8193),
                                         ("schedule_state", "Stopped"), ("user", "tomaz")])
def test_unexpected_privilege_or_service_state_fails_closed_without_registering(tmp_path, field, value):
    host = FakeHost(tmp_path)
    host.who[field] = value
    with pytest.raises(detach.DetachError):
        launch(tmp_path, host)
    assert "register" not in host.labels() and not host.tasks
    assert not (detach.proc_dir(tmp_path, RUN) / detach.LAUNCH).exists()


def test_require_unelevated_returns_the_identity_when_it_matches_the_measured_one(tmp_path):
    assert sched.require_unelevated(FakeHost(tmp_path).who)["user"] == "PC-X\\tomaz"
    with pytest.raises(sched.UnexpectedPrivilege):
        sched.require_unelevated({"admin_role": False, "integrity_rid": None, "schedule_state": "Running", "user": USER})
    with pytest.raises(sched.UnexpectedPrivilege):
        sched.require_unelevated({"admin_role": False, "schedule_state": "Running", "user": USER})                 # RID ausente
    assert sched.is_unelevated({"admin_role": False, "integrity_rid": 0x2000}) is True
    assert sched.is_unelevated({"admin_role": False, "integrity_rid": 0x2001}) is False


# ------------------------------------------------------------------------------------------------------ lancamento
def test_launch_registers_verifies_starts_and_records_everything(tmp_path):
    host = FakeHost(tmp_path)
    doc = launch(tmp_path, host)
    assert host.labels() == ["identity", "register", "info", "start", "ptable"]
    assert doc["mechanism"] == "task_scheduler" and doc["task"] == "\\AuneronSim\\" + RUN and doc["engine_pids"] == [900]
    assert doc["heartbeat_pid"] == 901 and doc["pid"] == 900 and doc["profile"] == "PROBE_L1" and doc["limit"] == "PT30M"
    saved = json.loads((detach.proc_dir(tmp_path, RUN) / detach.LAUNCH).read_text())
    assert saved["xml_sha256"] == sched.xml_sha256(host.tasks[RUN]["xml"]) and saved["user"] == USER
    blob = json.dumps(saved).lower()
    assert "password" not in blob and "s4u" not in blob
    assert [(c[0], c[2]) for c in host.calls if c[2]] == [("register", "\\AuneronSim\\"), ("info", "\\AuneronSim"),
                                                          ("start", "\\AuneronSim\\")]
    audit = sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN)
    events = [r["event"] for r in audit.records()]
    assert events == ["REGISTERED", "EXPORT_VERIFIED", "STARTED", "HEARTBEAT_SEEN"] and audit.verify()["ok"]
    assert audit.verify()["state"] == "STARTED"


def test_action_runs_the_absolute_venv_interpreter_with_the_same_redirection_as_before(tmp_path):
    host = FakeHost(tmp_path)
    launch(tmp_path, host, python="C:\\Users\\x\\.auneron-sim\\venv\\Scripts\\python.exe")
    action = sched.parse_task_xml(host.tasks[RUN]["xml"])["execs"][0]
    assert action["Command"] == sched.CMD_EXE and action["WorkingDirectory"] == REPO
    assert action["Arguments"].startswith('/d /v:on /s /c ""C:\\Users\\x\\.auneron-sim\\venv\\Scripts\\python.exe" -m sim.runner.cli ')
    assert "stdout.log" in action["Arguments"] and "exit_code.txt" in action["Arguments"]
    assert detach.build_cmdline("P", "a", detach.proc_dir(tmp_path, RUN)).startswith("cmd.exe /d /v:on /s /c ")


def test_wmi_is_rejected_and_disabled_by_default(tmp_path):
    host = FakeHost(tmp_path)
    with pytest.raises(detach.DetachError, match="REJECTED_BY_DETACH1"):
        launch(tmp_path, host, mechanism="wmi")
    with pytest.raises(detach.DetachError, match="REJECTED_BY_DETACH1"):
        launch(tmp_path, host, mechanism="wmi", allow_rejected=1)
    with pytest.raises(detach.DetachError, match="desconhecido"):
        launch(tmp_path, host, mechanism="cron")
    assert host.calls == []


@pytest.mark.parametrize("state", ["CREATED", "RUNNING", "COMPLETED", "ABORTED", None])
def test_only_a_preflighted_run_is_launched_by_the_scheduler(tmp_path, state):
    host = FakeHost(tmp_path)
    with pytest.raises(detach.DetachError, match="PREFLIGHTED"):
        launch(tmp_path, host, ledger_state=state)
    assert host.calls == []


def test_unknown_profile_and_second_launch_are_refused(tmp_path):
    host = FakeHost(tmp_path)
    with pytest.raises(detach.DetachError, match="perfil"):
        launch(tmp_path, host, profile="FOREVER")
    launch(tmp_path, host)
    with pytest.raises(detach.DetachError, match="ja foi lancado"):
        launch(tmp_path, host)
    assert host.labels().count("register") == 1


def test_register_never_overwrites_an_existing_task(tmp_path):
    host = FakeHost(tmp_path)
    host.tasks[RUN] = {"xml": "x", "state": 3, "last": 0, "instances": []}
    host.folder_exists = True
    with pytest.raises(detach.DetachError, match="Task Scheduler"):
        launch(tmp_path, host)
    assert host.tasks[RUN]["xml"] == "x" and "start" not in host.labels()


@pytest.mark.parametrize("break_it", [
    lambda x: x.replace("InteractiveToken", "S4U"),
    lambda x: x.replace("<Triggers />", "<Triggers><TimeTrigger /></Triggers>"),
    lambda x: x.replace("IgnoreNew", "Parallel"),
    lambda x: x.replace("LeastPrivilege", "HighestAvailable"),
    lambda x: x.replace("PT30M", "PT72H"),
])
def test_divergent_exported_configuration_removes_the_task_and_fails_closed(tmp_path, break_it):
    host = FakeHost(tmp_path, export=break_it)
    with pytest.raises(detach.DetachError, match="diverge"):
        launch(tmp_path, host)
    assert RUN not in host.tasks and "start" not in host.labels()
    events = [r["event"] for r in sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN).records()]
    assert events == ["REGISTERED", "CONFIG_DIVERGENCE", "UNREGISTERED"]


def test_divergence_with_an_unremovable_task_is_recorded_as_removal_failed(tmp_path):
    host = FakeHost(tmp_path, export=lambda x: x.replace("InteractiveToken", "S4U"), fail_unregister=True)
    with pytest.raises(detach.DetachError, match="diverge"):
        launch(tmp_path, host)
    events = [r["event"] for r in sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN).records()]
    assert events[-1] == "REMOVAL_FAILED" and RUN in host.tasks


def test_missing_heartbeat_is_reported_and_the_task_is_kept_for_reconciliation(tmp_path):
    host = FakeHost(tmp_path, heartbeat=False)
    ticks = iter(range(1000, 100000, 20))
    with pytest.raises(detach.DetachError, match="heartbeat"):
        launch(tmp_path, host, clock=lambda: next(ticks))
    audit = sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN)
    assert audit.records()[-1]["event"] == "START_TIMEOUT" and RUN in host.tasks


def test_only_the_aunerons_folder_is_ever_addressed_and_foreign_tasks_are_untouched(tmp_path):
    host = FakeHost(tmp_path)
    launch(tmp_path, host)
    sched.list_folder(host)
    for label, name, path in host.calls:
        assert path in (None, "\\AuneronSim\\", "\\AuneronSim") and name != THIRD_PARTY
    with pytest.raises(sched.SchedError):
        sched.validate_path("\\")
    with pytest.raises(sched.SchedError):
        sched.validate_path("\\Microsoft\\Windows\\")
    assert host.third_party == {THIRD_PARTY: {"state": 3}}


def test_a_xml_that_already_violates_the_design_is_refused_before_anything_is_registered(tmp_path):
    host = FakeHost(tmp_path)
    audit = sched.Audit(tmp_path / "p", tmp_path / "a", RUN)
    bad = good_xml().replace("InteractiveToken", "S4U")
    with pytest.raises(sched.ConfigDivergence, match="ja viola"):
        sched.register_verified(host, RUN, bad, user_aliases=[USER], arguments=ARGS, workdir=REPO, limit="PT30M", audit=audit)
    assert host.calls == [] and audit.records() == []


# ------------------------------------------------------------------------------------------------------ auditoria
def make_audit(tmp_path, n=4, name=RUN):
    audit = sched.Audit(tmp_path / "p", tmp_path / "anchors", name, clock=lambda: 7)
    for i in range(n):
        audit.append(["REGISTERED", "EXPORT_VERIFIED", "STARTED", "STOPPED"][i % 4], {"i": i})
    return audit


def test_audit_chain_verifies_and_anchors_exist_in_two_places(tmp_path):
    audit = make_audit(tmp_path)
    result = audit.verify()
    assert result["ok"] and result["records"] == 4 and result["state"] == "STOPPED"
    assert all(a.exists() for a in audit.anchors) and len({a.parent for a in audit.anchors}) == 2
    assert [r["seq"] for r in audit.records()] == [1, 2, 3, 4]


def test_empty_audit_is_ok_and_has_no_state(tmp_path):
    result = sched.Audit(tmp_path / "p", tmp_path / "a", RUN).verify()
    assert result["ok"] and result["state"] is None and result["records"] == 0


def rewrite(audit, lines):
    audit.path.write_bytes(b"".join(lines))


def test_tampering_with_a_record_is_detected(tmp_path):
    audit = make_audit(tmp_path)
    lines = audit.path.read_bytes().splitlines(keepends=True)
    lines[1] = lines[1].replace(b'"i":1', b'"i":9')
    rewrite(audit, lines)
    result = audit.verify()
    assert not result["ok"] and "AUDIT_TAMPERED" in result["codes"]


def test_a_removed_middle_record_is_detected_as_missing(tmp_path):
    audit = make_audit(tmp_path)
    lines = audit.path.read_bytes().splitlines(keepends=True)
    rewrite(audit, [lines[0], lines[2], lines[3]])
    result = audit.verify()
    assert not result["ok"] and "AUDIT_RECORDS_MISSING" in result["codes"]


def test_truncating_the_tail_is_detected_through_the_anchors(tmp_path):
    audit = make_audit(tmp_path)
    lines = audit.path.read_bytes().splitlines(keepends=True)
    rewrite(audit, lines[:2])
    result = audit.verify()
    assert not result["ok"] and "AUDIT_RECORDS_MISSING" in result["codes"] and any("truncada" in p for p in result["problems"])


def test_reordering_records_is_detected(tmp_path):
    audit = make_audit(tmp_path)
    lines = audit.path.read_bytes().splitlines(keepends=True)
    rewrite(audit, [lines[0], lines[2], lines[1], lines[3]])
    assert not audit.verify()["ok"]


def test_a_sequence_gap_is_detected_even_when_chain_and_anchors_were_rebuilt_consistently(tmp_path):
    audit = make_audit(tmp_path)
    records = audit.records()
    kept = [records[0], records[2], records[3]]                                # some o registro 2; seq 1,3,4
    previous = audit.genesis
    forged = []
    for record in kept:
        record = {**record, "prev": previous}
        record["hash"] = audit._hash(record)
        previous = record["hash"]
        forged.append(record)
    rewrite(audit, [sched.canonical_bytes(r) for r in forged])
    for anchor_path in audit.anchors:                                            # atacante com acesso local reescreve as ancoras
        anchor_path.write_bytes(sched.canonical_bytes({"task": RUN, "seq": 3, "hash": forged[-1]["hash"]}))
    result = audit.verify()
    assert not result["ok"] and result["codes"] == ["AUDIT_RECORDS_MISSING"]


@pytest.mark.parametrize("which", [0, 1])
def test_a_missing_anchor_is_detected(tmp_path, which):
    audit = make_audit(tmp_path)
    audit.anchors[which].unlink()
    result = audit.verify()
    assert not result["ok"] and "AUDIT_ANCHOR_MISSING" in result["codes"]


def test_replacing_the_whole_trail_with_a_forged_valid_chain_is_caught_by_the_anchors(tmp_path):
    audit = make_audit(tmp_path)
    audit.path.unlink()
    forged = sched.Audit(tmp_path / "p", tmp_path / "other-anchors", RUN, clock=lambda: 9)
    for event in ("REGISTERED", "EXPORT_VERIFIED", "STARTED", "STOPPED"):
        forged.append(event, {"forged": True})
    result = audit.verify()
    assert not result["ok"] and "AUDIT_TAMPERED" in result["codes"]


def test_unreadable_trail_and_wrong_task_genesis_are_refused(tmp_path):
    audit = make_audit(tmp_path)
    audit.path.write_bytes(b"not json\n")
    assert "AUDIT_TAMPERED" in audit.verify()["codes"]
    other = sched.Audit(tmp_path / "p", tmp_path / "anchors", "RUN-bbbbbbbbbbbb-A01")
    other.path.write_bytes(make_audit(tmp_path / "x").path.read_bytes())
    assert not other.verify()["ok"]


# ------------------------------------------------------------------------------------------------------ remocao
def started(tmp_path, **over):
    host = FakeHost(tmp_path, **over)
    launch(tmp_path, host)
    proc = detach.proc_dir(tmp_path, RUN)
    return host, proc, sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, RUN)


def finish_run(host, proc, name=RUN):
    """O run terminou: processo morto, `exit.json` gravado e a tarefa voltou a Ready (estado normal depois do fim)."""
    host.alive_pids.clear()
    host.tasks[name].update(state=sched.STATE_READY, instances=[], last=0)
    (proc / detach.EXIT).write_text("{}")


def test_evidence_is_preserved_before_the_task_is_unregistered_and_removal_is_verified(tmp_path):
    host, proc, audit = started(tmp_path)
    seen = {}

    def hook(h, label, name):
        if label == "unregister":
            seen["files"] = sorted(p.name for p in (proc / sched.EVIDENCE_DIR).iterdir())

    host.hook = hook
    result = sched.remove_verified(host, RUN, proc, audit)
    assert seen["files"] == ["manifest.json", "task_export.xml", "task_info.json"]
    assert result["removed"] and result["folder_removed"] and RUN not in host.tasks and not host.folder_exists
    events = [r["event"] for r in audit.records()]
    assert events.index("EVIDENCE_PRESERVED") < events.index("UNREGISTERED") and audit.verify()["state"] == "UNREGISTERED"


def test_a_task_that_cannot_be_removed_fails_closed_and_keeps_the_evidence(tmp_path):
    host, proc, audit = started(tmp_path, fail_unregister=True)
    with pytest.raises(sched.RemovalNotVerified):
        sched.remove_verified(host, RUN, proc, audit)
    assert (proc / sched.EVIDENCE_DIR / "task_export.xml").exists() and audit.verify()["state"] == "REMOVAL_FAILED"


def test_a_task_that_survives_unregister_is_not_reported_as_removed(tmp_path):
    host, proc, audit = started(tmp_path, keep_after_unregister=True)
    with pytest.raises(sched.RemovalNotVerified, match="continua registrada"):
        sched.remove_verified(host, RUN, proc, audit)
    assert audit.verify()["state"] == "REMOVAL_FAILED"


def test_the_folder_is_deleted_only_when_empty(tmp_path):
    host, proc, audit = started(tmp_path)
    host.tasks["other"] = {"xml": "x", "state": 3, "last": 0, "instances": []}
    assert sched.remove_verified(host, RUN, proc, audit)["folder_removed"] is False and host.folder_exists
    host.tasks.clear()
    assert sched.delete_folder_if_empty(host) is True and not host.folder_exists
    assert sched.delete_folder_if_empty(host) is False


# ------------------------------------------------------------------------------------------------------ reconciliacao / P15
def reconcile(tmp_path, host, proc, state, **over):
    kwargs = dict(proc=proc, anchors_dir=tmp_path / sched.ANCHOR_DIR, name=RUN, ledger_state=state, alive=host.alive, markers=[RUN])
    kwargs.update(over)
    return sched.reconcile(host, **kwargs)


def codes(report):
    return sorted({f["code"] for f in report["findings"]})


def test_terminal_run_with_a_registered_task_is_an_orphan_and_apply_removes_it_after_preserving_evidence(tmp_path):
    host, proc, audit = started(tmp_path)
    finish_run(host, proc)
    assert codes(reconcile(tmp_path, host, proc, "COMPLETED")) == ["ORPHAN_TASK"]
    report = reconcile(tmp_path, host, proc, "COMPLETED", apply=True)
    assert report["actions"][0]["removed"] is True and RUN not in host.tasks
    assert reconcile(tmp_path, host, proc, "COMPLETED")["ok"] is True


def test_compromised_audit_blocks_removal_without_confirm(tmp_path):
    host, proc, audit = started(tmp_path)
    finish_run(host, proc)
    lines = audit.path.read_bytes().splitlines(keepends=True)
    audit.path.write_bytes(b"".join([lines[0].replace(b"REGISTERED", b"REGISTERED ")] + lines[1:]))
    blocked = reconcile(tmp_path, host, proc, "ABORTED", apply=True)
    assert "AUDIT_TAMPERED" in codes(blocked) and "REMOVAL_BLOCKED" in codes(blocked) and RUN in host.tasks
    forced = reconcile(tmp_path, host, proc, "ABORTED", apply=True, confirm=True)
    assert forced["actions"] and RUN not in host.tasks


def test_state_divergence_is_detected_in_both_directions(tmp_path):
    host, proc, audit = started(tmp_path)
    host.tasks.pop(RUN)                                                                   # sumiu por fora
    assert "STATE_DIVERGENCE" in codes(reconcile(tmp_path, host, proc, "RUNNING"))
    host2, proc2, audit2 = started(tmp_path / "b")
    audit2.append("UNREGISTERED", {})                                                    # trilha diz removida, tarefa existe
    assert "STATE_DIVERGENCE" in codes(reconcile(tmp_path / "b", host2, proc2, "RUNNING"))


def test_unexpected_exit_is_reported_and_points_to_seal_unclean_without_resuming(tmp_path):
    host, proc, audit = started(tmp_path)
    host.alive_pids.clear()
    host.tasks[RUN]["state"], host.tasks[RUN]["instances"] = sched.STATE_READY, []
    report = reconcile(tmp_path, host, proc, "RUNNING", apply=True)
    assert "UNEXPECTED_EXIT" in codes(report) and any(f.get("action") == "seal-unclean" for f in report["findings"])
    assert RUN in host.tasks and report["actions"] == []                                  # nao remove nem retoma


def test_a_running_probe_is_never_removed_even_with_apply(tmp_path):
    host, proc, audit = started(tmp_path)
    report = reconcile(tmp_path, host, proc, None, apply=True)
    assert RUN in host.tasks and "REMOVAL_BLOCKED" in codes(report) and report["process_alive"] is True


def test_a_terminal_ledger_with_a_still_running_task_or_process_is_never_removed(tmp_path):
    host, proc, audit = started(tmp_path)
    report = reconcile(tmp_path, host, proc, "ABORTED", apply=True)                      # processo vivo e tarefa Running
    assert RUN in host.tasks and report["actions"] == [] and "REMOVAL_BLOCKED" in codes(report)
    host.alive_pids.clear()                                                              # morto, mas a tarefa ainda diz Running
    assert reconcile(tmp_path, host, proc, "ABORTED", apply=True)["actions"] == [] and RUN in host.tasks


def test_orphan_process_without_task_and_marker_processes_are_reported(tmp_path):
    host, proc, audit = started(tmp_path)
    host.tasks.pop(RUN)
    assert "ORPHAN_PROCESS" in codes(reconcile(tmp_path, host, proc, "COMPLETED"))
    host2, proc2, audit2 = started(tmp_path / "c")
    host2.alive_pids = {777}
    host2.procs = [{"pid": 777, "ppid": 1, "name": "python.exe"}]
    (proc2 / detach.EXIT).write_text("{}")
    host2.tasks[RUN]["state"], host2.tasks[RUN]["instances"] = sched.STATE_READY, []
    assert "ORPHAN_PROCESS" in codes(reconcile(tmp_path / "c", host2, proc2, "COMPLETED"))


def test_a_previous_failed_removal_is_reported_by_reconcile(tmp_path):
    host, proc, audit = started(tmp_path, fail_unregister=True)
    with pytest.raises(sched.RemovalNotVerified):
        sched.remove_verified(host, RUN, proc, audit)
    assert "REMOVAL_FAILED" in codes(reconcile(tmp_path, host, proc, "COMPLETED"))


def test_scan_reports_residues_and_p15_blocks_every_kind_of_residue(tmp_path):
    clean = FakeHost(tmp_path)
    facts = sched.scan_unreconciled(tmp_path, clean, alive=clean.alive)
    assert (facts["folder_tasks"], facts["unreconciled_runs"], facts["orphan_processes"]) == (0, 0, 0) and facts["scheduler_running"]
    host, proc, audit = started(tmp_path / "d")
    dirty = sched.scan_unreconciled(tmp_path / "d", host, alive=host.alive)
    assert dirty["folder_tasks"] == 1 and dirty["unreconciled_runs"] == 1 and dirty["tasks"] == [RUN]
    host.alive_pids.clear()
    host.tasks.clear()
    host.alive_pids = {901}
    again = sched.scan_unreconciled(tmp_path / "d", host, alive=host.alive)
    assert again["orphan_processes"] == 1


GOOD_P15 = {"scheduler_running": True, "folder_tasks": 0, "unreconciled_runs": 0, "orphan_processes": 0, "reboot_pending": False,
            "elevated": False}


def test_p15_accepts_only_a_clean_host_and_each_residue_fails_alone():
    assert preflight.judge_p15(dict(GOOD_P15)) == []
    for key, value in (("scheduler_running", False), ("folder_tasks", 1), ("unreconciled_runs", 2), ("orphan_processes", 1),
                       ("reboot_pending", True), ("elevated", True), ("reboot_pending", None), ("folder_tasks", True)):
        assert preflight.judge_p15({**GOOD_P15, key: value}), key


def test_p15_is_part_of_the_preflight_and_a_missing_fact_fails_closed():
    rim = make_rim()
    facts = good_preflight_facts(rim)
    assert preflight.judge_preflight(facts, rim) == []
    bad = {**facts, "P15": {**GOOD_P15, "folder_tasks": 1}}
    assert [f["check"] for f in preflight.judge_preflight(bad, rim)] == ["P15"]
    gone = {k: v for k, v in facts.items() if k != "P15"}
    assert preflight.judge_preflight(gone, rim)[0]["check"] == "FACTS" and "P15" in preflight.REQUIRED


# ------------------------------------------------------------------------------------------------------ kill / assess
def test_forced_end_needs_confirmation_stops_the_instance_and_keeps_the_task(tmp_path):
    host, proc, audit = started(tmp_path)
    with pytest.raises(detach.DetachError, match="--confirm"):
        detach.kill_task(proc, confirm=False, run=host)
    assert host.tasks[RUN]["state"] == sched.STATE_RUNNING
    result = detach.kill_task(proc, confirm=True, run=host)
    assert result["state_after"] == sched.STATE_READY and result["instances_after"] == 0 and RUN in host.tasks
    assert audit.records()[-1]["event"] == "FORCED_END" and (proc / detach.KILLED).exists()


def test_forced_end_refuses_runs_not_launched_by_the_scheduler(tmp_path):
    proc = detach.proc_dir(tmp_path, RUN)
    proc.mkdir(parents=True)
    (proc / detach.LAUNCH).write_text(json.dumps({"run_id": RUN, "mechanism": "wmi.Win32_Process.Create"}))
    with pytest.raises(detach.DetachError, match="Task Scheduler"):
        detach.kill_task(proc, confirm=True, run=FakeHost(tmp_path))


def test_assess_flags_orphan_tasks_and_processes_without_a_task(tmp_path):
    host, proc, audit = started(tmp_path)
    task = {"found": True, "state": 3, "last_result": 0, "instances": []}
    done = detach.assess(proc, "COMPLETED", alive=lambda pid: False, task=task)
    assert done["state"] == "TERMINAL" and done["orphan_task"] is True and done["action"] == "sched-reconcile"
    running = detach.assess(proc, "RUNNING", alive=lambda pid: True, now=lambda: 1001, task={"found": False})
    assert running["state"] == "UNEXPECTED_EXIT" and running["action"] == "seal-unclean"
    plain = detach.assess(proc, "RUNNING", alive=lambda pid: True, now=lambda: 1001)
    assert "task" not in plain and plain["state"] in ("RUNNING", "STARTING")


# ------------------------------------------------------------------------------------------------------ juiz DETACH-2
def good_facts():
    return {"pid": 901, "session_id": 1, "interactive_session": 1,
            "interpreter": {"prefix": "C:\\Users\\x\\.auneron-sim\\venv", "base_prefix": "C:\\Python311", "executable": "C:\\py",
                            "expected_prefix": "C:\\Users\\x\\.auneron-sim\\venv", "include_system_site_packages": "false"},
            "import_sim": True, "launch_json_read": True, "docker": [0, "29.6.2"], "git": [0, "git version"],
            "path": {"docker": True, "git": True}, "keep_awake": True, "heartbeat_written": True, "stop_obeyed": True}


def good_monitor(level="L1"):
    required = detach_probe.LEVELS[level]["required_s"]
    return {"task": "\\AuneronSim\\X", "xml_sha256": "ab", "registered_verified": True,
            "schedule": {"pid": 555, "name": "svchost.exe", "session": 0},
            "engine": {"pid": 900, "ppid": 555, "name": "cmd.exe", "session": 1}, "engine_tree": [900, 902, 901],
            "heartbeat_pid_in_tree": True, "launcher_pid": 4084, "launcher_alive": False, "probe_alive_before_stop": True,
            "beats": {"max_gap_s": 5, "duration_s": required, "survived_after_s": required - 5}, "exit": {"code": 0, "reason": "STOP"},
            "exit_code_txt": 0, "task_state_after": 3, "last_result_after": 0, "triggers": 0, "restart_on_failure": False,
            "instances": {"engines_max": 1, "engines_min_while_alive": 1, "distinct_engine_pids": [900], "samples": 120,
                          "sample_errors": 0, "second_start_attempted": True, "dense_samples_after_second_start": 20,
                          "start_records": 1, "beat_pids": [901]},
            "removal_verified": True, "orphan_processes": 0, "evidence_manifest_ok": True,
            "audit_ok": True, "folder_clean": True, "session_end": {"t": 100, "crosses": True, "gap_at_cross_s": 5}}


def test_detach2_has_exactly_twelve_binary_criteria_and_l1_passes_with_d2_12_not_proven():
    assert len(detach_probe.CRITERIA2) == 12 and len(set(detach_probe.CRITERIA2)) == 12
    verdict = detach_probe.judge_detach2(good_facts(), good_monitor(), "L1")
    assert verdict["verdict"] == "PASS" and verdict["failed"] == [] and verdict["not_proven"] == ["D2-12_session_end_continuity"]
    l2 = detach_probe.judge_detach2(good_facts(), good_monitor("L2"), "L2")
    assert l2["verdict"] == "PASS" and l2["not_proven"] == [] and len(l2["passed"]) == 12


@pytest.mark.parametrize("criterion,mutate", [
    ("D2-01_task_as_requested", lambda f, m: m.update(registered_verified=False)),
    ("D2-01_task_as_requested", lambda f, m: m.update(task="\\Microsoft\\X")),
    ("D2-01_task_as_requested", lambda f, m: m.update(xml_sha256=None)),
    ("D2-02_scheduler_parentage", lambda f, m: m["schedule"].update(pid=1)),
    ("D2-02_scheduler_parentage", lambda f, m: m["schedule"].update(pid=None)),
    ("D2-02_scheduler_parentage", lambda f, m: m["schedule"].update(name="explorer.exe")),
    ("D2-02_scheduler_parentage", lambda f, m: m.update(engine={})),
    ("D2-02_scheduler_parentage", lambda f, m: m["engine"].update(ppid=1)),
    ("D2-02_scheduler_parentage", lambda f, m: m["engine"].update(name="python.exe")),
    ("D2-02_scheduler_parentage", lambda f, m: m["engine"].update(session=0)),
    ("D2-02_scheduler_parentage", lambda f, m: m.update(heartbeat_pid_in_tree=False)),
    ("D2-02_scheduler_parentage", lambda f, m: m.update(launcher_pid=901)),
    ("D2-02_scheduler_parentage", lambda f, m: m.update(launcher_pid=None)),
    ("D2-03_same_session", lambda f, m: f.update(session_id=0)),
    ("D2-03_same_session", lambda f, m: f.update(session_id=None)),
    ("D2-04_isolated_interpreter", lambda f, m: f["interpreter"].update(prefix="C:\\Python311")),
    ("D2-04_isolated_interpreter", lambda f, m: f["interpreter"].update(include_system_site_packages="true")),
    ("D2-04_isolated_interpreter", lambda f, m: f["interpreter"].update(prefix="C:\\Python311", expected_prefix="C:\\Python311")),
    ("D2-04_isolated_interpreter", lambda f, m: f.update(import_sim=False)),
    ("D2-04_isolated_interpreter", lambda f, m: f.update(launch_json_read=False)),
    ("D2-05_docker_git_path", lambda f, m: f.update(docker=[1, ""])),
    ("D2-05_docker_git_path", lambda f, m: f.update(git=[None, "x"])),
    ("D2-05_docker_git_path", lambda f, m: f["path"].update(docker=False)),
    ("D2-06_keep_awake", lambda f, m: f.update(keep_awake=False)),
    ("D2-06_keep_awake", lambda f, m: f.update(heartbeat_written=False)),
    ("D2-07_heartbeat_continuous", lambda f, m: m["beats"].update(max_gap_s=16)),
    ("D2-07_heartbeat_continuous", lambda f, m: m["beats"].update(max_gap_s=None)),
    ("D2-07_heartbeat_continuous", lambda f, m: m["beats"].update(duration_s=899)),
    ("D2-08_survives_launcher", lambda f, m: m.update(launcher_alive=True)),
    ("D2-08_survives_launcher", lambda f, m: m.update(probe_alive_before_stop=False)),
    ("D2-08_survives_launcher", lambda f, m: m["beats"].update(survived_after_s=839)),
    ("D2-09_stop_exit_clean", lambda f, m: f.update(stop_obeyed=False)),
    ("D2-09_stop_exit_clean", lambda f, m: m.update(exit={"code": 1, "reason": "TIMEOUT"})),
    ("D2-09_stop_exit_clean", lambda f, m: m.update(exit_code_txt=None)),
    ("D2-09_stop_exit_clean", lambda f, m: m.update(task_state_after=4)),
    ("D2-09_stop_exit_clean", lambda f, m: m.update(last_result_after=1)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m.update(triggers=1)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m.update(restart_on_failure=True)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(engines_max=2)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(engines_max=0)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(engines_min_while_alive=0)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(engines_min_while_alive=None)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(distinct_engine_pids=[900, 950])),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(start_records=2)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(start_records=0)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(beat_pids=[901, 903])),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(second_start_attempted=False)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(dense_samples_after_second_start=7)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m["instances"].update(sample_errors=1)),
    ("D2-10_no_trigger_restart_parallel", lambda f, m: m.update(instances={})),
    ("D2-11_removed_clean_evidence", lambda f, m: m.update(removal_verified=False)),
    ("D2-11_removed_clean_evidence", lambda f, m: m.update(orphan_processes=1)),
    ("D2-11_removed_clean_evidence", lambda f, m: m.update(evidence_manifest_ok=False)),
    ("D2-11_removed_clean_evidence", lambda f, m: m.update(audit_ok=False)),
    ("D2-11_removed_clean_evidence", lambda f, m: m.update(folder_clean=False)),
])
def test_each_detach2_criterion_failing_alone_rejects_the_probe(criterion, mutate):
    facts, monitor = good_facts(), good_monitor()
    mutate(facts, monitor)
    verdict = detach_probe.judge_detach2(facts, monitor, "L1")
    assert verdict["verdict"] == "FAIL" and criterion in verdict["failed"] and len(verdict["failed"]) == 1


@pytest.mark.parametrize("mutate", [lambda m: m.update(session_end=None), lambda m: m["session_end"].update(crosses=False),
                                    lambda m: m["session_end"].update(gap_at_cross_s=16), lambda m: m["session_end"].update(t=None)])
def test_session_end_continuity_is_required_for_l2_only(mutate):
    monitor = good_monitor("L2")
    mutate(monitor)
    assert detach_probe.judge_detach2(good_facts(), monitor, "L2")["failed"] == ["D2-12_session_end_continuity"]
    l1 = detach_probe.judge_detach2(good_facts(), monitor, "L1")
    assert l1["verdict"] == "PASS" and l1["not_proven"] == ["D2-12_session_end_continuity"]


def test_detach2_treats_missing_facts_as_failure():
    assert len(detach_probe.judge_detach2({}, {}, "L1")["failed"]) == 11
    assert len(detach_probe.judge_detach2({}, {}, "L2")["failed"]) == 12


def test_beat_statistics_measure_gaps_survival_and_session_crossing():
    beats = [{"t": t} for t in (0, 5, 10, 30, 35)]
    stats = detach_probe.beat_stats(beats, after=10, cross=20)
    assert stats["max_gap_s"] == 20 and stats["duration_s"] == 35 and stats["survived_after_s"] == 25
    assert stats["crosses"] is True and stats["gap_at_cross_s"] == 20
    assert detach_probe.beat_stats([])["max_gap_s"] is None and detach_probe.beat_stats(beats, cross=99)["crosses"] is False


# ------------------------------------------------------------------------------------------------------ sonda incremental
class Clock:
    def __init__(self, t=1000):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def probe_ports(**over):
    ports = {"job": lambda: {"in_job": True, "kill_on_close": False}, "session": lambda: 1, "run": lambda cmd: (0, "ok"),
             "keep_awake": lambda: True, "which": lambda n: "C:\\bin\\" + n,
             "prefix": lambda: ("C:\\venv", "C:\\Python311", "C:\\venv\\Scripts\\python.exe")}
    ports.update(over)
    return ports


def probe_proc(tmp_path):
    proc = tmp_path / "p"
    proc.mkdir()
    (proc / detach.LAUNCH).write_text(json.dumps({"python": "C:\\venv\\Scripts\\python.exe", "pid": 1, "t_launch": 1}))
    return proc


def test_probe_writes_facts_incrementally_beats_and_obeys_stop(tmp_path):
    proc, clock = probe_proc(tmp_path), Clock()

    def sleep(s):
        clock.sleep(s)
        if clock.t >= 1030:
            (proc / detach.STOP).write_text("go")

    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(), sleep=sleep, clock=clock)
    phases = [json.loads(line)["phase"] for line in (proc / detach_probe.FACTS_LOG).read_text().splitlines()]
    assert phases == ["start", "import", "job", "docker", "git", "keep_awake", "heartbeat", "exit"]
    assert facts["stop_obeyed"] is True and facts["interpreter"]["expected_prefix"] == "C:\\venv" and facts["path"] == {"docker": True, "git": True}
    exit_doc = json.loads((proc / detach.EXIT).read_text())
    assert exit_doc["code"] == 0 and exit_doc["reason"] == "STOP"
    assert clock.t <= 1040                                                          # parou ao ver o STOP, nao no limite (600 s)
    beats = detach_probe.beat_series(proc)
    assert len(beats) >= 6 and all(b["t"] - a["t"] <= detach_probe.BEAT_EVERY_S + 1 for a, b in zip(beats, beats[1:]))


def test_probe_without_stop_times_out_and_records_it(tmp_path):
    proc, clock = probe_proc(tmp_path), Clock()
    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=30, ports=probe_ports(), sleep=clock.sleep, clock=clock)
    assert facts["stop_obeyed"] is False and json.loads((proc / detach.EXIT).read_text()) == {"code": 1, "reason": "TIMEOUT", "t_epoch": clock.t}


def test_facts_written_before_a_mid_run_death_are_preserved(tmp_path):
    proc, clock = probe_proc(tmp_path), Clock()

    def dies(cmd):
        raise KeyboardInterrupt("morte simulada")

    with pytest.raises(KeyboardInterrupt):
        detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(run=dies), sleep=clock.sleep, clock=clock)
    saved = json.loads((proc / detach_probe.FACTS).read_text())
    assert saved["phase"] == "job" and saved["pid"] == os.getpid() and saved["job"]["in_job"] is True and saved["import_sim"] is True
    assert "docker" not in saved and not (proc / detach.EXIT).exists()


def test_probe_cli_entry_selects_the_detach2_collector(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(detach_probe, "collect2", lambda proc, **kw: seen.update(kw, proc=str(proc)))
    monkeypatch.setattr(detach_probe, "collect", lambda proc, **kw: seen.update(old=True))
    assert detach_probe.main(["--proc-dir", str(tmp_path), "--interactive-session", "1", "--detach2", "--cap-s", "99"]) == 0
    assert seen["cap_s"] == 99 and "old" not in seen
    assert detach_probe.main(["--proc-dir", str(tmp_path), "--interactive-session", "1"]) == 0 and seen["old"] is True


# ------------------------------------------------------------------------------------------------------ orquestracao da sonda
def probe_world(tmp_path, level="L1", **host_over):
    host = FakeHost(tmp_path, **host_over)
    name = "DETACH2-PROBE-L1-20261008T120000Z" if level == "L1" else "DETACH2-PROBE-L2-20261008T120000Z"
    doc = detach_probe.probe_launch(tmp_path, name, level=level, python="C:\\venv\\Scripts\\python.exe", repo=REPO, interactive_session=1,
                                    run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000)
    return host, name, doc, detach.proc_dir(tmp_path, name)


def write_probe_outputs(proc, level, *, start=2990, gap=5, good=True):
    required = detach_probe.LEVELS[level]["required_s"]
    with (proc / detach_probe.BEATS).open("w", encoding="utf-8") as handle:
        for i in range(required // gap + 2):
            handle.write(json.dumps({"seq": i + 1, "t": start + i * gap, "pid": 901}) + "\n")
    facts = good_facts()
    facts["interpreter"]["expected_prefix"] = facts["interpreter"]["prefix"]
    (proc / detach_probe.FACTS).write_text(json.dumps(facts))
    (proc / detach_probe.FACTS_LOG).write_text(json.dumps({"phase": "start", "pid": 901}, sort_keys=True) + "\n"
                                               + json.dumps({"phase": "exit", "stop_obeyed": True}, sort_keys=True) + "\n")
    (proc / detach.HEARTBEAT).write_text(json.dumps({"pid": 901, "t_epoch": start + required, "status": {}}))


def test_probe_launch_uses_the_probe_module_the_probe_profile_and_one_task(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    action = sched.parse_task_xml(host.tasks[name]["xml"])
    assert "-m sim.runner.detach_probe" in action["execs"][0]["Arguments"] and "--detach2" in action["execs"][0]["Arguments"]
    assert action["settings"]["ExecutionTimeLimit"] == "PT30M" and len(host.tasks) == 1 and doc["profile"] == "PROBE_L1"
    assert sched.LIMITS["PROBE_L2"] == "PT2H" and sched.LIMITS["NH_RUN"] == "PT14H" and sched.LIMITS["SR1"] == "PT7H"
    assert detach_probe.probe_name("L1", clock=lambda: 0) == "DETACH2-PROBE-L1-19700101T000000Z"


def run_monitor_and_finish(tmp_path, host, name, proc, level="L1", *, probe_dies=False, tweak=None, **over):
    write_probe_outputs(proc, level)
    if tweak:
        tweak(proc)

    def sleep(seconds):
        if (proc / detach.STOP).exists() and not (proc / detach.EXIT).exists():
            (proc / detach.EXIT).write_text(json.dumps({"code": 0, "reason": "STOP", "t_epoch": 1}))
            (proc / detach.EXIT_CODE).write_text("0\n")
            host.tasks[name].update(state=sched.STATE_READY, instances=[], last=0)
            host.alive_pids -= {host.engine_pid, host.probe_pid, host.launcher_pid}        # so a arvore da tarefa termina

    if probe_dies:
        host.alive_pids.clear()
    # o lancador (passo A) ja terminou: o `launcher_pid` gravado na trilha nao esta vivo
    monitor = detach_probe.probe_monitor(tmp_path, name, level=level, run=host, alive=host.alive, sleep=sleep, clock=lambda: 3000,
                                         poll_s=1, **over)
    final = detach_probe.probe_finish(tmp_path, name, level=level, run=host, alive=host.alive, clock=lambda: 3100)
    return monitor, final


def test_end_to_end_l1_probe_with_doubles_passes_and_leaves_nothing_behind(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert final["verdict"]["verdict"] == "PASS", final["verdict"]
    assert final["verdict"]["not_proven"] == ["D2-12_session_end_continuity"]
    assert name not in host.tasks and not host.folder_exists and final["orphans"] == []
    assert (proc / detach_probe.VERDICT_JSON).exists() and (proc / detach_probe.EVIDENCE_MANIFEST).exists()
    assert (proc / sched.EVIDENCE_DIR / "task_export.xml").exists()
    inst = monitor["instances"]
    assert inst["engines_max"] == 1 and inst["engines_min_while_alive"] == 1 and inst["distinct_engine_pids"] == [900]
    assert inst["dense_samples_after_second_start"] == detach_probe.DENSE_SAMPLES and inst["sample_errors"] == 0
    assert inst["start_records"] == 1 and inst["beat_pids"] == [901] and monitor["launcher_alive"] is False
    assert monitor["engine"]["ppid"] == 555 and monitor["heartbeat_pid_in_tree"] is True and monitor["engine_tree"] == [900, 901, 902]
    audit = sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, name)
    assert audit.verify()["ok"] and audit.verify()["state"] == "UNREGISTERED"


def test_end_to_end_l2_requires_the_session_end_marker_to_be_crossed(tmp_path):
    host, name, doc, proc = probe_world(tmp_path, "L2")
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, "L2", session_end_t=3500)
    assert final["verdict"]["verdict"] == "PASS" and final["verdict"]["not_proven"] == []
    host2, name2, doc2, proc2 = probe_world(tmp_path / "x", "L2")
    monitor2, final2 = run_monitor_and_finish(tmp_path / "x", host2, name2, proc2, "L2")
    assert final2["verdict"]["failed"] == ["D2-12_session_end_continuity"]


def test_a_probe_that_died_fails_and_the_task_is_still_removed_with_evidence(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, probe_dies=True)
    verdict = final["verdict"]
    assert verdict["verdict"] == "FAIL" and "D2-08_survives_launcher" in verdict["failed"] and "D2-09_stop_exit_clean" in verdict["failed"]
    assert name not in host.tasks and (proc / sched.EVIDENCE_DIR / "manifest.json").exists()


def test_probe_finish_fails_when_the_task_cannot_be_removed(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    host.fail_unregister = True
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert "D2-11_removed_clean_evidence" in final["verdict"]["failed"] and final["verdict"]["verdict"] == "FAIL"
    assert name in host.tasks


def test_probe_finish_needs_the_monitor_first_and_evidence_is_verified(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    with pytest.raises(detach.DetachError, match="monitor"):
        detach_probe.probe_finish(tmp_path, name, level="L1", run=host, alive=host.alive)
    assert detach_probe.verify_evidence(proc) is False
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert detach_probe.verify_evidence(proc) is True
    (proc / detach_probe.BEATS).rename(proc / "beats.moved")                          # evidencia obrigatoria ausente
    assert detach_probe.verify_evidence(proc) is False
    (proc / "beats.moved").rename(proc / detach_probe.BEATS)
    (proc / sched.EVIDENCE_DIR / "task_export.xml").write_text("adulterado")
    assert detach_probe.verify_evidence(proc) is False


# ------------------------------------------------------------------------------------------------------ CLI
def test_cli_launch_defaults_to_the_scheduler_and_refuses_wmi_without_the_explicit_flag(tmp_path, monkeypatch, capsys):
    from sim.tests.runner_support import setup_run

    run = setup_run(tmp_path)
    root = str(run.registry.root)
    seen = {}
    monkeypatch.setattr(detach, "launch_detached", lambda *a, **k: seen.update(k) or {"ok": True})
    assert cli.main(["launch", "--run-id", run.run_id, "--runs-root", root, "--python", "C:\\v\\python.exe", "--repo", REPO,
                     "--instance", "i"]) == 0
    assert seen["mechanism"] == "task_scheduler" and seen["allow_rejected"] is False and seen["profile"] == "SR1"
    capsys.readouterr()
    monkeypatch.undo()
    assert cli.main(["launch", "--run-id", run.run_id, "--runs-root", root, "--python", "C:\\v\\python.exe", "--repo", REPO,
                     "--instance", "i", "--mechanism", "wmi"]) == 3
    assert "REJECTED_BY_DETACH1" in capsys.readouterr().out


def test_cli_status_kill_list_audit_and_reconcile_use_the_scheduler(tmp_path, monkeypatch, capsys):
    host = FakeHost(tmp_path)
    monkeypatch.setattr(sched, "run_default", host)
    launch(tmp_path, host)
    root = str(tmp_path)
    assert cli.main(["status", "--run-id", RUN, "--runs-root", root]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["task"]["found"] is True and status["task"]["state"] == sched.STATE_RUNNING
    assert cli.main(["sched-list", "--runs-root", root]) == 0 and RUN in capsys.readouterr().out
    assert cli.main(["sched-audit-verify", "--run-id", RUN, "--runs-root", root]) == 0
    capsys.readouterr()
    assert cli.main(["kill", "--run-id", RUN, "--runs-root", root]) == 3
    assert "--confirm" in capsys.readouterr().out
    assert cli.main(["kill", "--run-id", RUN, "--runs-root", root, "--confirm"]) == 0
    assert host.tasks[RUN]["state"] == sched.STATE_READY and host.labels().count("end") == 1             # Stop-ScheduledTask, nao taskkill
    assert sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN).records()[-1]["event"] == "FORCED_END"
    capsys.readouterr()
    assert cli.main(["sched-reconcile", "--run-id", RUN, "--runs-root", root]) in (0, 1)
    capsys.readouterr()
    audit = sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN)
    audit.path.write_bytes(audit.path.read_bytes()[:-30])
    assert cli.main(["sched-audit-verify", "--run-id", RUN, "--runs-root", root]) == 1


def test_cli_status_reports_a_scheduler_error_without_crashing(tmp_path, monkeypatch, capsys):
    host = FakeHost(tmp_path)
    launch(tmp_path, host)

    def broken(cmd, **kwargs):
        return type("R", (), {"stdout": "", "stderr": "x", "returncode": 1})()

    monkeypatch.setattr(sched, "run_default", broken)
    assert cli.main(["status", "--run-id", RUN, "--runs-root", str(tmp_path)]) == 0
    assert "task_error" in capsys.readouterr().out


def test_cli_sched_probe_requires_its_arguments(tmp_path, capsys):
    assert cli.main(["sched-probe", "--runs-root", str(tmp_path), "--phase", "launch", "--level", "L1"]) == 3
    assert cli.main(["sched-probe", "--runs-root", str(tmp_path), "--phase", "monitor", "--level", "L1"]) == 3
    assert cli.main(["sched-probe", "--runs-root", str(tmp_path), "--phase", "finish", "--level", "L9"]) == 3
    capsys.readouterr()
    with pytest.raises(SystemExit):
        cli.main(["sched-probe", "--runs-root", str(tmp_path)])


# ------------------------------------------------------------------------------------------------------ estaticos
def test_no_recurring_force_credential_or_wmi_path_exists_in_the_standard_code():
    import inspect

    source = inspect.getsource(sched).lower()
    for forbidden in ("/sc ", "schtasks", "-force", "-password", "s4u\"", "runlevel highest", "win32_process.create", "invoke-cimmethod"):
        assert forbidden not in source, forbidden
    launcher = inspect.getsource(detach.launch_detached)
    assert 'allow_rejected is not True' in launcher and "wmi" in launcher
    assert "schtasks" not in inspect.getsource(detach).lower() and "Register-ScheduledTask" not in inspect.getsource(detach)


# ------------------------------------------------------------------------------------------------------ SIM-1.5G.2 (DEF-4/5/6)
def table_entry(pid, ppid, name, cmd="", session=1):
    return {"pid": pid, "ppid": ppid, "name": name, "session": session, "cmd": cmd}


def test_def4_engines_are_cmd_children_of_the_schedule_service_carrying_the_task_marker():
    marker = "C:\\Runs\\X.proc"
    table = [table_entry(555, 1, "svchost.exe", session=0),
             table_entry(900, 555, "cmd.exe", f'cmd /c "py --proc-dir "{marker}""'),
             table_entry(902, 900, "python.exe", "venv"), table_entry(901, 902, "python.exe", "base"),
             table_entry(910, 555, "cmd.exe", "cmd /c outra tarefa"),                       # outro cmd do Scheduler: ignorado
             table_entry(920, 1, "cmd.exe", f"x {marker}"),                                  # marcador certo, pai errado: ignorado
             table_entry(930, 555, "python.exe", f"y {marker}")]                             # filho do Scheduler mas nao e cmd: ignorado
    inst = sched.task_instances(table, 555, marker.lower())
    assert [e["pid"] for e in inst["engines"]] == [900] and inst["engines"][0]["tree"] == [900, 901, 902]
    assert inst["schedule"]["pid"] == 555 and inst["schedule"]["name"] == "svchost.exe"
    two = table + [table_entry(950, 555, "CMD.EXE", f"cmd /c {marker.upper()}")]
    assert [e["pid"] for e in sched.task_instances(two, 555, marker)["engines"]] == [900, 950]
    assert sched.task_instances(table, 999, marker)["engines"] == [] and sched.task_instances(table, 999, marker)["schedule"] is None
    with pytest.raises(sched.SchedError):
        sched.task_instances(table, 555, "x.proc")                                          # marcador curto demais
    assert sched.descendants([table_entry(1, 2, "a"), table_entry(2, 1, "b"), table_entry(3, 2, "c")], 2) == [1, 3]   # a prova de ciclos


def test_def4_process_table_validates_the_schedule_pid_and_uses_the_constant_script(tmp_path):
    host = FakeHost(tmp_path)
    for bad in (0, -1, None, True, "555"):
        with pytest.raises(sched.SchedError):
            sched.process_table(host, bad)
    assert host.calls == []
    table = sched.process_table(host, 555)
    assert any(p["pid"] == 555 for p in table) and host.labels() == ["ptable"]
    assert "Get-CimInstance Win32_Process" in sched.PS_PTABLE and "$env:SIM_SCHEDULE_PID" in sched.PS_PTABLE


def test_def4_launch_takes_the_engine_from_the_process_tree_and_survives_a_failed_lookup(tmp_path):
    host = FakeHost(tmp_path)
    doc = launch(tmp_path, host)
    assert doc["engine_pids"] == [900] and doc["pid"] == 900
    host2 = FakeHost(tmp_path / "b", ptable_errors={1})
    doc2 = launch(tmp_path / "b", host2)
    assert doc2["engine_pids"] == [] and doc2["pid"] == doc2["heartbeat_pid"]
    events = [r["event"] for r in sched.Audit(detach.proc_dir(tmp_path / "b", RUN), tmp_path / "b" / sched.ANCHOR_DIR, RUN).records()]
    assert "ENGINE_LOOKUP_FAILED" in events


def second_start_hook(extra):
    """Depois da 2a chamada `start`, cada `ptable` seguinte ganha `extra` (instancia que 'sobrevive')."""
    def hook(host, label, name):
        if label == "start" and host.labels().count("start") == 2:
            host.table_extra = {host.ptable_calls + i: extra for i in range(1, 400)}
    return hook


SECOND_ENGINE = [table_entry(950, 555, "cmd.exe", "cmd /c --proc-dir {proc}"), table_entry(951, 950, "python.exe", "x")]


def second_engine(host_name_proc):
    return [dict(e, cmd=e["cmd"].replace("{proc}", str(host_name_proc))) for e in SECOND_ENGINE]


def test_def4_a_second_instance_that_persists_fails_d2_10_and_the_single_engine_requirement(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    host.hook = second_start_hook(second_engine(proc))
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["instances"]["engines_max"] == 2 and sorted(monitor["instances"]["distinct_engine_pids"]) == [900, 950]
    assert monitor["engine"] == {}                                                       # 2 motores: nao ha "o" motor
    assert {"D2-02_scheduler_parentage", "D2-10_no_trigger_restart_parallel"} <= set(final["verdict"]["failed"])


def test_def4_a_transient_second_instance_in_the_dense_window_is_still_caught(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)

    def hook(h, label, task):
        if label == "start" and h.labels().count("start") == 2:
            h.table_extra = {h.ptable_calls + 4: second_engine(proc)}                     # aparece em UMA unica foto
    host.hook = hook
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["instances"]["engines_max"] == 2 and monitor["engine"]["pid"] == 900   # a foto final tem 1 motor
    assert final["verdict"]["failed"] == ["D2-10_no_trigger_restart_parallel"]


def test_def4_sampling_gaps_fail_closed(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)

    def hook(h, label, task):
        if label == "start" and h.labels().count("start") == 2:
            h.ptable_errors = {h.ptable_calls + 3}
    host.hook = hook
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["instances"]["sample_errors"] == 1
    assert final["verdict"]["failed"] == ["D2-10_no_trigger_restart_parallel"]


def test_def4_the_dense_window_covers_the_second_start_and_the_baseline_passes_d2_02_and_d2_10(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    inst = monitor["instances"]
    assert inst["dense_samples_after_second_start"] == detach_probe.DENSE_SAMPLES >= detach_probe.DENSE_MIN
    assert inst["second_start_attempted"] is True and host.labels().count("start") == 2
    assert {"D2-02_scheduler_parentage", "D2-10_no_trigger_restart_parallel"} <= set(final["verdict"]["passed"])
    assert monitor["schedule"] == {"pid": 555, "name": "svchost.exe", "session": 0}


def test_def4_parentage_is_measured_in_the_process_table_not_through_the_heartbeat(tmp_path):
    host, name, doc, proc = probe_world(tmp_path, table_probe_pid=907)                  # heartbeat diz 901; a tabela tem 907
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["engine"]["pid"] == 900 and monitor["engine"]["ppid"] == 555         # motor achado SEM o heartbeat
    assert monitor["heartbeat_pid_in_tree"] is False and monitor["engine_tree"] == [900, 902, 907]
    assert final["verdict"]["failed"] == ["D2-02_scheduler_parentage"]


def test_def4_an_engine_whose_parent_is_not_the_schedule_service_fails_d2_02(tmp_path):
    host, name, doc, proc = probe_world(tmp_path, engine_parent=1)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["instances"]["engines_max"] == 0 and monitor["engine"] == {}        # sem filho do Schedule com o marcador
    assert {"D2-02_scheduler_parentage", "D2-10_no_trigger_restart_parallel"} <= set(final["verdict"]["failed"])


def test_def4_durable_witnesses_a_second_start_phase_or_a_second_beat_pid_fail_d2_10(tmp_path):
    def two_starts(proc):
        with (proc / detach_probe.FACTS_LOG).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"phase": "start", "pid": 999}) + "\n")

    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, tweak=two_starts)
    assert monitor["instances"]["start_records"] == 2 and "D2-10_no_trigger_restart_parallel" in final["verdict"]["failed"]

    def two_pids(proc):
        with (proc / detach_probe.BEATS).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"seq": 9999, "t": 10 ** 6, "pid": 903}) + "\n")

    host2, name2, doc2, proc2 = probe_world(tmp_path / "b")
    monitor2, final2 = run_monitor_and_finish(tmp_path / "b", host2, name2, proc2, tweak=two_pids)
    assert monitor2["instances"]["beat_pids"] == [901, 903] and "D2-10_no_trigger_restart_parallel" in final2["verdict"]["failed"]


def test_def4_kill_counts_instances_with_the_process_table(tmp_path):
    host, proc, audit = started(tmp_path)
    result = detach.kill_task(proc, confirm=True, run=host)
    assert result["instances_after"] == 0 and host.labels()[-2:] == ["identity", "ptable"]


# ---- DEF-6: orfaos x arvore do inspetor
INSPECTOR = [{"pid": os.getpid(), "name": "python.exe", "ppid": 7001}, {"pid": 7001, "name": "python.exe", "ppid": 1}]


def test_def6_the_inspector_tree_is_pid_plus_ancestors_and_fails_closed_when_unknown(tmp_path):
    host = FakeHost(tmp_path)
    assert sched.inspector_tree(host) == {os.getpid(), 7001}
    for bad in ([], [{"pid": 1234, "ppid": 1, "name": "python.exe", "session": 1, "cmd": ""}]):
        with pytest.raises(sched.SchedError, match="arvore do processo inspetor"):
            sched.inspector_tree(FakeHost(tmp_path, inspector_chain=bad))


def test_def6_only_the_inspector_tree_is_excluded_real_orphans_with_the_same_marker_remain(tmp_path):
    host = FakeHost(tmp_path, procs=INSPECTOR + [{"pid": 777, "name": "python.exe", "ppid": 1}, {"pid": 778, "name": "python.exe", "ppid": 1}])
    host.alive_pids = {os.getpid(), 7001, 777}                                              # 778 ja morreu
    found = sched.find_orphans(host, "DETACH2-PROBE-L1-X", alive=host.alive)
    assert [p["pid"] for p in found["orphans"]] == [777]
    assert sorted(p["pid"] for p in found["excluded"]) == sorted([os.getpid(), 7001]) and found["inspector_tree"] == sorted([os.getpid(), 7001])
    clean = FakeHost(tmp_path, procs=INSPECTOR)
    clean.alive_pids = {os.getpid(), 7001}
    assert sched.find_orphans(clean, "DETACH2-PROBE-L1-X", alive=clean.alive)["orphans"] == []


def test_def6_probe_finish_ignores_its_own_tree_but_not_real_orphans_and_fails_closed(tmp_path):
    host, name, doc, proc = probe_world(tmp_path, procs=INSPECTOR)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert final["verdict"]["verdict"] == "PASS" and final["orphans"] == []
    assert sorted(p["pid"] for p in final["inspector_tree_excluded"]) == sorted([os.getpid(), 7001])

    host2, name2, doc2, proc2 = probe_world(tmp_path / "b", procs=INSPECTOR + [{"pid": 777, "name": "python.exe", "ppid": 1}])
    host2.alive_pids |= {777}
    monitor2, final2 = run_monitor_and_finish(tmp_path / "b", host2, name2, proc2)
    assert [p["pid"] for p in final2["orphans"]] == [777] and final2["verdict"]["failed"] == ["D2-11_removed_clean_evidence"]

    host3, name3, doc3, proc3 = probe_world(tmp_path / "c", procs=INSPECTOR, inspector_chain=[])
    monitor3, final3 = run_monitor_and_finish(tmp_path / "c", host3, name3, proc3)
    assert final3["verdict"]["failed"] == ["D2-11_removed_clean_evidence"]                   # arvore indeterminada => reprova
    assert json.loads((proc3 / detach_probe.VERDICT_JSON).read_text())["orphans"] == []


def test_def6_reconcile_excludes_the_inspector_tree_only_and_reports_an_undetermined_tree(tmp_path):
    host, proc, audit = started(tmp_path, procs=INSPECTOR + [{"pid": 777, "name": "python.exe", "ppid": 1}])
    finish_run(host, proc)
    host.alive_pids = {os.getpid(), 7001, 777}
    report = reconcile(tmp_path, host, proc, "COMPLETED")
    orphan = [f for f in report["findings"] if f["code"] == "ORPHAN_PROCESS"]
    assert orphan and "1 processo(s)" in orphan[0]["detail"]                                   # so o 777
    host.alive_pids = {os.getpid(), 7001}
    assert "ORPHAN_PROCESS" not in codes(reconcile(tmp_path, host, proc, "COMPLETED"))
    host.inspector_chain = []
    undetermined = reconcile(tmp_path, host, proc, "COMPLETED")
    assert "INSPECTOR_TREE_UNDETERMINED" in codes(undetermined) and undetermined["ok"] is False


def test_def4_kill_reports_a_tree_that_survived_the_forced_end(tmp_path):
    host, proc, audit = started(tmp_path, end_leaves_running=True)
    result = detach.kill_task(proc, confirm=True, run=host)
    assert result["instances_after"] == 1 and result["state_after"] == sched.STATE_RUNNING


def test_def5_the_monitor_waits_for_the_exit_code_that_cmd_writes_after_python_ends(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    write_probe_outputs(proc, "L1")
    state = {"stop_seen": 0}

    def sleep(seconds):
        if (proc / detach.STOP).exists():
            if not (proc / detach.EXIT).exists():
                (proc / detach.EXIT).write_text(json.dumps({"code": 0, "reason": "STOP", "t_epoch": 1}))
                host.tasks[name].update(state=sched.STATE_READY, instances=[], last=0)
            state["stop_seen"] += 1
            if state["stop_seen"] == 5:                                    # o `cmd` grava o codigo DEPOIS do Python
                (proc / detach.EXIT_CODE).write_text("0\r\n")

    monitor = detach_probe.probe_monitor(tmp_path, name, level="L1", run=host, alive=host.alive, sleep=sleep, clock=lambda: 3000, poll_s=1)
    assert monitor["exit_code_txt"] == 0 and state["stop_seen"] >= 5
    # exit.json NUNCA substitui exit_code.txt: sem o arquivo, o criterio D2-09 reprova
    host2, name2, doc2, proc2 = probe_world(tmp_path / "b")
    write_probe_outputs(proc2, "L1")

    def sleep2(seconds):
        if (proc2 / detach.STOP).exists() and not (proc2 / detach.EXIT).exists():
            (proc2 / detach.EXIT).write_text(json.dumps({"code": 0, "reason": "STOP", "t_epoch": 1}))
            host2.tasks[name2].update(state=sched.STATE_READY, instances=[], last=0)

    monitor2 = detach_probe.probe_monitor(tmp_path / "b", name2, level="L1", run=host2, alive=host2.alive, sleep=sleep2,
                                          clock=lambda: 3000, poll_s=1)
    assert monitor2["exit"]["code"] == 0 and monitor2["exit_code_txt"] is None
    verdict = detach_probe.judge_detach2(good_facts(), {**good_monitor(), "exit_code_txt": monitor2["exit_code_txt"]}, "L1")
    assert verdict["failed"] == ["D2-09_stop_exit_clean"]


def test_def4_samples_taken_after_the_probe_died_do_not_count_as_engines_missing_while_alive(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)

    def hook(h, label, task):
        if label == "start" and h.labels().count("start") == 2:
            h.alive_pids -= {h.probe_pid}                                          # a sonda morre logo depois da 2a partida
            h.engine_parent = 1                                                    # e a tabela passa a nao ter motor
    host.hook = hook
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    inst = monitor["instances"]
    assert inst["engines_min_while_alive"] == 1 and inst["engines_max"] == 1 and monitor["probe_alive_before_stop"] is False
