"""
VALUE-3.4D-2b -- semantica declarada e fingerprint mecanico do produtor
automatico de `observed_fact`.

Tres conceitos SEPARADOS (nunca misturar):

* `PRODUCER_SPEC`   -- declaracao HUMANA e versionada da semantica
                       ("escalation_payment_observation:v1"). Nao e a
                       versao do Auneron.
* fingerprint `pf1` -- verificacao MECANICA de que o codigo em execucao
                       implementa essa semantica (mede o codigo).
* `source_digest`   -- identidade complementar do conjunto de fontes do
                       produto (`sd1`, build_identity.py).

O fingerprint NUNCA gera `v2` automaticamente. `medido != pinned` e uma
inconsistencia DETECTAVEL (o codigo semanticamente relevante mudou sem a
decisao humana de re-fixar o pin -- e, se a semantica mudou de fato, de
subir a spec): em runtime o produtor abstem
(`producer_fingerprint_mismatch`) e o teste de CI falha.

Algoritmo `pf1` (versionado; mudar o conjunto de alvos, a canonicalizacao
ou o formato exige um novo id, nunca o reuso de `pf1`):

* alvos: um conjunto FECHADO de elementos nomeados (`FINGERPRINT_TARGETS`)
  que determinam a selecao, a ligacao evento->episodio, a classificacao e
  a semantica de idempotencia -- e nada alem disso;
* cada alvo e serializado por um serializador canonico PROPRIO (nao
  `ast.dump`): ignora posicoes, comentarios, formatacao, anotacoes de tipo,
  docstrings e campos vazios; mantem tipos e valores de folha;
* alvos ordenados por `arquivo::qualname`;
  `fingerprint = sha256("pf1\\n" + "<alvo>\\t<sha256(canonico)>\\n"...)`.

Alvo ausente, arquivo ilegivel ou fonte nao parseavel => fingerprint
INDISPONIVEL (fail-closed). Nenhuma escrita, nenhum DB, somente stdlib.
Fica de FORA (decisao D-b4): mecanica de lote/loop/log/contadores do
worker, imports, anotacoes, docstrings, modelos/schema (cobertos pela
identidade de schema) e `human_assessment` (outro produtor).
"""

from __future__ import annotations

import ast
import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PRODUCER_SPEC = "escalation_payment_observation:v1"

FINGERPRINT_ALGORITHM = "pf1"

# Valor ESPERADO do fingerprint para a spec acima, medido sobre os alvos
# DEPOIS de todas as alteracoes autorizadas (nunca escolhido para fazer um
# teste passar). Fica neste arquivo, que NAO e um alvo.
PINNED_PRODUCER_FINGERPRINT = (
    "44dc1922c914e27bff2a90af240d7609434d45f5a8c267032fae890dec653c63"
)

_WORKER = "app/core/escalation_payment_observation_maintenance.py"
_SERVICE = "app/services/escalation_observation_service.py"
_ELIGIBILITY = "app/core/human_escalation_eligibility.py"

FINGERPRINT_TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        _WORKER,
        (
            "_list_candidates",
            "_resolve_episode_work_item",
        ),
    ),
    (
        _SERVICE,
        (
            "WORK_KEY_PREFIX",
            "ALLOWED_OBSERVED_FACT_NEW_STATUS",
            "EscalationObservationService._validate_escalation_work_item",
            "EscalationObservationService.record_observed_fact",
            "EscalationObservationService._find_by_key",
            "EscalationObservationService._insert_or_reconcile",
        ),
    ),
    (
        _ELIGIBILITY,
        ("work_key_for_episode",),
    ),
)

CODE_FINGERPRINT_UNAVAILABLE = "producer_fingerprint_unavailable"
CODE_FINGERPRINT_MISMATCH = "producer_fingerprint_mismatch"

# Campos que nao carregam semantica de execucao.
_SKIPPED_FIELDS = frozenset(
    {
        "lineno",
        "col_offset",
        "end_lineno",
        "end_col_offset",
        "type_comment",
        "kind",
        "returns",
        "annotation",
        "simple",
        "type_params",
    }
)


class ProducerFingerprintError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProducerFingerprint:
    algorithm: str
    digest: str
    target_count: int


@dataclass(frozen=True)
class ProducerFingerprintDiagnosis:
    valid: bool
    code: str | None
    detail: str | None
    measured: ProducerFingerprint | None
    pinned: str

    @property
    def state(self) -> str:
        return "valid" if self.valid else str(self.code)


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def canonical_text(node: object) -> str:
    """Serializacao canonica (ignora comentarios/formatacao/anotacoes)."""
    if isinstance(node, ast.AST):
        parts: list[str] = []
        for name, value in ast.iter_fields(node):
            if name in _SKIPPED_FIELDS:
                continue
            if (
                name == "body"
                and isinstance(
                    node,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                    ),
                )
                and value
                and _is_docstring(value[0])
            ):
                value = value[1:]
            if value is None or value == []:
                continue
            parts.append(f" {name}={canonical_text(value)}")
        return f"({type(node).__name__}{''.join(parts)})"
    if isinstance(node, list):
        return "[" + ",".join(canonical_text(item) for item in node) + "]"
    return f"{type(node).__name__}:{node!r}"


