"""SOMENTE TESTE: sobe o servidor REAL sem a camada TLS (ssl=None) e sem a porta de identidade.

Existe porque, nesta maquina, o antivirus intercepta TLS em loopback e impede o teste mTLS fim a fim.
Tudo acima do TLS (HTTP via uvicorn/h11, rotas, contrato, limites, Ollama, logs, cancelamento) e o
codigo de producao. A identidade em si e provada em tests/test_mem_mtls.py (TLS em memoria).
Nunca e importado pelo pacote.
"""

import asyncio
import sys
from pathlib import Path

from auneron_ai_gateway.config import load_config
from auneron_ai_gateway.glog import AuditLog
from auneron_ai_gateway.ollama import OllamaClient
from auneron_ai_gateway.server import GatewayProtocol, GatewayServer
from auneron_ai_gateway.startup import build_ssl_context


class PlainProtocol(GatewayProtocol):
    def _identity(self, transport):  # sem TLS nao ha identidade
        return "ok"


def main() -> int:
    cfg = load_config(Path(sys.argv[1]))
    ctx = build_ssl_context(cfg)  # valida o contexto real e o construtor
    log = AuditLog(cfg.log_dir, cfg.retention_days)
    server = GatewayServer(
        cfg, ctx, OllamaClient(cfg.ollama_port, chat_timeout=cfg.total_timeout + 5), log,
        protocol_base=PlainProtocol,
    )
    server.ssl_ctx = None
    port = server.bind()
    log.event("startup", code="ok_table")
    print(f"READY 127.0.0.1:{port}", flush=True)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
