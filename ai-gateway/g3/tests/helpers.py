"""Shared helpers for the G3 tests (stdlib only, no network, no model)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from g3eval import evaluator
from g3eval import fake
from g3eval import frozen

ROOT = Path(__file__).resolve().parent.parent
SEALED = ROOT / "sealed" / "cases_v3.json"
SEALED_SHA = "6305167845bb4bf899def8cbcd56fd6995b1c753d2c786c641926b4c05fc7cb3"
V1 = ROOT / "data" / "cases_v1.json"
V2 = ROOT / "data" / "cases_v2.json"
V1_SHA = "80bd6f7c53b1c6ffc8cfcc1276bc3d392d896a26c9b210b8d14baa1618e16a01"
V2_SHA = "609c8186d2836df69760d211f036c30c15feb86f8fde11bcb012753fcd1b10ea"


RELEASE = {
    "authorized_by": "teste", "authorized_at": "2026-01-01T00:00:00",
    "expires_at": "2027-01-01T00:00:00",
}


class Clock:
    """Fake monotonic + wall clock."""

    def __init__(self, start: datetime | None = None) -> None:
        self.t = 0.0
        self.start = start or datetime(2026, 11, 26, 9, 0, 0)

    def mono(self) -> float:
        return self.t

    def wall(self) -> datetime:
        return self.start + timedelta(seconds=self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def small_cases(n: int = 12) -> list[dict[str, Any]]:
    """First n cases of v3 in file order (mixed strata)."""
    return json.loads(SEALED.read_text(encoding="utf-8"))["cases"][:n]


def load_all() -> list[dict[str, Any]]:
    cases, _ = evaluator.load_sealed_cases(SEALED, SEALED_SHA)
    return cases


def spec_for(
    arm: evaluator.ArmConfig = evaluator.ARM_A, **kw: Any
) -> evaluator.RunSpec:
    base = dict(
        block_id="T", arm=arm, dataset_label="v3", dataset_sha256=SEALED_SHA
    )
    base.update(kw)
    return evaluator.RunSpec(**base)


def make_fake(
    arm: evaluator.ArmConfig, clock: Clock, behaviour: Any = None
) -> fake.FakeTransport:
    return fake.FakeTransport(
        digests={arm.model_id: arm.digest},
        behaviour=behaviour,
        advance=clock.advance,
    )


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g3test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def out(self, name: str) -> Path:
        return self.tmp / name

    def run_arm(
        self,
        name: str,
        arm: evaluator.ArmConfig = evaluator.ARM_A,
        cases: list[dict[str, Any]] | None = None,
        behaviour: Any = None,
        clock: Clock | None = None,
        spec_kw: dict[str, Any] | None = None,
        **kw: Any,
    ) -> tuple[int, dict[str, Any], fake.FakeTransport]:
        clock = clock or Clock()
        transport = make_fake(arm, clock, behaviour)
        code, status = evaluator.run_block(
            spec=spec_for(arm, **(spec_kw or {})),
            cases=cases if cases is not None else small_cases(),
            out_dir=self.out(name),
            transport=transport,
            free_memory_gib=kw.pop("free_memory_gib", lambda: 20.0),
            wall_clock=clock.wall,
            clock=clock.mono,
            release=kw.pop("release", RELEASE),
            **kw,
        )
        return code, status, transport

    def rows(self, name: str) -> list[dict[str, Any]]:
        text = (self.out(name) / "metrics.jsonl").read_text(encoding="utf-8")
        return [json.loads(x) for x in text.splitlines() if x.strip()]


__all__ = [
    "Clock", "TempDirCase", "small_cases", "load_all", "spec_for",
    "make_fake", "frozen", "evaluator", "SEALED", "SEALED_SHA", "V1", "V2",
    "V1_SHA", "V2_SHA", "ROOT",
]
