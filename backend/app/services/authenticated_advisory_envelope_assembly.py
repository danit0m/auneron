from typing import Any

from app.core.authentication import AuthenticatedSession
from app.core.authority_provenance import (
    authority_provenance_from_authenticated_session,
)
from app.orchestrator.advisory_envelope import (
    AuthenticatedAdvisoryEnvelope,
)
from app.orchestrator.orchestrator import AIOrchestrator
from app.services.orchestrator_skill_binding_projection import (
    OrchestratorSkillBindingProjectionService,
)


ADVISORY_EVENT_NAME_MAX_LENGTH = 128


class AuthenticatedAdvisoryEnvelopeAssemblyService:
    """
    Internal non-routed assembly of authenticated advisory context.

    This service composes existing observe-only decision data, SELECT-only
    advisory Skill projection and server-derived authority provenance. It does
    not persist, authorize or execute anything.
    """

    def __init__(
        self,
        projection_service: OrchestratorSkillBindingProjectionService,
    ) -> None:
        if not isinstance(
            projection_service,
            OrchestratorSkillBindingProjectionService,
        ):
            raise TypeError(
                "projection_service must be an "
                "OrchestratorSkillBindingProjectionService."
            )

        self.projection_service = projection_service

    def assemble(
        self,
        *,
        authenticated: AuthenticatedSession,
        event_name: str,
        payload: dict[str, Any],
        request_id: str | None = None,
    ) -> AuthenticatedAdvisoryEnvelope:
        if not isinstance(
            authenticated,
            AuthenticatedSession,
        ):
            raise TypeError(
                "authenticated must be an AuthenticatedSession."
            )

        if not isinstance(event_name, str):
            raise TypeError(
                "event_name must be a string."
            )

        normalized_event_name = event_name.strip()

        if (
            not normalized_event_name
            or len(normalized_event_name)
            > ADVISORY_EVENT_NAME_MAX_LENGTH
        ):
            raise ValueError(
                "event_name must be non-blank and at most "
                f"{ADVISORY_EVENT_NAME_MAX_LENGTH} characters."
            )

        if not isinstance(payload, dict):
            raise TypeError(
                "payload must be a dict."
            )

        authority = (
            authority_provenance_from_authenticated_session(
                authenticated,
                request_id=request_id,
            )
        )

        decision = AIOrchestrator.observe(
            event_name=normalized_event_name,
            payload=payload,
        )

        plan = self.projection_service.resolve(
            decision
        )

        return AuthenticatedAdvisoryEnvelope(
            decision=decision,
            plan=plan,
            authority=authority,
        )
