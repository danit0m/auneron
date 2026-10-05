"""
VALUE-3.4D-2a -- Build Identity Foundation.

Responde a uma unica pergunta: este processo/imagem consegue identificar,
de forma deterministica e verificavel, a fonte LIMPA a partir da qual foi
construido?

Cadeia (conjuntiva -- nenhum elo resgata outro):

* `AUNERON_GIT_SHA`   : alegacao do processo de build, exatamente 40 hex
                        minusculos;
* `AUNERON_GIT_DIRTY` : alegacao do processo de build, exatamente `false`
                        ou `true` (`true` e uma alegacao VALIDA mas
                        inapta);
* `source_digest`     : medido pelo proprio processo sobre os arquivos
                        que executa (algoritmo `sd1`), sem depender de
                        `.git`.

O runtime so consegue validar o FORMATO da alegacao; a PROVA de que a
alegacao e verdadeira e externa (CI/preflight recompoem o digest a partir
dos objetos do commit -- `scripts/verify_build_identity.py`).

Este modulo e somente stdlib e NAO tem efeito colateral: nao le nenhuma
variavel de ambiente alem das duas acima, nao escreve nada e nunca devolve
o valor cru de uma alegacao invalida (so o proprio valor quando semanticamente
valido; caso contrario um marcador constante). Identidade invalida e um
RESULTADO (`BuildIdentityDiagnosis`), nao uma excecao. Nada aqui bloqueia
startup nem altera o comportamento de qualquer worker (isso e D-2b).

Algoritmo `sd1` (versionado; mudar escopo, normalizacao ou formato exige
um novo id, nunca o reuso de `sd1`):

* escopo, relativo a raiz do backend (`/app` na imagem):
  `app/**/*.py`, `migrations/**/*.py`, `requirements.txt`;
* excluidos: `__pycache__`, bytecode, `tests/`, `scripts/`, `docs/` e
  qualquer outro arquivo fora do escopo;
* caminhos POSIX ASCII (nao-ASCII e erro), ordenados por bytes;
* `\\r\\n` -> `\\n` (CR isolado NAO e normalizado);
* symlink (arquivo ou diretorio) no escopo, escopo ausente ou conjunto
  vazio e erro;
* manifesto no formato `sha256sum` (`"<hex>  <caminho>\\n"`);
  `source_digest = sha256(manifesto)`.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from typing import Mapping


SOURCE_DIGEST_ALGORITHM = "sd1"

ENV_GIT_SHA = "AUNERON_GIT_SHA"
ENV_GIT_DIRTY = "AUNERON_GIT_DIRTY"

SCOPE_DIRS = ("app", "migrations")
SCOPE_FILES = ("requirements.txt",)

CODE_SHA_INVALID = "identity_sha_invalid"
CODE_DIRTY_INVALID = "identity_dirty_invalid"
CODE_DIRTY = "identity_dirty"
CODE_SOURCE_DIGEST_ERROR = "source_digest_error"

# Ordem deterministica de avaliacao; `code` e a primeira falha.
FAILURE_ORDER = (
    CODE_SHA_INVALID,
    CODE_DIRTY_INVALID,
    CODE_DIRTY,
    CODE_SOURCE_DIGEST_ERROR,
)

_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")

# Marcador CONSTANTE devolvido no lugar de qualquer alegacao presente mas
# semanticamente invalida: o valor cru nunca aparece no diagnostico.
INVALID_CLAIM_MARKER = "<invalid>"


class SourceDigestError(Exception):
    """Falha do calculo do `source_digest` (escopo do `sd1`).

    Uso interno da camada de digest; a resolucao de identidade a converte
    em `CODE_SOURCE_DIGEST_ERROR` (resultado, nao excecao).
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class SourceDigest:
    algorithm: str
    digest: str
    file_count: int


@dataclass(frozen=True)
class BuildIdentity:
    """Identidade valida e limpa. So existe quando a cadeia fecha."""

    git_sha: str
    source_digest: SourceDigest
    git_dirty: bool = False


@dataclass(frozen=True)
class BuildIdentityDiagnosis:
    valid: bool
    code: str | None
    failures: tuple[str, ...]
    detail: str | None
    git_sha: str | None
    git_dirty: str | None
    source_digest: SourceDigest | None
    identity: BuildIdentity | None

    @property
    def state(self) -> str:
        return "valid" if self.valid else "invalid"


def claim_view(raw: str | None, valid: bool) -> str | None:
    """
    Representacao diagnostica de uma alegacao. A seguranca depende da
    VALIDADE SEMANTICA da alegacao, nunca de um filtro generico de
    caracteres:

    * ausente -> `None`;
    * valida (SHA de 40 hex minusculos; `true`/`false`) -> o proprio valor;
    * presente mas invalida -> `INVALID_CLAIM_MARKER` (jamais o valor cru).
    """
    if raw is None:
        return None
    return raw if valid else INVALID_CLAIM_MARKER


def is_in_scope(relative_posix_path: str) -> bool:
    parts = relative_posix_path.split("/")
    if "__pycache__" in parts:
        return False
    if relative_posix_path in SCOPE_FILES:
        return True
    return (
        len(parts) > 1
        and parts[0] in SCOPE_DIRS
        and relative_posix_path.endswith(".py")
    )


