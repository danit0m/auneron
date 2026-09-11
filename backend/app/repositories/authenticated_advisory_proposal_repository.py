import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)


_EPISODE_ATTEMPT_KEY_PATTERN = re.compile(
    r"^conta_vencida:"
    r"(?P<account_id>[1-9][0-9]*):"
    r"(?P<due_date>\d{4}-\d{2}-\d{2}):"
    r"attempt:"
    r"(?P<attempt>[1-9][0-9]*)$"
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

    def find_by_system_idempotency(
        self,
        *,
        authority_user_id: int,
        idempotency_key: str,
    ) -> AuthenticatedAdvisoryProposal | None:
        """
        Lookup for the system_principal provenance -- mirrors
        find_by_idempotency() but has no auth_session_id, matching the
        partial unique index uq_authenticated_advisory_proposals_
        system_principal_key(authority_user_id, idempotency_key)
        WHERE authority_source = 'system_principal'.
        """
        statement = select(
            AuthenticatedAdvisoryProposal
        ).where(
            AuthenticatedAdvisoryProposal.authority_user_id
            == authority_user_id,
            AuthenticatedAdvisoryProposal.authority_source
            == "system_principal",
            AuthenticatedAdvisoryProposal.idempotency_key
            == idempotency_key,
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def find_legacy_proposal(
        self,
        *,
        account_id: int,
    ) -> AuthenticatedAdvisoryProposal | None:
        """
        Amendment A5. Looks up the pre-F1 legacy proposal shape
        (idempotency_key == "conta_vencida:{account_id}", no due_date/
        attempt suffix) so the F1 episode/attempt state machine can
        see and respect it before ever creating a competing
        conta_vencida:{account_id}:{due_date}:attempt:1 for the same
        account. Scoped to authority_source='authenticated_http_session'
        -- that legacy shape was only ever produced by the pre-F1
        human-session detect-overdue route.
        """
        statement = select(
            AuthenticatedAdvisoryProposal
        ).where(
            AuthenticatedAdvisoryProposal.authority_source
            == "authenticated_http_session",
            AuthenticatedAdvisoryProposal.idempotency_key
            == f"conta_vencida:{account_id}",
        )
        return self.db.execute(
            statement
        ).scalars().first()

    def find_latest_episode_attempt(
        self,
        *,
        authority_user_id: int,
        account_id: int,
        due_date: str,
    ) -> AuthenticatedAdvisoryProposal | None:
        """
        Returns the highest-attempt system_principal proposal for one
        overdue episode (account_id, due_date), or None if no attempt
        exists yet for this episode.

        The attempt ordinal lives inside the idempotency_key text
        (conta_vencida:{account_id}:{due_date}:attempt:{n}), so
        candidates are fetched by exact episode prefix, then each key
        is parsed through the strict regex and the ordinal is compared
        numerically -- never by lexical/textual ordering of the key
        string. Expected volume per episode is small (one row per
        attempt), so this is a plain fetch-then-compare, not a
        database-side numeric sort.
        """
        prefix = (
            f"conta_vencida:{account_id}:{due_date}:attempt:"
        )
        statement = select(
            AuthenticatedAdvisoryProposal
        ).where(
            AuthenticatedAdvisoryProposal.authority_user_id
            == authority_user_id,
            AuthenticatedAdvisoryProposal.authority_source
            == "system_principal",
            AuthenticatedAdvisoryProposal.idempotency_key.like(
                f"{prefix}%"
            ),
        )
        candidates = self.db.execute(
            statement
        ).scalars().all()

        latest: AuthenticatedAdvisoryProposal | None = None
        latest_attempt = 0

        for candidate in candidates:
            match = _EPISODE_ATTEMPT_KEY_PATTERN.fullmatch(
                candidate.idempotency_key
            )
            if match is None:
                continue
            if (
                int(match["account_id"]) != account_id
                or match["due_date"] != due_date
            ):
                continue
            attempt = int(match["attempt"])
            if attempt > latest_attempt:
                latest_attempt = attempt
                latest = candidate

        return latest
