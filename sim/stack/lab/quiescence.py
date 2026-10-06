"""
Barreira de quiescencia hibrida (Design Freeze V1.1, sec. 4-5):
1) eventos de conclusao POSTERIORES ao salto (logs do sim-backend);
2) tempo minimo (workers sem sinal -- GAP registrado);
3) estabilidade de um snapshot TECNICO (sem dado de negocio).
Excedeu o teto => HarnessError (run/execucao INVALID).
"""

from __future__ import annotations

import json
import time

from sim.stack.lab.guard import require_container


class HarnessError(RuntimeError):
    pass


def log_tokens(text: str) -> set:
    tokens = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        for key in ("event", "message"):
            value = data.get(key)
            if isinstance(value, str):
                tokens.add(value)
    return tokens


def backend_logs_since(runner, config, since_epoch: int) -> str:
    container = "auneron-sim-backend"
    require_container(container, config)
    result = runner.run(["docker", "logs", "--since", str(since_epoch), container])
    return result.out + result.err


def barrier(runner, config, *, since_epoch: float, expected_events: list, snapshot_fn,
            params: dict | None = None, sleep=time.sleep, now=time.time) -> dict:
    p = dict(config["quiescence"])
    p.update(params or {})
    started = now()
    evidence = {"params": {k: p[k] for k in ("event_wait_timeout_s", "time_floor_s", "stability_gap_s",
                                             "stability_max_attempts", "hard_timeout_s")},
                "expected_events": sorted(expected_events)}
    missing = set(expected_events)
    while True:
        found = log_tokens(backend_logs_since(runner, config, int(since_epoch)))
        missing = set(expected_events) - found
        if not missing:
            break
        if now() - started > p["event_wait_timeout_s"] or now() - started > p["hard_timeout_s"]:
            raise HarnessError(f"eventos ausentes apos o salto: {sorted(missing)}")
        sleep(5)
    evidence["events_seen_after_s"] = round(now() - started, 1)
    floor_wait = p["time_floor_s"] - (now() - since_epoch)
    if floor_wait > 0:
        sleep(floor_wait)
    evidence["time_floor_satisfied"] = True
    previous = snapshot_fn()
    stable = False
    for attempt in range(1, int(p["stability_max_attempts"]) + 1):
        if now() - started > p["hard_timeout_s"]:
            break
        sleep(p["stability_gap_s"])
        current = snapshot_fn()
        if current == previous:
            stable = True
            evidence["stable_after_attempts"] = attempt
            evidence["snapshot"] = current
            break
        previous = current
    total = now() - started
    evidence["total_s"] = round(total, 1)
    if not stable or total > p["hard_timeout_s"]:
        raise HarnessError(f"sem estabilidade tecnica (total {total:.0f}s)")
    return evidence
