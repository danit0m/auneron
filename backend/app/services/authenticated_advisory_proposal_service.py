import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.advisory_proposal_errors import (
    AdvisoryProposalConflictError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalIdempotencyConflictError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalValidationError,
)
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.orchestrator.advisory_envelope import (
    AuthenticatedAdvisoryEnvelope,
)
from app.repositories.authenticated_advisory_proposal_repository import (
    AuthenticatedAdvisoryProposalRepository,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBinding,
)


AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL = "authenticated_advisory_v1"
AUTHENTICATED_ADVISORY_PROPOSAL_SOURCE = "authenticated_http_session"

IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:-]{0,254}$"
)

MAX_AGENTS = 32
MAX_BINDINGS = 512
MAX_SNAPSHOT_BYTES = 65536
MAX_DECISION_NAME_LENGTH = 128
MAX_AGENT_NAME_LENGTH = 128
MAX_BINDING_TEXT_LENGTH = 64


@dataclass(frozen=True)
class AuthenticatedAdvisoryProposalCreationResult:
    proposal: AuthenticatedAdvisoryProposal
    created: bool
    duplicate: bool


def _positive_id(
    value: object,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise AdvisoryProposalValidationError(
            f"{field_name} must be a positive integer."
        )
    return value


def _bounded_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise AdvisoryProposalValidationError(
            f"{field_name} must be text."
        )

    normalized = value.strip()

    if not normalized or len(normalized) > max_length:
        raise AdvisoryProposalValidationError(
            f"{field_name} must be non-blank and at most "
            f"{max_length} characters."
        )

    return normalized


def _normalize_idempotency_key(
    value: object,
) -> str:
    if not isinstance(value, str):
        raise AdvisoryProposalValidationError(
            "idempotency_key must be text."
        )

    normalized = value.strip().lower()

    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(normalized):
        raise AdvisoryProposalValidationError(
            "idempotency_key is invalid."
        )

    return normalized


def _canonical_json_bytes(
    value: Any,
) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AdvisoryProposalValidationError(
            "advisory snapshot is not canonical JSON."
        ) from error


def _snapshot_from_envelope(
    envelope: AuthenticatedAdvisoryEnvelope,
) -> tuple[dict[str, Any], str, int, int, int]:
    if not isinstance(
        envelope,
        AuthenticatedAdvisoryEnvelope,
    ):
        raise AdvisoryProposalValidationError(
            "envelope must be an AuthenticatedAdvisoryEnvelope."
        )

    decision_name = _bounded_text(
        envelope.decision.decision_name,
        field_name="decision_name",
        max_length=MAX_DECISION_NAME_LENGTH,
    )

    selected_agents = tuple(
        envelope.decision.selected_agents
    )

    if len(selected_agents) > MAX_AGENTS:
        raise AdvisoryProposalValidationError(
            f"selected_agents exceeds {MAX_AGENTS}."
        )

    if len(set(selected_agents)) != len(selected_agents):
        raise AdvisoryProposalValidationError(
            "selected_agents contains duplicates."
        )

    if len(envelope.plan.agents) != len(selected_agents):
        raise AdvisoryProposalValidationError(
            "advisory plan agent count diverges from selected_agents."
        )

    normalized_selected_agents: list[str] = []
    normalized_agents: list[dict[str, Any]] = []
    binding_count = 0

    for index, agent_name in enumerate(selected_agents):
        normalized_agent_name = _bounded_text(
            agent_name,
            field_name=f"selected_agents[{index}]",
            max_length=MAX_AGENT_NAME_LENGTH,
        )

        planned_agent = envelope.plan.agents[index]

        if planned_agent.agent_name != agent_name:
            raise AdvisoryProposalValidationError(
                "advisory plan order/membership diverges from selected_agents."
            )

        normalized_selected_agents.append(
            normalized_agent_name
        )

        normalized_bindings: list[dict[str, Any]] = []

        for binding_index, binding in enumerate(
            planned_agent.bindings
        ):
            if not isinstance(
                binding,
                AdvisorySkillBinding,
            ):
                raise AdvisoryProposalValidationError(
                    "advisory binding has an invalid type."
                )

            if binding.agent_name != agent_name:
                raise AdvisoryProposalValidationError(
                    "advisory binding agent does not match its plan agent."
                )

            binding_count += 1

            if binding_count > MAX_BINDINGS:
                raise AdvisoryProposalValidationError(
                    f"advisory bindings exceeds {MAX_BINDINGS}."
                )

            priority = binding.binding_priority

            if (
                isinstance(priority, bool)
                or not isinstance(priority, int)
            ):
                raise AdvisoryProposalValidationError(
                    "binding_priority must be an integer."
                )

            normalized_bindings.append({
                "binding_id": _positive_id(
                    binding.binding_id,
                    field_name=(
                        f"agents[{index}].bindings[{binding_index}]."
                        "binding_id"
                    ),
                ),
                "skill_version_id": _positive_id(
                    binding.skill_version_id,
                    field_name=(
                        f"agents[{index}].bindings[{binding_index}]."
                        "skill_version_id"
                    ),
                ),
                "skill_id": _positive_id(
                    binding.skill_id,
                    field_name=(
                        f"agents[{index}].bindings[{binding_index}]."
                        "skill_id"
                    ),
                ),
                "binding_priority": priority,
                "execution_mode": _bounded_text(
                    binding.execution_mode,
                    field_name="execution_mode",
                    max_length=MAX_BINDING_TEXT_LENGTH,
                ),
                "runtime_kind": _bounded_text(
                    binding.runtime_kind,
                    field_name="runtime_kind",
                    max_length=MAX_BINDING_TEXT_LENGTH,
                ),
            })

        normalized_agents.append({
            "agent_name": normalized_agent_name,
            "bindings": normalized_bindings,
        })

    payload = {
        "decision_name": decision_name,
        "selected_agents": normalized_selected_agents,
        "agents": normalized_agents,
    }

    canonical = _canonical_json_bytes([
        AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
        payload,
    ])

    snapshot_bytes = len(canonical)

    if snapshot_bytes < 2 or snapshot_bytes > MAX_SNAPSHOT_BYTES:
        raise AdvisoryProposalValidationError(
            f"advisory snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes."
        )

    return (
        payload,
        hashlib.sha256(canonical).hexdigest(),
        len(normalized_selected_agents),
        binding_count,
        snapshot_bytes,
    )


def _validate_authority(
    envelope: AuthenticatedAdvisoryEnvelope,
) -> tuple[int, int, str, str | None]:
    authority = envelope.authority

    authority_user_id = _positive_id(
        authority.authority_user_id,
        field_name="authority_user_id",
    )
    auth_session_id = _positive_id(
        authority.auth_session_id,
        field_name="auth_session_id",
    )

    if authority.source != AUTHENTICATED_ADVISORY_PROPOSAL_SOURCE:
        raise AdvisoryProposalValidationError(
            "authority source is invalid."
        )

    request_id = authority.request_id

    if request_id is not None:
        request_id = _bounded_text(
            request_id,
            field_name="request_id",
            max_length=128,
        )

    return (
        authority_user_id,
        auth_session_id,
        authority.source,
        request_id,
    )


def _validate_persisted_proposal(
    proposal: AuthenticatedAdvisoryProposal,
    *,
    authority_user_id: int,
    auth_session_id: int,
    idempotency_key: str,
    expected_digest: str,
) -> None:
    if (
        proposal.authority_user_id != authority_user_id
        or proposal.auth_session_id != auth_session_id
        or proposal.idempotency_key != idempotency_key
        or proposal.authority_source
        != AUTHENTICATED_ADVISORY_PROPOSAL_SOURCE
        or proposal.protocol
        != AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL
    ):
        raise AdvisoryProposalConflictError(
            "persisted advisory proposal identity is inconsistent."
        )

    canonical = _canonical_json_bytes([
        proposal.protocol,
        proposal.snapshot_payload,
    ])
    persisted_digest = hashlib.sha256(
        canonical
    ).hexdigest()

    agents = proposal.snapshot_payload.get(
        "selected_agents"
    ) if isinstance(
        proposal.snapshot_payload,
        dict,
    ) else None
    planned_agents = proposal.snapshot_payload.get(
        "agents"
    ) if isinstance(
        proposal.snapshot_payload,
        dict,
    ) else None

    if (
        not isinstance(agents, list)
        or not isinstance(planned_agents, list)
    ):
        raise AdvisoryProposalConflictError(
            "persisted advisory proposal snapshot is invalid."
        )

    binding_count = 0

    for agent in planned_agents:
        if not isinstance(agent, dict):
            raise AdvisoryProposalConflictError(
                "persisted advisory proposal snapshot is invalid."
            )
        bindings = agent.get("bindings")
        if not isinstance(bindings, list):
            raise AdvisoryProposalConflictError(
                "persisted advisory proposal snapshot is invalid."
            )
        binding_count += len(bindings)

    if (
        proposal.snapshot_digest != persisted_digest
        or proposal.agent_count != len(agents)
        or proposal.binding_count != binding_count
        or proposal.snapshot_bytes != len(canonical)
    ):
        raise AdvisoryProposalConflictError(
            "persisted advisory proposal failed immutable validation."
        )

    if proposal.snapshot_digest != expected_digest:
        raise AdvisoryProposalIdempotencyConflictError(
            "idempotency_key was reused for different advisory content."
        )


class AuthenticatedAdvisoryProposalService:
    """
    Durable immutable persistence boundary for authenticated advisory context.

    Persisting a proposal grants no authority and creates no executable intent.
    """

    def __init__(
        self,
        db: Session,
        *,
        repository: (
            AuthenticatedAdvisoryProposalRepository | None
        ) = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else AuthenticatedAdvisoryProposalRepository(db)
        )

    def create(
        self,
        *,
        envelope: AuthenticatedAdvisoryEnvelope,
        idempotency_key: str,
    ) -> AuthenticatedAdvisoryProposalCreationResult:
        normalized_key = _normalize_idempotency_key(
            idempotency_key
        )

        (
            authority_user_id,
            auth_session_id,
            authority_source,
            request_id,
        ) = _validate_authority(envelope)

        (
            snapshot_payload,
            snapshot_digest,
            agent_count,
            binding_count,
            snapshot_bytes,
        ) = _snapshot_from_envelope(envelope)

        existing = self.repository.find_by_idempotency(
            authority_user_id=authority_user_id,
            auth_session_id=auth_session_id,
            idempotency_key=normalized_key,
        )

        if existing is not None:
            _validate_persisted_proposal(
                existing,
                authority_user_id=authority_user_id,
                auth_session_id=auth_session_id,
                idempotency_key=normalized_key,
                expected_digest=snapshot_digest,
            )
            return AuthenticatedAdvisoryProposalCreationResult(
                proposal=existing,
                created=False,
                duplicate=True,
            )

        proposal = AuthenticatedAdvisoryProposal(
            authority_user_id=authority_user_id,
            auth_session_id=auth_session_id,
            authority_source=authority_source,
            request_id=request_id,
            idempotency_key=normalized_key,
            protocol=AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
            snapshot_payload=snapshot_payload,
            snapshot_digest=snapshot_digest,
            agent_count=agent_count,
            binding_count=binding_count,
            snapshot_bytes=snapshot_bytes,
        )

        try:
            self.repository.add(
                proposal
            )
            self.db.commit()
            self.db.refresh(
                proposal
            )
        except IntegrityError as error:
            self.db.rollback()
            concurrent = self.repository.find_by_idempotency(
                authority_user_id=authority_user_id,
                auth_session_id=auth_session_id,
                idempotency_key=normalized_key,
            )

            if concurrent is None:
                raise AdvisoryProposalConflictError(
                    "concurrent advisory proposal persistence conflicted."
                ) from error

            _validate_persisted_proposal(
                concurrent,
                authority_user_id=authority_user_id,
                auth_session_id=auth_session_id,
                idempotency_key=normalized_key,
                expected_digest=snapshot_digest,
            )
            return AuthenticatedAdvisoryProposalCreationResult(
                proposal=concurrent,
                created=False,
                duplicate=True,
            )
        except Exception:
            self.db.rollback()
            raise

        _validate_persisted_proposal(
            proposal,
            authority_user_id=authority_user_id,
            auth_session_id=auth_session_id,
            idempotency_key=normalized_key,
            expected_digest=snapshot_digest,
        )

        return AuthenticatedAdvisoryProposalCreationResult(
            proposal=proposal,
            created=True,
            duplicate=False,
        )
