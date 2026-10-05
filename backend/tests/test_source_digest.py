"""
VALUE-3.4D-2a -- algoritmo `sd1` (source_digest): especificacao congelada,
reprodutibilidade e sensibilidade.

Escopo: `app/**/*.py`, `migrations/**/*.py`, `requirements.txt`.
Canonicalizacao: caminhos POSIX ASCII ordenados por BYTES, `\\r\\n` -> `\\n`
(CR isolado NAO normalizado), symlink/escopo ausente/conjunto vazio sao
erro; manifesto `"<sha256>  <caminho>\\n"`; digest = sha256(manifesto).
"""

from __future__ import annotations

import hashlib
import os
import random
import shutil
from pathlib import Path

import pytest

from app.core import build_identity as bi


# Vetor de ouro: arvore sintetica fixa (o valor foi calculado uma vez e
# esta pinado; o teste ainda o recompoe de forma independente com hashlib).
GOLDEN_ENTRIES = [
    ("app/__init__.py", b""),
    ("app/core/a.py", b"print('a')\n"),
    ("app/b.py", b"x = 1\r\ny = 2\r\n"),
    ("migrations/versions/m1.py", b"revision = 'abc'\n"),
    ("requirements.txt", b"alembic==1.19.0\n"),
]
GOLDEN_DIGEST = (
    "f838251fcbcb31ab77ab317d244db07265831abad06fc17bf93e1f63feb38340"
)


def write_tree(
    base: Path, entries: list[tuple[str, bytes]]
) -> Path:
    for relative_path, data in entries:
        target = base / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return base


def independent_digest(entries: list[tuple[str, bytes]]) -> str:
    """Reimplementacao independente da especificacao (nao usa o modulo)."""
    rows = sorted(
        (
            path.encode("ascii"),
            path,
            hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
        )
        for path, data in entries
    )
    manifest = "".join(f"{digest}  {path}\n" for _, path, digest in rows)
    return hashlib.sha256(manifest.encode("ascii")).hexdigest()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return write_tree(tmp_path / "backend", GOLDEN_ENTRIES)


# ---------------------------------------------------------------------
# 1. vetor de ouro e formato do manifesto
# ---------------------------------------------------------------------


def test_golden_vector_is_pinned_and_independently_recomputed() -> None:
    assert independent_digest(GOLDEN_ENTRIES) == GOLDEN_DIGEST

    digest = bi.manifest_digest(GOLDEN_ENTRIES)

    assert digest.algorithm == "sd1"
    assert digest.digest == GOLDEN_DIGEST
    assert digest.file_count == 5


def test_directory_computation_matches_the_golden_vector(
    tree: Path,
) -> None:
    digest = bi.compute_source_digest(tree)

    assert digest.digest == GOLDEN_DIGEST
    assert digest.file_count == 5


def test_manifest_is_sha256sum_compatible() -> None:
    # cada linha `<hash>  <caminho>` e verificavel por ferramenta padrao
    rows = sorted(
        (path.encode(), path, hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest())
        for path, data in GOLDEN_ENTRIES
    )
    manifest = "".join(f"{h}  {p}\n" for _, p, h in rows)

    assert all(
        len(line.split("  ")[0]) == 64 for line in manifest.splitlines()
    )
    assert hashlib.sha256(manifest.encode()).hexdigest() == GOLDEN_DIGEST


# ---------------------------------------------------------------------
# 2. CRLF / reprodutibilidade
# ---------------------------------------------------------------------


