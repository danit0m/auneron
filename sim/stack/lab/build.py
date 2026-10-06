"""
Build das imagens do lab a partir do COMMIT EXATO (D-1.4-3):
`git archive <commit>` fora do repo -> Dockerfile do produto sem alteracao
-> base; base + libfaketime -> derivada; C1-C5 verificados de forma
independente em cada uma (`verify_build_identity.py verify --require-clean`).
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import STACK_DIR


def extract_commit(runner, commit: str, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = target.with_suffix(".tar")
    result = runner.run(["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "-o", str(archive), commit])
    if result.code != 0:
        raise RuntimeError(f"git archive falhou: {result.err[-200:]}")
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    archive.unlink()
    return target


def docker_build(runner, args: list[str]) -> dict:
    result = runner.run(["docker", "build", *args], timeout=3600)
    return {"code": result.code, "tail": result.text[-800:]}


def image_id(runner, image: str) -> str | None:
    result = runner.run(["docker", "image", "inspect", "-f", "{{.Id}}", image])
    return result.out.strip() if result.code == 0 else None


def verify_identity(runner, image: str, commit: str, host_python: str | None = None) -> dict:
    python = host_python or shutil.which("python")
    result = runner.run(
        [python, "scripts/verify_build_identity.py", "verify", "--image", image, "--git-rev", commit,
         "--require-clean"],
        cwd=str(REPO_ROOT / "backend"), timeout=900,
    )
    checks = [line.strip() for line in result.text.splitlines() if line.strip().startswith(("[PASS]", "[FAIL]"))]
    verdict = next((line.strip() for line in result.text.splitlines() if "BUILD IDENTITY VERIFY" in line), "")
    return {"image": image, "code": result.code, "checks": checks, "verdict": verdict}


def build_all(runner, config, workdir: Path) -> dict:
    commit = config.commit
    evidence = {"commit": commit}
    source = extract_commit(runner, commit, workdir / f"src-{config.image_tag}")
    evidence["source_dir_outside_repo"] = str(source)
    base = config.image("base")
    evidence["base_build"] = docker_build(runner, [
        "-f", str(source / "backend" / "Dockerfile"),
        "--build-arg", f"GIT_SHA={commit}", "--build-arg", "GIT_DIRTY=false",
        "-t", base, str(source),
    ])
    if evidence["base_build"]["code"] != 0:
        return evidence
    evidence["base_verify"] = verify_identity(runner, base, commit)
    backend = config.image("backend")
    evidence["backend_build"] = docker_build(runner, [
        "-f", str(STACK_DIR / "backend-faketime.Dockerfile"),
        "--build-arg", f"BASE_IMAGE={base}", "-t", backend, str(STACK_DIR),
    ])
    if evidence["backend_build"]["code"] == 0:
        evidence["backend_verify"] = verify_identity(runner, backend, commit)
    postgres = config.image("postgres")
    evidence["postgres_build"] = docker_build(runner, [
        "-f", str(STACK_DIR / "postgres-faketime.Dockerfile"), "-t", postgres, str(STACK_DIR),
    ])
    evidence["image_ids"] = {name: image_id(runner, name) for name in (base, backend, postgres)}
    return evidence
