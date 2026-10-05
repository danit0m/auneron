"""
VALUE-3.4D-2a -- contrato de `BuildIdentity` (app/core/build_identity.py).

* Cadeia CONJUNTIVA: SHA (40 hex minusculos) + dirty (`false`) + digest.
  Nenhum elo resgata outro.
* Identidade invalida e um RESULTADO (`BuildIdentityDiagnosis`), nunca uma
  excecao; `diagnose_build_identity` nunca levanta.
* Nenhum conteudo arbitrario de variaveis invalidas e ecoado.
* Somente as duas variaveis `AUNERON_GIT_*` sao lidas.
* Memoizacao por processo com reset controlado.
"""

from __future__ import annotations

import ast
import dataclasses
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.core import build_identity as bi


BACKEND_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = BACKEND_DIR / "app" / "core" / "build_identity.py"

VALID_SHA = "0123456789abcdef0123456789abcdef01234567"
VALID_ENV = {
    bi.ENV_GIT_SHA: VALID_SHA,
    bi.ENV_GIT_DIRTY: "false",
}


def make_root(base: Path) -> Path:
    (base / "app" / "core").mkdir(parents=True)
    (base / "migrations" / "versions").mkdir(parents=True)
    (base / "app" / "__init__.py").write_bytes(b"")
    (base / "app" / "core" / "a.py").write_bytes(b"print('a')\n")
    (base / "migrations" / "versions" / "m1.py").write_bytes(
        b"revision = 'abc'\n"
    )
    (base / "requirements.txt").write_bytes(b"alembic==1.19.0\n")
    return base


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_root(tmp_path / "backend")


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch):
    bi.reset_process_build_identity()
    monkeypatch.delenv(bi.ENV_GIT_SHA, raising=False)
    monkeypatch.delenv(bi.ENV_GIT_DIRTY, raising=False)
    yield
    bi.reset_process_build_identity()


# ---------------------------------------------------------------------
# 1. contrato congelado
# ---------------------------------------------------------------------


def test_frozen_contract_constants() -> None:
    assert bi.SOURCE_DIGEST_ALGORITHM == "sd1"
    assert bi.ENV_GIT_SHA == "AUNERON_GIT_SHA"
    assert bi.ENV_GIT_DIRTY == "AUNERON_GIT_DIRTY"
    assert bi.FAILURE_ORDER == (
        "identity_sha_invalid",
        "identity_dirty_invalid",
        "identity_dirty",
        "source_digest_error",
    )
    assert bi.SCOPE_DIRS == ("app", "migrations")
    assert bi.SCOPE_FILES == ("requirements.txt",)


# ---------------------------------------------------------------------
# 2. SHA
# ---------------------------------------------------------------------

INVALID_SHAS = [
    pytest.param(None, id="absent"),
    pytest.param("", id="empty"),
    pytest.param("unknown", id="unknown"),
    pytest.param("HEAD", id="symbolic"),
    pytest.param(VALID_SHA.upper(), id="uppercase"),
    pytest.param(VALID_SHA[:39], id="39_chars"),
    pytest.param(VALID_SHA + "0", id="41_chars"),
    pytest.param(VALID_SHA[:7], id="abbreviated"),
    pytest.param(VALID_SHA + "\n", id="trailing_newline"),
    pytest.param(" " + VALID_SHA, id="leading_space"),
    pytest.param("g" * 40, id="non_hex"),
    pytest.param("٠" * 40, id="arabic_indic_digits"),
    pytest.param("0x" + VALID_SHA[:38], id="hex_prefix"),
]


@pytest.mark.parametrize("sha", INVALID_SHAS)
def test_invalid_sha_is_reported_and_never_valid(
    root: Path, sha: str | None
) -> None:
    environ = {bi.ENV_GIT_DIRTY: "false"}
    if sha is not None:
        environ[bi.ENV_GIT_SHA] = sha

    diagnosis = bi.diagnose_build_identity(environ, root)

    assert diagnosis.valid is False
    assert diagnosis.state == "invalid"
    assert diagnosis.code == "identity_sha_invalid"
    assert diagnosis.failures == ("identity_sha_invalid",)
    assert diagnosis.identity is None


