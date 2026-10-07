"""
CLI do Evidence Collector (roda DENTRO do container `auneron-sim-collector`):

    python -m sim.collector.cli load-refs            # JSON do Driver em stdin -> refs.json
    python -m sim.collector.cli mark-barrier --name after_floor_barrier
    python -m sim.collector.cli trigger --kind after_daily_barrier --day 3 --at 18:00
    python -m sim.collector.cli status

Ambiente: SIM_COLLECTOR_HOME, SIM_COLLECTOR_PLAN, SIM_COLLECTOR_SECRETS.
Exit 3 = HARNESS_ERROR (run INVALID).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sim.collector.auth import ObserverSession
from sim.collector.chainlog import ChainLog
from sim.collector.client import GetOnlyClient
from sim.collector.client import UrllibTransport
from sim.collector.collect import Collector
from sim.collector.collect import HarnessError
from sim.collector.collect import missing_items

BACKEND_URL = "http://sim-backend:8000"


def build_collector(env=None, transport=None) -> Collector:
    env = os.environ if env is None else env
    plan = json.loads(Path(env["SIM_COLLECTOR_PLAN"]).read_bytes().decode("utf-8"))
    secrets = json.loads(Path(env["SIM_COLLECTOR_SECRETS"]).read_bytes().decode("utf-8"))
    home = Path(env["SIM_COLLECTOR_HOME"])
    transport = transport or UrllibTransport()
    client = GetOnlyClient(transport, BACKEND_URL, secrets["api_key"])
    session = ObserverSession(transport, BACKEND_URL, secrets["api_key"], plan["observer_email"],
                              secrets["observer_password"], home / "session.json")
    return Collector(plan, client, session, home, ChainLog(home / "collected.jsonl"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim.collector.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    trigger = sub.add_parser("trigger")
    trigger.add_argument("--kind", required=True)
    trigger.add_argument("--day", type=int, required=True)
    trigger.add_argument("--at", required=True)
    barrier = sub.add_parser("mark-barrier")
    barrier.add_argument("--name", required=True)
    sub.add_parser("load-refs")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        collector = build_collector()
        if args.command == "trigger":
            result = collector.trigger(args.kind, args.day, args.at)
        elif args.command == "mark-barrier":
            collector.mark_barrier(args.name)
            result = {"completed_barriers": collector.context()["completed_barriers"]}
        elif args.command == "load-refs":
            data = json.loads(sys.stdin.read())
            (collector.home / "refs.json").write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            result = {"refs": len(data.get("refs", {}))}
        else:
            records = ChainLog.read(collector.chain.path) if collector.chain.path.exists() else []
            result = {"collected": len(records), "missing_total": len(missing_items(collector.plan, records))}
    except HarnessError as error:
        print(json.dumps({"harness_error": str(error)}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
