from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class TelemetryRecord:
    telemetry_id: str
    event_name: str
    strategy_name: str
    agent_name: str
    priority: int
    status: str
    duration_seconds: float
    started_at: str
    finished_at: str
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)

        data["duration_seconds"] = round(
            self.duration_seconds,
            6,
        )

        return data


class TelemetryService:
    """
    Mantém o histórico recente das execuções.

    A telemetria permanece em memória nesta sprint.
    O limite impede crescimento ilimitado da lista.
    """

    def __init__(
        self,
        *,
        max_records: int = 1000,
    ) -> None:
        self._records: list[
            TelemetryRecord
        ] = []

        self._max_records = max_records
        self._lock = Lock()

    def create_record(
        self,
        *,
        event_name: str,
        strategy_name: str,
        agent_name: str,
        priority: int,
        status: str,
        duration_seconds: float,
        started_at: datetime,
        finished_at: datetime,
        error: Exception | None = None,
    ) -> TelemetryRecord:
        record = TelemetryRecord(
            telemetry_id=str(uuid4()),
            event_name=event_name,
            strategy_name=strategy_name,
            agent_name=agent_name,
            priority=priority,
            status=status,
            duration_seconds=duration_seconds,
            started_at=started_at.isoformat(
                timespec="milliseconds",
            ),
            finished_at=finished_at.isoformat(
                timespec="milliseconds",
            ),
            error_type=(
                type(error).__name__
                if error is not None
                else None
            ),
            error_message=(
                str(error)
                if error is not None
                else None
            ),
        )

        with self._lock:
            self._records.append(record)

            if (
                len(self._records)
                > self._max_records
            ):
                excess = (
                    len(self._records)
                    - self._max_records
                )

                del self._records[:excess]

        return record

    def list_records(
        self,
        *,
        limit: int = 100,
        agent_name: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(
            1,
            min(limit, self._max_records),
        )

        with self._lock:
            records = list(
                reversed(self._records)
            )

        if agent_name:
            records = [
                record
                for record in records
                if record.agent_name
                == agent_name
            ]

        if status:
            normalized_status = (
                status.strip().upper()
            )

            records = [
                record
                for record in records
                if record.status
                == normalized_status
            ]

        return [
            record.to_dict()
            for record in records[:safe_limit]
        ]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


telemetry_service = TelemetryService()