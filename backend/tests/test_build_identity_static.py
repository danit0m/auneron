"""
VALUE-3.4D-2a -- guardas estaticas e de render da cadeia de identidade de
build: Dockerfile, composes (dev e prod) e workflow de CI.

Cadeia aprovada: build caller (`GIT_SHA`, `GIT_DIRTY`) -> `ARG` -> `LABEL`
-> `ENV AUNERON_GIT_*` -> `BuildIdentity` em runtime.

Invariantes:

* o default e SEMPRE `unknown`; `false` nunca e default de `dirty`;
* a identidade e baked na imagem: nenhum compose define `AUNERON_GIT_*` em
  `environment`/`env_file` (um override de runtime nao pode reivindicar
  identidade);
* o CI obtem as alegacoes do checkout REAL (nunca constantes) e verifica a
  imagem contra os objetos do commit.

Os testes de render usam `docker compose config` (somente leitura) e sao
pulados sem Docker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DOCKERFILE = BACKEND_DIR / "Dockerfile"
DEV_COMPOSE = BACKEND_DIR / "docker-compose.yml"
PROD_COMPOSE = BACKEND_DIR / "docker-compose.prod.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "backend-ci.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def dockerfile_instructions(text: str) -> list[str]:
    """Instrucoes logicas (continuacoes `\\` unidas), sem comentarios."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [
        re.sub(r"\s+", " ", line.strip())
        for line in joined.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ---------------------------------------------------------------------
# 1. Dockerfile
# ---------------------------------------------------------------------


def test_dockerfile_declares_both_args_with_unknown_defaults() -> None:
    instructions = dockerfile_instructions(read(DOCKERFILE))

    assert "ARG GIT_SHA=unknown" in instructions
    assert "ARG GIT_DIRTY=unknown" in instructions
    # exatamente uma declaracao de cada
    assert instructions.count("ARG GIT_SHA=unknown") == 1
    assert instructions.count("ARG GIT_DIRTY=unknown") == 1


def test_dockerfile_labels_and_env_chain_is_exact() -> None:
    instructions = dockerfile_instructions(read(DOCKERFILE))

    assert (
        'LABEL org.opencontainers.image.revision="${GIT_SHA}" '
        'org.auneron.build.dirty="${GIT_DIRTY}"'
    ) in instructions
    assert (
        'ENV AUNERON_GIT_SHA="${GIT_SHA}" '
        'AUNERON_GIT_DIRTY="${GIT_DIRTY}"'
    ) in instructions


def test_dockerfile_never_fabricates_a_clean_identity() -> None:
    text = read(DOCKERFILE)
    code = "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )

    for forbidden in (
        "ARG GIT_DIRTY=false",
        "GIT_DIRTY=false",
        "AUNERON_GIT_DIRTY=false",
        'AUNERON_GIT_DIRTY="false"',
        "AUNERON_GIT_SHA=0",
    ):
        assert forbidden not in code, forbidden
    # `dirty` so recebe valor do ARG, jamais literal
    for line in dockerfile_instructions(text):
        if "AUNERON_GIT_DIRTY" in line:
            assert "${GIT_DIRTY}" in line


def test_dockerfile_identity_layers_come_after_the_heavy_layers() -> None:
    instructions = dockerfile_instructions(read(DOCKERFILE))

    def index_of(prefix: str) -> int:
        return next(
            position
            for position, line in enumerate(instructions)
            if line.startswith(prefix)
        )

    pip_install = index_of("RUN python -m pip install")
    copy_backend = index_of("COPY --chown=auneron:auneron backend/")
    arg_sha = index_of("ARG GIT_SHA")
    arg_dirty = index_of("ARG GIT_DIRTY")
    label = index_of("LABEL org.opencontainers.image.revision")
    env = index_of("ENV AUNERON_GIT_SHA")
    user = index_of("USER auneron")
    last_run = max(
        position
        for position, line in enumerate(instructions)
        if line.startswith("RUN ")
    )

    # ARG/LABEL/ENV so alteram camadas de metadados (cache preservado)
    assert pip_install < copy_backend < arg_sha < arg_dirty
    assert last_run < arg_sha
    assert arg_dirty < label < env < user


def test_dockerfile_has_no_validation_run_so_manual_builds_still_work() -> (
    None
):
    instructions = dockerfile_instructions(read(DOCKERFILE))
    identity_start = next(
        position
        for position, line in enumerate(instructions)
        if line.startswith("ARG GIT_SHA")
    )

    assert not [
        line
        for line in instructions[identity_start:]
        if line.startswith("RUN ")
    ]


def test_existing_dockerfile_contract_is_preserved() -> None:
    text = read(DOCKERFILE)

    assert "USER auneron" in text
    assert "HEALTHCHECK" in text
    assert "PYTHONDONTWRITEBYTECODE=1" in text
    assert "--forwarded-allow-ips" not in text


# ---------------------------------------------------------------------
# 2. composes
# ---------------------------------------------------------------------