def test_valid_sha_and_clean_dirty_is_valid(root: Path) -> None:
    diagnosis = bi.diagnose_build_identity(VALID_ENV, root)

    assert diagnosis.valid is True
    assert diagnosis.state == "valid"
    assert diagnosis.code is None
    assert diagnosis.failures == ()
    assert diagnosis.detail is None
    assert diagnosis.git_sha == VALID_SHA
    assert diagnosis.git_dirty == "false"
    assert diagnosis.identity is not None
    assert diagnosis.identity.git_sha == VALID_SHA
    assert diagnosis.identity.git_dirty is False
    assert diagnosis.identity.source_digest == diagnosis.source_digest
    assert diagnosis.source_digest.algorithm == "sd1"
    assert diagnosis.source_digest.file_count == 4


# ---------------------------------------------------------------------
# 3. dirty
# ---------------------------------------------------------------------

INVALID_DIRTY = [
    pytest.param(None, id="absent"),
    pytest.param("", id="empty"),
    pytest.param("unknown", id="unknown"),
    pytest.param("False", id="capitalized_false"),
    pytest.param("TRUE", id="uppercase_true"),
    pytest.param("True", id="capitalized_true"),
    pytest.param("0", id="zero"),
    pytest.param("1", id="one"),
    pytest.param("no", id="no"),
    pytest.param(" false", id="leading_space"),
    pytest.param("false\n", id="trailing_newline"),
]


@pytest.mark.parametrize("dirty", INVALID_DIRTY)
def test_invalid_dirty_is_reported(root: Path, dirty: str | None) -> None:
    environ = {bi.ENV_GIT_SHA: VALID_SHA}
    if dirty is not None:
        environ[bi.ENV_GIT_DIRTY] = dirty

    diagnosis = bi.diagnose_build_identity(environ, root)

    assert diagnosis.valid is False
    assert diagnosis.code == "identity_dirty_invalid"
    assert diagnosis.failures == ("identity_dirty_invalid",)
    assert diagnosis.identity is None


def test_dirty_true_is_a_valid_claim_but_unfit(root: Path) -> None:
    diagnosis = bi.diagnose_build_identity(
        {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "true"}, root
    )

    assert diagnosis.valid is False
    assert diagnosis.code == "identity_dirty"
    assert diagnosis.failures == ("identity_dirty",)
    assert diagnosis.git_dirty == "true"
    assert diagnosis.identity is None


# ---------------------------------------------------------------------
# 4. conjuncao, ordem e "digest nao resgata"
# ---------------------------------------------------------------------


def test_failure_order_is_deterministic_and_code_is_the_first(
    tmp_path: Path,
) -> None:
    diagnosis = bi.diagnose_build_identity({}, tmp_path / "missing")

    assert diagnosis.failures == (
        "identity_sha_invalid",
        "identity_dirty_invalid",
        "source_digest_error",
    )
    assert diagnosis.code == "identity_sha_invalid"
    assert diagnosis.detail == "root_missing"


def test_sha_invalid_with_dirty_true_reports_both_in_order(
    root: Path,
) -> None:
    diagnosis = bi.diagnose_build_identity(
        {bi.ENV_GIT_SHA: "unknown", bi.ENV_GIT_DIRTY: "true"}, root
    )

    assert diagnosis.failures == ("identity_sha_invalid", "identity_dirty")


@pytest.mark.parametrize(
    "environ",
    [
        {bi.ENV_GIT_SHA: "unknown", bi.ENV_GIT_DIRTY: "false"},
        {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "true"},
        {bi.ENV_GIT_SHA: VALID_SHA, bi.ENV_GIT_DIRTY: "unknown"},
        {},
    ],
    ids=["sha_unknown", "dirty_true", "dirty_unknown", "no_claims"],
)
def test_a_computable_digest_never_rescues_the_claims(
    root: Path, environ: dict[str, str]
) -> None:
    diagnosis = bi.diagnose_build_identity(environ, root)

    assert diagnosis.source_digest is not None  # o digest existe...
    assert diagnosis.valid is False  # ...e mesmo assim nao resgata
    assert diagnosis.identity is None


