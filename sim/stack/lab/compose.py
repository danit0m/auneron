"""Chamadas ao `docker compose` do lab, sempre atras da guarda."""

from __future__ import annotations

import os

from sim.stack.lab.guard import assert_compose_safe
from sim.stack.lab.guard import require_project


def compose_env(config, secrets: dict, floor: str = "") -> dict:
    env = dict(os.environ)
    env.update({
        "SIM_DB_PASSWORD": secrets["db_password"],
        "SIM_API_KEY": secrets["api_key"],
        "SIM_IMAGE_TAG": config.image_tag,
        "SIM_EVIDENCE_FLOOR": floor,
    })
    return env


def compose(runner, config, env, *args, timeout: int = 900):
    require_project(config.project, config)
    assert_compose_safe(runner, config, env)
    return runner.run(
        ["docker", "compose", "-p", config.project, "-f", str(config.compose_file), *args],
        env=env, timeout=timeout,
    )
