"""
VALUE-3.4D-2a -- verificacao independente da identidade de build.

Modos (somente stdlib; carrega `app/core/build_identity.py` por caminho --
a MESMA implementacao do `sd1` usada pelo processo -- sem importar o pacote
`app`):

* `claims`  : alegacao do processo de build, a partir de um checkout/worktree
              real: `GIT_SHA=<40 hex>` e `GIT_DIRTY=<true|false>` (repo
              inteiro, `--untracked-files=all`). Saida pronta para
              `$GITHUB_OUTPUT` ou `--build-arg`.
* `digest`  : `sd1` esperado, a partir dos OBJETOS de um commit
              (`--git-rev`) ou de um diretorio (`--dir`).
* `report`  : identidade medida pelo proprio processo/imagem (JSON, sem
              ecoar conteudo arbitrario de variaveis).
* `verify`  : compara uma imagem com um commit (checks C1..C5). O digest
              esperado vem SEMPRE dos objetos do commit, nunca do relatorio
              da imagem (isso seria circular).

Saidas: 0 = PASS, 2 = FAIL, 3 = indisponivel (docker/git ausente, imagem ou
commit inexistente). Nunca grava nada nem altera imagens.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from typing import Mapping
from typing import Sequence


BACKEND_DIR = Path(__file__).resolve().parents[1]
_MODULE_PATH = BACKEND_DIR / "app" / "core" / "build_identity.py"

EXIT_PASS = 0
EXIT_FAIL = 2
EXIT_UNAVAILABLE = 3

LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_DIRTY = "org.auneron.build.dirty"

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_REGULAR_MODES = frozenset({"100644", "100755"})

Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


def _load_build_identity():
    spec = importlib.util.spec_from_file_location(
        "auneron_build_identity_module", _MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("build_identity.py nao encontrado")
    module = importlib.util.module_from_spec(spec)
    # `from __future__ import annotations` + dataclass exigem o modulo
    # registrado em sys.modules durante a execucao.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bi = _load_build_identity()


class ToolUnavailable(Exception):
    """git/docker ausente, imagem ou commit inexistente."""


def default_runner(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    input: bytes | None = None,
) -> "subprocess.CompletedProcess[bytes]":
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            input=input,
            capture_output=True,
            check=False,
            timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ToolUnavailable(
            f"{argv[0]}: {type(error).__name__}"
        ) from None


def _git(
    runner: Runner,
    repo_dir: Path,
    *arguments: str,
    input: bytes | None = None,
) -> bytes:
    result = runner(
        ["git", "-C", str(repo_dir), *arguments], input=input
    )
    if result.returncode != 0:
        raise ToolUnavailable(f"git {arguments[0]} falhou")
    return result.stdout


# ---------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------


def compute_claims(
    repo_dir: Path = BACKEND_DIR, runner: Runner = default_runner
) -> tuple[str, str]:
    sha = _git(runner, repo_dir, "rev-parse", "HEAD").decode().strip()
    if _FULL_SHA.fullmatch(sha) is None:
        raise ToolUnavailable("git rev-parse HEAD nao retornou 40 hex")

    status = _git(
        runner,
        repo_dir,
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    return sha, "true" if status.strip() else "false"


# ---------------------------------------------------------------------
# digest esperado (objetos do commit)
# ---------------------------------------------------------------------


def resolve_commit(
    rev: str, repo_dir: Path = BACKEND_DIR, runner: Runner = default_runner
) -> str:
    resolved = (
        _git(runner, repo_dir, "rev-parse", "--verify", f"{rev}^{{commit}}")
        .decode()
        .strip()
    )
    if _FULL_SHA.fullmatch(resolved) is None:
        raise ToolUnavailable("commit nao resolvido")
    return resolved


def digest_from_git(
    rev: str,
    repo_dir: Path = BACKEND_DIR,
    runner: Runner = default_runner,
):
    listing = _git(
        runner,
        repo_dir,
        "ls-tree",
        "-r",
        "-z",
        rev,
        "--",
        *bi.SCOPE_DIRS,
        *bi.SCOPE_FILES,
    )

    selected: list[tuple[str, str]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, object_type, object_id = meta.decode().split(" ")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            raise bi.SourceDigestError("non_ascii_path") from None
        if not bi.is_in_scope(path):
            continue
        if mode == "120000":
            raise bi.SourceDigestError("symlink")
        if mode not in _REGULAR_MODES or object_type != "blob":
            raise bi.SourceDigestError("non_regular_file")
        selected.append((path, object_id))

    if not selected:
        raise bi.SourceDigestError("empty_set")

    batch = _git(
        runner,
        repo_dir,
        "cat-file",
        "--batch",
        input="".join(f"{oid}\n" for _, oid in selected).encode(),
    )

    entries: list[tuple[str, bytes]] = []
    cursor = 0
    for path, object_id in selected:
        line_end = batch.index(b"\n", cursor)
        header = batch[cursor:line_end].decode().split(" ")
        if len(header) != 3 or header[0] != object_id or header[1] != "blob":
            raise ToolUnavailable("resposta inesperada do git cat-file")
        size = int(header[2])
        start = line_end + 1
        entries.append((path, batch[start : start + size]))
        cursor = start + size + 1

    return bi.manifest_digest(entries)


# ---------------------------------------------------------------------
# report (medido pelo proprio processo/imagem)
# ---------------------------------------------------------------------


def build_report(
    environ: Mapping[str, str] | None = None,
    root: Path | str | None = None,
) -> dict:
    diagnosis = bi.diagnose_build_identity(environ, root)
    digest = diagnosis.source_digest
    return {
        "state": diagnosis.state,
        "code": diagnosis.code,
        "failures": list(diagnosis.failures),
        "detail": diagnosis.detail,
        "git_sha": diagnosis.git_sha,
        "git_dirty": diagnosis.git_dirty,
        "source_digest_algorithm": bi.SOURCE_DIGEST_ALGORITHM,
        "source_digest": digest.digest if digest else None,
        "source_file_count": digest.file_count if digest else None,
    }


# ---------------------------------------------------------------------
# verify (imagem x commit)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    check_id: str
    description: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class VerifyResult:
    checks: tuple[Check, ...]
    exit_code: int

    @property
    def passed(self) -> bool:
        return self.exit_code == EXIT_PASS


def _env_value(environment: Sequence[str], name: str) -> str | None:
    prefix = f"{name}="
    for entry in environment:
        if entry.startswith(prefix):
            return entry[len(prefix) :]
    return None


def _inspect_image(image: str, runner: Runner) -> dict:
    result = runner(["docker", "image", "inspect", image])
    if result.returncode != 0:
        raise ToolUnavailable("docker image inspect falhou")
    try:
        return json.loads(result.stdout)[0]["Config"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ToolUnavailable("docker image inspect ilegivel") from None


def _image_report(image: str, runner: Runner) -> dict | None:
    result = runner(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image,
            "scripts/verify_build_identity.py",
            "report",
        ]
    )
    if result.returncode != 0:
        return None
    lines = [
        line
        for line in result.stdout.decode(errors="replace").splitlines()
        if line.strip()
    ]
    try:
        report = json.loads(lines[-1])
    except (ValueError, IndexError):
        return None
    return report if isinstance(report, dict) else None


def verify_image(
    image: str,
    git_rev: str | None,
    *,
    require_clean: bool = False,
    expect_invalid: bool = False,
    repo_dir: Path = BACKEND_DIR,
    runner: Runner = default_runner,
) -> VerifyResult:
    config = _inspect_image(image, runner)
    labels = config.get("Labels") or {}
    environment = config.get("Env") or []

    report = _image_report(image, runner)
    if report is None:
        return VerifyResult(
            (
                Check(
                    "C0",
                    "relatorio de identidade disponivel na imagem",
                    False,
                    "a imagem nao executou `report` (anterior ao D-2a?)",
                ),
            ),
            EXIT_FAIL,
        )

    if expect_invalid:
        # controle negativo: build sem identidade NAO e apto.
        checks = (
            Check(
                "N1",
                "identidade medida e invalida por SHA ausente/unknown",
                report.get("state") == "invalid"
                and report.get("code") == bi.CODE_SHA_INVALID,
                f"state={report.get('state')} code={report.get('code')}",
            ),
            Check(
                "N2",
                "label revision permanece unknown (sem identidade fabricada)",
                labels.get(LABEL_REVISION) == "unknown",
                "label revision nao e `unknown`",
            ),
            Check(
                "N3",
                "dirty permanece unknown (nunca `false` por default)",
                labels.get(LABEL_DIRTY) == "unknown"
                and _env_value(environment, bi.ENV_GIT_DIRTY) == "unknown",
                "dirty nao e `unknown`",
            ),
        )
        return VerifyResult(
            checks,
            EXIT_PASS if all(c.passed for c in checks) else EXIT_FAIL,
        )

    if not git_rev:
        raise ToolUnavailable("--git-rev e obrigatorio sem --expect-invalid")

    commit = resolve_commit(git_rev, repo_dir, runner)
    try:
        expected = digest_from_git(commit, repo_dir, runner)
    except bi.SourceDigestError as error:
        raise ToolUnavailable(
            f"digest esperado indisponivel: {error.detail}"
        ) from None

    reported_sha = report.get("git_sha")
    reported_dirty = report.get("git_dirty")

    checks = [
        Check(
            "C1",
            "identidade medida na imagem e valida",
            report.get("state") == "valid",
            f"state={report.get('state')} code={report.get('code')} "
            f"failures={report.get('failures')}",
        ),
        Check(
            "C2",
            "SHA da imagem == commit verificado",
            reported_sha == commit,
            f"imagem={reported_sha} commit={commit}",
        ),
        Check(
            "C3",
            "label e ENV da imagem coerentes com a identidade medida",
            labels.get(LABEL_REVISION) == reported_sha
            and labels.get(LABEL_DIRTY) == reported_dirty
            and _env_value(environment, bi.ENV_GIT_SHA) == reported_sha
            and _env_value(environment, bi.ENV_GIT_DIRTY)
            == reported_dirty,
            "label/ENV divergem do valor medido",
        ),
        Check(
            "C4",
            "source_digest medido na imagem == digest dos objetos do commit",
            report.get("source_digest_algorithm") == expected.algorithm
            and report.get("source_digest") == expected.digest
            and report.get("source_file_count") == expected.file_count,
            f"imagem={report.get('source_digest')} "
            f"esperado={expected.digest} "
            f"arquivos={report.get('source_file_count')}/"
            f"{expected.file_count}",
        ),
    ]
    if require_clean:
        checks.append(
            Check(
                "C5",
                "alegacao dirty == false",
                reported_dirty == "false",
                f"dirty={reported_dirty}",
            )
        )

    return VerifyResult(
        tuple(checks),
        EXIT_PASS if all(c.passed for c in checks) else EXIT_FAIL,
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_build_identity.py",
        description="Identidade de build do Auneron (VALUE-3.4D-2a).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    claims = commands.add_parser("claims")
    claims.add_argument("--repo", type=Path, default=BACKEND_DIR)

    digest = commands.add_parser("digest")
    source = digest.add_mutually_exclusive_group(required=True)
    source.add_argument("--git-rev")
    source.add_argument("--dir", type=Path)
    digest.add_argument("--repo", type=Path, default=BACKEND_DIR)

    commands.add_parser("report")

    verify = commands.add_parser("verify")
    verify.add_argument("--image", required=True)
    verify.add_argument("--git-rev")
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--expect-invalid", action="store_true")
    verify.add_argument("--repo", type=Path, default=BACKEND_DIR)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = default_runner,
    stdout=None,
) -> int:
    out = sys.stdout if stdout is None else stdout
    arguments = _build_parser().parse_args(argv)

    try:
        if arguments.command == "claims":
            sha, dirty = compute_claims(arguments.repo, runner)
            print(f"GIT_SHA={sha}", file=out)
            print(f"GIT_DIRTY={dirty}", file=out)
            return EXIT_PASS

        if arguments.command == "digest":
            try:
                if arguments.git_rev:
                    commit = resolve_commit(
                        arguments.git_rev, arguments.repo, runner
                    )
                    digest = digest_from_git(
                        commit, arguments.repo, runner
                    )
                else:
                    digest = bi.compute_source_digest(arguments.dir)
            except bi.SourceDigestError as error:
                print(
                    f"SOURCE DIGEST FAIL: {error.detail}", file=out
                )
                return EXIT_FAIL
            print(
                json.dumps(
                    {
                        "algorithm": digest.algorithm,
                        "source_digest": digest.digest,
                        "file_count": digest.file_count,
                    },
                    sort_keys=True,
                ),
                file=out,
            )
            return EXIT_PASS

        if arguments.command == "report":
            print(
                json.dumps(build_report(), sort_keys=True), file=out
            )
            return EXIT_PASS

        result = verify_image(
            arguments.image,
            arguments.git_rev,
            require_clean=arguments.require_clean,
            expect_invalid=arguments.expect_invalid,
            repo_dir=arguments.repo,
            runner=runner,
        )
    except ToolUnavailable as error:
        print(f"BUILD IDENTITY UNAVAILABLE: {error}", file=out)
        return EXIT_UNAVAILABLE

    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        suffix = "" if check.passed else f" -- {check.detail}"
        print(
            f"[{status}] {check.check_id} {check.description}{suffix}",
            file=out,
        )
    print(
        "BUILD IDENTITY VERIFY: "
        + ("PASS" if result.passed else "FAIL"),
        file=out,
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
