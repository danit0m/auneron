from datetime import datetime
from datetime import timezone

from sqlalchemy import func
from sqlalchemy import or_
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

    def _apply_request_filters(
        self,
        statement,
        *,
        statuses: tuple[str, ...] | None,
        risk_levels: tuple[str, ...] | None,
        required_permissions: tuple[str, ...] | None,
        now: datetime,
    ):
        """
        Shared WHERE construction for list_requests and count_requests --
        the two must never drift into independent interpretations of
        "visible"/"actionable" (Human Attention Indicator contract).

        expires_at > now is applied unconditionally to rows whose
        persisted status is "pending" -- never to the query as a whole.
        A row with any other status always satisfies
        `status != "pending"` and is therefore never affected by this
        clause, regardless of which statuses were requested. This is
        what keeps a mixed filter (e.g. pending + approved) from
        stripping already-resolved historical rows just because
        "pending" was also requested, and keeps status=approved/
        rejected/expired/cancelled queries byte-for-byte unchanged.
        """
        if statuses is not None:
            statement = statement.where(
                ApprovalRequest.status.in_(
                    statuses
                )
            )

        statement = statement.where(
            or_(
                ApprovalRequest.status
                != "pending",
                ApprovalRequest.expires_at
                > now,
            )
        )

        if risk_levels is not None:
            statement = statement.where(
                ApprovalRequest.risk_level.in_(
                    risk_levels
                )
            )

        if required_permissions is not None:
            statement = statement.where(
                ApprovalRequest.required_permission.in_(
                    required_permissions
                )
            )

        return statement

    def list_requests(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        risk_levels: tuple[str, ...] | None = None,
        required_permissions: tuple[str, ...] | None = None,
        after_id: int | None = None,
        limit: int = 51,
        now: datetime | None = None,
    ) -> list[ApprovalRequest]:
        if (
            required_permissions is not None
            and not required_permissions
        ):
            return []

        effective_now = (
            now
            if now is not None
            else datetime.now(timezone.utc)
        )

        statement = self._apply_request_filters(
            select(ApprovalRequest),
            statuses=statuses,
            risk_levels=risk_levels,
            required_permissions=required_permissions,
            now=effective_now,
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

    def count_requests(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        risk_levels: tuple[str, ...] | None = None,
        required_permissions: tuple[str, ...] | None = None,
        now: datetime | None = None,
    ) -> int:
        """
        Real SELECT COUNT in the database -- never len(list_requests(...)).
        Sharing list_requests's cursor/limit would silently truncate the
        count once pending requests exceed a page size.
        """
        if (
            required_permissions is not None
            and not required_permissions
        ):
            return 0

        effective_now = (
            now
            if now is not None
            else datetime.now(timezone.utc)
        )

        statement = self._apply_request_filters(
            select(
                func.count(
                    ApprovalRequest.id
                )
            ),
            statuses=statuses,
            risk_levels=risk_levels,
            required_permissions=required_permissions,
            now=effective_now,
        )

        return self.db.execute(
            statement
        ).scalar_one()

    def list_approved_agent_requests_without_consumption(
        self,
        *,
        limit: int,
        after_id: int = 0,
    ) -> list[ApprovalRequest]:
        """
        after_id is a transient, caller-owned pagination cursor -- it
        is never persisted by this repository. Passing after_id=0
        (the default) starts from the beginning, exactly as before
        this parameter existed.
        """
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
                ApprovalRequest.id > after_id,
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
