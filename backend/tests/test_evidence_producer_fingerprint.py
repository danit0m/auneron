"""
VALUE-3.4D-2b -- fingerprint `pf1` do produtor automatico.

Tres conceitos separados: `producer_spec` (declaracao humana versionada),
fingerprint (medicao mecanica do codigo) e `source_digest` (identidade do
conjunto de fontes). O fingerprint detecta mudanca SEMANTICA nos 9 alvos
congelados -- e ignora comentario, formatacao, docstring e anotacao -- e
NUNCA gera `v2` automaticamente: divergencia do pin e inconsistencia
detectavel (CI falha; em runtime o produtor abstem).

Os 9 alvos e o `pf1` sao contrato congelado: NAO ampliar nem reduzir.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import pytest

from app.core import evidence_producer_spec as spec


BACKEND_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = BACKEND_DIR / "app" / "core" / "evidence_producer_spec.py"

WORKER = "app/core/escalation_payment_observation_maintenance.py"
SERVICE = "app/services/escalation_observation_service.py"
ELIGIBILITY = "app/core/human_escalation_eligibility.py"

FROZEN_TARGETS = {
    f"{WORKER}::_list_candidates",
    f"{WORKER}::_resolve_episode_work_item",
    f"{SERVICE}::WORK_KEY_PREFIX",
    f"{SERVICE}::ALLOWED_OBSERVED_FACT_NEW_STATUS",
    f"{SERVICE}::EscalationObservationService._validate_escalation_work_item",
    f"{SERVICE}::EscalationObservationService.record_observed_fact",
    f"{SERVICE}::EscalationObservationService._find_by_key",
    f"{SERVICE}::EscalationObservationService._insert_or_reconcile",
    f"{ELIGIBILITY}::work_key_for_episode",
}

# sha256 do AST canonico da consulta ORIGINAL (bloco inline do worker em
# 72928738): a extracao `_resolve_episode_work_item` e comportamentalmente
# neutra -- mesma consulta, mesmo work_key, mesma selecao.
ORIGINAL_QUERY_AST_SHA256 = (
    "448be57e3083b532fe43d2686bdd81e8ea2f8927532fd90a6c4140fdb08c8e15"
)


def real_sources() -> dict[str, str]:
    return {
        path: (BACKEND_DIR / path).read_bytes().decode("utf-8").replace(
            "\r\n", "\n"
        )
        for path in (WORKER, SERVICE, ELIGIBILITY)
    }


def measure(sources: dict[str, str]) -> str:
    return spec.compute_producer_fingerprint(sources=sources).digest


@pytest.fixture(scope="module")
def baseline_sources() -> dict[str, str]:
    return real_sources()


@pytest.fixture(scope="module")
def baseline_digest(baseline_sources) -> str:
    return measure(baseline_sources)


@pytest.fixture(autouse=True)
def _reset_cache():
    spec.reset_process_producer_fingerprint()
    yield
    spec.reset_process_producer_fingerprint()


def mutate(sources, path, old, new):
    assert sources[path].count(old) >= 1, (path, old[:70])
    changed = dict(sources)
    changed[path] = sources[path].replace(old, new, 1)
    return changed


# ---------------------------------------------------------------------
# 1. contrato congelado e pin
# ---------------------------------------------------------------------


def test_frozen_spec_algorithm_and_targets() -> None:
    assert spec.PRODUCER_SPEC == "escalation_payment_observation:v1"
    assert spec.FINGERPRINT_ALGORITHM == "pf1"
    declared = {
        f"{path}::{qualname}"
        for path, qualnames in spec.FINGERPRINT_TARGETS
        for qualname in qualnames
    }
    assert declared == FROZEN_TARGETS
    assert len(declared) == 9


def test_the_pin_is_a_real_measured_value_not_a_placeholder() -> None:
    assert len(spec.PINNED_PRODUCER_FINGERPRINT) == 64
    assert set(spec.PINNED_PRODUCER_FINGERPRINT) <= set("0123456789abcdef")
    assert set(spec.PINNED_PRODUCER_FINGERPRINT) != {"0"}


def test_measured_fingerprint_equals_the_pin(baseline_digest: str) -> None:
    # Se este teste falha, o codigo semanticamente relevante mudou: decida
    # (bump de `PRODUCER_SPEC` quando a semantica mudou de fato) e re-fixe
    # o pin DELIBERADAMENTE. O fingerprint nunca gera `v2` sozinho.
    assert baseline_digest == spec.PINNED_PRODUCER_FINGERPRINT


def test_diagnosis_is_valid_for_the_real_tree() -> None:
    diagnosis = spec.diagnose_producer_fingerprint()

    assert diagnosis.valid is True
    assert diagnosis.state == "valid"
    assert diagnosis.code is None
    assert diagnosis.measured.algorithm == "pf1"
    assert diagnosis.measured.target_count == 9
    assert diagnosis.measured.digest == spec.PINNED_PRODUCER_FINGERPRINT


def test_the_spec_module_is_not_one_of_its_own_targets() -> None:
    assert all(
        path != "app/core/evidence_producer_spec.py"
        for path, _ in spec.FINGERPRINT_TARGETS
    )


# ---------------------------------------------------------------------
# 2. mudancas SEMANTICAS alteram o fingerprint
# ---------------------------------------------------------------------

SEMANTIC_CHANGES = [
    pytest.param(
        SERVICE,
        'ALLOWED_OBSERVED_FACT_NEW_STATUS = ("pago",)',
        'ALLOWED_OBSERVED_FACT_NEW_STATUS = ("atrasado",)',
        id="status_pago_para_atrasado",
    ),
    pytest.param(
        SERVICE,
        "account_event.occurred_at\n            < escalation_work_item.created_at",
        "account_event.occurred_at\n            <= escalation_work_item.created_at",
        id="menor_para_menor_igual",
    ),
    pytest.param(
        WORKER,
        "AccountEvent.occurred_at >= activation_floor",
        "AccountEvent.occurred_at > activation_floor",
        id="corte_do_floor",
    ),
    pytest.param(
        WORKER,
        ".order_by(AccountEvent.occurred_at, AccountEvent.id)",
        ".order_by(AccountEvent.id)",
        id="ordem_de_selecao",
    ),
    pytest.param(
        WORKER,
        'WorkItem.scope_type == "account",\n            WorkItem.work_key.like',
        'WorkItem.scope_type == "other",\n            WorkItem.work_key.like',
        id="filtro_de_selecao",
    ),
    pytest.param(
        SERVICE,
        'WORK_KEY_PREFIX = "human_escalation:v1:"',
        'WORK_KEY_PREFIX = "human_escalation:v2:"',
        id="work_key_prefix",
    ),
    pytest.param(
        SERVICE,
        '"escalation_observation:observed_fact:"',
        '"escalation_observation:observed_fact_v2:"',
        id="formato_da_chave_de_idempotencia",
    ),
    pytest.param(
        SERVICE,
        "observed_at=account_event.occurred_at",
        "observed_at=escalation_work_item.created_at",
        id="observed_at_do_fato",
    ),
    pytest.param(
        SERVICE,
        'observation_type="observed_fact",\n            linked_account_event_id',
        'observation_type="human_assessment",\n            linked_account_event_id',
        id="classificacao_observation_type",
    ),
    pytest.param(
        SERVICE,
        "linked_account_event_id=account_event.id,\n            observed_at",
        "linked_account_event_id=escalation_work_item.id,\n            observed_at",
        id="ligacao_account_event_observation",
    ),
    pytest.param(
        SERVICE,
        "provenance_context_id=provenance.context_id",
        "provenance_context_id=provenance.pass_id",
        id="ligacao_do_contexto_de_proveniencia",
    ),
    pytest.param(
        WORKER,
        "work_key_for_episode(account.id, account.vencimento)",
        "work_key_for_episode(account.id, account.id)",
        id="resolucao_do_episodio_vencimento",
    ),
    pytest.param(
        ELIGIBILITY,
        "{account_id}:{due_date.isoformat()}",
        "{account_id}:{due_date}",
        id="formato_do_work_key_do_episodio",
    ),
    pytest.param(
        SERVICE,
        'if escalation_work_item.scope_type != "account":',
        'if escalation_work_item.scope_type != "other":',
        id="validacao_do_workitem",
    ),
    pytest.param(
        SERVICE,
        "                and existing.linked_account_event_id\n                != expected_linked_account_event_id",
        "                and existing.linked_account_event_id\n                == expected_linked_account_event_id",
        id="reconciliacao_de_idempotencia",
    ),
    pytest.param(
        WORKER,
        "        .outerjoin(",
        "        .join(",
        id="outerjoin_para_join",
    ),
    pytest.param(
        WORKER,
        "EscalationObservation.id.is_(None)",
        "EscalationObservation.id.is_not(None)",
        id="filtro_sem_observation",
    ),
]


@pytest.mark.parametrize("path,old,new", SEMANTIC_CHANGES)
def test_semantic_changes_alter_the_fingerprint(
    baseline_sources, baseline_digest, path: str, old: str, new: str
) -> None:
    changed = mutate(baseline_sources, path, old, new)

    assert measure(changed) != baseline_digest


def test_a_semantic_change_makes_the_diagnosis_a_mismatch(
    baseline_sources,
) -> None:
    changed = mutate(
        baseline_sources,
        SERVICE,
        'ALLOWED_OBSERVED_FACT_NEW_STATUS = ("pago",)',
        'ALLOWED_OBSERVED_FACT_NEW_STATUS = ("atrasado",)',
    )

    diagnosis = spec.diagnose_producer_fingerprint(sources=changed)

    assert diagnosis.valid is False
    assert diagnosis.code == "producer_fingerprint_mismatch"
    assert diagnosis.measured is not None
    assert diagnosis.measured.digest != spec.PINNED_PRODUCER_FINGERPRINT


# ---------------------------------------------------------------------
# 3. mudancas NAO semanticas NAO alteram o fingerprint
# ---------------------------------------------------------------------

NON_SEMANTIC_CHANGES = [
    pytest.param(
        SERVICE,
        "    def record_observed_fact(",
        "    # comentario inocuo\n    def record_observed_fact(",
        id="comentario_novo",
    ),
    pytest.param(
        SERVICE,
        "        self._validate_escalation_work_item(escalation_work_item)\n\n        # VALUE-3.4D-2b",
        "        self._validate_escalation_work_item(escalation_work_item)\n\n\n\n        # VALUE-3.4D-2b",
        id="linhas_em_branco",
    ),
    pytest.param(
        SERVICE,
        "    ) -> EscalationObservation | None:",
        '    ) -> "EscalationObservation | None":',
        id="anotacao_de_retorno",
    ),
    pytest.param(
        WORKER,
        "def _list_candidates(\n    db: Session,",
        "def _list_candidates(\n    db: 'Session',",
        id="anotacao_de_parametro",
    ),
    pytest.param(
        WORKER,
        '    """\n    AccountEvent(pago) >= floor,',
        '    """\n    EDITADO. AccountEvent(pago) >= floor,',
        id="docstring_editada",
    ),
    pytest.param(
        WORKER,
        "import asyncio\nimport logging\n",
        "import asyncio\nimport logging\nimport os\n",
        id="import_novo",
    ),
    pytest.param(
        WORKER,
        '"escalation_payment_observation_recovery_completed",',
        '"escalation_payment_observation_recovery_completed_X",',
        id="mensagem_de_log_fora_dos_alvos",
    ),
    pytest.param(
        WORKER,
        "failure_count += 1",
        "failure_count += 2",
        id="contador_fora_dos_alvos",
    ),
]