def service_blocks(text: str) -> dict[str, str]:
    text = text.replace("\r\n", "\n")
    services = re.search(
        r"^services:\n(.*?)(?=^\S|\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert services is not None, "compose sem `services:`"
    parts = re.split(
        r"^  (\w[\w-]*):\n", services.group(1), flags=re.MULTILINE
    )
    return {
        parts[index]: parts[index + 1]
        for index in range(1, len(parts), 2)
    }


def build_block(service_text: str) -> str:
    match = re.search(
        r"^    build:\n((?:      .*\n|\n)+)", service_text, re.MULTILINE
    )
    assert match is not None, "servico sem bloco `build:`"
    return match.group(1)


ARGS_BLOCK = (
    "      args:\n"
    "        GIT_SHA: ${GIT_SHA:-unknown}\n"
    "        GIT_DIRTY: ${GIT_DIRTY:-unknown}\n"
)


@pytest.mark.parametrize(
    "compose_path,services",
    [
        (DEV_COMPOSE, ("migration", "backend")),
        (PROD_COMPOSE, ("migration", "backend")),
    ],
    ids=["dev", "prod"],
)
def test_backend_builds_pass_identity_args_with_unknown_defaults(
    compose_path: Path, services: tuple[str, ...]
) -> None:
    blocks = service_blocks(read(compose_path))

    for service in services:
        build = build_block(blocks[service])
        assert "      dockerfile: backend/Dockerfile\n" in build, service
        assert ARGS_BLOCK in build, service


@pytest.mark.parametrize(
    "compose_path", [DEV_COMPOSE, PROD_COMPOSE], ids=["dev", "prod"]
)
def test_migration_and_backend_share_identical_args(
    compose_path: Path,
) -> None:
    blocks = service_blocks(read(compose_path))

    def args_of(service: str) -> str:
        build = build_block(blocks[service])
        return build[build.index("      args:\n") :].split(
            "      tags:\n"
        )[0].rstrip("\n")

    assert args_of("migration") == args_of("backend")


@pytest.mark.parametrize(
    "compose_path", [DEV_COMPOSE, PROD_COMPOSE], ids=["dev", "prod"]
)
def test_frontend_build_has_no_identity_args(compose_path: Path) -> None:
    blocks = service_blocks(read(compose_path))
    frontend = [
        name for name in blocks if "frontend" in name
    ]
    assert frontend

    for name in frontend:
        if "build:" in blocks[name]:
            build = build_block(blocks[name])
            assert "args:" not in build
            assert not re.search(r"^\s+GIT_SHA: ", build, re.MULTILINE)
            assert "GIT_DIRTY" not in build


@pytest.mark.parametrize(
    "compose_path", [DEV_COMPOSE, PROD_COMPOSE], ids=["dev", "prod"]
)
def test_compose_never_sets_runtime_identity_environment(
    compose_path: Path,
) -> None:
    text = read(compose_path)

    # a identidade vem da IMAGEM: nem `environment` nem `env_file`
    assert "AUNERON_GIT_" not in text
    assert "env_file" not in text
    assert "GIT_DIRTY: false" not in text
    assert "GIT_DIRTY:-false" not in text


def test_prod_image_tag_convention_is_preserved() -> None:
    text = read(PROD_COMPOSE)

    assert text.count("- auneron-backend:${GIT_SHA:-local}") == 2
    assert text.count("- auneron-backend:prod") == 2


# ---------------------------------------------------------------------
# 3. workflow
# ---------------------------------------------------------------------


def workflow_steps() -> list[tuple[str, str]]:
    text = read(WORKFLOW)
    chunks = re.split(r"^      - name: ", text, flags=re.MULTILINE)[1:]
    return [
        (chunk.splitlines()[0].strip(), chunk) for chunk in chunks
    ]


def step(name_fragment: str) -> tuple[int, str]:
    for position, (name, chunk) in enumerate(workflow_steps()):
        if name_fragment in name:
            return position, chunk
    raise AssertionError(f"step ausente: {name_fragment}")


def test_workflow_claims_step_runs_before_the_backend_build() -> None:
    claims_at, claims = step("Alegação de identidade de build")
    build_at, _ = step("Build da imagem backend")

    assert claims_at < build_at
    assert "id: build_claims" in claims
    assert (
        'python scripts/verify_build_identity.py claims >> "$GITHUB_OUTPUT"'
        in claims
    )


def test_workflow_build_takes_claims_from_step_outputs_via_env() -> None:
    _, build = step("Build da imagem backend")

    assert (
        "GIT_SHA: ${{ steps.build_claims.outputs.GIT_SHA }}" in build
    )
    assert (
        "GIT_DIRTY: ${{ steps.build_claims.outputs.GIT_DIRTY }}" in build
    )
    assert '--build-arg GIT_SHA="$GIT_SHA"' in build
    assert '--build-arg GIT_DIRTY="$GIT_DIRTY"' in build
    assert "-t auneron-backend:ci" in build
    # nenhuma expressao interpolada diretamente no shell
    run_part = build[build.index("run:") :]
    assert "${{" not in run_part


def test_workflow_never_hardcodes_a_clean_claim() -> None:
    text = read(WORKFLOW)

    for forbidden in (
        "GIT_DIRTY=false",
        "GIT_DIRTY: false",
        'GIT_DIRTY: "false"',
        "GIT_DIRTY=\"false\"",
        "--build-arg GIT_DIRTY=false",
    ):
        assert forbidden not in text, forbidden
    # o SHA tambem nao e constante
    assert not re.search(r"GIT_SHA[=:]\s*[0-9a-f]{40}", text)


def test_workflow_verifies_the_image_against_the_commit_objects() -> None:
    build_at, _ = step("Build da imagem backend")
    verify_at, verify = step("Verificar identidade de build")

    assert verify_at == build_at + 1
    assert "verify_build_identity.py verify" in verify
    assert "--image auneron-backend:ci" in verify
    assert '--git-rev "$(git rev-parse HEAD)"' in verify
    assert "--require-clean" in verify


def test_workflow_has_the_negative_control_without_identity() -> None:
    verify_at, _ = step("Verificar identidade de build")
    negative_at, negative = step("Controle negativo")

    assert negative_at > verify_at
    assert "docker build -f backend/Dockerfile -t auneron-backend:ci-noidentity ." in negative
    # sem nenhum build-arg: prova que o default `unknown` e inapto
    assert "--build-arg" not in negative
    assert "--expect-invalid" in negative
    assert "--image auneron-backend:ci-noidentity" in negative


def test_workflow_identity_steps_precede_the_frontend_image_build() -> None:
    negative_at, _ = step("Controle negativo")
    frontend_at, _ = step("Build da imagem frontend")

    assert negative_at < frontend_at


def test_workflow_triggers_and_job_contract_are_unchanged() -> None:
    text = read(WORKFLOW)

    assert "name: Backend + Frontend CI" in text
    assert "contents: read" in text
    assert "python scripts/release_guard.py" in text
    assert "docker compose -f docker-compose.yml config --quiet" in text


# ---------------------------------------------------------------------
# 4. render do compose (somente leitura)
# ---------------------------------------------------------------------


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except Exception:  # noqa: BLE001
        return False


def _render_build_args(
    compose_file: str, tmp_path: Path, **extra_environment: str
) -> dict[str, dict[str, str]]:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("", encoding="utf-8")

    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "GIT_SHA",
            "GIT_DIRTY",
            "COMPOSE_FILE",
            "COMPOSE_PROFILES",
        }
    }
    environment["API_KEY"] = "compose-render-only-not-a-secret"
    for required in (
        "ACME_EMAIL",
        "CUSTOMER_DOMAIN",
        "EXPECTED_DATABASE_HOST",
        "EXPECTED_DATABASE_NAME",
        "POSTGRES_PASSWORD",
    ):
        environment.setdefault(required, "render-only-value")
    environment.update(extra_environment)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(empty_env_file),
            "-f",
            compose_file,
            "config",
            "--format",
            "json",
        ],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "compose nao renderiza neste ambiente: "
            + result.stderr.strip()[:200]
        )
    services = json.loads(result.stdout)["services"]
    return {
        name: service["build"].get("args", {})
        for name, service in services.items()
        if "build" in service
        and service["build"].get("dockerfile") == "backend/Dockerfile"
    }


SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.skipif(
    not _docker_available(), reason="Docker/Compose indisponivel"
)
@pytest.mark.parametrize(
    "extra_environment,expected",
    [
        ({}, {"GIT_SHA": "unknown", "GIT_DIRTY": "unknown"}),
        (
            {"GIT_SHA": SHA},
            {"GIT_SHA": SHA, "GIT_DIRTY": "unknown"},
        ),
        (
            {"GIT_SHA": SHA, "GIT_DIRTY": "false"},
            {"GIT_SHA": SHA, "GIT_DIRTY": "false"},
        ),
        (
            {"GIT_SHA": SHA, "GIT_DIRTY": "true"},
            {"GIT_SHA": SHA, "GIT_DIRTY": "true"},
        ),
        ({"GIT_SHA": ""}, {"GIT_SHA": "unknown", "GIT_DIRTY": "unknown"}),
    ],
    ids=[
        "no_claims",
        "sha_only",
        "both_claims",
        "dirty_claim",
        "empty_sha_means_unknown",
    ],
)
@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.prod.yml"]
)
def test_rendered_composes_resolve_identity_args(
    tmp_path: Path,
    compose_file: str,
    extra_environment: dict[str, str],
    expected: dict[str, str],
) -> None:
    rendered = _render_build_args(
        compose_file, tmp_path, **extra_environment
    )

    assert set(rendered) == {"migration", "backend"}
    for service, args in rendered.items():
        assert args == expected, service
