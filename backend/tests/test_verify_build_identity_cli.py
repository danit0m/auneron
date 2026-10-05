"""
VALUE-3.4D-2a -- scripts/verify_build_identity.py (`claims`, `digest`,
`report`, `verify`).

Cobre:

* `claims`: SHA e `dirty` (repo inteiro, `--untracked-files=all`) a partir
  de um checkout real (repositorio git sintetico);
* `digest`: `sd1` a partir dos OBJETOS do commit == working tree ==
  `git archive` (sem `.git`), inclusive com blobs CRLF e arvore adulterada;
* `report`: identidade medida pelo processo/imagem;
* `verify`: checks C1..C5, `--expect-invalid` e a NAO circularidade (o
  digest esperado vem dos objetos do commit, nunca do relatorio da imagem).

Nenhum teste usa Docker de verdade: o `runner` e injetavel; o git e real e
opera so em repositorios temporarios.
"""

from __future__ import annotations

import ast
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import scripts.verify_build_identity as vbi


BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BACKEND_DIR / "scripts" / "verify_build_identity.py"
bi = vbi.bi

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git indisponivel"
)

TREE = [
    ("backend/app/__init__.py", b""),
    ("backend/app/core/a.py", b"print('a')\n"),
    ("backend/app/b.py", b"x = 1\ny = 2\n"),
    ("backend/migrations/versions/m1.py", b"revision = 'abc'\n"),
    ("backend/requirements.txt", b"alembic==1.19.0\n"),
    # fora do escopo `sd1`
    ("backend/tests/test_x.py", b"def test_x(): ...\n"),
    ("backend/scripts/tool.py", b"print(1)\n"),
    ("backend/docs/readme.md", b"# doc\n"),
    ("frontend/package.json", b"{}\n"),
]


def run_git(repo: Path, *arguments: str, input: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        input=input,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode().strip()


def make_repo(
    base: Path, entries: list[tuple[str, bytes]] = TREE
) -> Path:
    repo = base / "repo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.name", "tester")
    run_git(repo, "config", "user.email", "tester@example.invalid")
    run_git(repo, "config", "core.autocrlf", "false")
    run_git(repo, "config", "commit.gpgsign", "false")
    for relative_path, data in entries:
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "synthetic")
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


def backend_of(repo: Path) -> Path:
    return repo / "backend"