@pytest.mark.parametrize("path,old,new", NON_SEMANTIC_CHANGES)
def test_non_semantic_changes_do_not_alter_the_fingerprint(
    baseline_sources, baseline_digest, path: str, old: str, new: str
) -> None:
    changed = mutate(baseline_sources, path, old, new)

    assert measure(changed) == baseline_digest


def test_total_reformatting_does_not_alter_the_fingerprint(
    baseline_sources, baseline_digest
) -> None:
    reformatted = {
        path: ast.unparse(ast.parse(text))
        for path, text in baseline_sources.items()
    }

    assert measure(reformatted) == baseline_digest


def test_reordering_module_functions_does_not_alter_it(
    baseline_sources, baseline_digest
) -> None:
    tree = ast.parse(baseline_sources[WORKER])
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    others = [n for n in tree.body if n not in functions]
    tree.body = others + functions[::-1]
    changed = dict(baseline_sources)
    changed[WORKER] = ast.unparse(tree)

    assert measure(changed) == baseline_digest


def test_reordering_class_methods_does_not_alter_it(
    baseline_sources, baseline_digest
) -> None:
    tree = ast.parse(baseline_sources[SERVICE])
    klass = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef)
        and n.name == "EscalationObservationService"
    )
    klass.body = klass.body[::-1]
    changed = dict(baseline_sources)
    changed[SERVICE] = ast.unparse(tree)

    assert measure(changed) == baseline_digest


