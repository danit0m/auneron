"""
Adaptador do Windows Task Scheduler (SIM-1.5G, DETACH-2): mecanismo CANDIDATO de execucao destacada, que substitui o WMI
(reprovado no DETACH-1) no caminho padrao. Nada aqui prova sobrevivencia: isso e o DETACH-2 (sondas L1/L2).

Contrato (Design Freeze DETACH-2 + condicoes do PO):

* uma tarefa por sonda/run, em `\\AuneronSim\\<nome>`, SEM gatilhos, sem reinicio, `IgnoreNew`, limite de execucao explicito;
* principal = usuario interativo ATUAL, `InteractiveToken`, `LeastPrivilege`; sem senha, S4U ou elevacao: privilegio diferente
  do medido (admin/alto/servico parado) => falha fechada, sem contornar;
* XML deterministico + validacao semantica do XML exportado depois do registro (divergencia => remove e falha);
* evidencias preservadas ANTES de remover; remocao verificada; falha de remocao => `RemovalNotVerified` (P15 bloqueia);
* trilha `sched_audit.jsonl` encadeada por hash, ancorada em dois lugares (adulteracao, perda de registros e truncamento
  sao detectados); reconciliacao compara a trilha, o estado observado do Scheduler e os processos;
* nunca toca tarefas fora de `\\AuneronSim\\` (nem a tarefa de deteccao de contas vencidas do produto).

Todo acesso ao host passa por `run` (compativel com `subprocess.run`): os testes injetam duplos; nenhuma tarefa real e criada
fora do DETACH-2 autorizado. Os scripts PowerShell sao CONSTANTES (a entrada vai por variaveis de ambiente, nunca interpolada).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from sim.generator.canonical import canonical_bytes
from sim.runner.errors import RunnerError

FOLDER = "\\AuneronSim\\"
FOLDER_NAME = "AuneronSim"
COM_FOLDER = "\\AuneronSim"               # API COM (`GetFolder`): SEM barra final (a barra final da 0x8007007B)
INTEGRITY_MEDIUM = 0x2000
CMD_EXE = "C:\\Windows\\System32\\cmd.exe"
MECHANISM = "task_scheduler"
STATE_READY, STATE_RUNNING = 3, 4
AUDIT_NAME, ANCHOR_NAME, ANCHOR_DIR = "sched_audit.jsonl", "sched_audit.anchor.json", "sched_anchors"
EVIDENCE_DIR = "evidence_pre_removal"
LIMITS = {"PROBE_L1": "PT30M", "PROBE_L2": "PT2H", "SR1": "PT7H", "NH_RUN": "PT14H"}   # SR1: gate A-05 (sessao <= 6,5 h) + folga
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]+\\[A-Za-z0-9._ $-]+$")
LIMIT_RE = re.compile(r"^PT(?:(\d{1,2})H)?(?:(\d{1,2})M)?$")
SAFE_TEXT = re.compile(r"^[^\x00-\x1f]*$")
FORBIDDEN_TAGS = frozenset({"Password", "RestartOnFailure", "BootTrigger", "LogonTrigger", "TimeTrigger", "CalendarTrigger",
                            "IdleTrigger", "EventTrigger", "RegistrationTrigger", "SessionStateChangeTrigger", "ComHandler",
                            "SendEmail", "ShowMessage"})
SETTING_DEFAULTS = {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "true", "StopIfGoingOnBatteries": "true",
                    "AllowHardTerminate": "true", "StartWhenAvailable": "false", "RunOnlyIfNetworkAvailable": "false",
                    "AllowStartOnDemand": "true", "Enabled": "true", "Hidden": "false", "RunOnlyIfIdle": "false",
                    "WakeToRun": "false", "ExecutionTimeLimit": "PT72H"}
EXPECTED_SETTINGS = {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false",
                     "AllowHardTerminate": "true", "StartWhenAvailable": "false", "RunOnlyIfNetworkAvailable": "false",
                     "AllowStartOnDemand": "true", "Enabled": "true", "Hidden": "true", "RunOnlyIfIdle": "false", "WakeToRun": "false"}


class SchedError(RunnerError):
    pass


class UnexpectedPrivilege(SchedError):
    pass


class ConfigDivergence(SchedError):
    pass


class RemovalNotVerified(SchedError):
    pass


# ---------------------------------------------------------------------------------------------------------------------
# scripts PowerShell (CONSTANTES; entrada so por ambiente)
# ---------------------------------------------------------------------------------------------------------------------
_NOT_FOUND = "(-2147024894, -2147024893)"
_PRELUDE = ("try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}; "
            "$OutputEncoding=[System.Text.Encoding]::UTF8; ")
_TOKEN_CS = (
    'using System; using System.Runtime.InteropServices; public class SimTokenIntegrity { '
    '[DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr p, uint a, out IntPtr t); '
    '[DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr t, int c, IntPtr b, int l, out int r); '
    '[DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr s, int i); '
    '[DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr s); '
    '[DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h); '
    'public static int Level() { IntPtr t; '
    'if (!OpenProcessToken(System.Diagnostics.Process.GetCurrentProcess().Handle, 8, out t)) return -1; '
    'try { int n; GetTokenInformation(t, 25, IntPtr.Zero, 0, out n); if (n <= 0) return -1; IntPtr b = Marshal.AllocHGlobal(n); '
    'try { if (!GetTokenInformation(t, 25, b, n, out n)) return -1; IntPtr sid = Marshal.ReadIntPtr(b); '
    'byte c = Marshal.ReadByte(GetSidSubAuthorityCount(sid)); return Marshal.ReadInt32(GetSidSubAuthority(sid, c - 1)); } '
    'finally { Marshal.FreeHGlobal(b); } } finally { CloseHandle(t); } } }'
)
_HR = "$h=$_.Exception.HResult; if ($_.Exception.InnerException) { $h=$_.Exception.InnerException.HResult }; "
PS_IDENTITY = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $id=[Security.Principal.WindowsIdentity]::GetCurrent(); "
    "$pr=New-Object Security.Principal.WindowsPrincipal($id); "
    "Add-Type -TypeDefinition '" + _TOKEN_CS + "'; $rid=[int][SimTokenIntegrity]::Level(); "
    "$s=Get-CimInstance Win32_Service -Filter \"Name='Schedule'\"; "
    "$rb=((Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired') -or "
    "(Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending')); "
    "[pscustomobject]@{user=$id.Name; sid=$id.User.Value; admin_role=$pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); "
    "integrity_rid=$rid; schedule_state=[string]$s.State; schedule_pid=[int]$s.ProcessId; "
    "session=[int](Get-Process -Id $PID).SessionId; reboot_pending=[bool]$rb} | ConvertTo-Json -Compress"
)
PS_REGISTER = (
    _PRELUDE + "$ErrorActionPreference='Stop'; Register-ScheduledTask -TaskName $env:SIM_TASK_NAME -TaskPath $env:SIM_TASK_PATH "
    "-Xml $env:SIM_TASK_XML | Out-Null; Write-Output 'OK'"
)
PS_START = _PRELUDE + "$ErrorActionPreference='Stop'; Start-ScheduledTask -TaskName $env:SIM_TASK_NAME -TaskPath $env:SIM_TASK_PATH; Write-Output 'OK'"
PS_END = _PRELUDE + "$ErrorActionPreference='Stop'; Stop-ScheduledTask -TaskName $env:SIM_TASK_NAME -TaskPath $env:SIM_TASK_PATH; Write-Output 'OK'"
PS_UNREGISTER = (
    _PRELUDE + "$ErrorActionPreference='Stop'; Unregister-ScheduledTask -TaskName $env:SIM_TASK_NAME -TaskPath $env:SIM_TASK_PATH "
    "-Confirm:$false; Write-Output 'OK'"
)
PS_INFO = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $svc=New-Object -ComObject Schedule.Service; $svc.Connect(); "
    "$o=[ordered]@{found=$false; folder_exists=$true}; "
    f"try {{ $f=$svc.GetFolder($env:SIM_COM_PATH) }} catch {{ {_HR}if ($h -in {_NOT_FOUND}) {{ $o.folder_exists=$false; "
    "$o | ConvertTo-Json -Compress; exit 0 } else { throw } }; "
    f"try {{ $t=$f.GetTask($env:SIM_TASK_NAME) }} catch {{ {_HR}if ($h -in {_NOT_FOUND}) {{ $o | ConvertTo-Json -Compress; "
    "exit 0 } else { throw } }; "
    "$inst=@(); foreach ($i in $t.GetInstances(0)) { $inst += [pscustomobject]@{pid=[int]$i.EnginePID; state=[int]$i.State} }; "
    "$o.found=$true; $o.state=[int]$t.State; $o.enabled=[bool]$t.Enabled; $o.last_result=[int]$t.LastTaskResult; "
    "$o.last_run=[int64]([DateTimeOffset]$t.LastRunTime).ToUnixTimeSeconds(); $o.instances=$inst; "
    "if ($env:SIM_WANT_XML -eq '1') { $o.xml=[string]$t.Xml }; $o | ConvertTo-Json -Compress -Depth 4"
)
PS_LIST = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $svc=New-Object -ComObject Schedule.Service; $svc.Connect(); $o=[ordered]@{folder_exists=$true; tasks=@(); folders=@()}; "
    f"try {{ $f=$svc.GetFolder($env:SIM_COM_PATH) }} catch {{ {_HR}if ($h -in {_NOT_FOUND}) {{ $o.folder_exists=$false; "
    "$o | ConvertTo-Json -Compress; exit 0 } else { throw } }; "
    "foreach ($t in $f.GetTasks(1)) { $o.tasks += [pscustomobject]@{name=[string]$t.Name; state=[int]$t.State} }; "
    "foreach ($d in $f.GetFolders(0)) { $o.folders += [string]$d.Name }; $o | ConvertTo-Json -Compress -Depth 4"
)
PS_DELETE_FOLDER = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $svc=New-Object -ComObject Schedule.Service; $svc.Connect(); $f=$svc.GetFolder($env:SIM_COM_PATH); "
    "if ($f.GetTasks(1).Count -ne 0 -or $f.GetFolders(0).Count -ne 0) { throw 'pasta nao vazia' }; "
    "$svc.GetFolder('\\').DeleteFolder($env:SIM_FOLDER_NAME, 0); Write-Output 'OK'"
)
PS_CHAIN = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $p=[int]$env:SIM_PID; $out=@(); for ($i=0; $i -lt 6 -and $p -gt 0; $i++) { "
    "$w=Get-CimInstance Win32_Process -Filter \"ProcessId=$p\"; if (-not $w) { break }; "
    "$out += [pscustomobject]@{pid=[int]$w.ProcessId; ppid=[int]$w.ParentProcessId; name=[string]$w.Name; session=[int]$w.SessionId; "
    "cmd=[string]$w.CommandLine}; $p=[int]$w.ParentProcessId }; ConvertTo-Json -InputObject @($out) -Compress"
)
PS_PROCS = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $m=$env:SIM_MARKER; $o=@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
    "$_.CommandLine.Contains($m) -and $_.Name -in 'python.exe','cmd.exe','pythonw.exe' } | ForEach-Object { [pscustomobject]@{"
    "pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId; name=[string]$_.Name} }); ConvertTo-Json -InputObject @($o) -Compress"
)
PS_PTABLE = (
    _PRELUDE + "$ErrorActionPreference='Stop'; $sp=[int]$env:SIM_SCHEDULE_PID; $o=@(Get-CimInstance Win32_Process | "
    "Where-Object { $_.ProcessId -eq $sp -or $_.Name -in 'cmd.exe','python.exe','pythonw.exe' } | ForEach-Object { "
    "[pscustomobject]@{pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId; name=[string]$_.Name; session=[int]$_.SessionId; "
    "cmd=[string]$_.CommandLine} }); ConvertTo-Json -InputObject @($o) -Compress"
)
SCRIPTS = (PS_IDENTITY, PS_REGISTER, PS_START, PS_END, PS_UNREGISTER, PS_INFO, PS_LIST, PS_DELETE_FOLDER, PS_CHAIN, PS_PROCS, PS_PTABLE)


def run_default(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)


# ---------------------------------------------------------------------------------------------------------------------
# nomes, XML deterministico e validacao semantica
# ---------------------------------------------------------------------------------------------------------------------
def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or ".." in name:
        raise SchedError(f"nome de tarefa invalido: {name!r}")
    return name


def validate_path(path: str) -> str:
    if path != FOLDER:
        raise SchedError(f"so a pasta {FOLDER} e permitida (recebido {path!r}); tarefas de terceiros nunca sao tocadas")
    return path


def validate_com_path(path: str) -> str:
    """Caminho para a API COM (`GetFolder`): `\\AuneronSim`, SEM barra final. Os cmdlets usam `FOLDER` (com barra final)."""
    if path != COM_FOLDER:
        raise SchedError(f"caminho COM invalido (esperado {COM_FOLDER}, sem barra final): {path!r}")
    return path


def validate_limit(limit: str) -> str:
    match = LIMIT_RE.fullmatch(limit or "")
    if not match or not any(int(g) for g in match.groups() if g):
        raise SchedError(f"limite de execucao invalido ou infinito: {limit!r}")
    return limit


def validate_user(user: str) -> str:
    if not isinstance(user, str) or not USER_RE.fullmatch(user):
        raise SchedError(f"usuario invalido: {user!r}")
    return user


def _text(value: str, what: str) -> str:
    if not isinstance(value, str) or not value or not SAFE_TEXT.fullmatch(value):
        raise SchedError(f"{what} invalido")
    return escape(value)


def build_task_xml(name: str, user: str, arguments: str, workdir: str, limit: str, description: str = "SIM DETACH-2 task") -> str:
    """XML deterministico (ordem fixa, sem datas). Sem `Password`, sem gatilhos, sem `RestartOnFailure`."""
    validate_name(name)
    validate_user(user)
    validate_limit(limit)
    s = EXPECTED_SETTINGS
    lines = [
        '<?xml version="1.0" encoding="UTF-16"?>',
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
        f"  <RegistrationInfo><Description>{_text(description, 'descricao')}</Description><URI>{FOLDER}{name}</URI></RegistrationInfo>",
        "  <Triggers />",
        f'  <Principals><Principal id="Author"><UserId>{_text(user, "usuario")}</UserId><LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>",
        "  <Settings>",
        f"    <MultipleInstancesPolicy>{s['MultipleInstancesPolicy']}</MultipleInstancesPolicy>",
        f"    <DisallowStartIfOnBatteries>{s['DisallowStartIfOnBatteries']}</DisallowStartIfOnBatteries>",
        f"    <StopIfGoingOnBatteries>{s['StopIfGoingOnBatteries']}</StopIfGoingOnBatteries>",
        f"    <AllowHardTerminate>{s['AllowHardTerminate']}</AllowHardTerminate>",
        f"    <StartWhenAvailable>{s['StartWhenAvailable']}</StartWhenAvailable>",
        f"    <RunOnlyIfNetworkAvailable>{s['RunOnlyIfNetworkAvailable']}</RunOnlyIfNetworkAvailable>",
        "    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>",
        f"    <AllowStartOnDemand>{s['AllowStartOnDemand']}</AllowStartOnDemand>",
        f"    <Enabled>{s['Enabled']}</Enabled>",
        f"    <Hidden>{s['Hidden']}</Hidden>",
        f"    <RunOnlyIfIdle>{s['RunOnlyIfIdle']}</RunOnlyIfIdle>",
        f"    <WakeToRun>{s['WakeToRun']}</WakeToRun>",
        f"    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>",
        "    <Priority>7</Priority>",
        "  </Settings>",
        f'  <Actions Context="Author"><Exec><Command>{CMD_EXE}</Command><Arguments>{_text(arguments, "argumentos")}</Arguments>'
        f"<WorkingDirectory>{_text(workdir, 'diretorio')}</WorkingDirectory></Exec></Actions>",
        "</Task>",
    ]
    return "\n".join(lines) + "\n"


def xml_sha256(xml: str) -> str:
    return hashlib.sha256(xml.encode("utf-8")).hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_task_xml(xml: str) -> dict:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ConfigDivergence(f"XML da tarefa ilegivel: {error}") from error
    doc = {"tags": sorted({_local(e.tag) for e in root.iter()}), "triggers": 0, "principals": [], "settings": {}, "execs": [],
           "actions_other": 0, "uri": None}
    for child in root:
        name = _local(child.tag)
        if name == "RegistrationInfo":
            for item in child:
                if _local(item.tag) == "URI":
                    doc["uri"] = (item.text or "").strip()
        elif name == "Triggers":
            doc["triggers"] = len(list(child))
        elif name == "Principals":
            for principal in child:
                doc["principals"].append({_local(i.tag): (i.text or "").strip() for i in principal})
        elif name == "Settings":
            for item in child:
                if _local(item.tag) == "IdleSettings":
                    for idle in item:
                        doc["settings"]["Idle." + _local(idle.tag)] = (idle.text or "").strip()
                else:
                    doc["settings"][_local(item.tag)] = (item.text or "").strip()
        elif name == "Actions":
            for action in child:
                if _local(action.tag) == "Exec":
                    doc["execs"].append({_local(i.tag): (i.text or "").strip() for i in action})
                else:
                    doc["actions_other"] += 1
    return doc


def validate_semantics(xml: str, *, user_aliases: list, arguments: str, workdir: str, limit: str, name: str) -> list:
    """Problemas (lista vazia = o XML e exatamente o desenho aprovado). Ausente = valor padrao do Windows (fail-closed)."""
    doc = parse_task_xml(xml)
    problems = []
    for tag in doc["tags"]:
        if tag in FORBIDDEN_TAGS:
            problems.append(f"elemento proibido: {tag}")
    aliases = {a.lower() for a in user_aliases}
    if len(doc["principals"]) != 1:
        problems.append(f"esperado 1 principal, ha {len(doc['principals'])}")
    else:
        p = doc["principals"][0]
        if p.get("UserId", "").lower() not in aliases:
            problems.append(f"principal difere do usuario atual: {p.get('UserId')!r}")
        if p.get("LogonType") != "InteractiveToken":
            problems.append(f"LogonType {p.get('LogonType')!r} != InteractiveToken")
        if p.get("RunLevel", "LeastPrivilege") != "LeastPrivilege":
            problems.append(f"RunLevel {p.get('RunLevel')!r} != LeastPrivilege")
        if "Password" in p:
            problems.append("principal com senha")
    if doc["triggers"] != 0:
        problems.append(f"{doc['triggers']} gatilho(s): a tarefa deve ser so sob demanda")
    for key, expected in EXPECTED_SETTINGS.items():
        actual = doc["settings"].get(key, SETTING_DEFAULTS[key]).lower() if key != "MultipleInstancesPolicy" \
            else doc["settings"].get(key, SETTING_DEFAULTS[key])
        if actual != expected:
            problems.append(f"{key} = {actual!r} (esperado {expected!r})")
    if doc["settings"].get("ExecutionTimeLimit", SETTING_DEFAULTS["ExecutionTimeLimit"]) != limit:
        problems.append(f"ExecutionTimeLimit {doc['settings'].get('ExecutionTimeLimit')!r} != {limit!r}")
    if len(doc["execs"]) != 1 or doc["actions_other"]:
        problems.append("a tarefa deve ter exatamente 1 acao Exec e nenhuma outra")
    else:
        e = doc["execs"][0]
        if e.get("Command", "").lower() != CMD_EXE.lower():
            problems.append(f"comando {e.get('Command')!r} != {CMD_EXE}")
        if e.get("Arguments") != arguments:
            problems.append("argumentos da acao diferem do pedido")
        if e.get("WorkingDirectory", "").lower() != workdir.lower():
            problems.append("diretorio de trabalho difere do pedido")
    if doc["uri"] is not None and doc["uri"].lower() != (FOLDER + name).lower():
        problems.append(f"URI {doc['uri']!r} fora de {FOLDER}")
    return problems


# ---------------------------------------------------------------------------------------------------------------------
# chamadas ao host (todas por `run`)
# ---------------------------------------------------------------------------------------------------------------------
def _ps(run, script: str, env: dict | None = None, timeout: int = 90) -> str:
    """Executa um script constante. UTF-8 explicito com `errors="replace"`: o diagnostico nunca se perde, e bytes
    ilegiveis na SAIDA reprovam (nunca viram sucesso)."""
    result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env={**os.environ, **(env or {})},
                 capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    if getattr(result, "returncode", 0) != 0:
        raise SchedError(f"powershell falhou (rc={result.returncode}): {(stderr or stdout).strip()[:300]}")
    if "\ufffd" in stdout:
        raise SchedError("saida do PowerShell com bytes nao decodificaveis (UTF-8): resultado rejeitado")
    return stdout.strip()


def _json(run, script: str, env: dict | None = None):
    out = _ps(run, script, env)
    try:
        return json.loads(out)
    except ValueError as error:
        raise SchedError(f"saida do Scheduler nao e JSON: {out[:200]!r}") from error


def _task_env(name: str, **extra) -> dict:
    return {"SIM_TASK_NAME": validate_name(name), "SIM_TASK_PATH": validate_path(FOLDER),
            "SIM_COM_PATH": validate_com_path(COM_FOLDER), **extra}


def identity(run) -> dict:
    return _json(run, PS_IDENTITY)


def is_unelevated(who: dict) -> bool:
    """Token de integridade MEDIA (RID 0x2000, lido da API do Windows) e nao-administrador. Ausente/desconhecido => False."""
    rid = who.get("integrity_rid")
    return who.get("admin_role") is False and isinstance(rid, int) and rid == INTEGRITY_MEDIUM


def require_unelevated(who: dict) -> dict:
    """Falha fechada se o privilegio difere do medido (token medio nao elevado, servico `Schedule` ativo)."""
    if not is_unelevated(who):
        raise UnexpectedPrivilege(f"privilegio inesperado ou desconhecido (admin_role={who.get('admin_role')!r}, "
                                  f"integrity_rid={who.get('integrity_rid')!r}): o desenho exige token de integridade media "
                                  f"(0x{INTEGRITY_MEDIUM:X}) nao elevado")
    if who.get("schedule_state") != "Running":
        raise UnexpectedPrivilege(f"servico Schedule nao esta ativo: {who.get('schedule_state')!r}")
    validate_user(who.get("user", ""))
    return who


def register(run, name: str, xml: str) -> None:
    out = _ps(run, PS_REGISTER, _task_env(name, SIM_TASK_XML=xml))
    if out != "OK":
        raise SchedError(f"registro sem confirmacao: {out[:120]!r}")


def info(run, name: str, *, want_xml: bool = False) -> dict:
    return _json(run, PS_INFO, _task_env(name, SIM_WANT_XML="1" if want_xml else "0"))


def start(run, name: str) -> None:
    if _ps(run, PS_START, _task_env(name)) != "OK":
        raise SchedError("inicio sem confirmacao")


def end(run, name: str) -> None:
    if _ps(run, PS_END, _task_env(name)) != "OK":
        raise SchedError("parada forcada sem confirmacao")


def unregister(run, name: str) -> None:
    if _ps(run, PS_UNREGISTER, _task_env(name)) != "OK":
        raise SchedError("remocao sem confirmacao")


def list_folder(run) -> dict:
    return _json(run, PS_LIST, {"SIM_COM_PATH": validate_com_path(COM_FOLDER)})


def delete_folder_if_empty(run) -> bool:
    listing = list_folder(run)
    if not listing.get("folder_exists"):
        return False
    if listing.get("tasks") or listing.get("folders"):
        return False
    _ps(run, PS_DELETE_FOLDER, {"SIM_COM_PATH": validate_com_path(COM_FOLDER), "SIM_FOLDER_NAME": FOLDER_NAME})
    return True


def process_chain(run, pid: int) -> list:
    data = _json(run, PS_CHAIN, {"SIM_PID": str(int(pid))})
    return data if isinstance(data, list) else [data]


def find_processes(run, marker: str) -> list:
    if not marker or len(marker) < 8 or not SAFE_TEXT.fullmatch(marker):
        raise SchedError("marcador de busca de processos curto ou invalido")
    data = _json(run, PS_PROCS, {"SIM_MARKER": marker})
    return data if isinstance(data, list) else [data]


def process_table(run, schedule_pid: int) -> list:
    """Tabela de processos (cmd/python + o processo do servico `Schedule`) lida do SO, INDEPENDENTE do heartbeat."""
    if not isinstance(schedule_pid, int) or isinstance(schedule_pid, bool) or schedule_pid <= 0:
        raise SchedError(f"PID do servico Schedule invalido: {schedule_pid!r}")
    data = _json(run, PS_PTABLE, {"SIM_SCHEDULE_PID": str(schedule_pid)})
    return data if isinstance(data, list) else [data]


def descendants(table: list, root_pid: int) -> list:
    """PIDs descendentes de `root_pid` na tabela (a prova de ciclos)."""
    found, frontier = [], [root_pid]
    while frontier:
        parent = frontier.pop()
        for p in table:
            if p.get("ppid") == parent and p["pid"] not in found and p["pid"] != root_pid:
                found.append(p["pid"])
                frontier.append(p["pid"])
    return sorted(found)


def task_instances(table: list, schedule_pid: int, proc_marker: str) -> dict:
    """Instancias da tarefa medidas pela arvore real: `cmd.exe` FILHO do servico `Schedule` cuja linha de comando carrega o
    diretorio `.proc` da tarefa (marcador unico). -> {schedule: entrada|None, engines: [{..., tree: [pids]}]}."""
    marker = proc_marker.lower()
    if len(marker) < 8:
        raise SchedError("marcador de instancia curto demais")
    schedule = next((p for p in table if p.get("pid") == schedule_pid), None)
    engines = []
    for p in table:
        if (str(p.get("name", "")).lower() == "cmd.exe" and p.get("ppid") == schedule_pid
                and marker in str(p.get("cmd") or "").lower()):
            engines.append({**p, "tree": [p["pid"], *descendants(table, p["pid"])]})
    return {"schedule": schedule, "engines": engines}


def inspector_tree(run, pid: int | None = None) -> set:
    """PIDs do processo que INSPECIONA (ele mesmo + ancestrais, ex.: launcher do venv). Indeterminavel => `SchedError`."""
    pid = os.getpid() if pid is None else pid
    chain = process_chain(run, pid)
    if not chain or chain[0].get("pid") != pid:
        raise SchedError(f"nao foi possivel determinar a arvore do processo inspetor (PID {pid})")
    return {c["pid"] for c in chain}


def find_orphans(run, marker: str, *, alive) -> dict:
    """Processos vivos cuja linha de comando contem `marker`, EXCLUINDO somente a arvore do inspetor (PID + ancestrais).
    Qualquer outro processo com o marcador continua sendo orfao. Arvore indeterminavel => `SchedError` (fail-closed)."""
    tree = inspector_tree(run)
    found = find_processes(run, marker)
    return {"orphans": [p for p in found if p["pid"] not in tree and alive(p["pid"])],
            "excluded": [p for p in found if p["pid"] in tree], "inspector_tree": sorted(tree)}


# ---------------------------------------------------------------------------------------------------------------------
# trilha de auditoria encadeada por hash e ancorada
# ---------------------------------------------------------------------------------------------------------------------
def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Audit:
    """JSONL append-only: {seq, prev, t, event, payload, hash}. Ancoras (seq + hash da cauda) em DOIS lugares: no `.proc`
    e em `<runs_root>/sched_anchors/<nome>.json`. Detecta registro alterado, ausente (lacuna ou truncamento) e ancora perdida."""

    def __init__(self, proc, anchors_dir, name: str, clock=time.time) -> None:
        self.proc, self.name, self.clock = Path(proc), validate_name(name), clock
        self.path = self.proc / AUDIT_NAME
        self.anchors = [self.proc / ANCHOR_NAME, Path(anchors_dir) / f"{name}.json"]
        self.genesis = hashlib.sha256(f"sim.sched.audit.v1:{name}".encode("utf-8")).hexdigest()

    @staticmethod
    def _hash(record: dict) -> str:
        body = {k: v for k, v in record.items() if k != "hash"}
        return hashlib.sha256(canonical_bytes(body)).hexdigest()

    def records(self) -> list:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_bytes().decode("utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def append(self, event: str, payload: dict | None = None) -> dict:
        existing = self.records()
        record = {"seq": len(existing) + 1, "prev": existing[-1]["hash"] if existing else self.genesis, "t": int(self.clock()),
                  "task": self.name, "event": event, "payload": payload or {}}
        record["hash"] = self._hash(record)
        self.proc.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_bytes(record))
        anchor = canonical_bytes({"task": self.name, "seq": record["seq"], "hash": record["hash"]})
        for target in self.anchors:
            _atomic(target, anchor)
        return record

    def verify(self) -> dict:
        problems, codes = [], set()
        try:
            records = self.records()
        except (ValueError, UnicodeDecodeError) as error:
            return {"ok": False, "problems": [f"trilha ilegivel: {error}"], "codes": ["AUDIT_TAMPERED"], "records": 0, "state": None}
        previous = self.genesis
        for index, record in enumerate(records, start=1):
            if record.get("seq") != index:
                problems.append(f"lacuna ou reordenacao: esperado seq {index}, achado {record.get('seq')}")
                codes.add("AUDIT_RECORDS_MISSING")
            if record.get("prev") != previous or record.get("hash") != self._hash(record):
                problems.append(f"registro {index} adulterado ou encadeamento quebrado")
                codes.add("AUDIT_TAMPERED")
            previous = record.get("hash")
        for anchor_path in self.anchors:
            try:
                anchor = json.loads(anchor_path.read_bytes().decode("utf-8"))
            except (OSError, ValueError):
                if records:
                    problems.append(f"ancora ausente ou ilegivel: {anchor_path.name}")
                    codes.add("AUDIT_ANCHOR_MISSING")
                continue
            if anchor.get("seq", 0) > len(records):
                problems.append(f"trilha truncada: ancora em seq {anchor['seq']}, ha {len(records)} registro(s)")
                codes.add("AUDIT_RECORDS_MISSING")
            elif anchor.get("seq", 0) >= 1 and records[anchor["seq"] - 1].get("hash") != anchor.get("hash"):
                problems.append("hash da ancora difere do registro correspondente")
                codes.add("AUDIT_TAMPERED")
        return {"ok": not problems, "problems": problems, "codes": sorted(codes), "records": len(records),
                "state": recorded_state(records)}


STATE_EVENTS = {"REGISTERED": "REGISTERED", "STARTED": "STARTED", "STOPPED": "STOPPED", "UNREGISTERED": "UNREGISTERED",
                "REMOVAL_FAILED": "REMOVAL_FAILED", "CONFIG_DIVERGENCE": "CONFIG_DIVERGENCE"}


def recorded_state(records: list) -> str | None:
    state = None
    for record in records:
        state = STATE_EVENTS.get(record.get("event"), state)
    return state


# ---------------------------------------------------------------------------------------------------------------------
# operacoes de alto nivel
# ---------------------------------------------------------------------------------------------------------------------
def register_verified(run, name: str, xml: str, *, user_aliases: list, arguments: str, workdir: str, limit: str, audit: Audit) -> dict:
    """Registra SEM sobrescrever, le o XML exportado e o compara semanticamente; divergencia => remove (so este nome) e falha."""
    requested = validate_semantics(xml, user_aliases=user_aliases, arguments=arguments, workdir=workdir, limit=limit, name=name)
    if requested:
        raise ConfigDivergence(f"o XML gerado ja viola o desenho: {requested}")
    register(run, name, xml)
    audit.append("REGISTERED", {"xml_sha256": xml_sha256(xml), "limit": limit})
    exported = info(run, name, want_xml=True)
    if not exported.get("found"):
        audit.append("CONFIG_DIVERGENCE", {"problems": ["tarefa registrada nao foi encontrada"]})
        raise ConfigDivergence("tarefa registrada nao foi encontrada")
    problems = validate_semantics(exported.get("xml") or "<Task/>", user_aliases=user_aliases, arguments=arguments, workdir=workdir,
                                  limit=limit, name=name)
    if problems:
        audit.append("CONFIG_DIVERGENCE", {"problems": problems[:20]})
        try:
            unregister(run, name)
            check = info(run, name)
            audit.append("UNREGISTERED" if not check.get("found") else "REMOVAL_FAILED", {"after": "config_divergence"})
        except SchedError as error:
            audit.append("REMOVAL_FAILED", {"error": str(error)[:200]})
        raise ConfigDivergence(f"configuracao exportada diverge do desenho: {problems}")
    audit.append("EXPORT_VERIFIED", {"exported_xml_sha256": xml_sha256(exported["xml"])})
    return exported


def preserve_evidence(run, name: str, proc: Path, audit: Audit, *, note: str = "pre-removal") -> dict:
    """Grava XML exportado, informacoes e arvore ANTES de qualquer remocao. Sem tarefa, grava so o fato."""
    folder = Path(proc) / EVIDENCE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    state = info(run, name, want_xml=True)
    xml = state.pop("xml", None) if state.get("found") else None
    if xml:
        (folder / "task_export.xml").write_text(xml, encoding="utf-8")
    (folder / "task_info.json").write_bytes(canonical_bytes({k: v for k, v in state.items() if isinstance(v, (int, str, bool, type(None)))}
                                                           | {"instances": [list(sorted(i.items())) for i in state.get("instances", [])]}))
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.is_file() and p.name != "manifest.json"}
    (folder / "manifest.json").write_bytes(canonical_bytes(manifest))
    audit.append("EVIDENCE_PRESERVED", {"note": note, "files": manifest, "found": bool(state.get("found"))})
    return state


def remove_verified(run, name: str, proc: Path, audit: Audit) -> dict:
    """Evidencia primeiro, remocao depois, verificacao por fim. Nao verificada => `RemovalNotVerified` (P15 bloqueia)."""
    state = preserve_evidence(run, name, proc, audit)
    if not state.get("found"):
        audit.append("UNREGISTERED", {"already_absent": True})
        return {"removed": False, "already_absent": True, "folder_removed": delete_folder_if_empty(run)}
    try:
        unregister(run, name)
        after = info(run, name)
    except SchedError as error:
        audit.append("REMOVAL_FAILED", {"error": str(error)[:200]})
        raise RemovalNotVerified(f"remocao da tarefa {name} nao verificada: {error}") from error
    if after.get("found"):
        audit.append("REMOVAL_FAILED", {"still_registered": True})
        raise RemovalNotVerified(f"a tarefa {name} continua registrada apos a remocao")
    audit.append("UNREGISTERED", {"verified_absent": True})
    return {"removed": True, "already_absent": False, "folder_removed": delete_folder_if_empty(run)}


# ---------------------------------------------------------------------------------------------------------------------
# reconciliacao (trilha x Scheduler x processos x ledger) e fatos do P15
# ---------------------------------------------------------------------------------------------------------------------
TERMINAL = ("COMPLETED", "ABORTED", "INVALID")


def reconcile(run, *, proc: Path, anchors_dir, name: str, ledger_state: str | None, alive, apply: bool = False,
              confirm: bool = False, markers: list | None = None) -> dict:
    """-> {ok, findings, recorded_state, observed, actions}. So remove com `apply` (e `confirm` se a trilha estiver comprometida)."""
    audit = Audit(proc, anchors_dir, name)
    check = audit.verify()
    findings = []
    for code in check["codes"]:
        findings.append({"code": code, "detail": check["problems"]})
    observed = info(run, name)
    recorded = check["state"]
    terminal = ledger_state in TERMINAL
    found = bool(observed.get("found"))
    pids = [i.get("pid") for i in observed.get("instances", [])]
    heartbeat = None
    try:
        heartbeat = json.loads((Path(proc) / "heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    process_alive = bool(heartbeat and alive(heartbeat.get("pid")))
    exit_present = (Path(proc) / "exit.json").exists()
    if recorded == "UNREGISTERED" and found:
        findings.append({"code": "STATE_DIVERGENCE", "detail": "a trilha registra a remocao, mas a tarefa existe"})
    if recorded in ("REGISTERED", "EXPORT_VERIFIED", "STARTED", "STOPPED") and check["records"] and not found:
        findings.append({"code": "STATE_DIVERGENCE", "detail": "a trilha registra a tarefa, mas ela nao existe no Scheduler"})
    finished = terminal or (ledger_state is None and exit_present)
    unexpected = (recorded == "STARTED" and not terminal and not exit_present
                  and (not process_alive or (found and observed.get("state") == STATE_READY)))
    if unexpected:
        findings.append({"code": "UNEXPECTED_EXIT", "detail": "processo morto ou tarefa em Ready sem exit.json (ledger nao terminal)",
                         "action": "seal-unclean"})
    if finished and found:
        findings.append({"code": "ORPHAN_TASK", "detail": "run/sonda encerrado e tarefa ainda registrada"})
    if process_alive and not found:
        findings.append({"code": "ORPHAN_PROCESS", "detail": "processo vivo sem tarefa"})
    live_extra = []
    if markers:
        try:
            live_extra = find_orphans(run, markers[0], alive=alive)["orphans"]
        except SchedError as error:
            findings.append({"code": "INSPECTOR_TREE_UNDETERMINED", "detail": str(error)[:200]})
    if live_extra and (finished or not found):
        findings.append({"code": "ORPHAN_PROCESS", "detail": f"{len(live_extra)} processo(s) com o marcador do run"})
    if recorded in ("REMOVAL_FAILED",):
        findings.append({"code": "REMOVAL_FAILED", "detail": "remocao anterior nao verificada"})
    actions = []
    removable = found and finished and not process_alive and observed.get("state") != STATE_RUNNING
    if apply and removable:
        if check["ok"] or confirm:
            actions.append(remove_verified(run, name, proc, audit) | {"action": "remove"})
        else:
            findings.append({"code": "REMOVAL_BLOCKED", "detail": "trilha comprometida: remocao exige --confirm"})
    elif apply and found:
        findings.append({"code": "REMOVAL_BLOCKED", "detail": "run/sonda ainda ativo ou nao terminal: a tarefa nao e removida"})
    return {"ok": not findings, "findings": findings, "recorded_state": recorded,
            "observed": {k: observed.get(k) for k in ("found", "state", "last_result", "enabled", "instances")},
            "process_alive": process_alive, "actions": actions}


def scan_unreconciled(runs_root, run, *, alive) -> dict:
    """Fatos do P15 SEM mutar: tarefas na pasta, trilhas nao reconciliadas, processos orfaos, reboot pendente, privilegio."""
    who = identity(run)
    listing = list_folder(run)
    tasks = [t["name"] for t in listing.get("tasks", [])]
    unreconciled = []
    orphans = 0
    root = Path(runs_root)
    for proc in sorted(root.glob("*.proc")) if root.exists() else []:
        trail = proc / AUDIT_NAME
        if not trail.exists():
            continue
        name = proc.name[: -len(".proc")]
        try:
            verdict = Audit(proc, root / ANCHOR_DIR, name).verify()
        except (SchedError, ValueError):
            unreconciled.append(name)
            continue
        if not verdict["ok"] or verdict["state"] not in ("UNREGISTERED",):
            unreconciled.append(name)
        try:
            beat = json.loads((proc / "heartbeat.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            beat = None
        if beat and alive(beat.get("pid")) and name not in tasks:
            orphans += 1
    return {"scheduler_running": who.get("schedule_state") == "Running", "folder_tasks": len(tasks),
            "unreconciled_runs": len(unreconciled), "orphan_processes": orphans, "reboot_pending": bool(who.get("reboot_pending")),
            "elevated": not is_unelevated(who), "tasks": sorted(tasks), "unreconciled": sorted(unreconciled)}
