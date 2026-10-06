"""
Fixtures do gate SIM-1.2 (T-1..T-10). Os cenarios sao gerados UMA vez por
sessao, em memoria; os testes tambem comparam com os artefatos congelados
em `sim/scenarios/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim.generator.cli import DEFAULT_SEED
from sim.generator.cli import generate
from sim.generator.config import load_inputs

SIM_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = SIM_ROOT / "scenarios"
VARIANTS = ("nh-small", "nh-standard")


class Generated:
    def __init__(self, variant: str) -> None:
        self.variant = variant
        self.scenario_id, self.files, self.summary = generate(variant, DEFAULT_SEED)
        self.agenda = json.loads(self.files["public_agenda.json"])
        self.world = json.loads(self.files["world.json"])
        self.oracle = json.loads(self.files["oracle.json"])
        self.manifest = json.loads(self.files["manifest.json"])


@pytest.fixture(scope="session")
def inputs():
    return load_inputs()


@pytest.fixture(scope="session")
def generated():
    return {variant: Generated(variant) for variant in VARIANTS}


@pytest.fixture(scope="session", params=VARIANTS)
def scenario(request, generated):
    return generated[request.param]


# --- sim.scenario.v2 (SIM-1.3) --------------------------------------------
class GeneratedV2(Generated):
    def __init__(self, variant: str) -> None:
        from sim.generator.cli import generate_v2

        self.variant = variant
        self.scenario_id, self.files, self.summary = generate_v2(variant, DEFAULT_SEED)
        self.agenda = json.loads(self.files["public_agenda.json"])
        self.world = json.loads(self.files["world.json"])
        self.oracle = json.loads(self.files["oracle.json"])
        self.manifest = json.loads(self.files["manifest.json"])


@pytest.fixture(scope="session")
def inputs_v2():
    from sim.generator.config import load_inputs_v2

    return load_inputs_v2()


@pytest.fixture(scope="session")
def generated_v2():
    return {variant: GeneratedV2(variant) for variant in VARIANTS}


@pytest.fixture(scope="session", params=VARIANTS)
def scenario_v2(request, generated_v2):
    return generated_v2[request.param]