def _find_target(tree: ast.Module, qualname: str) -> ast.AST:
    scope: list[ast.stmt] = tree.body
    segments = qualname.split(".")
    for position, segment in enumerate(segments):
        found: ast.stmt | None = None
        for statement in scope:
            if (
                isinstance(
                    statement,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                    ),
                )
                and statement.name == segment
            ):
                found = statement
                break
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == segment
                for target in statement.targets
            ):
                found = statement
                break
        if found is None:
            raise LookupError(qualname)
        if position < len(segments) - 1:
            scope = getattr(found, "body", [])
    return found


def default_backend_root() -> Path:
    # backend/app/core/evidence_producer_spec.py -> backend/
    return Path(__file__).resolve().parents[2]


def compute_producer_fingerprint(
    root: Path | str | None = None,
    *,
    sources: Mapping[str, str] | None = None,
) -> ProducerFingerprint:
    """`pf1` dos alvos. `sources` (caminho -> texto) permite medir fontes
    em memoria (testes); sem ele, le os arquivos sob `root`."""
    base = Path(default_backend_root() if root is None else root)
    rows: list[tuple[str, str]] = []

    for relative_path, qualnames in FINGERPRINT_TARGETS:
        if sources is not None and relative_path in sources:
            text = sources[relative_path]
        else:
            try:
                text = (base / relative_path).read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                raise ProducerFingerprintError(
                    CODE_FINGERPRINT_UNAVAILABLE,
                    f"source_unreadable:{relative_path}",
                ) from None
        try:
            tree = ast.parse(text)
        except SyntaxError:
            raise ProducerFingerprintError(
                CODE_FINGERPRINT_UNAVAILABLE,
                f"source_unparseable:{relative_path}",
            ) from None

        for qualname in qualnames:
            try:
                node = _find_target(tree, qualname)
            except LookupError:
                raise ProducerFingerprintError(
                    CODE_FINGERPRINT_UNAVAILABLE,
                    f"target_missing:{relative_path}::{qualname}",
                ) from None
            rows.append(
                (
                    f"{relative_path}::{qualname}",
                    hashlib.sha256(
                        canonical_text(node).encode("utf-8")
                    ).hexdigest(),
                )
            )

    rows.sort()
    manifest = FINGERPRINT_ALGORITHM + "\n" + "".join(
        f"{key}\t{digest}\n" for key, digest in rows
    )
    return ProducerFingerprint(
        algorithm=FINGERPRINT_ALGORITHM,
        digest=hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
        target_count=len(rows),
    )


def diagnose_producer_fingerprint(
    root: Path | str | None = None,
    *,
    pinned: str | None = None,
    sources: Mapping[str, str] | None = None,
) -> ProducerFingerprintDiagnosis:
    """Mede e compara com o pin. NUNCA levanta excecao."""
    expected = PINNED_PRODUCER_FINGERPRINT if pinned is None else pinned
    try:
        measured = compute_producer_fingerprint(root, sources=sources)
    except ProducerFingerprintError as error:
        return ProducerFingerprintDiagnosis(
            valid=False,
            code=error.code,
            detail=error.detail,
            measured=None,
            pinned=expected,
        )
    except Exception:
        return ProducerFingerprintDiagnosis(
            valid=False,
            code=CODE_FINGERPRINT_UNAVAILABLE,
            detail="unexpected_error",
            measured=None,
            pinned=expected,
        )

    if measured.digest != expected:
        return ProducerFingerprintDiagnosis(
            valid=False,
            code=CODE_FINGERPRINT_MISMATCH,
            detail=None,
            measured=measured,
            pinned=expected,
        )

    return ProducerFingerprintDiagnosis(
        valid=True,
        code=None,
        detail=None,
        measured=measured,
        pinned=expected,
    )


# Memoizacao por processo (le arquivos da imagem 1x); reset controlado.


class _ProcessFingerprintCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._diagnosis: ProducerFingerprintDiagnosis | None = None

    def get(self) -> ProducerFingerprintDiagnosis:
        with self._lock:
            if self._diagnosis is None:
                self._diagnosis = diagnose_producer_fingerprint()
            return self._diagnosis

    def reset(self) -> None:
        with self._lock:
            self._diagnosis = None


_PROCESS_CACHE = _ProcessFingerprintCache()


def process_producer_fingerprint_diagnosis() -> (
    ProducerFingerprintDiagnosis
):
    return _PROCESS_CACHE.get()


def reset_process_producer_fingerprint() -> None:
    """Reset controlado da memoizacao (uso de testes)."""
    _PROCESS_CACHE.reset()
