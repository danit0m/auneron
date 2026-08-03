from datetime import date
from datetime import datetime
from typing import Any

from app.database.database import SessionLocal
from app.orchestrator import registry
from app.services.knowledge_service import (
    KnowledgeService,
)


class RiskAgent:
    """
    Calcula o Risk Score dos clientes
    e registra o resultado no Brain.
    """

    @staticmethod
    def on_cliente_criado(
        payload: dict[str, Any],
    ) -> None:
        cliente = str(
            payload.get(
                "cliente",
                "Cliente não informado",
            )
        )

        valor = float(
            payload.get("valor", 0) or 0
        )

        status = str(
            payload.get(
                "status",
                "não informado",
            )
        ).strip().lower()

        vencimento = str(
            payload.get(
                "vencimento",
                "",
            )
        )

        account_id = (
            RiskAgent.converter_account_id(
                payload.get("id")
            )
        )

        dias_atraso = (
            RiskAgent.calcular_dias_atraso(
                vencimento=vencimento,
                status=status,
            )
        )

        score = RiskAgent.calcular_score(
            valor=valor,
            status=status,
            dias_atraso=dias_atraso,
        )

        classificacao = (
            RiskAgent.classificar_score(
                score
            )
        )

        severidade = (
            RiskAgent.definir_severidade(
                score
            )
        )

        print()
        print(
            "========== RISK AGENT =========="
        )
        print(
            "Evento recebido: cliente_criado"
        )
        print(f"Cliente: {cliente}")
        print(f"Valor: R$ {valor:,.2f}")
        print(f"Status: {status}")
        print(f"Vencimento: {vencimento}")
        print(
            f"Dias em atraso: {dias_atraso}"
        )
        print(f"Risk Score: {score}/100")
        print(
            f"Classificação: {classificacao}"
        )

        db = SessionLocal()

        try:
            KnowledgeService.create(
                db=db,
                agent_name="RiskAgent",
                event_name="cliente_criado",
                knowledge_type="risk_score",
                severity=severidade,
                title=(
                    f"Risk Score {score} — "
                    f"{classificacao}"
                ),
                message=(
                    f"O cliente {cliente} recebeu "
                    f"Risk Score {score}/100, com "
                    f"classificação {classificacao}. "
                    f"Valor: R$ {valor:,.2f}. "
                    f"Status: {status}. "
                    f"Dias em atraso: {dias_atraso}. "
                    f"{RiskAgent.gerar_recomendacao(score)}"
                ),
                account_id=account_id,
            )

            print(
                "Risk Score salvo no Brain."
            )

            if score >= 70:
                KnowledgeService.create(
                    db=db,
                    agent_name="RiskAgent",
                    event_name="cliente_criado",
                    knowledge_type="recommendation",
                    severity=severidade,
                    title=(
                        "Ação de risco recomendada"
                    ),
                    message=(
                        f"O cliente {cliente} apresenta "
                        f"risco {classificacao.lower()}, "
                        f"com score {score}/100. "
                        f"{RiskAgent.gerar_recomendacao(score)}"
                    ),
                    account_id=account_id,
                )

                print(
                    "Recomendação de risco salva."
                )

        except Exception as error:
            db.rollback()

            print(
                "Erro ao registrar análise "
                f"do RiskAgent: {error}"
            )

        finally:
            db.close()

        print("RiskAgent finalizado.")
        print(
            "================================"
        )
        print()

    @staticmethod
    def calcular_score(
        *,
        valor: float,
        status: str,
        dias_atraso: int,
    ) -> int:
        score = 0

        if valor >= 50000:
            score += 40
        elif valor >= 30000:
            score += 30
        elif valor >= 15000:
            score += 20
        elif valor > 0:
            score += 10

        if status == "atrasado":
            score += 35
        elif status == "aberto":
            score += 10

        if dias_atraso >= 30:
            score += 25
        elif dias_atraso >= 15:
            score += 20
        elif dias_atraso >= 7:
            score += 15
        elif dias_atraso >= 1:
            score += 10

        return min(score, 100)

    @staticmethod
    def classificar_score(
        score: int,
    ) -> str:
        if score >= 90:
            return "CRÍTICO"

        if score >= 70:
            return "ALTO"

        if score >= 50:
            return "MÉDIO"

        return "BAIXO"

    @staticmethod
    def definir_severidade(
        score: int,
    ) -> str:
        if score >= 90:
            return "critical"

        if score >= 70:
            return "high"

        if score >= 50:
            return "medium"

        return "info"

    @staticmethod
    def gerar_recomendacao(
        score: int,
    ) -> str:
        if score >= 90:
            return (
                "Recomenda-se ação imediata, "
                "contato com o cliente e plano "
                "prioritário de cobrança."
            )

        if score >= 70:
            return (
                "Recomenda-se acompanhamento "
                "prioritário e contato nas "
                "próximas 24 horas."
            )

        if score >= 50:
            return (
                "Recomenda-se monitoramento "
                "frequente da situação."
            )

        return (
            "Manter o acompanhamento padrão."
        )

    @staticmethod
    def calcular_dias_atraso(
        *,
        vencimento: str,
        status: str,
    ) -> int:
        if (
            status != "atrasado"
            or not vencimento
        ):
            return 0

        try:
            data_vencimento = (
                datetime.strptime(
                    vencimento,
                    "%Y-%m-%d",
                ).date()
            )

            diferenca = (
                date.today() -
                data_vencimento
            ).days

            return max(diferenca, 0)

        except ValueError:
            print(
                "Vencimento inválido recebido: "
                f"{vencimento}"
            )

            return 0

    @staticmethod
    def converter_account_id(
        account_id: Any,
    ) -> int | None:
        if account_id is None:
            return None

        try:
            return int(account_id)

        except (TypeError, ValueError):
            return None


registry.register(
    "cliente_criado",
    RiskAgent.on_cliente_criado,
)