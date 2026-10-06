"""
Ativacao do floor sintetico em D14 pelo PROCEDIMENTO REAL
(EVIDENCE_ACTIVATION_PROCEDURE): T0 CAPTURADO do relogio do PostgreSQL
(`evidence_floor_preflight.py capture`), validado (`check`), e o backend
RECRIADO com o floor. Nunca escrita direta em banco. So no auneron_sim.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from sim.stack.lab.guard import require_container

BACKEND = "auneron-sim-backend"


def preflight(runner, config, *args):
    require_container(BACKEND, config)
    return runner.run(["docker", "exec", BACKEND, "python", "scripts/evidence_floor_preflight.py", *args])


def future_of(t0: str, hours: int = 1) -> str:
    value = datetime.fromisoformat(t0.replace("Z", "+00:00")) + timedelta(hours=hours)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_t0(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("T0="):
            return line[3:]
    return None


def capture_and_check(runner, config) -> dict:
    # stdout e o contrato do script (T0= / OK / ACTIVATION PREFLIGHT FAIL: <codigo>);
    # stderr so traz warnings do ambiente e NUNCA entra no criterio.
    capture = preflight(runner, config, "capture")
    t0 = parse_t0(capture.out) if capture.code == 0 else None
    evidence = {"capture_code": capture.code, "t0": t0, "capture_stdout": capture.out.strip()}
    if t0 is None:
        return evidence
    check = preflight(runner, config, "check", t0)
    evidence["check_code"] = check.code
    evidence["check_stdout"] = check.out.strip()
    negative_value = future_of(t0)
    negative = preflight(runner, config, "check", negative_value)
    evidence["negative_future_value"] = negative_value
    evidence["negative_future_code"] = negative.code
    evidence["negative_future_stdout"] = negative.out.strip()
    return evidence


def preflight_ok(evidence: dict) -> bool:
    t0 = evidence.get("t0")
    return (t0 is not None and evidence.get("check_code") == 0
            and f"OK T0={t0}" in evidence.get("check_stdout", "").splitlines()
            and evidence.get("negative_future_code") == 2
            and "ACTIVATION PREFLIGHT FAIL: future" in evidence.get("negative_future_stdout", "").splitlines())
