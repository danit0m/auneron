from dataclasses import asdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecisionSignal:
    """
    Evidência identificada durante a análise
    realizada pelo Decision Engine.
    """

    name: str
    value: Any
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrchestrationDecision:
    """
    Decisão final produzida pelo Decision Engine.
    """

    decision_name: str
    selected_agents: tuple[str, ...]
    reason: str
    confidence: float
    signals: tuple[DecisionSignal, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_name": self.decision_name,
            "selected_agents": list(
                self.selected_agents
            ),
            "reason": self.reason,
            "confidence": round(
                self.confidence,
                4,
            ),
            "confidence_percentage": round(
                self.confidence * 100,
                2,
            ),
            "signals": [
                signal.to_dict()
                for signal in self.signals
            ],
        }