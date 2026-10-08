"""
Bundle de cenario (SIM-1.5F): abstrai QUAL cenario o runner executa (NH-small ou fixture SR-1) sem mudar o esquema fechado
do RIM. Cada bundle expoe os mesmos seis arquivos logicos e a linha do tempo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sim.runner.errors import RunnerError

REPO = Path(__file__).resolve().parents[2]
NH_SCENARIO_ID = "nh-small-v2-s340001"


class BundleError(RunnerError):
    pass


@dataclass(frozen=True)
class Bundle:
    name: str
    kind: str                         # RUN | REHEARSAL
    scenario_id: str
    variant: str
    seed: int
    oracle_version: str
    generator_version: str
    files: dict                       # agenda, world, v2_manifest, v2_oracle, oracle, oracle_manifest -> Path
    derived_from_oracle_sha256: str
    timeline: dict

    def sha(self, key: str) -> str:
        return hashlib.sha256(Path(self.files[key]).read_bytes()).hexdigest()

    def load(self, key: str):
        return json.loads(Path(self.files[key]).read_bytes().decode("utf-8"))

    @property
    def agenda_path(self) -> Path:
        return Path(self.files["agenda"])


def nh_bundle(repo: Path = REPO) -> Bundle:
    from sim.runner.declare import NH_TIMELINE

    scenario = repo / "sim" / "scenarios" / NH_SCENARIO_ID
    oracle_dir = repo / "sim" / "oracle" / "artifacts" / f"{NH_SCENARIO_ID}-oracle-v2.2"
    manifest = json.loads((oracle_dir / "manifest_v2_2.json").read_bytes().decode("utf-8"))
    files = {"agenda": scenario / "public_agenda.json", "world": scenario / "world.json", "v2_manifest": scenario / "manifest.json",
             "v2_oracle": scenario / "oracle.json", "oracle": oracle_dir / "oracle.json",
             "oracle_manifest": oracle_dir / "manifest_v2_2.json"}
    return Bundle(name="nh", kind="RUN", scenario_id=NH_SCENARIO_ID, variant="nh-small", seed=340001, oracle_version="v2.2",
                  generator_version=manifest["generator_version"], files=files,
                  derived_from_oracle_sha256=manifest["derived_from"]["oracle.json"], timeline=dict(NH_TIMELINE))


def sr1_bundle(out_dir: Path, scale_permille: int = 1000, *, generate: bool = True) -> Bundle:
    """Bundle da fixture SR-1. Gera (deterministicamente) se ainda nao existir e SEMPRE verifica o fixture_manifest."""
    from sim.rehearsal import generate as G
    from sim.rehearsal import schedule as S

    out = Path(out_dir)
    if generate and not (out / G.MANIFEST_NAME).exists():
        G.write_bundle(out, scale_permille=scale_permille)
    problems = G.verify_bundle(out)
    if problems:
        raise BundleError(f"bundle da fixture adulterado: {problems}")
    files = {"agenda": out / "public_agenda.json", "world": out / "world.json", "v2_manifest": out / "manifest.json",
             "v2_oracle": out / "oracle.json", "oracle": out / "oracle.json", "oracle_manifest": out / G.MANIFEST_NAME}
    oracle_sha = hashlib.sha256((out / "oracle.json").read_bytes()).hexdigest()
    timeline = {"d0": S.D0, "utc_offset_minutes": -180, "floor_day": S.FLOOR_DAY, "restart_day": S.RESTART_DAY,
                "last_day": S.LAST_DAY}
    return Bundle(name="sr1", kind="REHEARSAL", scenario_id=S.SCENARIO_ID, variant="sim-harness-sr1", seed=S.SEED,
                  oracle_version="v2.2", generator_version=S.GENERATOR_VERSION, files=files,
                  derived_from_oracle_sha256=oracle_sha, timeline=timeline)


def get_bundle(name: str, out_dir: Path | None = None) -> Bundle:
    if name == "nh":
        return nh_bundle()
    if name == "sr1":
        if out_dir is None:
            raise BundleError("o bundle sr1 exige um diretorio de saida")
        return sr1_bundle(out_dir)
    raise BundleError(f"bundle desconhecido: {name!r}")
