from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.knowledge import Knowledge


class ExecutiveService:
    """
    Responsável por gerar o resumo executivo
    do Brain do Auneron AI.
    """

    @staticmethod
    def generate_report(db: Session) -> dict:
        total = db.query(Knowledge).count()

        pendentes = (
            db.query(Knowledge)
            .filter(Knowledge.resolved.is_(False))
            .count()
        )

        resolvidos = (
            db.query(Knowledge)
            .filter(Knowledge.resolved.is_(True))
            .count()
        )

        criticos = (
            db.query(Knowledge)
            .filter(Knowledge.severity == "critical")
            .count()
        )

        altos = (
            db.query(Knowledge)
            .filter(Knowledge.severity == "high")
            .count()
        )

        medios = (
            db.query(Knowledge)
            .filter(Knowledge.severity == "medium")
            .count()
        )

        informativos = (
            db.query(Knowledge)
            .filter(Knowledge.severity == "info")
            .count()
        )

        agentes = (
            db.query(
                Knowledge.agent_name,
                func.count(Knowledge.id),
            )
            .group_by(Knowledge.agent_name)
            .all()
        )

        ranking_agentes = [
            {
                "agent": agente,
                "knowledge": quantidade,
            }
            for agente, quantidade in agentes
        ]

        prioridade = ExecutiveService._calculate_priority(
            criticos,
            altos,
        )

        resumo = ExecutiveService._generate_summary(
            criticos,
            altos,
            pendentes,
        )

        return {
            "total": total,
            "pending": pendentes,
            "resolved": resolvidos,
            "critical": criticos,
            "high": altos,
            "medium": medios,
            "info": informativos,
            "priority": prioridade,
            "summary": resumo,
            "agents": ranking_agentes,
        }

    @staticmethod
    def _calculate_priority(
        critical: int,
        high: int,
    ) -> str:
        if critical >= 5:
            return "CRÍTICA"

        if critical >= 1:
            return "ALTA"

        if high >= 5:
            return "MÉDIA"

        return "NORMAL"

    @staticmethod
    def _generate_summary(
        critical: int,
        high: int,
        pending: int,
    ) -> str:
        if critical == 1:
            return (
                "Foi encontrado 1 alerta crítico "
                "que exige atenção imediata."
            )

        if critical > 1:
            return (
                f"Foram encontrados {critical} alertas críticos "
                "que exigem atenção imediata."
            )

        if high == 1:
            return (
                "Existe 1 conhecimento importante "
                "que merece acompanhamento."
            )

        if high > 1:
            return (
                f"Existem {high} conhecimentos importantes "
                "que merecem acompanhamento."
            )

        if pending == 1:
            return (
                "Há 1 conhecimento pendente "
                "de tratamento."
            )

        if pending > 1:
            return (
                f"Há {pending} conhecimentos pendentes "
                "de tratamento."
            )

        return (
            "Nenhum risco relevante foi encontrado. "
            "O ambiente encontra-se estável."
        )
