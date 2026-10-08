"""
Duplos do SIM-1.5G: um "host" falso que responde aos scripts PowerShell CONSTANTES de `sim.runner.sched` (por identidade do
texto do script), mantendo o estado das tarefas. Nenhuma tarefa real e criada; nenhum processo real e iniciado.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from sim.runner import detach
from sim.runner import sched

THIRD_PARTY = "Auneron - Deteccao de Contas Vencidas"
COM_LABELS = ("info", "list", "delete_folder")
CMDLET_LABELS = ("register", "start", "end", "unregister")
LABELS = {sched.PS_IDENTITY: "identity", sched.PS_REGISTER: "register", sched.PS_START: "start", sched.PS_END: "end",
          sched.PS_UNREGISTER: "unregister", sched.PS_INFO: "info", sched.PS_LIST: "list", sched.PS_DELETE_FOLDER: "delete_folder",
          sched.PS_CHAIN: "chain", sched.PS_PROCS: "procs", sched.PS_PTABLE: "ptable"}


class FakeHost:
    def __init__(self, runs_root, *, user="PC-X\\tomaz", sid="S-1-5-21-1-1000", admin=False, integrity=0x2000,
                 schedule="Running", reboot=False, export=None, fail_unregister=False, keep_after_unregister=False, heartbeat=True,
                 engine_pid=900, probe_pid=901, launcher_pid=902, session=1, schedule_pid=555, chain=None, procs=None, hook=None,
                 inspector_chain=None, engine_parent=None, table_extra=None, ptable_errors=(), table_probe_pid=None, end_leaves_running=False):
        self.runs_root, self.user, self.sid = Path(runs_root), user, sid
        self.who = {"user": user, "sid": sid, "admin_role": admin, "integrity_rid": integrity, "schedule_state": schedule,
                    "schedule_pid": schedule_pid, "session": session, "reboot_pending": reboot}
        self.export, self.fail_unregister, self.keep_after_unregister = export or (lambda xml: xml), fail_unregister, keep_after_unregister
        self.heartbeat, self.engine_pid, self.probe_pid = heartbeat, engine_pid, probe_pid
        self.tasks, self.folder_exists, self.calls, self.alive_pids = {}, False, [], set()
        self.chain = chain if chain is not None else [{"pid": probe_pid, "ppid": engine_pid, "name": "python.exe", "session": session, "cmd": "x"},
                                                      {"pid": engine_pid, "ppid": schedule_pid, "name": "cmd.exe", "session": session, "cmd": "y"}]
        self.procs, self.hook = procs or [], hook
        self.launcher_pid, self.engine_parent = launcher_pid, engine_parent
        self.end_leaves_running = end_leaves_running
        self.table_probe_pid = table_probe_pid if table_probe_pid is not None else probe_pid
        # processo que INSPECIONA (esta suite): ele mesmo + o launcher do venv (ancestral)
        self.inspector_chain = inspector_chain if inspector_chain is not None else [
            {"pid": os.getpid(), "ppid": 7001, "name": "python.exe", "session": session, "cmd": "pytest"},
            {"pid": 7001, "ppid": 1, "name": "python.exe", "session": session, "cmd": "venv launcher"}]
        self.table_extra = table_extra or {}                              # n-esima chamada de `ptable` -> entradas extras (transitorias)
        self.ptable_errors, self.ptable_calls = set(ptable_errors), 0
        self.third_party = {THIRD_PARTY: {"state": 3}}                      # no raiz; nunca deve ser tocada

    # -- utilidades --------------------------------------------------------------------------------------------
    def proc_of(self, name: str) -> Path:
        return detach.proc_dir(self.runs_root, name)

    def alive(self, pid) -> bool:
        return pid in self.alive_pids

    def labels(self) -> list:
        return [c[0] for c in self.calls]

    def _ok(self, text="OK", rc=0, err=""):
        return SimpleNamespace(stdout=text, stderr=err, returncode=rc)

    def _json(self, doc):
        return self._ok(json.dumps(doc))

    # -- despacho ----------------------------------------------------------------------------------------------
    def __call__(self, cmd, **kwargs):
        script, env = cmd[-1], kwargs["env"]
        label = LABELS[script]
        name = env.get("SIM_TASK_NAME")
        if label in COM_LABELS:                                      # API COM: caminho SEM barra final (senao 0x8007007B)
            path = env.get("SIM_COM_PATH")
            if path != "\\AuneronSim":
                return self._ok("", 1, f"GetFolder({path!r}): 0x8007007B nome de pasta invalido")
        elif label in CMDLET_LABELS:                                 # cmdlets: -TaskPath COM barra final
            path = env.get("SIM_TASK_PATH")
            if path != "\\AuneronSim\\":
                return self._ok("", 1, f"-TaskPath {path!r} invalido")
        else:
            path = None
        self.calls.append((label, name, path))
        if self.hook:
            self.hook(self, label, name)
        method = getattr(self, f"do_{label}")
        return method(name, env)

    def do_identity(self, name, env):
        return self._json(self.who)

    def do_register(self, name, env):
        if name in self.tasks:
            return self._ok("", 1, "ja existe")
        self.tasks[name] = {"xml": env["SIM_TASK_XML"], "state": sched.STATE_READY, "last": 267011, "instances": []}
        self.folder_exists = True
        return self._ok()

    def do_info(self, name, env):
        if not self.folder_exists:
            return self._json({"found": False, "folder_exists": False})
        task = self.tasks.get(name)
        if task is None:
            return self._json({"found": False, "folder_exists": True})
        doc = {"found": True, "folder_exists": True, "state": task["state"], "enabled": True, "last_result": task["last"], "last_run": 1,
               "instances": list(task["instances"])}
        if env.get("SIM_WANT_XML") == "1":
            doc["xml"] = self.export(task["xml"])
        return self._json(doc)

    def do_start(self, name, env):
        task = self.tasks[name]
        if task["state"] == sched.STATE_RUNNING:
            return self._ok()                                              # IgnoreNew
        task["state"], task["instances"] = sched.STATE_RUNNING, []                  # como no host real: instancias COM vazias
        self.alive_pids |= {self.engine_pid, self.probe_pid}
        if self.heartbeat:
            proc = self.proc_of(name)
            proc.mkdir(parents=True, exist_ok=True)
            (proc / detach.HEARTBEAT).write_text(json.dumps({"pid": self.probe_pid, "t_epoch": 1000, "t_real": "x", "status": {}}))
        return self._ok()

    def do_end(self, name, env):
        if self.end_leaves_running:                                       # Stop-ScheduledTask "ok", mas a arvore continua
            return self._ok()
        task = self.tasks[name]
        task["state"], task["instances"], task["last"] = sched.STATE_READY, [], 1
        self.alive_pids -= {self.engine_pid, self.probe_pid}
        return self._ok()

    def do_unregister(self, name, env):
        if self.fail_unregister:
            return self._ok("", 1, "acesso negado")
        if not self.keep_after_unregister:
            self.tasks.pop(name, None)
        return self._ok()

    def do_list(self, name, env):
        if not self.folder_exists:
            return self._json({"folder_exists": False, "tasks": [], "folders": []})
        return self._json({"folder_exists": True, "tasks": [{"name": n, "state": t["state"]} for n, t in sorted(self.tasks.items())],
                           "folders": []})

    def do_delete_folder(self, name, env):
        assert not self.tasks, "a pasta so pode ser removida vazia"
        self.folder_exists = False
        return self._ok()

    def do_chain(self, name, env):
        return self._json(self.inspector_chain if int(env["SIM_PID"]) == os.getpid() else self.chain)

    def do_ptable(self, name, env):
        self.ptable_calls += 1
        if self.ptable_calls in self.ptable_errors:
            return self._ok("", 1, "Get-CimInstance falhou")
        schedule_pid = int(env["SIM_SCHEDULE_PID"])
        table = [{"pid": schedule_pid, "ppid": 1, "name": "svchost.exe", "session": 0, "cmd": ""},
                 {"pid": 4242, "ppid": 1, "name": "python.exe", "session": 1, "cmd": "python outro.py"}]
        for task_name, task in sorted(self.tasks.items()):
            if task["state"] != sched.STATE_RUNNING:
                continue
            table += [{"pid": self.engine_pid, "ppid": self.engine_parent if self.engine_parent is not None else schedule_pid,
                       "name": "cmd.exe", "session": self.who["session"],
                       "cmd": f'cmd.exe /d /v:on /s /c ""py" -m x --proc-dir "{self.proc_of(task_name)}" 1>>"o"'},
                      {"pid": self.launcher_pid, "ppid": self.engine_pid, "name": "python.exe", "session": self.who["session"], "cmd": "venv"},
                      {"pid": self.table_probe_pid, "ppid": self.launcher_pid, "name": "python.exe", "session": self.who["session"], "cmd": "base"}]
        table += self.table_extra.get(self.ptable_calls, [])
        return self._json(table)

    def do_procs(self, name, env):
        return self._json(self.procs)


def good_user_aliases(host: FakeHost) -> list:
    return [host.user, host.sid]
