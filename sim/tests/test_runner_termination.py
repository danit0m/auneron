"""
SIM-1.5G.3 -- tabela unica de codigos de encerramento, classificacao PURA do termino (`classify_termination`), gravacao do
`exit.json` terminal sem mascarar a causa original (`write_terminal`, `run_and_record_exit`) e o contrato do NH.

Nenhum processo real: so arquivos em `tmp_path` e duplos.
"""

from __future__ import annotations

import json

import pytest

from sim.runner import cli
from sim.runner import detach
from sim.runner import termination
from sim.runner.errors import RunnerError

C = termination.classify_termination


def doc(code, reason, **extra):
    return {"code": code, "reason": reason, "t_epoch": 1, **extra}


# ------------------------------------------------------------------------------------------------------ tabela
def test_the_exit_code_table_is_the_single_source_and_never_reuses_the_cli_contract_code():
    assert termination.EXIT_CODES == {"STOP": 0, "COMPLETED": 0, "EXCEPTION": 1, "ABORTED": 2, "TIMEOUT": 4, "EVIDENCE_FAILURE": 5,
                                      "ENGINE_LOST": 6, "CONTAINMENT_UNAVAILABLE": 7}
    assert termination.CLI_CONTRACT_VIOLATION == 3 and 3 not in termination.EXIT_CODES.values()      # 3 = violacao de contrato da CLI
    assert [r for r, c in termination.EXIT_CODES.items() if c == 0] == ["STOP", "COMPLETED"]            # so STOP e COMPLETED saem 0
    for reason in ("TIMEOUT", "EXCEPTION", "ABORTED", "EVIDENCE_FAILURE"):
        assert termination.exit_code_for(reason) != 0
    for bad in ("", "stop", "FORCED_END", None):
        with pytest.raises(ValueError):
            termination.exit_code_for(bad)


def test_the_cli_documents_the_exit_codes_it_keeps():
    assert "3 violacao de contrato" in cli.__doc__ and "0 ok; 1 reprovado" in cli.__doc__


# ------------------------------------------------------------------------------------------------------ classificacao: positivos
@pytest.mark.parametrize("reason,kind,success", [("STOP", "STOP_CLEAN", True), ("COMPLETED", "COMPLETED", True), ("TIMEOUT", "TIMEOUT", False),
                                                  ("EXCEPTION", "EXCEPTION", False), ("ABORTED", "ABORTED", False),
                                                  ("EVIDENCE_FAILURE", "EVIDENCE_WRITE_FAILURE", False)])
def test_consistent_signals_classify_each_reason_and_only_stop_and_completed_are_success(reason, kind, success):
    code = termination.EXIT_CODES[reason]
    result = C(doc(code, reason), code, code)
    assert result["class"] == kind and result["success"] is success and result["scheduler_checked"] is True
    unchecked = C(doc(code, reason), code, None)
    assert unchecked["class"] == kind and unchecked["scheduler_checked"] is False


def test_the_nh_contract_uses_process_code_and_keeps_the_semantic_code():
    assert C(doc(2, "ABORTED", process_code=1), 1, 1)["class"] == "ABORTED"                            # code 2 semantico; a CLI devolve 1
    assert C(doc(2, "INVALID", process_code=1), 1, 1)["class"] == "NON_COMPLETED"
    assert C(doc(1, "EXCEPTION", process_code=3), 3, 3)["class"] == "EXCEPTION"                        # RunnerError => CLI 3
    assert C(doc(0, "COMPLETED", process_code=0), 0, 0)["success"] is True
    assert C(doc(2, "ABORTED", process_code=1), 2, 2)["class"] == "INCONSISTENT_SIGNALS"               # o arquivo diz 2, o contrato diz 1
    assert C(doc(2, "WHATEVER", process_code=0), 0, 0)["class"] == "UNKNOWN"                           # codigo 2 mas o processo saiu 0: nao e "nao concluido"


# ------------------------------------------------------------------------------------------------------ classificacao: negativos
def test_the_l2_attempt_1_signals_are_inconsistent_not_a_clean_exit():
    """exit.json code=1/TIMEOUT, exit_code.txt=0, LastTaskResult=0: o caso real da tentativa expirada."""
    result = C(doc(1, "TIMEOUT"), 0, 0)
    assert result["class"] == "INCONSISTENT_SIGNALS" and result["success"] is False