def head(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD")


def export_tree(repo: Path, rev: str, destination: Path) -> Path:
    """Equivalente ao contexto de uma imagem: `git archive`, sem `.git`."""
    data = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", rev, "backend"],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(destination)
    root = destination / "backend"
    assert not any(path.name == ".git" for path in destination.rglob(".git"))
    return root


# ---------------------------------------------------------------------
# 1. claims
# ---------------------------------------------------------------------


def test_claims_clean_checkout(repo: Path) -> None:
    sha, dirty = vbi.compute_claims(backend_of(repo))

    assert sha == head(repo)
    assert len(sha) == 40
    assert dirty == "false"


def test_claims_modified_tracked_file_is_dirty(repo: Path) -> None:
    (backend_of(repo) / "app" / "b.py").write_bytes(b"changed\n")

    assert vbi.compute_claims(backend_of(repo))[1] == "true"


def test_claims_staged_new_file_is_dirty(repo: Path) -> None:
    (backend_of(repo) / "app" / "new.py").write_bytes(b"x\n")
    run_git(repo, "add", "backend/app/new.py")

    assert vbi.compute_claims(backend_of(repo))[1] == "true"


def test_claims_untracked_file_in_a_subdirectory_is_dirty(
    repo: Path,
) -> None:
    (backend_of(repo) / "docs" / "deep").mkdir(parents=True)
    (backend_of(repo) / "docs" / "deep" / "note.txt").write_bytes(b"x")

    assert vbi.compute_claims(backend_of(repo))[1] == "true"


def test_claims_dirty_covers_the_whole_repository(repo: Path) -> None:
    # arquivo nao rastreado FORA de backend/ tambem torna o build sujo
    (repo / "notes.txt").write_bytes(b"x")

    assert vbi.compute_claims(backend_of(repo))[1] == "true"


def test_claims_ignored_files_are_not_dirty(repo: Path) -> None:
    (repo / ".gitignore").write_bytes(b"*.log\n")
    run_git(repo, "add", ".gitignore")
    run_git(repo, "commit", "-q", "-m", "ignore")
    (backend_of(repo) / "debug.log").write_bytes(b"x")

    assert vbi.compute_claims(backend_of(repo))[1] == "false"


def test_claims_outside_a_repository_is_unavailable(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(vbi.ToolUnavailable):
        vbi.compute_claims(plain)


def test_claims_cli_prints_exactly_two_lines(repo: Path) -> None:
    out = io.StringIO()

    code = vbi.main(
        ["claims", "--repo", str(backend_of(repo))], stdout=out
    )

    assert code == vbi.EXIT_PASS
    assert out.getvalue().splitlines() == [
        f"GIT_SHA={head(repo)}",
        "GIT_DIRTY=false",
    ]


def test_claims_cli_outside_a_repository_exits_3(tmp_path: Path) -> None:
    out = io.StringIO()

    code = vbi.main(["claims", "--repo", str(tmp_path)], stdout=out)

    assert code == vbi.EXIT_UNAVAILABLE
    assert "UNAVAILABLE" in out.getvalue()


# ---------------------------------------------------------------------
# 2. digest: objetos git == working tree == git archive
# ---------------------------------------------------------------------


def test_git_objects_equal_working_tree_equal_archive(
    repo: Path, tmp_path: Path
) -> None:
    from_git = vbi.digest_from_git("HEAD", backend_of(repo))
    from_tree = bi.compute_source_digest(backend_of(repo))
    from_archive = bi.compute_source_digest(
        export_tree(repo, "HEAD", tmp_path / "export")
    )

    assert from_git == from_tree == from_archive
    assert from_git.file_count == 5
    assert from_git.algorithm == "sd1"


def test_crlf_blobs_in_git_still_match_an_lf_tree(tmp_path: Path) -> None:
    crlf_entries = [
        (path, data.replace(b"\n", b"\r\n")) for path, data in TREE
    ]
    repo = make_repo(tmp_path / "crlf", crlf_entries)

    from_git = vbi.digest_from_git("HEAD", backend_of(repo))
    lf_tree = tmp_path / "lf"
    for path, data in TREE:
        if path.startswith("backend/"):
            target = lf_tree / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    assert from_git == bi.compute_source_digest(lf_tree / "backend")


def test_digest_ignores_out_of_scope_files_in_the_commit(
    repo: Path, tmp_path: Path
) -> None:
    baseline = vbi.digest_from_git("HEAD", backend_of(repo))
    (backend_of(repo) / "tests" / "test_x.py").write_bytes(b"changed\n")
    (backend_of(repo) / "scripts" / "tool.py").write_bytes(b"changed\n")
    run_git(repo, "commit", "-aq", "-m", "out of scope")

    assert vbi.digest_from_git("HEAD", backend_of(repo)) == baseline


def test_digest_changes_with_a_commit_that_touches_the_scope(
    repo: Path,
) -> None:
    baseline = vbi.digest_from_git("HEAD", backend_of(repo))
    (backend_of(repo) / "app" / "b.py").write_bytes(b"changed\n")
    run_git(repo, "commit", "-aq", "-m", "scope")

    assert vbi.digest_from_git("HEAD", backend_of(repo)) != baseline
    # o commit anterior continua reproduzivel por objeto
    assert vbi.digest_from_git("HEAD~1", backend_of(repo)) == baseline


def test_digest_is_taken_from_the_commit_not_the_working_tree(
    repo: Path,
) -> None:
    committed = vbi.digest_from_git("HEAD", backend_of(repo))
    (backend_of(repo) / "app" / "b.py").write_bytes(b"uncommitted\n")

    assert vbi.digest_from_git("HEAD", backend_of(repo)) == committed
    assert bi.compute_source_digest(backend_of(repo)) != committed


def test_symlink_entry_in_a_commit_is_rejected(repo: Path) -> None:
    blob = run_git(repo, "hash-object", "-w", "--stdin", input=b"a.py")
    run_git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{blob},backend/app/link.py",
    )
    run_git(repo, "commit", "-q", "-m", "symlink")

    with pytest.raises(bi.SourceDigestError) as error:
        vbi.digest_from_git("HEAD", backend_of(repo))

    assert error.value.detail == "symlink"

    out = io.StringIO()
    code = vbi.main(
        ["digest", "--git-rev", "HEAD", "--repo", str(backend_of(repo))],
        stdout=out,
    )
    assert code == vbi.EXIT_FAIL
    assert "SOURCE DIGEST FAIL: symlink" in out.getvalue()


def test_commit_without_scope_is_an_error(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path, [("backend/README", b"x\n"), ("other/a.py", b"x\n")]
    )

    with pytest.raises(bi.SourceDigestError) as error:
        vbi.digest_from_git("HEAD", backend_of(repo))

    assert error.value.detail == "empty_set"


def test_digest_cli_modes_agree(repo: Path) -> None:
    git_out, dir_out = io.StringIO(), io.StringIO()

    assert (
        vbi.main(
            [
                "digest",
                "--git-rev",
                "HEAD",
                "--repo",
                str(backend_of(repo)),
            ],
            stdout=git_out,
        )
        == vbi.EXIT_PASS
    )
    assert (
        vbi.main(
            ["digest", "--dir", str(backend_of(repo))], stdout=dir_out
        )
        == vbi.EXIT_PASS
    )

    from_git = json.loads(git_out.getvalue())
    from_dir = json.loads(dir_out.getvalue())
    assert from_git == from_dir
    assert from_git["algorithm"] == "sd1"
    assert set(from_git) == {"algorithm", "source_digest", "file_count"}


def test_digest_cli_unknown_revision_exits_3(repo: Path) -> None:
    out = io.StringIO()

    code = vbi.main(
        [
            "digest",
            "--git-rev",
            "does-not-exist",
            "--repo",
            str(backend_of(repo)),
        ],
        stdout=out,
    )

    assert code == vbi.EXIT_UNAVAILABLE


def test_real_repository_head_digest_is_reproducible() -> None:
    if not (BACKEND_DIR.parent / ".git").exists():
        pytest.skip("checkout sem .git")

    first = vbi.digest_from_git("HEAD", BACKEND_DIR)
    second = vbi.digest_from_git("HEAD", BACKEND_DIR)

    assert first == second
    assert first.algorithm == "sd1"
    assert first.file_count >= 200


# ---------------------------------------------------------------------
# 3. report (medido pelo processo/imagem) e ciclo archive -> "imagem"
# ---------------------------------------------------------------------


def valid_env(sha: str) -> dict[str, str]:
    return {bi.ENV_GIT_SHA: sha, bi.ENV_GIT_DIRTY: "false"}


def test_report_of_an_exported_tree_equals_the_commit_digest(
    repo: Path, tmp_path: Path
) -> None:
    root = export_tree(repo, "HEAD", tmp_path / "export")
    expected = vbi.digest_from_git("HEAD", backend_of(repo))

    report = vbi.build_report(valid_env(head(repo)), root)

    assert report["state"] == "valid"
    assert report["source_digest"] == expected.digest
    assert report["source_file_count"] == expected.file_count
    assert report["source_digest_algorithm"] == "sd1"
    assert report["git_sha"] == head(repo)
    assert report["git_dirty"] == "false"


def test_a_tampered_export_changes_the_digest_but_not_the_claims(
    repo: Path, tmp_path: Path
) -> None:
    root = export_tree(repo, "HEAD", tmp_path / "export")
    expected = vbi.digest_from_git("HEAD", backend_of(repo))
    with (root / "app" / "b.py").open("ab") as handle:
        handle.write(b"# tamper\n")

    report = vbi.build_report(valid_env(head(repo)), root)

    # limite honesto: o runtime so valida a alegacao...
    assert report["state"] == "valid"
    # ...a prova e a comparacao externa
    assert report["source_digest"] != expected.digest


def test_report_never_echoes_arbitrary_environment_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "backend"
    for relative_path, data in TREE:
        if relative_path.startswith("backend/"):
            target = tmp_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    report = vbi.build_report(
        {
            bi.ENV_GIT_SHA: "LEAK_SENTINEL secret=1",
            bi.ENV_GIT_DIRTY: "LEAK too",
        },
        root,
    )

    assert "LEAK" not in json.dumps(report)
    assert report["git_sha"] == "<invalid>"
    assert report["git_dirty"] == "<invalid>"
    assert report["state"] == "invalid"


CHARSET_SAFE_INVALID = [
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "z" * 40,
    "my_api_token_123456789",
    "tok-en.with-dash.and.dot_1234567890",
    "unknown",
    "A" * 64,
]


@pytest.mark.parametrize("value", CHARSET_SAFE_INVALID)
def test_report_never_returns_an_invalid_raw_claim(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(bi.ENV_GIT_SHA, value)
    monkeypatch.setenv(bi.ENV_GIT_DIRTY, value)
    out = io.StringIO()

    assert vbi.main(["report"], stdout=out) == vbi.EXIT_PASS

    printed = out.getvalue()
    report = json.loads(printed)
    assert report["state"] == "invalid"
    assert report["git_sha"] == "<invalid>"
    assert report["git_dirty"] == "<invalid>"
    if value != "unknown":
        assert value not in printed


def test_report_real_process_never_returns_an_invalid_raw_claim() -> None:
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "report"],
        capture_output=True,
        text=True,
        env={
            "PATH": "",
            "SYSTEMROOT": _system_root(),
            bi.ENV_GIT_SHA: token,
            bi.ENV_GIT_DIRTY: "my_api_token_123456789",
        },
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr
    assert "my_api_token_123456789" not in result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["git_sha"] == "<invalid>"
    assert report["git_dirty"] == "<invalid>"


def test_report_in_a_real_subprocess_matches_the_tree() -> None:
    environment = {
        "PATH": "",
        bi.ENV_GIT_SHA: "0123456789abcdef0123456789abcdef01234567",
        bi.ENV_GIT_DIRTY: "false",
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "report"],
        capture_output=True,
        text=True,
        env={**environment, "SYSTEMROOT": _system_root()},
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    expected = bi.compute_source_digest(BACKEND_DIR)
    assert report["state"] == "valid"
    assert report["source_digest"] == expected.digest
    assert report["source_file_count"] == expected.file_count


def _system_root() -> str:
    import os

    return os.environ.get("SYSTEMROOT", "")


# ---------------------------------------------------------------------
# 4. verify (imagem x commit) com docker injetado
# ---------------------------------------------------------------------


class FakeDocker:
    """Runner: git REAL (repositorio temporario); docker simulado."""

    def __init__(
        self,
        *,
        config: dict,
        report: dict | str | None,
        inspect_returncode: int = 0,
        run_returncode: int = 0,
    ) -> None:
        self.config = config
        self.report = report
        self.inspect_returncode = inspect_returncode
        self.run_returncode = run_returncode
        self.docker_calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        if argv[0] == "git":
            return vbi.default_runner(argv, **kwargs)
        self.docker_calls.append(list(argv))
        if argv[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(
                argv,
                self.inspect_returncode,
                json.dumps([{"Config": self.config}]).encode(),
                b"",
            )
        if argv[:2] == ["docker", "run"]:
            body = (
                self.report
                if isinstance(self.report, str)
                else json.dumps(self.report)
            )
            return subprocess.CompletedProcess(
                argv, self.run_returncode, body.encode(), b""
            )
        raise AssertionError(f"comando docker inesperado: {argv}")


def image_config(sha: str, dirty: str = "false") -> dict:
    return {
        "Labels": {
            vbi.LABEL_REVISION: sha,
            vbi.LABEL_DIRTY: dirty,
        },
        "Env": [
            "PATH=/usr/bin",
            f"{bi.ENV_GIT_SHA}={sha}",
            f"{bi.ENV_GIT_DIRTY}={dirty}",
        ],
    }


@pytest.fixture
def pipeline(repo: Path, tmp_path: Path):
    """commit -> `git archive` -> relatorio da 'imagem' (positivo)."""
    sha = head(repo)
    root = export_tree(repo, "HEAD", tmp_path / "export")
    report = vbi.build_report(valid_env(sha), root)

    class Pipeline:
        pass

    state = Pipeline()
    state.repo = repo
    state.sha = sha
    state.root = root
    state.report = report
    return state


def verify(
    pipeline, runner, **options
) -> "vbi.VerifyResult":
    return vbi.verify_image(
        "auneron-backend:test",
        options.pop("git_rev", pipeline.sha),
        repo_dir=backend_of(pipeline.repo),
        runner=runner,
        **options,
    )


def failed_ids(result: "vbi.VerifyResult") -> set[str]:
    return {check.check_id for check in result.checks if not check.passed}


def test_verify_passes_for_a_faithful_image(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    result = verify(pipeline, runner, require_clean=True)

    assert result.passed
    assert result.exit_code == vbi.EXIT_PASS
    assert [c.check_id for c in result.checks] == [
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
    ]
    assert failed_ids(result) == set()


def test_verify_runs_the_image_report_without_network_and_with_python(
    pipeline,
) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    verify(pipeline, runner)

    run_call = next(c for c in runner.docker_calls if c[:2] == ["docker", "run"])
    assert "--rm" in run_call
    assert run_call[run_call.index("--network") + 1] == "none"
    assert run_call[run_call.index("--entrypoint") + 1] == "python"
    assert run_call[-2:] == [
        "scripts/verify_build_identity.py",
        "report",
    ]
    assert {c[1] for c in runner.docker_calls} <= {"image", "run"}


def test_c1_fails_when_the_measured_identity_is_invalid(pipeline) -> None:
    report = vbi.build_report(
        {bi.ENV_GIT_SHA: pipeline.sha, bi.ENV_GIT_DIRTY: "true"},
        pipeline.root,
    )
    runner = FakeDocker(
        config=image_config(pipeline.sha, "true"), report=report
    )

    result = verify(pipeline, runner, require_clean=True)

    assert result.exit_code == vbi.EXIT_FAIL
    assert {"C1", "C5"} <= failed_ids(result)


def test_c2_fails_when_the_image_sha_is_not_the_verified_commit(
    pipeline,
) -> None:
    other = "f" * 40
    report = vbi.build_report(valid_env(other), pipeline.root)
    runner = FakeDocker(config=image_config(other), report=report)

    result = verify(pipeline, runner)

    assert failed_ids(result) == {"C2"}


@pytest.mark.parametrize(
    "mutation",
    [
        "label_revision",
        "label_dirty",
        "env_sha",
        "env_dirty",
        "labels_missing",
        "env_missing",
    ],
)
def test_c3_fails_when_label_or_env_diverge_from_the_measurement(
    pipeline, mutation: str
) -> None:
    config = image_config(pipeline.sha)
    if mutation == "label_revision":
        config["Labels"][vbi.LABEL_REVISION] = "e" * 40
    elif mutation == "label_dirty":
        config["Labels"][vbi.LABEL_DIRTY] = "true"
    elif mutation == "env_sha":
        config["Env"][1] = f"{bi.ENV_GIT_SHA}={'e' * 40}"
    elif mutation == "env_dirty":
        config["Env"][2] = f"{bi.ENV_GIT_DIRTY}=true"
    elif mutation == "labels_missing":
        config["Labels"] = None
    else:
        config["Env"] = ["PATH=/usr/bin"]
    runner = FakeDocker(config=config, report=pipeline.report)

    result = verify(pipeline, runner)

    assert failed_ids(result) == {"C3"}


def test_c4_fails_for_a_tampered_image_even_if_it_claims_valid(
    pipeline,
) -> None:
    with (pipeline.root / "app" / "b.py").open("ab") as handle:
        handle.write(b"# tamper\n")
    tampered = vbi.build_report(valid_env(pipeline.sha), pipeline.root)
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=tampered
    )

    result = verify(pipeline, runner, require_clean=True)

    assert tampered["state"] == "valid"  # a imagem se declara valida...
    assert failed_ids(result) == {"C4"}  # ...e a verificacao a reprova
    assert result.exit_code == vbi.EXIT_FAIL


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_digest_algorithm", "sd2"),
        ("source_file_count", 99),
        ("source_digest", "0" * 64),
    ],
)
def test_c4_checks_algorithm_count_and_digest(
    pipeline, field: str, value
) -> None:
    report = {**pipeline.report, field: value}
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=report
    )

    assert failed_ids(verify(pipeline, runner)) == {"C4"}


