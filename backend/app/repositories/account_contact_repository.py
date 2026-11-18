from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account_contact_event import AccountContactEvent


class AccountContactRepository:
    """
    A5 -- repositorio SOMENTE de inclusao e leitura de
    account_contact_events. Nao existe metodo de update nem de delete
    (append-only garantido na aplicacao).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def find_by_idempotency_key(
        self, idempotency_key: str
    ) -> AccountContactEvent | None:
        return self.db.execute(
            select(AccountContactEvent).where(
                AccountContactEvent.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()

    def add(
        self,
        *,
        account_id: int,
        event_type: str,
        promised_date: date | None,
        note: str | None,
        actor_user_id: int,
        idempotency_key: str,
    ) -> AccountContactEvent:
        event = AccountContactEvent(
            account_id=account_id,
            event_type=event_type,
            promised_date=promised_date,
            note=note,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )
        self.db.add(event)
        self.db.flush()
        self.db.refresh(event)
        return event

    def list_for_account(
        self, account_id: int
    ) -> list[AccountContactEvent]:
        """Historico, mais recente primeiro."""
        return list(
            self.db.execute(
                select(AccountContactEvent)
                .where(AccountContactEvent.account_id == account_id)
                .order_by(
                    AccountContactEvent.created_at.desc(),
                    AccountContactEvent.id.desc(),
                )
            ).scalars()
        )

    def list_for_accounts(
        self, account_ids: list[int]
    ) -> dict[int, list[AccountContactEvent]]:
        """Uma unica consulta em lote (sem N+1), em ordem cronologica."""
        grouped: dict[int, list[AccountContactEvent]] = {}
        if not account_ids:
            return grouped
        rows = self.db.execute(
            select(AccountContactEvent)
            .where(AccountContactEvent.account_id.in_(account_ids))
            .order_by(AccountContactEvent.id.asc())
        ).scalars()
        for event in rows:
            grouped.setdefault(event.account_id, []).append(event)
        return grouped