def test_crlf_in_every_python_file_does_not_change_the_digest(
    tmp_path: Path,
) -> None:
    crlf_entries = [
        (path, data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        for path, data in GOLDEN_ENTRIES
    ]

    assert (
        bi.compute_source_digest(
            write_tree(tmp_path / "crlf", crlf_entries)
        ).digest
        == GOLDEN_DIGEST
    )


def test_a_lone_cr_is_not_normalized(tmp_path: Path) -> None:
    changed = [
        (path, data.replace(b"\r\n", b"\n").replace(b"\n", b"\r"))
        if path == "app/b.py"
        else (path, data)
        for path, data in GOLDEN_ENTRIES
    ]

    assert (
        bi.compute_source_digest(
            write_tree(tmp_path / "lonecr", changed)
        ).digest
        != GOLDEN_DIGEST
    )


def test_insertion_order_does_not_matter() -> None:
    shuffled = list(GOLDEN_ENTRIES)
    for seed in range(5):
        random.Random(seed).shuffle(shuffled)
        assert bi.manifest_digest(shuffled).digest == GOLDEN_DIGEST


def test_paths_are_sorted_by_bytes_not_case_insensitively() -> None:
    entries = [
        ("app/B.py", b"b\n"),
        ("app/a.py", b"a\n"),
        ("requirements.txt", b"x\n"),
    ]

    assert bi.manifest_digest(entries).digest == independent_digest(
        entries
    )
    # a ordenacao por `casefold` produziria outro manifesto
    casefolded = sorted(entries, key=lambda item: item[0].casefold())
    rows = "".join(
        f"{hashlib.sha256(d).hexdigest()}  {p}\n" for p, d in casefolded
    )
    assert hashlib.sha256(rows.encode()).hexdigest() != (
        bi.manifest_digest(entries).digest
    )


def test_digest_is_stable_across_repeated_runs(tree: Path) -> None:
    assert {bi.compute_source_digest(tree).digest for _ in range(3)} == {
        GOLDEN_DIGEST
    }


# ---------------------------------------------------------------------
# 3. sensibilidade
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    ["one_byte", "rename", "extra_py", "remove", "requirements"],
)
def test_digest_changes_when_the_scoped_content_changes(
    tree: Path, mutation: str
) -> None:
    if mutation == "one_byte":
        with (tree / "app" / "core" / "a.py").open("ab") as handle:
            handle.write(b"#")
    elif mutation == "rename":
        (tree / "app" / "core" / "a.py").rename(
            tree / "app" / "core" / "z.py"
        )
    elif mutation == "extra_py":
        (tree / "app" / "extra.py").write_bytes(b"x = 1\n")
    elif mutation == "remove":
        (tree / "migrations" / "versions" / "m1.py").unlink()
    else:
        (tree / "requirements.txt").write_bytes(b"alembic==9.9.9\n")

    assert bi.compute_source_digest(tree).digest != GOLDEN_DIGEST


# ---------------------------------------------------------------------
# 4. escopo e exclusoes
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path,expected",
    [
        ("app/main.py", True),
        ("app/core/deep/x.py", True),
        ("migrations/versions/m.py", True),
        ("migrations/env.py", True),
        ("requirements.txt", True),
        ("app/__pycache__/main.py", False),
        ("app/core/__pycache__/x.py", False),
        ("app/data.txt", False),
        ("app/mod.pyc", False),
        ("app/X.PY", False),
        ("migrations/script.py.mako", False),
        ("migrations/README", False),
        ("tests/test_x.py", False),
        ("scripts/tool.py", False),
        ("docs/guide.py", False),
        ("alembic.ini", False),
        ("requirements-dev.txt", False),
        ("sub/requirements.txt", False),
        ("main.py", False),
        ("app", False),
    ],
)
def test_scope_table(relative_path: str, expected: bool) -> None:
    assert bi.is_in_scope(relative_path) is expected


def test_out_of_scope_files_do_not_change_the_digest(tree: Path) -> None:
    for relative_path, data in [
        ("app/data.txt", b"x"),
        ("app/__pycache__/m.py", b"cached"),
        ("app/__pycache__/m.cpython-311.pyc", b"\x00\x01"),
        ("app/core/__pycache__/n.py", b"cached"),
        ("migrations/script.py.mako", b"template"),
        ("migrations/README", b"readme"),
        ("tests/test_x.py", b"def test(): ..."),
        ("scripts/tool.py", b"print(1)"),
        ("docs/guide.md", b"# doc"),
        ("alembic.ini", b"[alembic]"),
        ("requirements-dev.txt", b"pytest"),
        ("app/X.PY", b"upper"),
    ]:
        target = tree / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    assert bi.compute_source_digest(tree).digest == GOLDEN_DIGEST


# ---------------------------------------------------------------------
# 5. erros explicitos
# ---------------------------------------------------------------------


def test_empty_set_is_an_error() -> None:
    with pytest.raises(bi.SourceDigestError) as error:
        bi.manifest_digest([])

    assert error.value.detail == "empty_set"


