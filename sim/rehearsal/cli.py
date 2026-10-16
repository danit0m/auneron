"""
CLI da fixture SR-1 (offline, so arquivos):

    python -m sim.rehearsal.cli generate --out <dir> [--scale 1000]
    python -m sim.rehearsal.cli verify   --dir <dir>
    python -m sim.rehearsal.cli scan     --root <dir>          # varredura de contaminacao pelos marcadores

Codigos de saida: 0 ok; 1 problema encontrado.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sim.rehearsal import contamination
from sim.rehearsal import generate


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.rehearsal.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--out", required=True)
    gen.add_argument("--scale", type=int, default=1000)
    ver = sub.add_parser("verify")
    ver.add_argument("--dir", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    if args.command == "generate":
        manifest = generate.write_bundle(args.out, scale_permille=args.scale)
        print(json.dumps({"bundle_sha256": manifest["bundle_sha256"], "files": manifest["files"]}, sort_keys=True))
        return 0
    if args.command == "verify":
        problems = generate.verify_bundle(Path(args.dir))
        print(json.dumps({"ok": not problems, "problems": problems}))
        return 0 if not problems else 1
    report = contamination.scan_tree(Path(args.root))
    print(json.dumps({"ok": not report["hits"], **report}))
    return 0 if not report["hits"] else 1


if __name__ == "__main__":
    sys.exit(main())