@pytest.mark.parametrize(
    "mutator,detail",
    [
        (lambda r: (r / "requirements.txt").unlink(), "requirements_missing"),
        (lambda r: (r / "app" / "core" / "a.py").unlink(), None),
    ],
    ids=["requirements_missing", "file_removed_is_still_a_digest"],
)
def test_source_digest_error_blocks_an_otherwise_valid_claim(
    root: Path, mutator, detail
) -> None:
    mutator(root)
    diagnosis = bi.diagnose_build_identity(VALID_ENV, root)

    if detail is None:
        assert diagnosis.valid is True
        return
    assert diagnosis.valid is False
    assert diagnosis.code == "source_digest_error"
    assert diagnosis.detail == detail
    assert diagnosis.source_digest is None
    assert diagnosis.identity is None


def test_missing_root_is_a_digest_error(tmp_path: Path) -> None:
    diagnosis = bi.diagnose_build_identity(
        VALID_ENV, tmp_path / "does-not-exist"
    )

    assert diagnosis.code == "source_digest_error"
    assert diagnosis.detail == "root_missing"


@pytest.mark.parametrize("scope_dir", ["app", "migrations"])
def test_missing_scope_dir_is_a_digest_error(
    root: Path, scope_dir: str
) -> None:
    import shutil

    shutil.rmtree(root / scope_dir)

    diagnosis = bi.diagnose_build_identity(VALID_ENV, root)

    assert diagnosis.code == "source_digest_error"
    assert diagnosis.detail == f"scope_dir_missing:{scope_dir}"


# ---------------------------------------------------------------------
# 5. resultado somente leitura e sem excecao como contrato normal
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,attribute",
    [
        ("diagnosis", "valid"),
        ("diagnosis", "code"),
        ("identity", "git_sha"),
        ("identity", "git_dirty"),
        ("source_digest", "digest"),
        ("source_digest", "algorithm"),
    ],
)
def test_results_are_frozen(
    root: Path, target: str, attribute: str
) -> None:
    diagnosis = bi.diagnose_build_identity(VALID_ENV, root)
    subject = {
        "diagnosis": diagnosis,
        "identity": diagnosis.identity,
        "source_digest": diagnosis.source_digest,
    }[target]

    assert dataclasses.is_dataclass(subject)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(subject, attribute, "x")