def test_crlf_line_endings_do_not_alter_the_fingerprint(
    baseline_sources, baseline_digest
) -> None:
    crlf = {
        path: text.replace("\n", "\r\n")
        for path, text in baseline_sources.items()
    }

    assert measure(crlf) == baseline_digest


def test_known_noise_renaming_a_local_variable_does_change_it(
    baseline_sources, baseline_digest
) -> None:
    # Ruido conhecido e aceito (forca um re-pin consciente).
    changed = mutate(
        baseline_sources,
        SERVICE,
        "        observation = EscalationObservation(",
        "        obs_row = EscalationObservation(",
    )
    changed[SERVICE] = changed[SERVICE].replace(
        "        return self._insert_or_reconcile(\n            observation,",
        "        return self._insert_or_reconcile(\n            obs_row,",
        1,
    )

    assert measure(changed) != baseline_digest


# ---------------------------------------------------------------------
# 4. fail-closed: alvo ausente, fonte ilegivel/nao parseavel
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,old,new",
    [
        (
            SERVICE,
            "    def record_observed_fact(",
            "    def record_observed_fact_renamed(",
        ),
        (WORKER, "def _list_candidates(", "def _list_candidates_x("),
        (WORKER, "def _resolve_episode_work_item(", "def _resolve_x("),
        (ELIGIBILITY, "def work_key_for_episode(", "def work_key_x("),
        (SERVICE, 'WORK_KEY_PREFIX = "', 'WORK_KEY_PREFIX_X = "'),
    ],
    ids=[
        "record_observed_fact",
        "list_candidates",
        "resolve_episode_work_item",
        "work_key_for_episode",
        "work_key_prefix",
    ],
)
def test_a_missing_target_makes_the_fingerprint_unavailable(
    baseline_sources, path: str, old: str, new: str
) -> None:
    changed = mutate(baseline_sources, path, old, new)

    with pytest.raises(spec.ProducerFingerprintError) as error:
        spec.compute_producer_fingerprint(sources=changed)

    assert error.value.code == "producer_fingerprint_unavailable"
    assert "target_missing" in error.value.detail

    diagnosis = spec.diagnose_producer_fingerprint(sources=changed)
    assert diagnosis.valid is False
    assert diagnosis.code == "producer_fingerprint_unavailable"
    assert diagnosis.measured is None


