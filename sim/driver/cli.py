"""
CLI do Driver (roda DENTRO do container `auneron-sim-driver`; uma invocacao por
slot, para que o estado persistente seja a unica memoria):

    python -m sim.driver.cli run-slot --day 3 --at 11:00
    python -m sim.driver.cli mark-harness-done --seq 2478 --note restart
    python -m sim.driver.cli export-refs
    python -m sim.driver.cli slots            # lista (day, at) da agenda
    python -m sim.driver.cli status

Ambiente: SIM_DRIVER_HOME (estado), SIM_DRIVER_AGENDA, SIM_DRIVER_MANIFEST,
SIM_DRIVER_SECRETS. Saida JSON em stdout. Exit 3 = HARNESS_ERROR (run INVALID).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sim.driver.agenda import load_agenda
from sim.driver.agenda import load_manifest
from sim.driver.errors import HarnessError
from sim.driver.evidence import EvidenceLog
from sim.driver.http import DriverHttp
from sim.driver.http import UrllibTransport
from sim.driver.params import load_params
from sim.driver.runner import Driver
from sim.driver.sessions import PersonaSessions
from sim.driver.state import StateStore


def build_driver(env=None, transport=None) -> Driver:
    env = os.environ if env is None else env
    params = load_params()
    manifest = load_manifest(env["SIM_DRIVER_MANIFEST"])
    ops = load_agenda(env["SIM_DRIVER_AGENDA"], manifest)
    secrets = json.loads(Path(env["SIM_DRIVER_SECRETS"]).read_bytes().decode("utf-8"))
    home = Path(env["SIM_DRIVER_HOME"])
    store = StateStore(home / "driver_state.db")
    evidence = EvidenceLog(home / "driver_evidence.jsonl")
    http = DriverHttp(transport or UrllibTransport(), params["backend_url"], secrets["api_key"],
                      timeout=float(params["http_timeout_s"]))
    sessions = PersonaSessions(http, store, secrets["passwords"], params["email_domain"],
                               int(params["session"]["relogin_margin_minutes"]))
    return Driver(params=params, ops=ops, manifest=manifest, http=http, sessions=sessions, store=store,
                  evidence=evidence)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim.driver.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    slot = sub.add_parser("run-slot")
    slot.add_argument("--day", type=int, required=True)
    slot.add_argument("--at", required=True)
    done = sub.add_parser("mark-harness-done")
    done.add_argument("--seq", type=int, required=True)
    done.add_argument("--note", default="")
    for name in ("export-refs", "slots", "status"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    try:
        driver = build_driver()
        if args.command == "run-slot":
            result = driver.run_slot(args.day, args.at)
        elif args.command == "mark-harness-done":
            driver.mark_harness_done(args.seq, args.note)
            result = {"seq": args.seq, "marked": True}
        elif args.command == "export-refs":
            result = {"refs": driver.store.all_refs(), "due_days": driver.store.all_due()}
        elif args.command == "slots":
            result = [list(key) for key in driver.slot_keys()]
        else:
            result = {"counts": driver.store.counts(), "in_flight": driver.store.in_flight()}
    except HarnessError as error:
        print(json.dumps({"harness_error": str(error)}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
