"""CLI de teste do cliente de referencia (dados sinteticos apenas).

Uso: python -m auneron_ai_gateway.client_cli --port N --ca ca.pem --cert c.pem --key k.pem health
     python -m auneron_ai_gateway.client_cli ... generate --request-id ID --instructions T --input T
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .client import GatewayClient, GatewayClientError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="client_cli")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--ca", type=Path, required=True)
    ap.add_argument("--cert", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    g = sub.add_parser("generate")
    g.add_argument("--request-id", required=True)
    g.add_argument("--instructions", required=True)
    g.add_argument("--input", required=True)
    g.add_argument("--schema", default='{"type":"object","properties":{"ok":{"type":"boolean"}}}')
    args = ap.parse_args(argv)
    try:
        client = GatewayClient(args.port, args.ca, args.cert, args.key)
        if args.cmd == "health":
            print(client.health())
        else:
            out = client.generate(
                request_id=args.request_id,
                instructions=args.instructions,
                input_text=args.input,
                output_schema=json.loads(args.schema),
            )
            print(json.dumps(out["output"]))
    except GatewayClientError as exc:
        print(f"erro: {exc.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
