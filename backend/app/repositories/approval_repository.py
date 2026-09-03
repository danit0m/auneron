from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest


class ApprovalRepository:
    """
    Persistência SQLAlchemy do domínio de aprovação.

    Esta camada executa statements e flush, mas nunca commit,
    rollback, begin ou begin_nested. A fronteira transacional
    pertence aos serviços de domínio que orquestram a operação.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def add_request(
        self,
        request: ApprovalRequest,
    ) -> ApprovalRequest:
        self.db.add(request)
        self.db.flush()
        return request

    def get_request(
        self,
        request_id: int,
    ) -> ApprovalRequest | None:
        return self.db.get(
            ApprovalRequest,
            request_id,
        )

    def lock_request(
        self,
        request_id: int,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.id
                == request_id
            )
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def find_request_by_idempotency(
        self,
        *,
        requester_actor_type: str,
        requester_reference: str,
        idempotency_key: str,
        for_update: bool = False,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.requester_actor_type
                == requester_actor_type,
                ApprovalRequest.requester_reference
                == requester_reference,
                ApprovalRequest.idempotency_key
                == idempotency_key,
            )
        )
        if for_update:
            statement = statement.with_for_update()

        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def add_decision(
        self,
        decision: ApprovalDecision,
    ) -> ApprovalDecision:
        self.db.add(decision)
        self.db.flush()
        return decision

    def get_decision(
        self,
        request_id: int,
    ) -> ApprovalDecision | None:
        statement = (
            select(ApprovalDecision)
            .where(
                ApprovalDecision.approval_request_id
                == request_id
            )
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()
    def add_consumption(
        self,
        consumption: ApprovalConsumption,
    ) -> ApprovalConsumption:
        self.db.add(
            consumption
        )
        self.db.flush()
        return consumption

    def get_consumption_by_request(
        self,
        request_id: int,
    ) -> ApprovalConsumption | None:
        statement = (
            select(ApprovalConsumption)
            .where(
                ApprovalConsumption.approval_request_id
                == request_id
            )
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def lock_consumption(
        self,
        consumption_id: int,
    ) -> ApprovalConsumption | None:
        statement = (
            select(ApprovalConsumption)
            .where(
                ApprovalConsumption.id
                == consumption_id
            )
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_requests(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        risk_levels: tuple[str, ...] | None = None,
        required_permissions: tuple[str, ...] | None = None,
        after_id: int | None = None,
        limit: int = 51,
    ) -> list[ApprovalRequest]:
        statement = select(
            ApprovalRequest
        )

        if statuses is not None:
            statement = statement.where(
                ApprovalRequest.status.in_(
                    statuses
                )
            )

        if risk_levels is not None:
            statement = statement.where(
                ApprovalRequest.risk_level.in_(
                    risk_levels
                )
            )

        if required_permissions is not None:
            if not required_permissions:
                return []
            statement = statement.where(
                ApprovalRequest.required_permission.in_(
                    required_permissions
                )
            )

        if after_id is not None:
            statement = statement.where(
                ApprovalRequest.id
                > after_id
            )

        statement = (
            statement
            .order_by(
                ApprovalRequest.id.asc()
            )
            .limit(limit)
        )

        return list(
            self.db.execute(
                statement
            ).scalars().all()
        )

    def list_approved_agent_requests_without_consumption(
        self,
        *,
        limit: int,
    ) -> list[ApprovalRequest]:
        statement = (
            select(ApprovalRequest)
            .outerjoin(
                ApprovalConsumption,
                ApprovalConsumption.approval_request_id
                == ApprovalRequest.id,
            )
            .where(
                ApprovalRequest.status == "approved",
                ApprovalRequest.requester_actor_type == "agent",
                ApprovalConsumption.id.is_(None),
            )
            .order_by(
                ApprovalRequest.id.asc()
            )
            .limit(limit)
        )
        return list(
            self.db.execute(
                statement
            ).scalars().all()
        )