def test_diagnose_never_raises_even_if_the_digest_layer_explodes(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    def explode(_root):
        raise RuntimeError("boom LEAK_SENTINEL")

    monkeypatch.setattr(bi, "compute_source_digest", explode)

    diagnosis = bi.diagnose_build_identity(VALID_ENV, root)

    assert diagnosis.valid is False
    assert diagnosis.code == "source_digest_error"
    assert diagnosis.detail == "unexpected_error"
    assert "LEAK_SENTINEL" not in repr(diagnosis)


def test_invalid_identity_is_not_signalled_by_exception(
    root: Path,
) -> None:
    # nenhuma das entradas invalidas pode levantar
    for sha in (None, "", "unknown", "\x00" * 3, VALID_SHA.upper()):
        for dirty in (None, "", "maybe", "true", "false"):
            environ = {}
            if sha is not None:
                environ[bi.ENV_GIT_SHA] = sha
            if dirty is not None:
                environ[bi.ENV_GIT_DIRTY] = dirty
            bi.diagnose_build_identity(environ, root)


# ---------------------------------------------------------------------
# 6. representacao diagnostica: a seguranca depende da VALIDADE da
#    alegacao, nunca de um filtro generico de caracteres
# ---------------------------------------------------------------------

INVALID_MARKER = "<invalid>"

# Valores INVALIDOS compostos apenas por caracteres "inofensivos": um filtro
# por charset os ecoaria; a validade semantica nao.
CHARSET_SAFE_INVALID_VALUES = [
    pytest.param("z" * 40, id="40_z"),
    pytest.param("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", id="token_ghp"),
    pytest.param("my_api_token_123456789", id="token_underscore"),
    pytest.param("tok-en.with-dash.and.dot_1234567890", id="token_dash_dot"),
    pytest.param("unknown", id="unknown"),
    pytest.param("A" * 64, id="64_allowed_chars"),
    pytest.param("a1_" * 21 + "a", id="64_mixed_allowed_chars"),
    pytest.param("sk-live-abcdef0123456789", id="sk_live_like"),
    pytest.param(VALID_SHA.upper(), id="uppercase_sha"),
    pytest.param(VALID_SHA[:39], id="39_hex"),
    pytest.param(VALID_SHA + "0", id="41_hex"),
]


@pytest.mark.parametrize("value", CHARSET_SAFE_INVALID_VALUES)
def test_an_invalid_sha_made_of_allowed_characters_is_never_returned(
    root: Path, value: str
) -> None:
    diagnosis = bi.diagnose_build_identity(
        {bi.ENV_GIT_SHA: value, bi.ENV_GIT_DIRTY: "false"}, root
    )

    assert diagnosis.valid is False
    assert diagnosis.git_sha == INVALID_MARKER
    assert value not in repr(diagnosis)


@pytest.mark.parametrize("value", CHARSET_SAFE_INVALID_VALUES)
def test_an_invalid_dirty_made_of_allowed_characters_is_never_returned(
    root: Path, value: str
) -> None:
    diagnosis = bi.diagnose_build_identity(
        {bi.ENV_GIT_SHA: "f" * 40, bi.ENV_GIT_DIRTY: value}, root
    )

    assert diagnosis.valid is False
    assert diagnosis.git_dirty == INVALID_MARKER
    assert value not in repr(diagnosis)


@pytest.mark.parametrize(
    "value",
    [
        "sk-live: abc!",
        "token=abc",
        "with space",
        "line\nbreak",
        VALID_SHA + "\n",
        "é",
        "password:hunter2",
        "",
        " ",
        "\x00",
    ],
)
def test_other_invalid_values_get_the_same_constant_marker(
    root: Path, value: str
) -> None:
    diagnosis = bi.diagnose_build_identity(
        {bi.ENV_GIT_SHA: value, bi.ENV_GIT_DIRTY: value}, root
    )

    assert diagnosis.git_sha == INVALID_MARKER
    assert diagnosis.git_dirty == INVALID_MARKER


def test_only_valid_claims_are_returned_verbatim(root: Path) -> None:
    for sha in (VALID_SHA, "f" * 40, "0" * 40):
        for dirty in ("true", "false"):
            diagnosis = bi.diagnose_build_identity(
                {bi.ENV_GIT_SHA: sha, bi.ENV_GIT_DIRTY: dirty}, root
            )
            assert diagnosis.git_sha == sha
            assert diagnosis.git_dirty == dirty


def test_absent_claims_are_none_not_a_marker(root: Path) -> None:
    diagnosis = bi.diagnose_build_identity({}, root)

    assert diagnosis.git_sha is None
    assert diagnosis.git_dirty is None


def test_claim_view_contract() -> None:
    assert bi.INVALID_CLAIM_MARKER == INVALID_MARKER
    assert bi.claim_view(None, False) is None
    assert bi.claim_view(None, True) is None
    assert bi.claim_view(VALID_SHA, True) == VALID_SHA
    assert bi.claim_view("anything", False) == INVALID_MARKER
    assert bi.claim_view("", False) == INVALID_MARKER


def test_there_is_no_generic_charset_allowlist_anymore() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert not hasattr(bi, "safe_echo")
    assert "_SAFE_ECHO_PATTERN" not in source
    assert "[0-9A-Za-z._-]" not in source


def test_property_no_invalid_raw_claim_ever_appears_in_the_diagnosis(
    root: Path,
) -> None:
    """Propriedade forte, com fuzz deterministico sobre o alfabeto
    "inofensivo" (o pior caso para um filtro por charset)."""
    import random

    alphabet = (
        "0123456789abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ._-"
    )
    generator = random.Random(34_02)
    checked = 0
    for length in (8, 12, 20, 39, 40, 41, 50, 64):
        for _ in range(25):
            value = "".join(
                generator.choice(alphabet) for _ in range(length)
            )
            sha_is_valid = (
                len(value) == 40
                and all(ch in "0123456789abcdef" for ch in value)
            )
            if sha_is_valid:
                continue
            diagnosis = bi.diagnose_build_identity(
                {bi.ENV_GIT_SHA: value, bi.ENV_GIT_DIRTY: value}, root
            )
            assert diagnosis.git_sha == INVALID_MARKER
            assert diagnosis.git_dirty == INVALID_MARKER
            assert value not in repr(diagnosis)
            checked += 1

    assert checked > 150


def test_diagnosis_never_echoes_arbitrary_environment_content(
    root: Path,
) -> None:
    diagnosis = bi.diagnose_build_identity(
        {
            bi.ENV_GIT_SHA: "LEAK_SENTINEL: secret/token=abc",
            bi.ENV_GIT_DIRTY: "LEAK sentinel 2",
        },
        root,
    )

    assert diagnosis.git_sha == INVALID_MARKER
    assert diagnosis.git_dirty == INVALID_MARKER
    assert "LEAK" not in repr(diagnosis)
    assert "secret" not in repr(diagnosis)


# ---------------------------------------------------------------------
# 7. somente as duas variaveis sao lidas
# ---------------------------------------------------------------------


class _RecordingEnviron(Mapping):
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data
        self.accessed: list[str] = []

    def get(self, key, default=None):
        self.accessed.append(key)
        return self._data.get(key, default)

    def __getitem__(self, key):
        self.accessed.append(key)
        return self._data[key]

    def __iter__(self):
        raise AssertionError("o ambiente nao pode ser enumerado")

    def __len__(self):
        raise AssertionError("o ambiente nao pode ser medido")


def test_only_the_two_identity_variables_are_read(root: Path) -> None:
    environ = _RecordingEnviron(
        {**VALID_ENV, "DATABASE_URL": "postgresql://user:pw@host/db"}
    )

    bi.diagnose_build_identity(environ, root)

    assert sorted(set(environ.accessed)) == sorted(
        [bi.ENV_GIT_SHA, bi.ENV_GIT_DIRTY]
    )


def test_explicit_environ_is_used_instead_of_the_process_environment(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv(bi.ENV_GIT_SHA, "unknown")

    assert bi.diagnose_build_identity(VALID_ENV, root).valid is True


def test_process_environment_is_the_default(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv(bi.ENV_GIT_SHA, VALID_SHA)
    monkeypatch.setenv(bi.ENV_GIT_DIRTY, "false")

    assert bi.diagnose_build_identity(root=root).valid is True


# ---------------------------------------------------------------------
# 8. raiz padrao e arvore real
# ---------------------------------------------------------------------


def test_default_source_root_is_the_backend_directory() -> None:
    assert bi.default_source_root() == BACKEND_DIR
    assert (bi.default_source_root() / "app").is_dir()


def test_real_tree_digest_is_computable_and_matches_the_scope() -> None:
    diagnosis = bi.diagnose_build_identity(VALID_ENV)

    assert diagnosis.valid is True
    assert diagnosis.source_digest.file_count >= 200
    assert len(diagnosis.source_digest.digest) == 64


# ---------------------------------------------------------------------
# 9. memoizacao por processo com reset controlado
# ---------------------------------------------------------------------


def test_process_diagnosis_is_memoized_and_resettable(
    monkeypatch: pytest.MonkeyPatch, isolated_cache
) -> None:
    first = bi.process_build_identity_diagnosis()
    assert bi.process_build_identity_diagnosis() is first

    # mudar o ambiente NAO afeta o resultado memoizado...
    monkeypatch.setenv(bi.ENV_GIT_SHA, VALID_SHA)
    monkeypatch.setenv(bi.ENV_GIT_DIRTY, "false")
    assert bi.process_build_identity_diagnosis() is first
    assert first.valid is False

    # ...ate o reset controlado
    bi.reset_process_build_identity()
    refreshed = bi.process_build_identity_diagnosis()
    assert refreshed is not first
    assert refreshed.valid is True


def test_diagnose_and_compute_are_pure_and_uncached(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    first = bi.compute_source_digest(root)
    (root / "app" / "core" / "a.py").write_bytes(b"print('changed')\n")

    assert bi.compute_source_digest(root) != first
    assert (
        bi.diagnose_build_identity(VALID_ENV, root).source_digest
        != first
    )


# ---------------------------------------------------------------------
# 10. fronteira estatica do modulo
# ---------------------------------------------------------------------


def test_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])

    assert roots <= set(sys.stdlib_module_names), sorted(
        roots - set(sys.stdlib_module_names)
    )


def test_module_never_shells_out_nor_touches_git_network_or_db() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )

    for forbidden in (
        "subprocess",
        "socket",
        "urllib",
        "sqlalchemy",
        "psycopg",
        "app.core.config",
        "settings",
        "os.system",
        ".write_text(",
        ".write_bytes(",
        "open(",
    ):
        assert forbidden not in code, forbidden


def test_module_reads_no_environment_other_than_the_two_variables() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert source.count("os.environ") == 1  # apenas o default de `environ`
    assert "getenv" not in source
