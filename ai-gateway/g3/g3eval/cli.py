"""
CLI of the G3 harness. The only model-free modes are `verify`, `plan`,
`dry-run` (FakeTransport) and `report`. `run --real` is a refusal stub: the
real transport is switched off (see transport.py) and nothing here builds it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from g3eval import evaluator
from g3eval import fake
from g3eval import gen_v3
from g3eval import plan
from g3eval import report as report_mod
from g3eval import transport as transport_mod
from g3eval import verify_v3


def cmd_dry_run(args: argparse.Namespace) -> int:
    cases, sha = evaluator.load_sealed_cases(
        Path(args.cases), args.expected_sha256
    )
    arm = evaluator.ARM_A if args.arm == "A" else evaluator.ARM_B
    fake_transport = fake.FakeTransport(digests={arm.model_id: arm.digest})
    spec = evaluator.RunSpec(
        block_id="dry-" + args.arm, arm=arm, dataset_label="dry",
        dataset_sha256=sha,
    )
    code, status = evaluator.run_block(
        spec=spec, cases=cases, out_dir=Path(args.out_dir),
        transport=fake_transport,
        free_memory_gib=lambda: 20.0,
        require_release=False,  # FakeTransport only: no model is involved
    )
    print(json.dumps(status, sort_keys=True))
    return code


def cmd_run_real(args: argparse.Namespace) -> int:
    try:
        transport_mod.RealOllamaTransport(
            enabled=bool(args.real),
            authorization_path=(
                Path(args.authorization) if args.authorization else None
            ),
            arm_id=args.arm,
            dataset_sha256=args.expected_sha256,
            now=datetime.now,
        )
    except transport_mod.TransportDisabled as exc:
        print("RECUSADO (transporte real desligado):", exc)
        return evaluator.EXIT_REFUSED

    print("RECUSADO: esta entrega nao executa o transporte real.")
    return evaluator.EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G3 harness (offline)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    v = sub.add_parser("verify")
    v.add_argument("--cases", type=Path, default=gen_v3.SEALED_PATH)
    v.add_argument("--sha256", required=True)
    d = sub.add_parser("dry-run")
    d.add_argument("--cases", type=Path, required=True)
    d.add_argument("--expected-sha256", required=True)
    d.add_argument("--arm", choices=("A", "B"), default="A")
    d.add_argument("--out-dir", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--real", action="store_true")
    r.add_argument("--authorization", type=Path)
    r.add_argument("--arm", choices=("A", "B"), required=True)
    r.add_argument("--expected-sha256", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--a", type=Path, required=True)
    rep.add_argument("--b", type=Path, required=True)
    rep.add_argument("--c", type=Path)
    rep.add_argument("--reps-a", type=Path, nargs="*", default=[])
    rep.add_argument("--reps-b", type=Path, nargs="*", default=[])
    args = parser.parse_args(argv)

    if args.cmd == "plan":
        print(json.dumps(plan.as_table(), indent=1))
        print(json.dumps({"required": plan.totals(False),
                          "with_optional": plan.totals(True)}))
        return 0

    if args.cmd == "verify":
        return verify_v3.main(
            ["--cases", str(args.cases), "--sha256", args.sha256]
        )

    if args.cmd == "dry-run":
        return cmd_dry_run(args)

    if args.cmd == "run":
        return cmd_run_real(args)

    comparison = report_mod.build_comparison(
        args.a, args.b, args.c, args.reps_a, args.reps_b
    )
    print(report_mod.render_markdown(comparison))
    return 0


if __name__ == "__main__":
    sys.exit(main())