def test_expected_digest_comes_from_the_commit_objects_not_the_image(
    pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = vbi.digest_from_git

    def spy(rev, repo_dir=vbi.BACKEND_DIR, runner=vbi.default_runner):
        calls.append(rev)
        return real(rev, repo_dir, runner)

    monkeypatch.setattr(vbi, "digest_from_git", spy)
    # imagem adulterada cujo relatorio e internamente consistente
    with (pipeline.root / "app" / "b.py").open("ab") as handle:
        handle.write(b"# tamper\n")
    tampered = vbi.build_report(valid_env(pipeline.sha), pipeline.root)
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=tampered
    )

    result = verify(pipeline, runner)

    assert calls == [pipeline.sha]  # resolvido para o commit completo
    assert not result.passed

    # prova de que um verificador CIRCULAR passaria (e por isso proibido)
    circular_expected = bi.SourceDigest(
        "sd1",
        tampered["source_digest"],
        tampered["source_file_count"],
    )
    monkeypatch.setattr(
        vbi, "digest_from_git", lambda *a, **k: circular_expected
    )
    assert verify(pipeline, runner).passed


def test_c0_when_the_image_cannot_run_the_report(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha),
        report="",
        run_returncode=1,
    )

    result = verify(pipeline, runner)

    assert result.exit_code == vbi.EXIT_FAIL
    assert failed_ids(result) == {"C0"}