@pytest.mark.parametrize("exit_doc,txt,last,kind", [
    (doc(0, "STOP"), None, 0, "INCOMPLETE_EVIDENCE"),                      # exit.json sem exit_code.txt
    (doc(0, "STOP"), 1, 0, "INCONSISTENT_SIGNALS"),
    (doc(0, "STOP"), 0, 1, "INCONSISTENT_SIGNALS"),                        # o Scheduler viu outro codigo
    (doc(1, "STOP"), 1, 1, "INCONSISTENT_SIGNALS"),                        # motivo e codigo fora da tabela
    (doc(0, "TIMEOUT"), 0, 0, "INCONSISTENT_SIGNALS"),
    (doc(1, "TIMEOUT"), 1, 1, "INCONSISTENT_SIGNALS"),                     # tabela antiga (TIMEOUT era 1)
    (doc(0, "SOMETHING"), 0, 0, "UNKNOWN"),                                # motivo desconhecido nunca e sucesso
    (doc(2, "SOMETHING"), 2, 2, "NON_COMPLETED"),
    ({}, 0, 0, "UNKNOWN"),                                                 # exit.json presente mas ilegivel
    ({"code": True, "reason": "STOP"}, 1, 1, "UNKNOWN"),
    ({"code": 0}, 0, 0, "UNKNOWN"),
    (doc(0, "STOP", process_code="0"), 0, 0, "UNKNOWN"),
    ("texto", 0, 0, "UNKNOWN"),
])
def test_inconsistent_incomplete_or_unreadable_signals_are_never_success(exit_doc, txt, last, kind):
    result = C(exit_doc, txt, last)
    assert result["class"] == kind and result["success"] is False


def test_without_exit_json_only_positive_evidence_names_a_cause():
    assert C(None, termination.EXIT_CODES["EVIDENCE_FAILURE"], 5)["class"] == "EVIDENCE_WRITE_FAILURE"
    assert C(None, 1, 1)["class"] == "PROCESS_FAILED"
    assert C(None, 0, 0)["class"] == "UNKNOWN" and C(None, 0, 0)["success"] is False                    # saiu 0 sem registro terminal
    assert C(None, None, None)["class"] == "UNKNOWN" and C(None, None, 0)["class"] == "UNKNOWN"


def test_forced_end_and_scheduler_limit_need_their_own_positive_evidence():
    T = termination.TASK_TERMINATED
    assert C(None, None, T, ["STARTED", "FORCED_END"], owned_alive=0)["class"] == "FORCED_END"
    assert C(None, None, T, [{"event": "FORCED_END"}], owned_alive=0)["class"] == "FORCED_END"          # registros da trilha tambem servem
    assert C(None, None, 0, ["FORCED_END"], owned_alive=0)["class"] == "UNKNOWN"                         # a trilha diz, o Scheduler nao confirma
    assert C(None, None, None, ["FORCED_END"], owned_alive=0)["class"] == "UNKNOWN"
    assert C(None, None, T, [], owned_alive=0)["class"] == "UNKNOWN"                                      # terminado, mas sem causa
    assert C(None, None, T, [], elapsed_s=7200, limit_s=7200, owned_alive=0)["class"] == "SCHEDULER_LIMIT"
    assert C(None, None, T, [], elapsed_s=7199, limit_s=7200, owned_alive=0)["class"] == "UNKNOWN"       # antes do limite
    assert C(None, None, T, [], elapsed_s=None, limit_s=7200, owned_alive=0)["class"] == "UNKNOWN"
    assert C(None, None, T, [], elapsed_s=8000, limit_s=0, owned_alive=0)["class"] == "UNKNOWN"
    assert C(None, None, 0, [], elapsed_s=8000, limit_s=7200, owned_alive=0)["class"] == "UNKNOWN"       # sem 0x41306 nao e limite
    assert C(None, None, T, ["FORCED_END"], elapsed_s=8000, limit_s=7200, owned_alive=0)["class"] == "FORCED_END"   # com FORCED_END, nunca LIMIT
    for forged in (C(None, None, T, [], elapsed_s=True, limit_s=True, owned_alive=0), C(None, None, T, ["forced_end"], owned_alive=0)):
        assert forged["class"] == "UNKNOWN"
    assert C(None, None, T, ["FORCED_END"], owned_alive=0)["success"] is False
    assert C(None, None, T, [], elapsed_s=9, limit_s=9, owned_alive=0)["success"] is False


