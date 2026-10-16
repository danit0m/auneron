"""
SIM-1.5G.3/1.5G.4 -- codigos semanticos de encerramento e classificacao PURA do termino de um processo destacado.

Tres sinais descrevem como um processo destacado terminou, e ate o L2 (tentativa 1) divergiam:

* `exit.json`        -- gravado pelo proprio processo; carrega o codigo SEMANTICO (`code`, `reason`);
* `exit_code.txt`    -- gravado pelo `cmd` depois do Python terminar: o codigo REAL do processo;
* `LastTaskResult`   -- o que o Task Scheduler registra: o codigo de saida do `cmd` (agora igual ao do Python).

Contrato (uma unica tabela): o codigo do PROCESSO e o `code` de `exit.json`. O fluxo NH preserva o contrato publico da CLI
(`0` ok, `1` reprovado, `3` violacao de contrato) e declara no `exit.json` o campo aditivo `process_code`, que e o codigo que a
CLI de fato devolve; o `code` semantico do NH (0/1/2) nao muda. `3` pertence a CLI e nunca e reutilizado aqui.

SIM-1.5G.4 (L1-C, achado F-1): o limite do Scheduler encerra SO o `cmd`; os processos Python descendentes continuam vivos. Por isso
`Ready` / `LastTaskResult 0x41306` / tarefa ausente NUNCA provam que o runner terminou: a classificacao recebe `owned_alive`
(processos do run comprovadamente vivos, por identidade). Com `owned_alive > 0` o resultado e `ORPHAN_RUNNING`; com `owned_alive`
nao medido, `FORCED_END` e `SCHEDULER_LIMIT` ficam `UNKNOWN`.

A classificacao NUNCA infere sucesso por ausencia: `FORCED_END` e `SCHEDULER_LIMIT` exigem evidencia positiva propria; sem ela
o resultado e `UNKNOWN`.
"""

from __future__ import annotations

EXIT_CODES = {"STOP": 0, "COMPLETED": 0, "EXCEPTION": 1, "ABORTED": 2, "TIMEOUT": 4, "EVIDENCE_FAILURE": 5, "ENGINE_LOST": 6,
              "CONTAINMENT_UNAVAILABLE": 7}
CLI_CONTRACT_VIOLATION = 3          # `sim.runner.cli`: RunnerError (R0, interlock, ledger) => 3; reservado, fora da tabela
TASK_TERMINATED = 0x41306           # SCHED_S_TASK_TERMINATED: OBSERVADO no L1-C (limite PT1M); Stop-ScheduledTask ainda nao testado
SUCCESS_CLASSES = ("STOP_CLEAN", "COMPLETED")

_BY_REASON = {"STOP": "STOP_CLEAN", "COMPLETED": "COMPLETED", "TIMEOUT": "TIMEOUT", "EXCEPTION": "EXCEPTION", "ABORTED": "ABORTED",
              "EVIDENCE_FAILURE": "EVIDENCE_WRITE_FAILURE", "ENGINE_LOST": "ENGINE_LOST",
              "CONTAINMENT_UNAVAILABLE": "CONTAINMENT_UNAVAILABLE"}


