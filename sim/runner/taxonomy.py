"""
Taxonomia de abort/INVALID (SIM-1.5D sec. 9 + SIM-1.5E B.3). Cada classe mapeia para UM estado terminal.
Classe desconhecida => erro (fail-closed): nada vira `ABORTED` por omissao.
"""

from __future__ import annotations

from sim.runner.errors import RunnerError

INVALID = "INVALID"
ABORTED = "ABORTED"

# a evidencia perdeu validade: instrumento, isolamento ou regra do Oracle
INVALID_CLASSES = frozenset({
    "ORACLE_ISOLATION_VIOLATION", "HARNESS_ERROR", "CLOCK_VIOLATION", "BARRIER_FAILURE", "TOPOLOGY_VIOLATION",
    "IDENTITY_DRIFT", "FLOOR_FAILURE", "RESTART_FAILURE", "AUTHORITY_OUTSIDE_C_AUTH_2",
    "UNEXPECTED_STATUS_UNEXPLAINED", "PACKAGE_VERIFY_FAILED", "MASS_BALANCE_MISMATCH", "UNCLASSIFIED_EXCEPTION",
})
# o ambiente nao deixou terminar
ABORTED_CLASSES = frozenset({
    "INFRA_FAILURE", "DISK_LOW", "OPERATOR_STOP", "UNCLEAN_EXIT", "O1_THRESHOLD_REVIEW_REQUIRED",
    "PREFLIGHT_FAILED",
})
# resultado de negocio: NAO altera o estado
NON_TERMINAL_RESULT_CLASSES = frozenset({
    "PRODUCT_DIVERGENCE", "PRODUCT_FINDING", "KNOWN_LIMIT", "CORRECT_ABSTENTION", "NOT_REPRESENTABLE",
})


def terminal_state(failure_class: str) -> str:
    if failure_class in INVALID_CLASSES:
        return INVALID
    if failure_class in ABORTED_CLASSES:
        return ABORTED
    raise RunnerError(f"classe de falha desconhecida ou nao terminal: {failure_class!r}")


def worst_state(classes: list) -> str:
    """INVALID prevalece sobre ABORTED quando ha varias causas no mesmo fechamento."""
    states = {terminal_state(c) for c in classes}
    if not states:
        raise RunnerError("nenhuma classe de falha")
    return INVALID if INVALID in states else ABORTED
