"""
DEV SAFETY SNAPSHOT: estado do Docker FORA do prefixo do lab (antes/depois)
e verificacao read-only do `auneron_test` (nunca escrito).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sim.stack.lab.config import REPO_ROOT


def _lines(runner, args) -> list[str]:
    result = runner.run(args)
    if result.code != 0:
        raise RuntimeError(f"docker falhou: {' '.join(args[:3])}: {result.err[-200:]}")
    return [line for line in result.out.splitlines() if line.strip()]


def docker_snapshot(runner) -> dict:
    containers = []
    for line in _lines(runner, ["docker", "ps", "-a", "--format", "{{.Names}}|{{.ID}}|{{.Image}}"]):
        name, cid, image = line.split("|")
        if name.startswith("auneron-sim-"):
            continue
        started = _lines(runner, ["docker", "inspect", "-f", "{{.State.StartedAt}}|{{.Image}}|{{.State.Status}}", cid])[0]
        containers.append(f"{name}|{cid}|{image}|{started}")
    images = [
        line for line in _lines(runner, ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}"])
        if not line.startswith("auneron-sim-")
    ]
    volumes = [v for v in _lines(runner, ["docker", "volume", "ls", "-q"]) if not v.startswith("auneron_sim_")]
    networks = [n for n in _lines(runner, ["docker", "network", "ls", "--format", "{{.Name}}"])
                if n != "auneron_sim_internal"]
    return {
        "containers": sorted(containers),
        "images": sorted(images),
        "volumes": sorted(volumes),
        "networks": sorted(networks),
    }


_TEST_DB_PROBE = r"""
import json, sys
from sqlalchemy.engine import make_url
import psycopg
url = None
for line in open(sys.argv[1], encoding="utf-8-sig"):
    if line.startswith("TEST_DATABASE_URL"):
        url = line.split("=", 1)[1].strip().strip('"').strip("'")
u = make_url(url)
assert u.database == "auneron_test", u.database
conninfo = f"host={u.host} port={u.port} dbname={u.database} user={u.username} password={u.password}"
with psycopg.connect(conninfo) as conn:
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    version = conn.execute("select version_num from alembic_version").fetchall()
    tables = conn.execute("select count(*) from information_schema.tables where table_schema='public'").fetchone()[0]
    schemas = conn.execute("select count(*) from pg_namespace where nspname like '%sim%'").fetchone()[0]
    conn.rollback()
print(json.dumps({"database": u.database, "alembic_version": [r[0] for r in version], "public_tables": tables, "sim_schemas": schemas}))
"""


def auneron_test_readonly(runner, host_python: str | None = None) -> dict:
    python = host_python or shutil.which("python")
    env_file = REPO_ROOT / "backend" / ".env.test"
    result = runner.run([python, "-c", _TEST_DB_PROBE, str(env_file)])
    if result.code != 0:
        return {"error": result.err[-200:]}
    return json.loads(result.out.strip().splitlines()[-1])


def capture(runner, path: Path) -> dict:
    snapshot = {"docker": docker_snapshot(runner), "auneron_test": auneron_test_readonly(runner)}
    path.write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
    return snapshot


def diff(before: dict, after: dict) -> dict:
    out = {}
    for key in before["docker"]:
        b, a = set(before["docker"][key]), set(after["docker"][key])
        if a != b:
            out[key] = {"removed": sorted(b - a), "added": sorted(a - b)}
    if before["auneron_test"] != after["auneron_test"]:
        out["auneron_test"] = {"before": before["auneron_test"], "after": after["auneron_test"]}
    return out
