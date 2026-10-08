"""
Duplos do SIM-1.5G: um "host" falso que responde aos scripts PowerShell CONSTANTES de `sim.runner.sched` (por identidade do
texto do script), mantendo o estado das tarefas. Nenhuma tarefa real e criada; nenhum processo real e iniciado.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sim.runner import detach
from sim.runner import sched

THIRD_PARTY = "Auneron - Deteccao de Contas Vencidas"
LABELS = {sched.PS_IDENTITY: "identity", sched.PS_REGISTER: "register", sched.PS_START: "start", sched.PS_END: "end",
          sched.PS_UNREGISTER: "unregister", sched.PS_INFO: "info", sched.PS_LIST: "list", sched.PS_DELETE_FOLDER: "delete_folder",
          sched.PS_CHAIN: "chain", sched.PS_PROCS: "procs"}


class FakeHost:
    def __init__(self, runs_root, *, user="PC-X\\tomaz", sid="S-1-5-21-1-1000", admin=False, high=False, medium=True,
                 schedule="Running", reboot=False, export=None, fail_unregister=False, keep_after_unregister=False, heartbeat=True,
                 engine_pid=900, probe_pid=901, session=1, schedule_pid=555, chain=None, procs=None, hook=None):
        self.runs_root, self.user, self.sid = Path(runs_root), user, sid
        self.who = {"user": user, "sid": sid, "admin_role": admin, "medium": medium, "high": high, "schedule_state": schedule,
                    "schedule_pid": schedule_pid, "session": session, "reboot_pending": reboot}
        self.export, self.fail_unregister, self.keep_after_unregister = export or (lambda xml: xml), fail_unregister, keep_after_unregister
        self.heartbeat, self.engine_pid, self.probe_pid = heartbeat, engine_pid, probe_pid
        self.tasks, self.folder_exists, self.calls, self.alive_pids = {}, False, [], set()
        self.chain = chain if chain is not None else [{"pid": probe_pid, "ppid": engine_pid, "name": "python.exe", "session": session, "cmd": "x"},
                                                      {"pid": engine_pid, "ppid": schedule_pid, "name": "cmd.exe", "session": session, "cmd": "y"}]
        self.procs, self.hook = procs or [], hook
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
        self.calls.append((label, name, env.get("SIM_TASK_PATH")))
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
        task["state"], task["instances"] = sched.STATE_RUNNING, [{"pid": self.engine_pid, "state": 4}]
        self.alive_pids |= {self.engine_pid, self.probe_pid}
        if self.heartbeat:
            proc = self.proc_of(name)
            proc.mkdir(parents=True, exist_ok=True)
            (proc / detach.HEARTBEAT).write_text(json.dumps({"pid": self.probe_pid, "t_epoch": 1000, "t_real": "x", "status": {}}))
        return self._ok()

    def do_end(self, name, env):
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
        return self._json(self.chain)

    def do_procs(self, name, env):
        return self._json(self.procs)


def good_user_aliases(host: FakeHost) -> list:
    return [host.user, host.sid]