def test_unparseable_source_is_unavailable(baseline_sources) -> None:
    broken = dict(baseline_sources)
    broken[SERVICE] = "def broken(:\n"

    diagnosis = spec.diagnose_producer_fingerprint(sources=broken)

    assert diagnosis.code == "producer_fingerprint_unavailable"
    assert "source_unparseable" in diagnosis.detail


def test_unreadable_root_is_unavailable(tmp_path: Path) -> None:
    diagnosis = spec.diagnose_producer_fingerprint(root=tmp_path / "nope")

    assert diagnosis.valid is False
    assert diagnosis.code == "producer_fingerprint_unavailable"
    assert "source_unreadable" in diagnosis.detail


def test_diagnosis_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("boom LEAK_SENTINEL")

    monkeypatch.setattr(spec, "compute_producer_fingerprint", explode)

    diagnosis = spec.diagnose_producer_fingerprint()

    assert diagnosis.code == "producer_fingerprint_unavailable"
    assert diagnosis.detail == "unexpected_error"
    assert "LEAK_SENTINEL" not in repr(diagnosis)


def test_an_explicit_wrong_pin_is_a_mismatch_not_a_new_version() -> None:
    diagnosis = spec.diagnose_producer_fingerprint(pinned="0" * 64)

    assert diagnosis.valid is False
    assert diagnosis.code == "producer_fingerprint_mismatch"
    assert diagnosis.state == "producer_fingerprint_mismatch"
    # o fingerprint medido continua o real; nada vira `v2` automaticamente
    assert diagnosis.measured.digest == spec.PINNED_PRODUCER_FINGERPRINT
    assert spec.PRODUCER_SPEC.endswith(":v1")


