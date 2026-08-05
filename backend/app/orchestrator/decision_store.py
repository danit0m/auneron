from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from app.core.money import money_to_json_number
from app.orchestrator.decision import (
    OrchestrationDecision,
)


def _serialize_decimals(value: Any) -> Any:
    """Converte Decimal somente na fronteira de serialização JSON."""

    if isinstance(value, Decimal):
        return money_to_json_number(value)

    if isinstance(value, dict):
        return {
            key: _serialize_decimals(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _serialize_decimals(item)
            for item in value
        ]

    return value


@dataclass(frozen=True)
class StoredDecision:
    """
    Representa uma decisão armazenada
    pelo Decision Engine.
    """

    decision_id: str
    event_name: str
    decision_name: str
    reason: str
    confidence: float
    selected_agents: tuple[str, ...]
    signals: tuple[dict[str, Any], ...]
    cliente: str
    valor: Decimal
    status: str
    vencimento: str
    dias_atraso: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "event_name": self.event_name,
            "decision_name": self.decision_name,
            "reason": self.reason,
            "confidence": round(
                self.confidence,
                4,
            ),
            "confidence_percentage": round(
                self.confidence * 100,
                2,
            ),
            "selected_agents": list(
                self.selected_agents,
            ),
            "signals": [
                _serialize_decimals(
                    dict(signal)
                )
                for signal in self.signals
            ],
            "context": {
                "cliente": self.cliente,
                "valor": money_to_json_number(
                    self.valor
                ),
                "status": self.status,
                "vencimento": self.vencimento,
                "dias_atraso": self.dias_atraso,
            },
            "created_at": self.created_at,
        }


class DecisionStore:
    """
    Armazena em memória as decisões recentes
    produzidas pelo Decision Engine.

    Os dados são reiniciados quando o backend
    é encerrado ou recarregado.
    """

    def __init__(
        self,
        *,
        max_records: int = 500,
    ) -> None:
        if max_records < 1:
            raise ValueError(
                "max_records deve ser maior "
                "ou igual a 1."
            )

        self._records: list[
            StoredDecision
        ] = []

        self._max_records = max_records
        self._lock = Lock()

    def save(
        self,
        *,
        event_name: str,
        decision: OrchestrationDecision,
        cliente: str,
        valor: Decimal,
        status: str,
        vencimento: str,
        dias_atraso: int,
    ) -> StoredDecision:
        """
        Salva uma nova decisão e devolve
        o registro criado.
        """

        record = StoredDecision(
            decision_id=str(uuid4()),
            event_name=event_name,
            decision_name=(
                decision.decision_name
            ),
            reason=decision.reason,
            confidence=decision.confidence,
            selected_agents=(
                decision.selected_agents
            ),
            signals=tuple(
                signal.to_dict()
                for signal in decision.signals
            ),
            cliente=cliente,
            valor=valor,
            status=status,
            vencimento=vencimento,
            dias_atraso=dias_atraso,
            created_at=datetime.now().isoformat(
                timespec="milliseconds",
            ),
        )

        with self._lock:
            self._records.append(record)
            self._apply_limit()

        return record

    def get_latest(
        self,
    ) -> StoredDecision | None:
        """
        Retorna a decisão mais recente.
        """

        with self._lock:
            if not self._records:
                return None

            return self._records[-1]

    def get_by_id(
        self,
        decision_id: str,
    ) -> StoredDecision | None:
        """
        Busca uma decisão pelo identificador.
        """

        with self._lock:
            for record in self._records:
                if (
                    record.decision_id
                    == decision_id
                ):
                    return record

        return None

    def list_records(
        self,
        *,
        limit: int = 100,
        decision_name: str | None = None,
        event_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Lista decisões da mais recente
        para a mais antiga.
        """

        safe_limit = max(
            1,
            min(
                int(limit),
                self._max_records,
            ),
        )

        normalized_decision_name = (
            decision_name.strip().upper()
            if decision_name
            else None
        )

        normalized_event_name = (
            event_name.strip()
            if event_name
            else None
        )

        with self._lock:
            records = list(
                reversed(self._records)
            )

        if normalized_decision_name:
            records = [
                record
                for record in records
                if (
                    record.decision_name.upper()
                    == normalized_decision_name
                )
            ]

        if normalized_event_name:
            records = [
                record
                for record in records
                if (
                    record.event_name
                    == normalized_event_name
                )
            ]

        return [
            record.to_dict()
            for record in records[:safe_limit]
        ]

    def count(self) -> int:
        """
        Retorna o número de decisões
        armazenadas atualmente.
        """

        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        """
        Remove todas as decisões armazenadas.
        """

        with self._lock:
            self._records.clear()

    def _apply_limit(self) -> None:
        """
        Mantém somente os registros mais recentes.

        Deve ser executado enquanto o lock
        estiver adquirido.
        """

        excess = (
            len(self._records)
            - self._max_records
        )

        if excess > 0:
            del self._records[:excess]


decision_store = DecisionStore()