"""
Claim do run, GERADA do estado do ledger e da avaliacao daquele `run_id` (D-1.5E-9). Nunca herdada do texto dos
artefatos v2 e nunca emite a expressao de verificacao: `SIMULATION VERIFIED` so por designacao do PO, fora do codigo.
"""

from __future__ import annotations

FORBIDDEN_PHRASE = "SIMULATION VERIFIED"
CLAIM_SCHEMA = "sim.run.claim.v1"


def claim_for(*, kind: str, state: str | None, run_id: str, oracle_version: str, evaluated: bool, partial: bool = False,
              invalidated_post_hoc: bool = False) -> dict:
    if kind == "REHEARSAL":
        text = "TECHNICAL REHEARSAL of the run mechanism over [SIM-HARNESS] fixtures. No business claim."
        level = "TECHNICAL_REHEARSAL"
    elif state in ("ABORTED", "INVALID") or invalidated_post_hoc or state is None:
        text = "NO CLAIM: this run is not valid evidence."
        level = "NO_CLAIM"
    elif state != "COMPLETED":
        text = "NO CLAIM: the run has not completed."
        level = "NO_CLAIM"
    elif not evaluated:
        text = "RUN COMPLETED, NOT EVALUATED. No result is claimed."
        level = "COMPLETED_NOT_EVALUATED"
    else:
        text = (f"SIMULATION RESULT over a known synthetic population, run {run_id}, Oracle {oracle_version}. "
                "Never OPERATIONALLY OBSERVED. No verification claim is made by this package.")
        level = "SIMULATION_RESULT"
    if FORBIDDEN_PHRASE in text:
        raise AssertionError("a claim gerada nao pode conter a expressao de verificacao")
    return {"schema": CLAIM_SCHEMA, "run_id": run_id, "kind": kind, "level": level, "partial": bool(partial), "text": text}
