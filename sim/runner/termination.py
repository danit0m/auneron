"""
SIM-1.5G.3 -- codigos semanticos de encerramento e classificacao PURA do termino de um processo destacado.

Tres sinais descrevem como um processo destacado terminou, e ate o L2 (tentativa 1) divergiam:

* `exit.json`        -- gravado pelo proprio processo; carrega o codigo SEMANTICO (`code`, `reason`);
* `exit_code.txt`    -- gravado pelo `cmd` depois do Python terminar: o codigo REAL do processo;
* `LastTaskResult`   -- o que o Task Scheduler registra: o codigo de saida do `cmd` (agora igual ao do Python).

Contrato (uma unica tabela): o codigo do PROCESSO e o `code` de `exit.json`. O fluxo NH preserva o contrato publico da CLI
(`0` ok, `1` reprovado, `3` violacao de contrato) e declara no `exit.json` o campo aditivo `process_code`, que e o codigo que a
CLI de fato devolve; o `code` semantico do NH (0/1/2) nao muda. `3` pertence a CLI e nunca e reutilizado aqui.

A classificacao NUNCA infere sucesso por ausencia: `FORCED_END` e `SCHEDULER_LIMIT` exigem evidencia positiva propria; sem ela
o resultado e `UNKNOWN`.
"""

from __future__ import annotations

EXIT_CODES = {"STOP": 0, "COMPLETED": 0, "EXCEPTION": 1, "ABORTED": 2, "TIMEOUT": 4, "EVIDENCE_FAILURE": 5}
CLI_CONTRACT_VIOLATION = 3          # `sim.runner.cli`: RunnerError (R0, interlock, ledger) => 3; reservado, fora da tabela
TASK_TERMINATED = 0x41306           # SCHED_S_TASK_TERMINATED: HIPOTESE H-2 (a provar no L1-C); sem ele nao ha FORCED_END/LIMIT
SUCCESS_CLASSES = ("STOP_CLEAN", "COMPLETED")

_BY_REASON = {"STOP": "STOP_CLEAN", "COMPLETED": "COMPLETED", "TIMEOUT": "TIMEOUT", "EXCEPTION": "EXCEPTION", "ABORTED": "ABORTED",
              "EVIDENCE_FAILURE": "EVIDENCE_WRITE_FAILURE"}


def exit_code_for(reason: str) -> int:
    """Codigo do processo/`exit.json` para um motivo da tabela; motivo desconhecido => `ValueError` (nunca um default)."""
    if reason not in EXIT_CODES:
        raise ValueError(f"motivo de encerramento fora da tabela: {reason!r}")
    return EXIT_CODES[reason]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _result(kind: str, detail: str, **extra) -> dict:
    return {"class": kind, "detail": detail, "success": kind in SUCCESS_CLASSES, **extra}


def classify_termination(exit_doc, exit_code_txt, last_result, events=(), *, elapsed_s=None, limit_s=None) -> dict:
    """-> {class, detail, success, scheduler_checked}. Entrada: `exit.json` (None = ausente), `exit_code.txt` lido (None = ausente
    ou invalido), `LastTaskResult` (None = nao medido), eventos da auditoria (nomes ou registros), tempo decorrido e limite.

    Classes: STOP_CLEAN, COMPLETED, TIMEOUT, EXCEPTION, ABORTED, NON_COMPLETED, EVIDENCE_WRITE_FAILURE, PROCESS_FAILED, FORCED_END,
    SCHEDULER_LIMIT, INCOMPLETE_EVIDENCE, INCONSISTENT_SIGNALS, UNKNOWN. So STOP_CLEAN e COMPLETED sao sucesso."""
    names = {e if isinstance(e, str) else (e or {}).get("event") for e in events}
    if exit_doc is not None:
        if not isinstance(exit_doc, dict) or not _is_int(exit_doc.get("code")) or not isinstance(exit_doc.get("reason"), str):
            return _result("UNKNOWN", "exit.json ilegivel ou sem code/reason", scheduler_checked=False)
        code, reason = exit_doc["code"], exit_doc["reason"]
        expected = exit_doc.get("process_code", code)
        if not _is_int(expected):
            return _result("UNKNOWN", "process_code invalido em exit.json", scheduler_checked=False)
        if exit_code_txt is None:
            return _result("INCOMPLETE_EVIDENCE", "exit.json sem exit_code.txt valido", scheduler_checked=False)
        if exit_code_txt != expected:
            return _result("INCONSISTENT_SIGNALS",
                           f"exit.json={code}/{reason} (processo esperado {expected}) mas exit_code.txt={exit_code_txt}",
                           scheduler_checked=False)
        checked = last_result is not None
        if checked and (last_result & 0xFFFFFFFF) != (exit_code_txt & 0xFFFFFFFF):
            return _result("INCONSISTENT_SIGNALS", f"LastTaskResult={last_result} mas exit_code.txt={exit_code_txt}",
                           scheduler_checked=True)
        if reason in EXIT_CODES:
            if EXIT_CODES[reason] != code:
                return _result("INCONSISTENT_SIGNALS", f"{reason} deveria ter code {EXIT_CODES[reason]}, tem {code}", scheduler_checked=checked)
            return _result(_BY_REASON[reason], f"{reason}/{code}", scheduler_checked=checked)
        if code == 2 and expected != 0:                      # NH: estado terminal diferente de COMPLETED (ex.: nao ha motivo na tabela)
            return _result("NON_COMPLETED", f"{reason}/{code}", scheduler_checked=checked)
        return _result("UNKNOWN", f"motivo fora da tabela: {reason!r}/{code}", scheduler_checked=checked)
    if exit_code_txt is not None:
        if exit_code_txt == EXIT_CODES["EVIDENCE_FAILURE"]:
            return _result("EVIDENCE_WRITE_FAILURE", "o processo declarou falha ao gravar evidencia (codigo reservado)", scheduler_checked=False)
        if exit_code_txt != 0:
            return _result("PROCESS_FAILED", f"processo terminou com {exit_code_txt} sem exit.json", scheduler_checked=False)
        return _result("UNKNOWN", "processo terminou com 0 sem exit.json", scheduler_checked=False)
    terminated = last_result is not None and (last_result & 0xFFFFFFFF) == TASK_TERMINATED
    if "FORCED_END" in names:
        if terminated:
            return _result("FORCED_END", "FORCED_END na auditoria, sem arquivos de saida e LastTaskResult=0x41306", scheduler_checked=True)
        return _result("UNKNOWN", "FORCED_END na auditoria, mas LastTaskResult nao confirma o termino pelo Scheduler", scheduler_checked=last_result is not None)
    if terminated and _is_int(elapsed_s) and _is_int(limit_s) and limit_s > 0 and elapsed_s >= limit_s:
        return _result("SCHEDULER_LIMIT", "sem arquivos de saida, LastTaskResult=0x41306 e tempo >= limite, sem FORCED_END", scheduler_checked=True)
    return _result("UNKNOWN", "sem exit.json, sem exit_code.txt e sem evidencia positiva de causa", scheduler_checked=last_result is not None)
