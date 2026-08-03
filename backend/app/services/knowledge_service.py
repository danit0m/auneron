from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.knowledge import Knowledge


class KnowledgeService:
    """
    Serviço responsável por registrar e consultar
    o conhecimento gerado pelos agentes do Auneron AI.
    """

    @staticmethod
    def create(
        db: Session,
        *,
        agent_name: str,
        event_name: str,
        knowledge_type: str,
        severity: str,
        title: str,
        message: str,
        account_id: int | None = None,
    ) -> Knowledge:
        knowledge = Knowledge(
            agent_name=agent_name,
            event_name=event_name,
            knowledge_type=knowledge_type,
            severity=severity,
            title=title,
            message=message,
            account_id=account_id,
            resolved=False,
        )

        db.add(knowledge)
        db.commit()
        db.refresh(knowledge)

        return knowledge

    @staticmethod
    def list(
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
        resolved: bool | None = None,
    ) -> Sequence[Knowledge]:
        query = db.query(Knowledge)

        if resolved is not None:
            query = query.filter(
                Knowledge.resolved == resolved,
            )

        return (
            query
            .order_by(Knowledge.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def find_by_account(
        db: Session,
        account_id: int,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Knowledge]:
        return (
            db.query(Knowledge)
            .filter(Knowledge.account_id == account_id)
            .order_by(Knowledge.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def find_by_agent(
        db: Session,
        agent_name: str,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Knowledge]:
        return (
            db.query(Knowledge)
            .filter(Knowledge.agent_name == agent_name)
            .order_by(Knowledge.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def find_by_severity(
        db: Session,
        severity: str,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Knowledge]:
        return (
            db.query(Knowledge)
            .filter(Knowledge.severity == severity)
            .order_by(Knowledge.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_by_id(
        db: Session,
        knowledge_id: int,
    ) -> Knowledge | None:
        return (
            db.query(Knowledge)
            .filter(Knowledge.id == knowledge_id)
            .first()
        )

    @staticmethod
    def mark_resolved(
        db: Session,
        knowledge_id: int,
    ) -> Knowledge | None:
        knowledge = KnowledgeService.get_by_id(
            db,
            knowledge_id,
        )

        if knowledge is None:
            return None

        knowledge.resolved = True

        db.commit()
        db.refresh(knowledge)

        return knowledge

    @staticmethod
    def reopen(
        db: Session,
        knowledge_id: int,
    ) -> Knowledge | None:
        knowledge = KnowledgeService.get_by_id(
            db,
            knowledge_id,
        )

        if knowledge is None:
            return None

        knowledge.resolved = False

        db.commit()
        db.refresh(knowledge)

        return knowledge

    @staticmethod
    def delete(
        db: Session,
        knowledge_id: int,
    ) -> bool:
        knowledge = KnowledgeService.get_by_id(
            db,
            knowledge_id,
        )

        if knowledge is None:
            return False

        db.delete(knowledge)
        db.commit()

        return True