def test_non_ascii_path_is_rejected_by_the_manifest() -> None:
    with pytest.raises(bi.SourceDigestError) as error:
        bi.manifest_digest([("app/não.py", b"x")])

    assert error.value.detail == "non_ascii_path"


def test_non_ascii_path_is_rejected_on_disk(tree: Path) -> None:
    try:
        (tree / "app" / "não.py").write_bytes(b"x")
    except OSError:
        pytest.skip("sistema de arquivos sem suporte a nome nao-ASCII")

    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(tree)

    assert error.value.detail == "non_ascii_path"


def test_missing_root_and_scope_are_errors(tmp_path: Path) -> None:
    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(tmp_path / "nope")
    assert error.value.detail == "root_missing"

    root = write_tree(tmp_path / "backend", GOLDEN_ENTRIES)
    (root / "requirements.txt").unlink()
    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(root)
    assert error.value.detail == "requirements_missing"

    shutil.rmtree(root / "app")
    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(root)
    assert error.value.detail == "scope_dir_missing:app"


def _symlink_or_skip(link: Path, target: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip(
            "symlink indisponivel neste host (coberto no CI Linux)"
        )


def test_symlinked_file_in_scope_is_rejected(tree: Path) -> None:
    _symlink_or_skip(
        tree / "app" / "link.py",
        tree / "app" / "core" / "a.py",
        directory=False,
    )

    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(tree)

    assert error.value.detail == "symlink"


def test_symlinked_directory_in_scope_is_rejected(tree: Path) -> None:
    _symlink_or_skip(
        tree / "app" / "linkdir", tree / "app" / "core", directory=True
    )

    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(tree)

    assert error.value.detail == "symlink"


def test_symlinked_requirements_is_rejected(tree: Path) -> None:
    real = tree / "real_requirements.txt"
    real.write_bytes(b"alembic==1.19.0\n")
    (tree / "requirements.txt").unlink()
    _symlink_or_skip(tree / "requirements.txt", real, directory=False)

    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(tree)

    assert error.value.detail == "symlink"


def test_symlinked_scope_root_is_rejected(tmp_path: Path) -> None:
    root = write_tree(tmp_path / "backend", GOLDEN_ENTRIES)
    shutil.move(str(root / "migrations"), str(tmp_path / "elsewhere"))
    _symlink_or_skip(
        root / "migrations", tmp_path / "elsewhere", directory=True
    )

    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(root)

    assert error.value.detail == "symlink"


# Simulacao independente de plataforma: o host Windows nao cria symlinks
# sem privilegio, entao o mesmo contrato e exercido forcando `islink`.


@pytest.mark.parametrize(
    "linked_name,description",
    [
        ("a.py", "arquivo .py"),
        ("core", "diretorio dentro do escopo"),
        ("requirements.txt", "requirements.txt"),
        ("migrations", "raiz de escopo"),
        ("app", "raiz de escopo app"),
    ],
)
def test_symlink_detection_is_platform_independent(
    monkeypatch: pytest.MonkeyPatch,
    tree: Path,
    linked_name: str,
    description: str,
) -> None:
    real_islink = os.path.islink

    def fake_islink(path) -> bool:
        return Path(path).name == linked_name or real_islink(path)

    monkeypatch.setattr(bi.os.path, "islink", fake_islink)

    with pytest.raises(bi.SourceDigestError) as error:
        bi.compute_source_digest(tree)

    assert error.value.detail == "symlink", description


def test_without_symlinks_the_simulation_hook_is_inert(
    monkeypatch: pytest.MonkeyPatch, tree: Path
) -> None:
    monkeypatch.setattr(bi.os.path, "islink", lambda path: False)

    assert bi.compute_source_digest(tree).digest == GOLDEN_DIGEST


# ---------------------------------------------------------------------
# 6. a arvore real
# ---------------------------------------------------------------------


def test_real_backend_tree_digest_is_deterministic() -> None:
    first = bi.compute_source_digest(bi.default_source_root())
    second = bi.compute_source_digest(bi.default_source_root())

    assert first == second
    assert first.algorithm == "sd1"
    assert first.file_count >= 200
