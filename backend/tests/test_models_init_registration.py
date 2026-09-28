"""
VALUE-2.3 -- previne repetir, para EscalationObservation, o gap ja
identificado (2.2A) de modelos ausentes de app.models.__all__ /
Base.metadata (o que faria `alembic revision --autogenerate` nao
enxergar a tabela, mesmo que ela exista de verdade no banco).
"""

import app.models
from app.database.database import Base
from app.models.escalation_observation import EscalationObservation


def test_escalation_observation_in_models_all() -> None:
    assert "EscalationObservation" in app.models.__all__
    assert app.models.EscalationObservation is EscalationObservation


def test_escalation_observation_table_in_base_metadata() -> None:
    assert "escalation_observations" in Base.metadata.tables
