from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)


class AuthenticatedAdvisoryProposalRepository:
    """
    Transaction-free persistence for immutable authenticated advisory proposals.

    This repository may execute statements and flush, but transaction ownership
    remains in the service layer.
    """

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add(
        self,
        proposal: AuthenticatedAdvisoryProposal,
    ) -> AuthenticatedAdvisoryProposal:
        self.db.add(proposal)
        self.db.flush()
        return proposal

    def get_by_id(
        self,
        proposal_id: int,
    ) -> AuthenticatedAdvisoryProposal | None:
        return self.db.get(
            AuthenticatedAdvisoryProposal,
            proposal_id,
        )

    def find_by_idempotency(
        self,
        *,
        authority_user_id: int,
        auth_session_id: int,
        idempotency_key: str,
    ) -> AuthenticatedAdvisoryProposal | None:
        statement = select(
            AuthenticatedAdvisoryProposal
        ).where(
            AuthenticatedAdvisoryProposal.authority_user_id
            == authority_user_id,
            AuthenticatedAdvisoryProposal.auth_session_id
            == auth_session_id,
            AuthenticatedAdvisoryProposal.idempotency_key
            == idempotency_key,
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()