def manifest_digest(
    entries: Iterable[tuple[str, bytes]],
) -> SourceDigest:
    """Digest `sd1` de pares (caminho relativo POSIX, bytes brutos)."""
    rows: list[tuple[bytes, str, str]] = []
    for relative_path, data in entries:
        try:
            encoded_path = relative_path.encode("ascii")
        except UnicodeEncodeError:
            raise SourceDigestError("non_ascii_path") from None
        normalized = data.replace(b"\r\n", b"\n")
        rows.append(
            (
                encoded_path,
                relative_path,
                hashlib.sha256(normalized).hexdigest(),
            )
        )

    if not rows:
        raise SourceDigestError("empty_set")

    rows.sort()
    manifest = "".join(
        f"{file_hash}  {relative_path}\n"
        for _, relative_path, file_hash in rows
    )
    return SourceDigest(
        algorithm=SOURCE_DIGEST_ALGORITHM,
        digest=hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        file_count=len(rows),
    )


def default_source_root() -> Path:
    # backend/app/core/build_identity.py -> backend/  (/app na imagem)
    return Path(__file__).resolve().parents[2]


def compute_source_digest(root: Path | str) -> SourceDigest:
    """`sd1` sobre os arquivos de `root`; nao usa `.git`."""
    root_path = Path(root)
    if not root_path.is_dir():
        raise SourceDigestError("root_missing")

    collected: list[tuple[str, Path]] = []

    for scope_dir in SCOPE_DIRS:
        base = root_path / scope_dir
        if os.path.islink(base):
            raise SourceDigestError("symlink")
        if not base.is_dir():
            raise SourceDigestError(f"scope_dir_missing:{scope_dir}")

        for dirpath, dirnames, filenames in os.walk(
            base, followlinks=False
        ):
            for dirname in dirnames:
                if os.path.islink(os.path.join(dirpath, dirname)):
                    raise SourceDigestError("symlink")
            dirnames[:] = [
                name for name in dirnames if name != "__pycache__"
            ]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full_path = Path(dirpath) / filename
                collected.append(
                    (full_path.relative_to(root_path).as_posix(), full_path)
                )

    for scope_file in SCOPE_FILES:
        full_path = root_path / scope_file
        if os.path.islink(full_path):
            raise SourceDigestError("symlink")
        if not full_path.is_file():
            raise SourceDigestError("requirements_missing")
        collected.append((scope_file, full_path))

    entries: list[tuple[str, bytes]] = []
    for relative_path, full_path in collected:
        if os.path.islink(full_path):
            raise SourceDigestError("symlink")
        try:
            entries.append((relative_path, full_path.read_bytes()))
        except OSError:
            raise SourceDigestError("read_error") from None

    return manifest_digest(entries)


def diagnose_build_identity(
    environ: Mapping[str, str] | None = None,
    root: Path | str | None = None,
) -> BuildIdentityDiagnosis:
    """Avalia a cadeia conjuntiva. NUNCA levanta excecao."""
    environment = os.environ if environ is None else environ
    raw_sha = environment.get(ENV_GIT_SHA)
    raw_dirty = environment.get(ENV_GIT_DIRTY)

    failures: list[str] = []

    sha_valid = (
        raw_sha is not None and _SHA_PATTERN.fullmatch(raw_sha) is not None
    )
    dirty_valid = raw_dirty in ("true", "false")

    if not sha_valid:
        failures.append(CODE_SHA_INVALID)

    if not dirty_valid:
        failures.append(CODE_DIRTY_INVALID)
    elif raw_dirty == "true":
        failures.append(CODE_DIRTY)

    source_digest: SourceDigest | None = None
    detail: str | None = None
    try:
        source_digest = compute_source_digest(
            default_source_root() if root is None else root
        )
    except SourceDigestError as error:
        detail = error.detail
        failures.append(CODE_SOURCE_DIGEST_ERROR)
    except Exception:
        detail = "unexpected_error"
        failures.append(CODE_SOURCE_DIGEST_ERROR)

    valid = not failures
    identity = (
        BuildIdentity(
            git_sha=str(raw_sha),
            source_digest=source_digest,
        )
        if valid and source_digest is not None
        else None
    )

    return BuildIdentityDiagnosis(
        valid=valid,
        code=failures[0] if failures else None,
        failures=tuple(failures),
        detail=detail,
        git_sha=claim_view(raw_sha, sha_valid),
        git_dirty=claim_view(raw_dirty, dirty_valid),
        source_digest=source_digest,
        identity=identity,
    )


# ---------------------------------------------------------------------
# Memoizacao por processo (o `conftest` abre o lifespan por teste; sem
# isto o `sd1` seria recalculado centenas de vezes). `diagnose_*` e
# `compute_source_digest` permanecem puros e SEM cache.
# ---------------------------------------------------------------------


class _ProcessIdentityCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._diagnosis: BuildIdentityDiagnosis | None = None

    def get(self) -> BuildIdentityDiagnosis:
        with self._lock:
            if self._diagnosis is None:
                self._diagnosis = diagnose_build_identity()
            return self._diagnosis

    def reset(self) -> None:
        with self._lock:
            self._diagnosis = None


_PROCESS_CACHE = _ProcessIdentityCache()


def process_build_identity_diagnosis() -> BuildIdentityDiagnosis:
    return _PROCESS_CACHE.get()


def reset_process_build_identity() -> None:
    """Reset controlado da memoizacao (uso de testes)."""
    _PROCESS_CACHE.reset()