@pytest.mark.parametrize("body", ["not json", "[1, 2]", ""])
def test_unparseable_report_is_c0(pipeline, body: str) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=body
    )

    assert failed_ids(verify(pipeline, runner)) == {"C0"}


def test_inspect_failure_is_unavailable_not_fail(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha),
        report=pipeline.report,
        inspect_returncode=1,
    )

    with pytest.raises(vbi.ToolUnavailable):
        verify(pipeline, runner)


def test_unknown_commit_is_unavailable(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    with pytest.raises(vbi.ToolUnavailable):
        verify(pipeline, runner, git_rev="no-such-rev")


def test_git_rev_is_mandatory_without_expect_invalid(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    with pytest.raises(vbi.ToolUnavailable):
        verify(pipeline, runner, git_rev=None)


def test_require_clean_is_an_independent_check(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    without = verify(pipeline, runner)
    with_clean = verify(pipeline, runner, require_clean=True)

    assert [c.check_id for c in without.checks] == ["C1", "C2", "C3", "C4"]
    assert "C5" in [c.check_id for c in with_clean.checks]


# ---------------------------------------------------------------------
# 5. --expect-invalid (controle negativo: unknown e inapto)
# ---------------------------------------------------------------------


def no_identity_report(pipeline) -> dict:
    return vbi.build_report(
        {bi.ENV_GIT_SHA: "unknown", bi.ENV_GIT_DIRTY: "unknown"},
        pipeline.root,
    )


def test_expect_invalid_passes_for_a_build_without_identity(
    pipeline,
) -> None:
    config = image_config("unknown", "unknown")
    runner = FakeDocker(config=config, report=no_identity_report(pipeline))

    result = verify(pipeline, runner, git_rev=None, expect_invalid=True)

    assert result.passed
    assert [c.check_id for c in result.checks] == ["N1", "N2", "N3"]


def test_expect_invalid_does_not_need_git(pipeline) -> None:
    runner = FakeDocker(
        config=image_config("unknown", "unknown"),
        report=no_identity_report(pipeline),
    )

    result = vbi.verify_image(
        "auneron-backend:test",
        None,
        expect_invalid=True,
        repo_dir=Path("/nonexistent"),
        runner=runner,
    )

    assert result.passed


def test_expect_invalid_fails_if_the_image_carries_a_valid_identity(
    pipeline,
) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    result = verify(pipeline, runner, git_rev=None, expect_invalid=True)

    assert not result.passed
    assert "N1" in failed_ids(result)


def test_expect_invalid_fails_if_dirty_defaults_to_false(pipeline) -> None:
    # mutacao: um Dockerfile que fabrica `dirty=false` por default
    config = image_config("unknown", "false")
    report = vbi.build_report(
        {bi.ENV_GIT_SHA: "unknown", bi.ENV_GIT_DIRTY: "false"},
        pipeline.root,
    )
    runner = FakeDocker(config=config, report=report)

    result = verify(pipeline, runner, git_rev=None, expect_invalid=True)

    assert not result.passed
    assert "N3" in failed_ids(result)


def test_expect_invalid_fails_if_the_label_revision_is_fabricated(
    pipeline,
) -> None:
    config = image_config(pipeline.sha, "unknown")
    runner = FakeDocker(config=config, report=no_identity_report(pipeline))

    result = verify(pipeline, runner, git_rev=None, expect_invalid=True)

    assert "N2" in failed_ids(result)


# ---------------------------------------------------------------------
# 6. CLI `verify`: saida e codigos de saida
# ---------------------------------------------------------------------


def run_verify_cli(pipeline, runner, *extra: str):
    out = io.StringIO()
    code = vbi.main(
        [
            "verify",
            "--image",
            "auneron-backend:test",
            "--repo",
            str(backend_of(pipeline.repo)),
            *extra,
        ],
        runner=runner,
        stdout=out,
    )
    return code, out.getvalue()


def test_cli_verify_pass_output(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    code, output = run_verify_cli(
        pipeline, runner, "--git-rev", pipeline.sha, "--require-clean"
    )

    assert code == 0
    lines = output.splitlines()
    assert lines[-1] == "BUILD IDENTITY VERIFY: PASS"
    assert all(line.startswith("[PASS] ") for line in lines[:-1])


def test_cli_verify_fail_output_names_the_failed_check(pipeline) -> None:
    with (pipeline.root / "app" / "b.py").open("ab") as handle:
        handle.write(b"#x\n")
    tampered = vbi.build_report(valid_env(pipeline.sha), pipeline.root)
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=tampered
    )

    code, output = run_verify_cli(
        pipeline, runner, "--git-rev", pipeline.sha
    )

    assert code == 2
    assert "[FAIL] C4" in output
    assert output.splitlines()[-1] == "BUILD IDENTITY VERIFY: FAIL"


def test_cli_verify_unavailable_exit_3(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha),
        report=pipeline.report,
        inspect_returncode=1,
    )

    code, output = run_verify_cli(
        pipeline, runner, "--git-rev", pipeline.sha
    )

    assert code == 3
    assert "BUILD IDENTITY UNAVAILABLE" in output


def test_cli_verify_without_git_rev_is_unavailable(pipeline) -> None:
    runner = FakeDocker(
        config=image_config(pipeline.sha), report=pipeline.report
    )

    code, _ = run_verify_cli(pipeline, runner)

    assert code == 3


def test_cli_verify_expect_invalid(pipeline) -> None:
    runner = FakeDocker(
        config=image_config("unknown", "unknown"),
        report=no_identity_report(pipeline),
    )

    code, output = run_verify_cli(pipeline, runner, "--expect-invalid")

    assert code == 0
    assert "[PASS] N1" in output


# ---------------------------------------------------------------------
# 7. fronteira estatica do script
# ---------------------------------------------------------------------


def test_script_imports_only_the_standard_library() -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])

    assert roots <= set(sys.stdlib_module_names), sorted(
        roots - set(sys.stdlib_module_names)
    )


def test_script_is_read_only_and_uses_only_inspect_and_run() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )

    for forbidden in (
        ".write_text(",
        ".write_bytes(",
        "open(",
        "rmtree",
        "unlink(",
        '"rm"',
        '"rmi"',
        '"build"',
        '"push"',
        '"tag"',
        '"exec"',
        '"commit"',
        '"checkout"',
        '"worktree"',
        "settings",
        "sqlalchemy",
        "psycopg",
    ):
        assert forbidden not in code, forbidden

    assert code.count('"inspect"') == 1
    assert code.count('"run"') == 1
