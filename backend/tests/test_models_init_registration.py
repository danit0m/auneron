"""
VALUE-2.3 -- previne repetir, para EscalationObservation, o gap ja
identificado (2.2A) de modelos ausentes de app.models.__all__ /
Base.metadata (o que faria `alembic revision --autogenerate` nao
enxergar a tabela, mesmo que ela exista de verdade no banco).

OPS-CI-1.1 -- fecha a CLASSE da regressao: o `env.py` do Alembic so faz
`import app.models`; toda tabela mapeada pelo app precisa estar no
`Base.metadata` carregado por esse import sozinho, senao `alembic check`
(e um futuro `--autogenerate`) enxerga a tabela como "a remover".
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import app.models
from app.database.database import Base
from app.models.escalation_observation import EscalationObservation


BACKEND_DIR = Path(__file__).resolve().parents[1]

# Roda num interpretador NOVO: dentro do pytest o conftest ja importou
# app.main, entao Base.metadata aqui esta sempre completo e qualquer
# assert in-process passaria mesmo com o __init__ incompleto.
_FRESH_INTERPRETER_SNAPSHOT = r"""
import importlib, json, pkgutil
import app.models as pkg
from app.database.database import Base

registered = set(Base.metadata.tables)

for module in pkgutil.iter_modules(pkg.__path__):
    importlib.import_module("app.models." + module.name)
import app.main  # noqa: F401  (modelos definidos em qualquer lugar do app)

complete = set(Base.metadata.tables)
owner = {
    mapper.local_table.name: mapper.class_.__module__
    for mapper in Base.registry.mappers
}
print(json.dumps({
    "registered": sorted(registered),
    "complete": sorted(complete),
    "owner": owner,
}))
"""


def test_escalation_observation_in_models_all() -> None:
    assert "EscalationObservation" in app.models.__all__
    assert app.models.EscalationObservation is EscalationObservation


def test_escalation_observation_table_in_base_metadata() -> None:
    assert "escalation_observations" in Base.metadata.tables


@pytest.mark.parametrize(
    "class_name,module_name",
    [
        (
            "BusinessEffectVerification",
            "app.models.business_effect_verification",
        ),
        (
            "NbaRecommendationSnapshot",
            "app.models.nba_recommendation_snapshot",
        ),
        (
            "PolicyAuthorityConsumption",
            "app.models.policy_authority_consumption",
        ),
        (
            "PolicyAuthorityGrant",
            "app.models.policy_authority_grant",
        ),
    ],
)
def test_ops_ci_models_are_exported_from_app_models(
    class_name: str, module_name: str
) -> None:
    # Convencao (nao propriedade generica: `__all__` ja era incompleto
    # antes -- ex.: ApprovalConsumption).
    assert class_name in app.models.__all__
    exported = getattr(app.models, class_name)
    assert exported.__module__ == module_name


def test_import_app_models_alone_registers_every_mapped_table() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _FRESH_INTERPRETER_SNAPSHOT],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]

    snapshot = json.loads(completed.stdout.strip().splitlines()[-1])
    missing = sorted(
        set(snapshot["complete"]) - set(snapshot["registered"])
    )

    assert not missing, (
        "`import app.models` sozinho nao registra tabelas que o app "
        "mapeia -- o env.py do Alembic as veria como 'a remover'. "
        "Adicione o import correspondente em app/models/__init__.py: "
        + ", ".join(
            f"{table} ({snapshot['owner'].get(table)})"
            for table in missing
        )
    )
