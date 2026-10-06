"""
CLI do laboratorio SIM-1.4. Uma fase por comando, para execucao controlada:

    python -m sim.stack.lab.cli <fase> [--instance ID] [--new]

Fases: guard, snapshot-before, build, up, provision, rbac, probe, clock,
quiescence, floor, restart, isolation, teardown, snapshot-after, summary.
GuardViolation => ABORT (exit 3), sem corrigir o ambiente.
"""

from __future__ import annotations

import argparse
import json
import sys

from sim.stack.lab import gates
from sim.stack.lab.guard import GuardViolation

PHASES = {
    "guard": gates.phase_guard,
    "snapshot-before": lambda lab: gates.phase_snapshot(lab, "before"),
    "snapshot-pre-canonical": lambda lab: gates.phase_snapshot(lab, "pre_canonical"),
    "build": gates.phase_build,
    "s1-reuse": gates.phase_s1_reuse,
    "up": gates.phase_up,
    "s2-recheck": gates.phase_s2_recheck,
    "provision": gates.phase_provision,
    "rbac": gates.phase_rbac,
    "probe": gates.phase_probe,
    "clock": gates.phase_clock,
    "quiescence": gates.phase_quiescence,
    "floor": gates.phase_floor,
    "restart": gates.phase_restart,
    "isolation": gates.phase_isolation,
    "reset": gates.phase_reset,
    "teardown": gates.phase_teardown,
    "snapshot-after": lambda lab: gates.phase_snapshot(lab, "after"),
}


def summary(lab) -> dict:
    return {"instance_id": lab.instance_id, "evidence_file": str(lab.evidence_file),
            "gates": {k: v["status"] for k, v in sorted(lab.evidence["gates"].items())}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.stack.lab.cli")
    parser.add_argument("phase", choices=[*PHASES, "summary"])
    parser.add_argument("--instance", default=None)
    parser.add_argument("--new", action="store_true", help="nova instancia (novos segredos)")
    args = parser.parse_args(argv)
    try:
        lab = gates.Lab(args.instance, new=args.new)
        if args.phase == "summary":
            print(json.dumps(summary(lab), indent=1))
            return 0
        result = PHASES[args.phase](lab)
    except GuardViolation as error:
        print(f"ABORT (guarda): {error}")
        return 3
    print(json.dumps({"phase": args.phase, "instance_id": lab.instance_id, "status": result["status"]}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