def test_the_end_of_the_engine_never_proves_the_end_of_the_runner():
    """F-1 do L1-C: limite/parada forcada com 0x41306 e SEM arquivos, mas processos do run ainda vivos => ORPHAN_RUNNING."""
    T = termination.TASK_TERMINATED
    for args in ((None, None, T, [], 3), (None, None, T, ["FORCED_END"], 1)):
        result = C(*args[:4], elapsed_s=9000, limit_s=60, owned_alive=args[4])
        assert result["class"] == "ORPHAN_RUNNING" and result["success"] is False and result["owned_checked"] is True
    # processos vivos tambem desqualificam um "sucesso" aparente: nunca STOP_CLEAN com o runner ainda ativo
    assert C(doc(0, "STOP"), 0, 0, owned_alive=1)["class"] == "ORPHAN_RUNNING"
    # nao medido => nao ha FORCED_END/SCHEDULER_LIMIT por inferencia (e o motivo registrado distingue os dois casos)
    assert C(None, None, T, ["FORCED_END"], owned_alive=None)["class"] == "UNKNOWN"
    assert "nao foram verificados" in C(None, None, T, ["FORCED_END"], owned_alive=None)["detail"]
    assert "nao confirma" in C(None, None, 0, ["FORCED_END"], owned_alive=0)["detail"]
    assert "nao confirma" in C(None, None, None, ["FORCED_END"], owned_alive=None)["detail"]
    assert C(None, None, T, [], elapsed_s=9000, limit_s=60, owned_alive=None)["class"] == "UNKNOWN"
    assert C(None, None, T, [], elapsed_s=9000, limit_s=60, owned_alive=True)["class"] == "UNKNOWN"      # bool nao e contagem
    assert C(doc(0, "STOP"), 0, 0, owned_alive=0)["owned_checked"] is True and C(doc(0, "STOP"), 0, 0)["owned_checked"] is False


def test_engine_lost_and_containment_unavailable_are_classified_from_the_table():
    assert C(doc(6, "ENGINE_LOST", process_code=1), 1, 1, owned_alive=0)["class"] == "ENGINE_LOST"
    assert C(doc(6, "ENGINE_LOST"), 6, 6, owned_alive=0)["class"] == "ENGINE_LOST" and C(doc(6, "ENGINE_LOST"), 6, 6)["success"] is False
    assert C(doc(7, "CONTAINMENT_UNAVAILABLE", process_code=3), 3, 3, owned_alive=0)["class"] == "CONTAINMENT_UNAVAILABLE"
    assert C(doc(1, "ENGINE_LOST"), 1, 1)["class"] == "INCONSISTENT_SIGNALS"                              # codigo fora da tabela
    assert termination.exit_code_for("ENGINE_LOST") == 6 and termination.exit_code_for("CONTAINMENT_UNAVAILABLE") == 7


def test_a_terminated_scheduler_result_never_overrides_files_that_exist():
    assert C(doc(0, "STOP"), 0, termination.TASK_TERMINATED)["class"] == "INCONSISTENT_SIGNALS"


# ------------------------------------------------------------------------------------------------------ exit.json
def test_write_exit_keeps_the_historic_shape_and_adds_fields_only_when_given(tmp_path):
    detach.write_exit(tmp_path, 2, "ABORTED", clock=lambda: 7)
    assert json.loads((tmp_path / detach.EXIT).read_text()) == {"code": 2, "reason": "ABORTED", "t_epoch": 7}
    detach.write_exit(tmp_path, 2, "ABORTED", clock=lambda: 7, process_code=1, detail="x" * 500)
    saved = json.loads((tmp_path / detach.EXIT).read_text())
    assert saved["process_code"] == 1 and len(saved["detail"]) == 300 and saved["code"] == 2


def test_read_exit_tells_absent_from_present_but_invalid(tmp_path):
    assert detach.read_exit(tmp_path) is None
    (tmp_path / detach.EXIT).write_text("{", encoding="utf-8")
    assert detach.read_exit(tmp_path) == {}
    (tmp_path / detach.EXIT).write_bytes(b"\xff\xfe")
    assert detach.read_exit(tmp_path) == {}
    (tmp_path / detach.EXIT).write_text("[1]", encoding="utf-8")
    assert detach.read_exit(tmp_path) == {}
    (tmp_path / detach.EXIT).write_text('{"code": 0, "reason": "STOP"}', encoding="utf-8")
    assert detach.read_exit(tmp_path) == {"code": 0, "reason": "STOP"}


def test_write_terminal_returns_the_secondary_failure_instead_of_raising(tmp_path, monkeypatch):
    assert detach.write_terminal(tmp_path, 0, "STOP", clock=lambda: 1) is None
    monkeypatch.setattr(detach, "_atomic_write", lambda path, doc: (_ for _ in ()).throw(PermissionError("acesso negado")))
    failure = detach.write_terminal(tmp_path, 0, "STOP", clock=lambda: 1)
    assert failure.startswith("PermissionError") and "acesso negado" in failure


# ------------------------------------------------------------------------------------------------------ NH: run_and_record_exit
def fake_cleanup(calls):
    return lambda: calls.append("cleanup")


