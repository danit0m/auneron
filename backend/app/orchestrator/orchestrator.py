from datetime import datetime
from time import perf_counter
from typing import Any

from app.orchestrator.registry import registry


class AIOrchestrator:
    """
    Coordena a execução dos agentes do Auneron AI.

    Recebe um evento, localiza os agentes registrados
    e executa cada um de forma isolada.
    """

    @staticmethod
    def execute(
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        handlers = registry.get_handlers(
            event_name
        )

        inicio_orquestracao = perf_counter()

        print()
        print(
            "=========================================="
        )
        print("AI ORCHESTRATOR")
        print(
            "=========================================="
        )
        print(f"Evento: {event_name}")
        print(
            "Horário: "
            f"{datetime.now().isoformat(timespec='seconds')}"
        )
        print(
            f"Agentes encontrados: {len(handlers)}"
        )

        if not handlers:
            print(
                "Nenhum agente registrado "
                "para este evento."
            )
            print(
                "=========================================="
            )
            print()
            return

        executados = 0
        erros = 0

        for handler in handlers:
            nome_agente = (
                handler.__qualname__.split(".")[0]
            )

            inicio_agente = perf_counter()

            print()
            print(
                f"Executando: {nome_agente}"
            )

            try:
                handler(payload)

                duracao_agente = (
                    perf_counter() -
                    inicio_agente
                )

                executados += 1

                print(
                    f"{nome_agente} concluído "
                    f"em {duracao_agente:.4f}s."
                )

            except Exception as error:
                duracao_agente = (
                    perf_counter() -
                    inicio_agente
                )

                erros += 1

                print(
                    f"Erro ao executar "
                    f"{nome_agente} "
                    f"após {duracao_agente:.4f}s:"
                )
                print(
                    f"{type(error).__name__}: "
                    f"{error}"
                )

        duracao_total = (
            perf_counter() -
            inicio_orquestracao
        )

        print()
        print(
            "Resumo da orquestração:"
        )
        print(
            f"Agentes concluídos: {executados}"
        )
        print(f"Erros: {erros}")
        print(
            f"Tempo total: {duracao_total:.4f}s"
        )
        print(
            "=========================================="
        )
        print()