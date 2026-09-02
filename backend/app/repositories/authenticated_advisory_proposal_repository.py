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

    def find_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> AuthenticatedAdvisoryProposal | None:
        """
        Busca uma proposta existente pelo idempotency_key, ignorando
        authority_user_id e auth_session_id.

        Usada pela detecção real de vencimento (25Q.0-light): a
        varredura agendada roda sob sessões de autenticação distintas
        a cada execução (a AuthSession expira), então a deduplicação
        por (authority_user_id, auth_session_id, idempotency_key) de
        find_by_idempotency() não é suficiente entre execuções. Aqui
        a chave é estável por conta ("conta_vencida:{account.id}"),
        e uma única correspondência já basta para não duplicar a
        proposta em execuções futuras.
        """
        statement = select(
            AuthenticatedAdvisoryProposal
        ).where(
            AuthenticatedAdvisoryProposal.idempotency_key
            == idempotency_key,
        )
        return self.db.execute(
            statement
        ).scalars().first()