def exit_code_for(reason: str) -> int:
    """Codigo do processo/`exit.json` para um motivo da tabela; motivo desconhecido => `ValueError` (nunca um default)."""
    if reason not in EXIT_CODES:
        raise ValueError(f"motivo de encerramento fora da tabela: {reason!r}")
    return EXIT_CODES[reason]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def classify_termination(exit_doc, exit_code_txt, last_result, events=(), *, elapsed_s=None, limit_s=None, owned_alive=None) -> dict:
    """-> {class, detail, success, scheduler_checked, owned_checked}. Entrada: `exit.json` (None = ausente), `exit_code.txt` lido (None
    = ausente ou invalido), `LastTaskResult` (None = nao medido), eventos da auditoria (nomes ou registros), tempo decorrido, limite e
    `owned_alive` (nº de processos do run comprovadamente vivos; None = nao medido).

    Classes: STOP_CLEAN, COMPLETED, TIMEOUT, EXCEPTION, ABORTED, NON_COMPLETED, EVIDENCE_WRITE_FAILURE, ENGINE_LOST,
    CONTAINMENT_UNAVAILABLE, PROCESS_FAILED, FORCED_END, SCHEDULER_LIMIT, ORPHAN_RUNNING, INCOMPLETE_EVIDENCE, INCONSISTENT_SIGNALS,
    UNKNOWN. So STOP_CLEAN e COMPLETED sao sucesso."""
    names = {e if isinstance(e, str) else (e or {}).get("event") for e in events}
    owned_checked = _is_int(owned_alive)

    def result(kind: str, detail: str, **extra) -> dict:
        return {"class": kind, "detail": detail, "success": kind in SUCCESS_CLASSES, "owned_checked": owned_checked,
                "scheduler_checked": extra.pop("scheduler_checked", False), **extra}

    if owned_checked and owned_alive > 0:
        return result("ORPHAN_RUNNING", f"{owned_alive} processo(s) do run ainda vivo(s): o fim do motor/da tarefa nao prova o fim do runner",
                      owned_alive=owned_alive, scheduler_checked=last_result is not None)
    if exit_doc is not None:
        if not isinstance(exit_doc, dict) or not _is_int(exit_doc.get("code")) or not isinstance(exit_doc.get("reason"), str):
            return result("UNKNOWN", "exit.json ilegivel ou sem code/reason")
        code, reason = exit_doc["code"], exit_doc["reason"]
        expected = exit_doc.get("process_code", code)
        if not _is_int(expected):
            return result("UNKNOWN", "process_code invalido em exit.json")
        if exit_code_txt is None:
            return result("INCOMPLETE_EVIDENCE", "exit.json sem exit_code.txt valido")
        if exit_code_txt != expected:
            return result("INCONSISTENT_SIGNALS", f"exit.json={code}/{reason} (processo esperado {expected}) mas exit_code.txt={exit_code_txt}")
        checked = last_result is not None
        if checked and (last_result & 0xFFFFFFFF) != (exit_code_txt & 0xFFFFFFFF):
            return result("INCONSISTENT_SIGNALS", f"LastTaskResult={last_result} mas exit_code.txt={exit_code_txt}", scheduler_checked=True)
        if reason in EXIT_CODES:
            if EXIT_CODES[reason] != code:
                return result("INCONSISTENT_SIGNALS", f"{reason} deveria ter code {EXIT_CODES[reason]}, tem {code}", scheduler_checked=checked)
            return result(_BY_REASON[reason], f"{reason}/{code}", scheduler_checked=checked)
        if code == 2 and expected != 0:                      # NH: estado terminal diferente de COMPLETED (ex.: nao ha motivo na tabela)
            return result("NON_COMPLETED", f"{reason}/{code}", scheduler_checked=checked)
        return result("UNKNOWN", f"motivo fora da tabela: {reason!r}/{code}", scheduler_checked=checked)
    if exit_code_txt is not None:
        if exit_code_txt == EXIT_CODES["EVIDENCE_FAILURE"]:
            return result("EVIDENCE_WRITE_FAILURE", "o processo declarou falha ao gravar evidencia (codigo reservado)")
        if exit_code_txt != 0:
            return result("PROCESS_FAILED", f"processo terminou com {exit_code_txt} sem exit.json")
        return result("UNKNOWN", "processo terminou com 0 sem exit.json")
    terminated = last_result is not None and (last_result & 0xFFFFFFFF) == TASK_TERMINATED
    if "FORCED_END" in names:
        if terminated and owned_checked:
            return result("FORCED_END", "FORCED_END na auditoria, sem arquivos de saida, LastTaskResult=0x41306 e 0 processos do run vivos",
                          scheduler_checked=True)
        if terminated:
            return result("UNKNOWN", "FORCED_END e 0x41306, mas os processos do run nao foram verificados (owned_alive ausente)",
                          scheduler_checked=True)
        return result("UNKNOWN", "FORCED_END na auditoria, mas LastTaskResult nao confirma o termino pelo Scheduler",
                      scheduler_checked=last_result is not None)
    if terminated and _is_int(elapsed_s) and _is_int(limit_s) and limit_s > 0 and elapsed_s >= limit_s:
        if owned_checked:
            return result("SCHEDULER_LIMIT", "sem arquivos de saida, LastTaskResult=0x41306, tempo >= limite, sem FORCED_END e 0 processos do run vivos",
                          scheduler_checked=True)
        return result("UNKNOWN", "limite do Scheduler aparente, mas os processos do run nao foram verificados (owned_alive ausente)",
                      scheduler_checked=True)
    return result("UNKNOWN", "sem exit.json, sem exit_code.txt e sem evidencia positiva de causa", scheduler_checked=last_result is not None)
