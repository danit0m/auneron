"""
VALUE-3.4D-2b -- identidade de schema: revisao ESPERADA (codigo) x REAL
(banco), sempre separadas.

* esperada: head do `migrations/` (somente leitura de arquivos; exatamente
  1 head; nenhum banco, nenhuma escrita);
* real: `SELECT version_num FROM alembic_version` (leitura simples; exatamente
  1 linha);
* `unknown` nunca e revisao valida; nenhuma migration e executada.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core import schema_identity as si
from app.database.database import SessionLocal


BACKEND_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = BACKEND_DIR / "app" / "core" / "schema_identity.py"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


@pytest.fixture(autouse=True)
def _reset_cache():
    si.reset_process_expected_schema_revision()
    yield
    si.reset_process_expected_schema_revision()


def write_migration(
    directory: Path, revision: str, down_revision: str | None
) -> None:
    versions = directory / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    down = "None" if down_revision is None else repr(down_revision)
    (versions / f"{revision}_m.py").write_text(
        f'"""m"""\nrevision = {revision!r}\ndown_revision = {down}\n'
        "branch_labels = None\ndepends_on = None\n\n"
        "def upgrade() -> None:\n    pass\n\n"
        "def downgrade() -> None:\n    pass\n",
        encoding="utf-8",
    )


def independent_head() -> str:
    """Head calculado SEM alembic: revisoes que ninguem referencia."""
    revisions: dict[str, tuple[str, ...]] = {}
    for path in (MIGRATIONS_DIR / "versions").glob("*.py"):
        values: dict[str, object] = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            target = None
            if isinstance(node, ast.Assign) and isinstance(
                node.targets[0], ast.Name
            ):
                target = node.targets[0].id
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                target = node.target.id
            if target in ("revision", "down_revision"):
                values[target] = ast.literal_eval(node.value)
        assert "revision" in values, path.name
        down = values.get("down_revision")
        revisions[str(values["revision"])] = (
            ()
            if down is None
            else (down,)
            if isinstance(down, str)
            else tuple(down)
        )
    referenced = {d for downs in revisions.values() for d in downs}
    heads = [r for r in revisions if r not in referenced]
    assert len(heads) == 1, heads
    return heads[0]


# ---------------------------------------------------------------------
# 1. revisao esperada
# ---------------------------------------------------------------------


def test_expected_revision_is_the_single_head_of_the_real_migrations() -> None:
    expected = si.expected_schema_revision()

    assert expected == independent_head()
    assert si.is_valid_revision(expected)


def test_expected_revision_never_creates_files_or_touches_a_database() -> None:
    before = sorted(
        path.relative_to(MIGRATIONS_DIR).as_posix()
        for path in MIGRATIONS_DIR.rglob("*")
        if "__pycache__" not in path.parts
    )

    si.expected_schema_revision()

    after = sorted(
        path.relative_to(MIGRATIONS_DIR).as_posix()
        for path in MIGRATIONS_DIR.rglob("*")
        if "__pycache__" not in path.parts
    )
    assert after == before


def test_expected_revision_with_a_linear_chain(tmp_path: Path) -> None:
    write_migration(tmp_path, "aaaaaaaaaaaa", None)
    write_migration(tmp_path, "bbbbbbbbbbbb", "aaaaaaaaaaaa")

    assert si.expected_schema_revision(tmp_path) == "bbbbbbbbbbbb"


def test_two_heads_are_unavailable(tmp_path: Path) -> None:
    write_migration(tmp_path, "aaaaaaaaaaaa", None)
    write_migration(tmp_path, "bbbbbbbbbbbb", "aaaaaaaaaaaa")
    write_migration(tmp_path, "cccccccccccc", "aaaaaaaaaaaa")

    with pytest.raises(si.SchemaIdentityError) as error:
        si.expected_schema_revision(tmp_path)

    assert error.value.code == "expected_revision_unavailable"
    assert error.value.detail == "heads=2"


def test_zero_heads_are_unavailable(tmp_path: Path) -> None:
    (tmp_path / "versions").mkdir()

    with pytest.raises(si.SchemaIdentityError) as error:
        si.expected_schema_revision(tmp_path)

    assert error.value.code == "expected_revision_unavailable"
    assert error.value.detail == "heads=0"


def test_missing_directory_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(si.SchemaIdentityError) as error:
        si.expected_schema_revision(tmp_path / "nope")

    assert error.value.code == "expected_revision_unavailable"


def test_unknown_head_is_not_a_valid_revision(tmp_path: Path) -> None:
    write_migration(tmp_path, "unknown", None)

    with pytest.raises(si.SchemaIdentityError) as error:
        si.expected_schema_revision(tmp_path)

    assert error.value.code == "expected_revision_unavailable"
    assert error.value.detail == "head_not_a_valid_revision"


def test_expected_revision_is_memoized_and_resettable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = si.expected_schema_revision

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "expected_schema_revision", counting)

    first = si.process_expected_schema_revision()
    assert si.process_expected_schema_revision() == first
    assert len(calls) == 1

    si.reset_process_expected_schema_revision()
    si.process_expected_schema_revision()
    assert len(calls) == 2


# ---------------------------------------------------------------------
# 2. validade de revisao
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("7432a1c2dd66", True),
        ("a" * 64, True),
        ("rev_1", True),
        ("A1", True),
        ("unknown", False),
        ("UNKNOWN", False),
        ("Unknown", False),
        ("", False),
        (" ", False),
        ("a" * 65, False),
        ("has space", False),
        ("with-dash", False),
        ("tab\t", False),
        ("new\nline", False),
        ("é", False),
        (None, False),
        (123, False),
        (b"abc", False),
    ],
)
def test_is_valid_revision(value: object, expected: bool) -> None:
    assert si.is_valid_revision(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("7432a1c2dd66", "7432a1c2dd66"),
        ("unknown", "<invalid>"),
        ("sk-live: abc", "<invalid>"),
        ("password:hunter2", "<invalid>"),
        ("a" * 65, "<invalid>"),
        ("", "<invalid>"),
    ],
)
def test_revision_view_never_echoes_an_invalid_value(
    value: object, expected: str | None
) -> None:
    assert si.revision_view(value) == expected


# ---------------------------------------------------------------------
# 3. revisao real (banco) -- sessoes simuladas
# ---------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeSession:
    def __init__(self, rows=None, raises: Exception | None = None) -> None:
        self.rows = rows
        self.raises = raises
        self.statements: list[str] = []
        self.closed = False
        self.committed = False

    def execute(self, statement):
        self.statements.append(str(statement))
        if self.raises is not None:
            raise self.raises
        return FakeResult(self.rows)

    def commit(self) -> None:  # pragma: no cover - nunca deve ser chamado
        self.committed = True

    def close(self) -> None:
        self.closed = True


def read_with(session: FakeSession) -> str:
    return si.read_actual_database_revision(lambda: session)


def test_actual_revision_reads_exactly_one_valid_row() -> None:
    session = FakeSession(rows=[("7432a1c2dd66",)])

    assert read_with(session) == "7432a1c2dd66"
    assert session.closed is True
    assert session.committed is False


def test_actual_revision_is_a_single_plain_select() -> None:
    session = FakeSession(rows=[("7432a1c2dd66",)])

    read_with(session)

    assert len(session.statements) == 1
    assert re.fullmatch(
        r"SELECT version_num FROM alembic_version", session.statements[0]
    )


@pytest.mark.parametrize(
    "rows,detail",
    [
        ([], "rows=0"),
        ([("a1",), ("b2",)], "rows=2"),
        ([("unknown",)], "value_not_a_valid_revision"),
        ([(None,)], "value_not_a_valid_revision"),
        ([("with space",)], "value_not_a_valid_revision"),
    ],
)
def test_actual_revision_requires_exactly_one_valid_row(
    rows, detail: str
) -> None:
    session = FakeSession(rows=rows)

    with pytest.raises(si.SchemaIdentityError) as error:
        read_with(session)

    assert error.value.code == "actual_revision_unavailable"
    assert error.value.detail == detail
    assert session.closed is True


def test_actual_revision_query_failure_is_unavailable_and_closes() -> None:
    session = FakeSession(raises=RuntimeError("relation does not exist"))

    with pytest.raises(si.SchemaIdentityError) as error:
        read_with(session)

    assert error.value.code == "actual_revision_unavailable"
    assert error.value.detail == "query_failed"
    assert "relation" not in str(error.value)
    assert session.closed is True


def test_actual_revision_session_failure_is_unavailable() -> None:
    def broken_factory():
        raise RuntimeError("no connection LEAK_SENTINEL")

    with pytest.raises(si.SchemaIdentityError) as error:
        si.read_actual_database_revision(broken_factory)

    assert error.value.code == "actual_revision_unavailable"
    assert error.value.detail == "session_unavailable"
    assert "LEAK_SENTINEL" not in str(error.value)


def test_actual_revision_is_never_memoized() -> None:
    first = FakeSession(rows=[("aaaaaaaaaaaa",)])
    second = FakeSession(rows=[("bbbbbbbbbbbb",)])
    sessions = iter([first, second])

    assert (
        si.read_actual_database_revision(lambda: next(sessions))
        == "aaaaaaaaaaaa"
    )
    assert (
        si.read_actual_database_revision(lambda: next(sessions))
        == "bbbbbbbbbbbb"
    )


def test_actual_revision_reads_the_real_database() -> None:
    revision = si.read_actual_database_revision(SessionLocal)

    assert si.is_valid_revision(revision)


# ---------------------------------------------------------------------
# 4. fronteira estatica
# ---------------------------------------------------------------------


def test_module_never_writes_or_migrates() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    attribute_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }

    assert not attribute_calls & {
        "add",
        "add_all",
        "commit",
        "delete",
        "merge",
        "flush",
        "begin",
        "upgrade",
        "downgrade",
        "stamp",
    }
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "ALTER",
        "CREATE",
        "DROP",
        "subprocess",
        "os.environ",
    ):
        assert forbidden not in code, forbidden


def test_alembic_is_imported_lazily_only_inside_the_function() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    top_level_imports = {
        (node.module or "").split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert "alembic" not in top_level_imports


def test_stable_codes() -> None:
    assert si.CODE_EXPECTED_UNAVAILABLE == "expected_revision_unavailable"
    assert si.CODE_ACTUAL_UNAVAILABLE == "actual_revision_unavailable"
    assert si.CODE_MISMATCH == "schema_revision_mismatch"