# ---------------------------------------------------------------------
# 5. neutralidade da extracao `_resolve_episode_work_item`
# ---------------------------------------------------------------------


def test_extracted_resolver_query_ast_equals_the_original_inline_query(
    baseline_sources,
) -> None:
    tree = ast.parse(baseline_sources[WORKER])
    function = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "_resolve_episode_work_item"
    )
    returned = next(
        s for s in function.body if isinstance(s, ast.Return)
    )

    canonical = spec.canonical_text(returned.value)

    assert (
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        == ORIGINAL_QUERY_AST_SHA256
    )


def test_the_run_function_no_longer_inlines_the_episode_query(
    baseline_sources,
) -> None:
    tree = ast.parse(baseline_sources[WORKER])
    run = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "run_escalation_payment_observation_recovery"
    )
    inline_queries = [
        n
        for n in ast.walk(run)
        if isinstance(n, ast.Attribute) and n.attr == "one_or_none"
    ]

    assert inline_queries == []


# ---------------------------------------------------------------------
# 6. propriedades do serializador canonico
# ---------------------------------------------------------------------


def canon(code: str) -> str:
    return spec.canonical_text(ast.parse(code))


def test_canonical_text_ignores_positions_comments_and_docstrings() -> None:
    plain = canon("def f(a):\n    return a + 1\n")
    decorated = canon(
        'def f(a):\n    """doc"""\n    # comentario\n\n    return a + 1\n'
    )

    assert plain == decorated


def test_canonical_text_ignores_annotations() -> None:
    assert canon("def f(a):\n    return a\n") == canon(
        "def f(a: int) -> str:\n    return a\n"
    )
    assert canon("x: int = 1\n") == canon("x: str = 1\n")


def test_canonical_text_distinguishes_value_types_and_operators() -> None:
    assert canon("x = 1\n") != canon("x = True\n")
    assert canon("x = 1\n") != canon("x = '1'\n")
    assert canon("a < b\n") != canon("a <= b\n")
    assert canon("a == b\n") != canon("a != b\n")
    assert canon("f(a, b)\n") != canon("f(b, a)\n")


# ---------------------------------------------------------------------
# 7. memoizacao e fronteira estatica
# ---------------------------------------------------------------------


def test_process_diagnosis_is_memoized_and_resettable() -> None:
    first = spec.process_producer_fingerprint_diagnosis()

    assert spec.process_producer_fingerprint_diagnosis() is first

    spec.reset_process_producer_fingerprint()

    assert spec.process_producer_fingerprint_diagnosis() is not first


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


def test_module_has_no_io_beyond_reading_the_target_sources() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )

    for forbidden in (
        "subprocess",
        "socket",
        "sqlalchemy",
        "os.environ",
        "getenv",
        ".write_text(",
        ".write_bytes(",
        "open(",
    ):
        assert forbidden not in code, forbidden
