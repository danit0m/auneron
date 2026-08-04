from dataclasses import asdict
from dataclasses import dataclass
from threading import Lock


@dataclass
class AgentMetrics:
    agent_name: str
    executions: int = 0
    successes: int = 0
    failures: int = 0
    total_duration_seconds: float = 0.0

    @property
    def average_duration_seconds(self) -> float:
        if self.executions == 0:
            return 0.0

        return (
            self.total_duration_seconds
            / self.executions
        )

    @property
    def success_rate(self) -> float:
        if self.executions == 0:
            return 0.0

        return (
            self.successes
            / self.executions
        ) * 100

    def to_dict(self) -> dict:
        data = asdict(self)

        data["average_duration_seconds"] = round(
            self.average_duration_seconds,
            6,
        )

        data["success_rate"] = round(
            self.success_rate,
            2,
        )

        data["total_duration_seconds"] = round(
            self.total_duration_seconds,
            6,
        )

        return data


class MetricsCollector:
    """
    Armazena métricas agregadas da execução dos agentes.

    Nesta primeira versão, os dados permanecem em memória.
    Ao reiniciar o backend, as métricas são zeradas.
    """

    def __init__(self) -> None:
        self._metrics: dict[
            str,
            AgentMetrics,
        ] = {}

        self._lock = Lock()

    def record_execution(
        self,
        *,
        agent_name: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        with self._lock:
            metric = self._metrics.setdefault(
                agent_name,
                AgentMetrics(
                    agent_name=agent_name,
                ),
            )

            metric.executions += 1
            metric.total_duration_seconds += (
                duration_seconds
            )

            if success:
                metric.successes += 1
            else:
                metric.failures += 1

    def get_agent_metrics(
        self,
        agent_name: str,
    ) -> AgentMetrics | None:
        with self._lock:
            return self._metrics.get(
                agent_name,
            )

    def get_all_metrics(self) -> list[dict]:
        with self._lock:
            metrics = [
                metric.to_dict()
                for metric in self._metrics.values()
            ]

        return sorted(
            metrics,
            key=lambda item: item["agent_name"],
        )

    def get_summary(self) -> dict:
        with self._lock:
            executions = sum(
                metric.executions
                for metric in self._metrics.values()
            )

            successes = sum(
                metric.successes
                for metric in self._metrics.values()
            )

            failures = sum(
                metric.failures
                for metric in self._metrics.values()
            )

            total_duration = sum(
                metric.total_duration_seconds
                for metric in self._metrics.values()
            )

        average_duration = (
            total_duration / executions
            if executions > 0
            else 0.0
        )

        success_rate = (
            successes / executions * 100
            if executions > 0
            else 0.0
        )

        return {
            "registered_agents": len(
                self._metrics
            ),
            "executions": executions,
            "successes": successes,
            "failures": failures,
            "success_rate": round(
                success_rate,
                2,
            ),
            "average_duration_seconds": round(
                average_duration,
                6,
            ),
            "total_duration_seconds": round(
                total_duration,
                6,
            ),
        }

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()


metrics_collector = MetricsCollector()