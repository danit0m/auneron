"""Configuracao congelada do lab (`lab_config.yaml`) e caminhos."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

LAB_DIR = Path(__file__).resolve().parent
STACK_DIR = LAB_DIR.parent
REPO_ROOT = STACK_DIR.parents[1]
CONFIG_FILE = LAB_DIR / "lab_config.yaml"


@dataclass(frozen=True)
class LabConfig:
    raw: dict
    sha256: str

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def commit(self) -> str:
        return self.raw["target"]["commit"]

    @property
    def image_tag(self) -> str:
        return self.commit[:12]

    @property
    def project(self) -> str:
        return self.raw["docker"]["project"]

    @property
    def compose_file(self) -> Path:
        return REPO_ROOT / self.raw["docker"]["compose_file"]

    def image(self, kind: str) -> str:
        return f"{self.raw['docker']['images'][kind]}:{self.image_tag}"

    def email(self, user: str) -> str:
        return f"{user}@{self.raw['email_domain']}"


def load_config(path: Path = CONFIG_FILE) -> LabConfig:
    data = path.read_bytes()
    return LabConfig(raw=yaml.safe_load(data.decode("utf-8")), sha256=hashlib.sha256(data).hexdigest())


def lab_home() -> Path:
    """Estado de runtime e segredos: SEMPRE fora do repositorio."""
    home = Path(os.environ.get("AUNERON_SIM_HOME", str(Path.home() / ".auneron-sim"))).resolve()
    repo = REPO_ROOT.resolve()
    if home == repo or repo in home.parents:
        raise RuntimeError("AUNERON_SIM_HOME nao pode ficar dentro do repositorio")
    return home
