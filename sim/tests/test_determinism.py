"""
T-1 -- mesma seed => mesmos hashes (inclusive com PYTHONHASHSEED diferente
e contra os artefatos congelados).
T-2 -- estabilidade por sub-stream: o cliente N+1 nao altera os clientes
1..N.
"""

from __future__ import annotations

import os
import subprocess
import sys

from sim.generator.canonical import sha256_bytes
from sim.generator.cli import DEFAULT_SEED
from sim.generator.cli import generate
from sim.generator.rng import substream
from sim.generator.world import build_customer
from sim.oracle.schema import validate_scenario

from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT


def test_t1_same_seed_same_bytes(generated):
    again = generate("nh-small", DEFAULT_SEED)[1]
    first = generated["nh-small"].files
    assert {k: sha256_bytes(v) for k, v in first.items()} == {
        k: sha256_bytes(v) for k, v in again.items()
    }


def test_t1_different_seed_differs():
    a = generate("nh-small", DEFAULT_SEED)[1]["world.json"]
    b = generate("nh-small", DEFAULT_SEED + 1)[1]["world.json"]
    assert sha256_bytes(a) != sha256_bytes(b)


def test_t1_independent_of_python_hash_seed():
    repo_root = SIM_ROOT.parent
    outputs = []
    for hash_seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        result = subprocess.run(
            [sys.executable, "-m", "sim.generator.cli", "hashes", "--variant", "nh-small"],
            cwd=repo_root, env=env, capture_output=True, text=True, timeout=600,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


def test_t1_frozen_artifacts_match_regeneration(scenario):
    frozen = SCENARIOS / scenario.scenario_id
    for name, data in scenario.files.items():
        assert (frozen / name).read_bytes() == data, name
    assert validate_scenario(frozen)["status"] == "VALID"


def test_t1_manifest_binds_inputs_and_artifacts(scenario, inputs):
    manifest = scenario.manifest
    assert manifest["inputs"] == dict(sorted(inputs.hashes.items()))
    for name in ("public_agenda.json", "world.json", "oracle.json"):
        assert manifest["artifacts"][name] == sha256_bytes(scenario.files[name])
    assert manifest["seed"] == DEFAULT_SEED
    assert "SIMULATION VERIFIED" in manifest["claim"]


def test_t2_customer_substreams_are_index_stable(inputs):
    profiles = ["P1", "P2", "P3", "P4", "P5"]
    segments = list(inputs.population["segments"])
    def make(count):
        return [
            build_customer(inputs, DEFAULT_SEED, i, profiles[i % 5], segments[i % 5])
            for i in range(count)
        ]
    base = make(60)
    extended = make(61)
    assert [vars(c) for c in base] == [vars(c) for c in extended[:60]]


def test_t2_substream_keys_are_isolated():
    a = substream(DEFAULT_SEED, "title", 3, 1).random()
    substream(DEFAULT_SEED, "title", 99, 7).random()
    b = substream(DEFAULT_SEED, "title", 3, 1).random()
    assert a == b
    assert substream(DEFAULT_SEED, "title", 3, 1).random() != substream(DEFAULT_SEED, "title", 3, 2).random()


def test_t2_names_are_unique(generated):
    names = [c["name"] for c in generated["nh-standard"].world["customers"]]
    assert len(names) == len(set(names))
