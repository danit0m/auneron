"""
CLI do plano de controle do run:

    # OFFLINE (sem Docker, so arquivos)
    python -m sim.runner.cli verify-ledger  --run-id RUN-...
    python -m sim.runner.cli verify-package --run-id RUN-... [--digest <sha>]
    python -m sim.runner.cli seal-unclean   --run-id RUN-...
    python -m sim.runner.cli authorize-rehearsal --run-id RUN-... --by <quem> --reference <texto>   # NUNCA para kind=RUN
    python -m sim.runner.cli claim          --run-id RUN-...

    # LIVE (precisam de um lab ja de pe; NAO exercitados neste checkpoint)
    python -m sim.runner.cli stage|open|preflight|run --instance <id> [...]

`run` so entra em RUNNING com D0_AUTHORIZATION.json valido. Este CLI nao tem comando que emita essa autorizacao para
`kind = RUN`. Codigos de saida: 0 ok; 1 reprovado; 3 violacao de contrato (R0, interlock, ledger).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sim.runner import claim as claim_mod
from sim.runner import interlock
from sim.runner import package
from sim.runner import runner as runner_mod
from sim.runner.errors import RunnerError
from sim.runner.ledger import RunLedger
from sim.runner.registry import Registry

OFFLINE = ("verify-ledger", "verify-package", "seal-unclean", "authorize-rehearsal", "claim")
LIVE = ("stage", "open", "preflight", "run")


def _runs_root(args) -> Path:
    if args.runs_root:
        return Path(args.runs_root)
    from sim.runner import nh_phases

    return nh_phases.runs_root()


def _offline(args) -> int:
    root = _runs_root(args)
    run_dir = root / args.run_id
    registry = Registry(root)
    if args.command == "verify-ledger":
        report = RunLedger(run_dir).verify()
        interlock.verify_ledger_authorization(RunLedger(run_dir), run_dir)
        print(json.dumps({"ok": True, **report}, sort_keys=True))
        return 0
    if args.command == "verify-package":
        report = package.verify(run_dir, expected_digest=args.digest)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1
    if args.command == "seal-unclean":
        sealed = runner_mod.seal_unclean_exit(run_dir, registry)
        print(json.dumps({"sealed": sealed}))
        return 0
    if args.command == "authorize-rehearsal":
        rim = json.loads((run_dir / "rim.json").read_bytes().decode("utf-8"))
        token = interlock.authorize_rehearsal(run_dir, args.run_id, json.loads((run_dir / "run_id.json").read_bytes())["rim_sha256"],
                                              rim["kind"], args.by, args.reference)
        print(json.dumps({"token_sha256": token["token_sha256"]}))
        return 0
    ledger = RunLedger(run_dir)
    rim = json.loads((run_dir / "rim.json").read_bytes().decode("utf-8"))
    oracle_version = rim["declared"]["oracle_version"]
    evaluated = "EVALUATED" in ledger.replay.seen
    print(json.dumps(claim_mod.claim_for(kind=rim["kind"], state=ledger.state, run_id=args.run_id, oracle_version=oracle_version,
                                         evaluated=evaluated, invalidated_post_hoc="INVALIDATED_POST_HOC" in ledger.replay.seen),
                     sort_keys=True))
    return 0


def _live(args) -> int:
    from sim.live import env as E
    from sim.runner import nh_phases

    lab = E.open_lab(args.instance)
    if args.command == "stage":
        result = nh_phases.phase_stage(lab)
    elif args.command == "open":
        result = nh_phases.phase_open(lab, kind=args.kind, growth_model=Path(args.growth_model) if args.growth_model else None,
                                      purpose=args.purpose, parent=args.parent, reason=args.reason)
    elif args.command == "preflight":
        result = nh_phases.phase_preflight(lab, args.run_id)
    else:
        result = nh_phases.phase_run(lab, args.run_id)
    print(json.dumps({"phase": f"nh-{args.command}", "status": result["status"]}))
    return 0 if result["status"] == "PASS" else 1


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.runner.cli")
    parser.add_argument("command", choices=[*OFFLINE, *LIVE])
    parser.add_argument("--run-id")
    parser.add_argument("--runs-root")
    parser.add_argument("--digest")
    parser.add_argument("--by")
    parser.add_argument("--reference")
    parser.add_argument("--instance")
    parser.add_argument("--kind", default="RUN", choices=("RUN", "REHEARSAL"))
    parser.add_argument("--growth-model")
    parser.add_argument("--purpose", default="FIRST", choices=("FIRST", "RETRY", "REPRODUCTION"))
    parser.add_argument("--parent")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    if args.command in OFFLINE + ("preflight", "run") and not args.run_id:
        parser.error("--run-id e obrigatorio")
    if args.command in LIVE and not args.instance:
        parser.error("--instance e obrigatorio nas fases live")
    if args.command == "authorize-rehearsal" and not (args.by and args.reference):
        parser.error("--by e --reference sao obrigatorios")
    try:
        return _offline(args) if args.command in OFFLINE else _live(args)
    except RunnerError as error:
        print(f"CONTRATO VIOLADO ({type(error).__name__}): {error}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
