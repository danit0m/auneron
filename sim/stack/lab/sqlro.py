"""
SQL SOMENTE LEITURA no `auneron_sim` (D-1.4.1-1): instrumentacao de
INFRAESTRUTURA do lab (catalogo de skills, bindings, grants, principal,
alembic_version, relogio do PostgreSQL). Nunca evidencia de negocio; nunca
usado pelo Driver/Evaluator.
"""

from __future__ import annotations

import re

from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.guard import require_container

_WRITE = re.compile(r"\b(insert|update|delete|truncate|alter|create|drop|grant|revoke|copy|call|do)\b", re.I)


def query(runner, config, sql: str) -> list[list[str]]:
    if _WRITE.search(sql):
        raise GuardViolation("SQL de escrita proibido no lab")
    container = "auneron-sim-postgres"
    require_container(container, config)
    script = f"BEGIN TRANSACTION READ ONLY; {sql}; ROLLBACK;"
    result = runner.run([
        "docker", "exec", container, "psql", "-U", config["database"]["user"], "-d",
        config["database"]["name"], "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", script,
    ])
    if result.code != 0:
        raise RuntimeError(f"sql read-only falhou: {result.err[-300:]}")
    lines = [line for line in result.out.splitlines() if line and line not in ("BEGIN", "ROLLBACK")]
    return [line.split("|") for line in lines]
