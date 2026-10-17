from datetime import datetime

from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.intelligence_budget import IntelligenceBudget
from app.models.intelligence_budget import IntelligenceModelCall


_BUCKETS = (
    "reserved_micro",
    "reserved_tokens",
    "settled_micro",
    "settled_tokens",
    "unknown_micro",
    "unknown_tokens",
    "overrun_micro",
    "overrun_tokens",
)

BPS_DENOMINATOR = 10_000


class IntelligenceBudgetRepository:
    """
    Transaction-free persistence for the intelligence budget ledger.

    Only add/flush/select/update: commit and rollback belong to the
    caller (BudgetLedger), one database transaction per transition.
    """

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add_budget(
        self,
        budget: IntelligenceBudget,
    ) -> IntelligenceBudget:
        self.db.add(budget)
        self.db.flush()
        return budget

    def get_budget(
        self,
        budget_id: int,
    ) -> IntelligenceBudget | None:
        return self.db.get(IntelligenceBudget, budget_id)

    def read_budget_fresh(
        self,
        budget_id: int,
    ) -> IntelligenceBudget | None:
        return self.db.execute(
            select(IntelligenceBudget)
            .where(IntelligenceBudget.id == budget_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def try_reserve(
        self,
        budget_id: int,
        *,
        micro: int,
        tokens: int,
        now: datetime,
        unknown_fraction_bps: int,
    ) -> bool:
        """
        BI-2: atomic admission. The predicate lives in the UPDATE's
        WHERE so concurrent writers re-evaluate it against the
        committed row after the row lock is released (READ COMMITTED),
        never against a stale read.

            R + S + U + O + r <= L          (money and tokens)
            U < theta * L                    (unknown breaker)
            now inside [period_start, period_end) when periodic
        """
        table = IntelligenceBudget
        result = self.db.execute(
            update(table)
            .where(
                and_(
                    table.id == budget_id,
                    table.reserved_micro
                    + table.settled_micro
                    + table.unknown_micro
                    + table.overrun_micro
                    + micro
                    <= table.limit_micro,
                    table.reserved_tokens
                    + table.settled_tokens
                    + table.unknown_tokens
                    + table.overrun_tokens
                    + tokens
                    <= table.limit_tokens,
                    table.unknown_micro * BPS_DENOMINATOR
                    < unknown_fraction_bps * table.limit_micro,
                    table.unknown_tokens * BPS_DENOMINATOR
                    < unknown_fraction_bps * table.limit_tokens,
                    or_(
                        table.period_start.is_(None),
                        and_(
                            table.period_start <= now,
                            now < table.period_end,
                        ),
                    ),
                )
            )
            .values(
                reserved_micro=table.reserved_micro + micro,
                reserved_tokens=table.reserved_tokens + tokens,
                version=table.version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def apply_deltas(
        self,
        budget_id: int,
        *,
        now: datetime,
        **deltas: int,
    ) -> None:
        """
        Applies signed bucket deltas. Database CHECK constraints
        (BI-1) reject any result that is negative or exceeds the
        limit; the caller treats IntegrityError as a rollback.
        """
        unknown = set(deltas) - set(_BUCKETS)

        if unknown:
            raise ValueError(f"Unknown bucket(s): {sorted(unknown)}")

        table = IntelligenceBudget
        values = {
            name: getattr(table, name) + delta
            for name, delta in deltas.items()
        }
        values["version"] = table.version + 1
        values["updated_at"] = now

        result = self.db.execute(
            update(table)
            .where(table.id == budget_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            raise LookupError("Budget not found.")

    def add_call(
        self,
        call: IntelligenceModelCall,
    ) -> IntelligenceModelCall:
        self.db.add(call)
        self.db.flush()
        return call

    def get_call_for_update(
        self,
        call_id: int,
    ) -> IntelligenceModelCall | None:
        return self.db.execute(
            select(IntelligenceModelCall)
            .where(IntelligenceModelCall.id == call_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_call(
        self,
        call_id: int,
    ) -> IntelligenceModelCall | None:
        return self.db.execute(
            select(IntelligenceModelCall)
            .where(IntelligenceModelCall.id == call_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_call_by_key(
        self,
        call_key: str,
    ) -> IntelligenceModelCall | None:
        return self.db.execute(
            select(IntelligenceModelCall).where(
                IntelligenceModelCall.call_key == call_key
            )
        ).scalar_one_or_none()

    def list_call_ids_due(
        self,
        *,
        state: str,
        deadline_before: datetime,
        limit: int = 500,
    ) -> list[int]:
        return list(
            self.db.execute(
                select(IntelligenceModelCall.id)
                .where(
                    IntelligenceModelCall.state == state,
                    IntelligenceModelCall.deadline_at
                    < deadline_before,
                )
                .order_by(IntelligenceModelCall.id)
                .limit(limit)
            ).scalars()
        )

    def list_calls(
        self,
        *,
        budget_id: int,
    ) -> list[IntelligenceModelCall]:
        return list(
            self.db.execute(
                select(IntelligenceModelCall)
                .where(
                    or_(
                        IntelligenceModelCall.user_budget_id
                        == budget_id,
                        IntelligenceModelCall.objective_budget_id
                        == budget_id,
                        IntelligenceModelCall.deployment_budget_id
                        == budget_id,
                    )
                )
                .order_by(IntelligenceModelCall.id)
                .execution_options(populate_existing=True)
            ).scalars()
        )