@pytest.mark.parametrize("state,code,reason,process", [("COMPLETED", 0, "COMPLETED", 0), ("ABORTED", 2, "ABORTED", 1), ("INVALID", 2, "INVALID", 1)])
def test_nh_exit_json_keeps_the_semantic_code_and_declares_the_cli_process_code(tmp_path, state, code, reason, process):
    calls = []
    result = detach.run_and_record_exit(tmp_path, lambda: {"state": state}, fake_cleanup(calls), clock=lambda: 5)
    saved = json.loads((tmp_path / detach.EXIT).read_text())
    assert result == {"state": state} and calls == ["cleanup"]
    assert saved == {"code": code, "reason": reason, "t_epoch": 5, "process_code": process}
    assert C(saved, process, process)["success"] is (state == "COMPLETED")


def test_nh_exceptions_are_recorded_and_re_raised_with_the_cli_codes(tmp_path):
    calls = []
    with pytest.raises(RunnerError):
        detach.run_and_record_exit(tmp_path, lambda: (_ for _ in ()).throw(RunnerError("contrato")), fake_cleanup(calls), clock=lambda: 5)
    saved = json.loads((tmp_path / detach.EXIT).read_text())
    assert saved["code"] == 1 and saved["reason"] == "EXCEPTION" and saved["process_code"] == 3 and "RunnerError: contrato" in saved["detail"]
    assert calls == ["cleanup"]
    with pytest.raises(ZeroDivisionError):
        detach.run_and_record_exit(tmp_path / "b", lambda: 1 / 0, fake_cleanup(calls), clock=lambda: 5)
    saved = json.loads((tmp_path / "b" / detach.EXIT).read_text())
    assert saved["code"] == 1 and saved["process_code"] == 1
    with pytest.raises(KeyboardInterrupt):
        detach.run_and_record_exit(tmp_path / "c", lambda: (_ for _ in ()).throw(KeyboardInterrupt()), fake_cleanup(calls), clock=lambda: 5)
    assert json.loads((tmp_path / "c" / detach.EXIT).read_text())["reason"] == "EXCEPTION"


def test_a_failure_to_write_exit_json_after_a_result_is_never_success(tmp_path, monkeypatch):
    monkeypatch.setattr(detach, "write_exit", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    with pytest.raises(detach.EvidenceWriteError, match="disco cheio"):
        detach.run_and_record_exit(tmp_path, lambda: {"state": "COMPLETED"}, lambda: None)
    assert issubclass(detach.EvidenceWriteError, RunnerError)                                              # a CLI devolve 3, nunca 0


def test_a_failure_to_write_exit_json_during_an_exception_preserves_the_original_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(detach, "write_exit", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    with pytest.raises(RunnerError, match="causa original") as caught:
        detach.run_and_record_exit(tmp_path, lambda: (_ for _ in ()).throw(RunnerError("causa original")), lambda: None)
    assert any("disco cheio" in note for note in caught.value.__notes__)


def test_a_failing_cleanup_does_not_hide_the_run_result_or_the_original_exception(tmp_path):
    def bad_cleanup():
        raise OSError("limpeza")

    with pytest.raises(OSError, match="limpeza"):                                   # sem excecao anterior, a da limpeza e a causa
        detach.run_and_record_exit(tmp_path, lambda: {"state": "COMPLETED"}, bad_cleanup, clock=lambda: 1)
    assert json.loads((tmp_path / detach.EXIT).read_text())["reason"] == "EXCEPTION"
    with pytest.raises(RunnerError) as caught:
        detach.run_and_record_exit(tmp_path / "b", lambda: (_ for _ in ()).throw(RunnerError("original")), bad_cleanup)
    assert any("limpeza" in note for note in caught.value.__notes__)


def test_nh_cli_return_values_are_unchanged_by_the_exit_json_contract(tmp_path, monkeypatch, capsys):
    """Consumidores dos codigos da CLI (`status`, exit_code.txt, LastTaskResult) continuam vendo 0 ok / 1 reprovado / 3 contrato."""
    from sim.runner import nh_phases

    for status, expected in (("PASS", 0), ("FAIL", 1)):
        monkeypatch.setattr(nh_phases, "phase_run", lambda lab, run_id, status=status: {"status": status})
        monkeypatch.setattr(nh_phases.detach, "proc_dir", lambda root, run_id: tmp_path)
        monkeypatch.setattr("sim.live.env.open_lab", lambda instance: object())
        monkeypatch.setattr(nh_phases, "apply_launch_env", lambda proc: {})
        assert cli.main(["run", "--run-id", "RUN-x", "--instance", "i"]) == expected
    monkeypatch.setattr(nh_phases, "phase_run", lambda lab, run_id: (_ for _ in ()).throw(RunnerError("contrato")))
    assert cli.main(["run", "--run-id", "RUN-x", "--instance", "i"]) == 3
    capsys.readouterr()
