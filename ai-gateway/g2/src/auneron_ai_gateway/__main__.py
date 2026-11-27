"""Ponto de entrada: python -m auneron_ai_gateway --config gateway.toml"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .exposure_watch import ExposureWatch
from .glog import AuditLog
from .ollama import OllamaClient
from .server import GatewayServer
from .startup import StartupError, preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auneron_ai_gateway")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        ctx, exposure = preflight(cfg)
    except (ConfigError, StartupError) as exc:
        print(f"PARTIDA RECUSADA: {exc}", file=sys.stderr, flush=True)
        return 2
    log = AuditLog(cfg.log_dir, cfg.retention_days)
    ollama = OllamaClient(cfg.ollama_port, chat_timeout=cfg.total_timeout + 5)
    server = GatewayServer(cfg, ctx, ollama, log, exposure_watch=ExposureWatch(cfg, log))
    try:
        port = server.bind()
    except OSError:
        print("PARTIDA RECUSADA: porta indisponivel", file=sys.stderr, flush=True)
        return 2
    log.event("startup", code=exposure)
    print(f"READY 127.0.0.1:{port}", flush=True)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass
    finally:
        log.event("shutdown", code="ok")
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
