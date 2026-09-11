import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from secrets import compare_digest
from typing import Any

from sqlalchemy.orm import Session

from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionAuthorizationError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalConsumptionStaleError,
)
from app.core.advisory_proposal_errors import AdvisoryProposalNotFoundError
from app.core.advisory_proposal_errors import AdvisoryProposalValidationError
from app.core.authentication import AuthenticatedSession
from app.core.authentication import is_session_elevated
from app.core.authentication import utc_now
from app.core.authority_provenance import SystemPrincipalProvenance
from app.core.authorization import has_permission
from app.core.skill_authorization import authorize_skill_execution
from app.core.skill_errors import SkillAuthorizationError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillScopeNotFoundError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.models.auth_session import AuthSession
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.user import User
from app.repositories.authenticated_advisory_proposal_repository import (
    AuthenticatedAdvisoryProposalRepository,
)
from app.repositories.skill_repository import SkillRepository
from app.services.authenticated_advisory_proposal_service import (
    AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL,
)
from app.services.authenticated_advisory_proposal_service import (
    AUTHENTICATED_ADVISORY_PROPOSAL_SOURCE,
)
from app.services.authenticated_advisory_proposal_service import MAX_AGENTS
from app.services.authenticated_advisory_proposal_service import MAX_BINDINGS
from app.services.authenticated_advisory_proposal_service import (
    MAX_AGENT_NAME_LENGTH,
)
from app.services.authenticated_advisory_proposal_service import (
    MAX_BINDING_TEXT_LENGTH,
)
from app.services.authenticated_advisory_proposal_service import (
    MAX_DECISION_NAME_LENGTH,
)
from app.services.authenticated_advisory_proposal_service import (
    MAX_SNAPSHOT_BYTES,
)
from app.services.authenticated_advisory_proposal_service import (
    SYSTEM_ADVISORY_PROPOSAL_PROTOCOL,
)
from app.services.authenticated_advisory_proposal_service import (
    SYSTEM_ADVISORY_PROPOSAL_SOURCE,
)


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_MODES = {"read_only", "mutating", "external"}
_RUNTIME_KINDS = {"internal_python", "plugin"}

SYSTEM_PRINCIPAL_ALLOWED_EVENT = "conta_vencida"
SYSTEM_PRINCIPAL_ALLOWED_SKILL_KEY = "account.mark_overdue"

_EPISODE_ATTEMPT_KEY_PATTERN = re.compile(
    r"^conta_vencida:"
    r"(?P<account_id>[1-9][0-9]*):"
    r"(?P<due_date>\d{4}-\d{2}-\d{2}):"
    r"attempt:"
    r"(?P<attempt>[1-9][0-9]*)$"
)


def _validate_episode_key_correlation(
    *,
    idempotency_key: str,
    account_id: int,
    input_payload: Any,
) -> None:
    """
    The episode key is part of the episode's identity, not just an
    idempotent label. This rejects a proposal whose key talks about a
    different account/due_date than what the current candidate and
    input actually describe.
    """
    match = _EPISODE_ATTEMPT_KEY_PATTERN.fullmatch(
        idempotency_key
    )
    if match is None:
        raise AdvisoryProposalConsumptionStaleError(
            "system_principal proposal idempotency_key is not a valid "
            "overdue episode key."
        )
    if int(match["account_id"]) != account_id:
        raise AdvisoryProposalConsumptionStaleError(
            "Episode key account_id diverges from the candidate scope."
        )
    if not isinstance(input_payload, dict):
        raise AdvisoryProposalValidationError(
            "system_principal input_payload must be an object."
        )
    if match["due_date"] != input_payload.get(
        "expected_due_date"
    ):
        raise AdvisoryProposalConsumptionStaleError(
            "Episode key due_date diverges from input_payload."
        )
    if input_payload.get("expected_status") != "aberto":
        raise AdvisoryProposalValidationError(
            "system_principal expected_status must be 'aberto'."
        )


@dataclass(frozen=True)
class AuthenticatedAdvisoryProposalConsumptionValidation:
    """
    Ephemeral proof that one persisted advisory binding candidate passed
    current validation.

    This value is not an authority token, cannot be reused as authorization,
    and does not permit Skill execution, Work/Approval mutation, or dispatch.
    """

    proposal_id: int
    snapshot_digest: str
    authority_user_id: int
    auth_session_id: int
    agent_name: str
    binding_id: int
    skill_version_id: int
    skill_id: int
    binding_priority: int
    execution_mode: str
    runtime_kind: str
    account_id: int | None
    subject_user_id: int | None


@dataclass(frozen=True)
class SystemAdvisoryProposalConsumptionValidation:
    """
    Ephemeral proof that one persisted system_principal advisory
    binding candidate passed current validation.

    Sibling of AuthenticatedAdvisoryProposalConsumptionValidation, not
    a generalization of it -- there is no auth_session_id field here,
    by construction, not as an optional/None value.

    This value is not an authority token, cannot be reused as
    authorization, and does not permit Skill execution, Work/Approval
    mutation, or dispatch.
    """

    proposal_id: int
    snapshot_digest: str
    authority_user_id: int
    agent_name: str
    binding_id: int
    skill_version_id: int
    skill_id: int
    binding_priority: int
    execution_mode: str
    runtime_kind: str
    account_id: int
    subject_user_id: int | None


@dataclass(frozen=True)
class _SnapshotBinding:
    agent_name: str
    binding_id: int
    skill_version_id: int
    skill_id: int
    binding_priority: int
    execution_mode: str
    runtime_kind: str


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


def _authority_id(
    value: object,
    *,
    field_name: str,
) -> int:
    try:
        return _positive_id(
            value,
            field_name=field_name,
        )
    except AdvisoryProposalValidationError as error:
        raise AdvisoryProposalConsumptionAuthorizationError(
            "Current authenticated authority is invalid."
        ) from error


def _bounded_snapshot_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise AdvisoryProposalConsumptionStaleError(
            f"Persisted advisory {field_name} is invalid."
        )

    normalized = value.strip()

    if (
        not normalized
        or len(normalized) > max_length
    ):
        raise AdvisoryProposalConsumptionStaleError(
            f"Persisted advisory {field_name} is invalid."
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
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted advisory snapshot is not canonical JSON."
        ) from error


def _is_expired(
    expires_at: object,
    *,
    now: datetime,
) -> bool:
    if not isinstance(expires_at, datetime):
        return True

    comparison_now = now

    if (
        expires_at.tzinfo is None
        and comparison_now.tzinfo is not None
    ):
        comparison_now = comparison_now.replace(
            tzinfo=None
        )
    elif (
        expires_at.tzinfo is not None
        and comparison_now.tzinfo is None
    ):
        comparison_now = comparison_now.replace(
            tzinfo=expires_at.tzinfo
        )

    return expires_at <= comparison_now


def _snapshot_binding_common(
    proposal: AuthenticatedAdvisoryProposal,
    *,
    binding_id: int,
) -> _SnapshotBinding:
    """
    Structural snapshot re-validation shared by the human and the
    system_principal consumption paths. Provenance/source/protocol are
    checked by the caller BEFORE this runs -- this function only knows
    about the immutable snapshot shape, never about who owns it.
    """
    payload = proposal.snapshot_payload

    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "decision_name",
            "selected_agents",
            "agents",
        }
    ):
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted advisory snapshot shape is invalid."
        )

    _bounded_snapshot_text(
        payload["decision_name"],
        field_name="decision_name",
        max_length=MAX_DECISION_NAME_LENGTH,
    )

    selected_agents = payload["selected_agents"]
    agents = payload["agents"]

    if (
        not isinstance(selected_agents, list)
        or not isinstance(agents, list)
        or len(selected_agents) > MAX_AGENTS
        or len(agents) != len(selected_agents)
    ):
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted advisory agent structure is invalid."
        )

    normalized_agents: list[str] = []
    seen_agents: set[str] = set()

    for index, value in enumerate(selected_agents):
        name = _bounded_snapshot_text(
            value,
            field_name=f"selected_agents[{index}]",
            max_length=MAX_AGENT_NAME_LENGTH,
        )
        if name in seen_agents:
            raise AdvisoryProposalConsumptionStaleError(
                "Persisted advisory selected_agents contains duplicates."
            )
        seen_agents.add(name)
        normalized_agents.append(name)

    binding_count = 0
    seen_binding_ids: set[int] = set()
    matches: list[_SnapshotBinding] = []

    for agent_index, agent in enumerate(agents):
        if (
            not isinstance(agent, dict)
            or set(agent) != {"agent_name", "bindings"}
        ):
            raise AdvisoryProposalConsumptionStaleError(
                "Persisted advisory agent entry is invalid."
            )

        agent_name = _bounded_snapshot_text(
            agent["agent_name"],
            field_name=f"agents[{agent_index}].agent_name",
            max_length=MAX_AGENT_NAME_LENGTH,
        )

        if agent_name != normalized_agents[agent_index]:
            raise AdvisoryProposalConsumptionStaleError(
                "Persisted advisory agent order or membership is stale."
            )

        bindings = agent["bindings"]

        if not isinstance(bindings, list):
            raise AdvisoryProposalConsumptionStaleError(
                "Persisted advisory bindings must be a list."
            )

        for binding_index, raw in enumerate(bindings):
            if (
                not isinstance(raw, dict)
                or set(raw)
                != {
                    "binding_id",
                    "skill_version_id",
                    "skill_id",
                    "binding_priority",
                    "execution_mode",
                    "runtime_kind",
                }
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory binding entry is invalid."
                )

            try:
                candidate_binding_id = _positive_id(
                    raw["binding_id"],
                    field_name=(
                        f"agents[{agent_index}].bindings"
                        f"[{binding_index}].binding_id"
                    ),
                )
                skill_version_id = _positive_id(
                    raw["skill_version_id"],
                    field_name="skill_version_id",
                )
                skill_id = _positive_id(
                    raw["skill_id"],
                    field_name="skill_id",
                )
            except AdvisoryProposalValidationError as error:
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory binding identity is invalid."
                ) from error

            if candidate_binding_id in seen_binding_ids:
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory snapshot contains duplicate binding ids."
                )
            seen_binding_ids.add(candidate_binding_id)

            priority = raw["binding_priority"]
            if (
                isinstance(priority, bool)
                or not isinstance(priority, int)
                or priority < 1
                or priority > 1000
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory binding priority is invalid."
                )

            execution_mode = _bounded_snapshot_text(
                raw["execution_mode"],
                field_name="execution_mode",
                max_length=MAX_BINDING_TEXT_LENGTH,
            )
            runtime_kind = _bounded_snapshot_text(
                raw["runtime_kind"],
                field_name="runtime_kind",
                max_length=MAX_BINDING_TEXT_LENGTH,
            )

            if execution_mode not in _EXECUTION_MODES:
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory execution_mode is invalid."
                )
            if runtime_kind not in _RUNTIME_KINDS:
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory runtime_kind is invalid."
                )

            binding_count += 1

            if binding_count > MAX_BINDINGS:
                raise AdvisoryProposalConsumptionStaleError(
                    "Persisted advisory binding count exceeds the protocol bound."
                )

            if candidate_binding_id == binding_id:
                matches.append(
                    _SnapshotBinding(
                        agent_name=agent_name,
                        binding_id=candidate_binding_id,
                        skill_version_id=skill_version_id,
                        skill_id=skill_id,
                        binding_priority=priority,
                        execution_mode=execution_mode,
                        runtime_kind=runtime_kind,
                    )
                )

    canonical = _canonical_json_bytes([
        proposal.protocol,
        payload,
    ])
    digest = hashlib.sha256(
        canonical
    ).hexdigest()

    if (
        len(canonical) < 2
        or len(canonical) > MAX_SNAPSHOT_BYTES
        or not isinstance(proposal.snapshot_digest, str)
        or not _DIGEST_PATTERN.fullmatch(
            proposal.snapshot_digest
        )
        or proposal.snapshot_digest != digest
        or proposal.agent_count != len(selected_agents)
        or proposal.binding_count != binding_count
        or proposal.snapshot_bytes != len(canonical)
    ):
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted advisory proposal failed immutable validation."
        )

    if not matches:
        raise AdvisoryProposalNotFoundError(
            "Advisory proposal binding does not exist."
        )

    if len(matches) != 1:
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted advisory binding identity is ambiguous."
        )

    return matches[0]


def _snapshot_binding(
    proposal: AuthenticatedAdvisoryProposal,
    *,
    binding_id: int,
) -> _SnapshotBinding:
    if (
        proposal.authority_source
        != AUTHENTICATED_ADVISORY_PROPOSAL_SOURCE
        or proposal.protocol
        != AUTHENTICATED_ADVISORY_PROPOSAL_PROTOCOL
    ):
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted advisory proposal protocol or source is stale."
        )

    return _snapshot_binding_common(
        proposal,
        binding_id=binding_id,
    )


def _snapshot_binding_system(
    proposal: AuthenticatedAdvisoryProposal,
    *,
    binding_id: int,
) -> _SnapshotBinding:
    if (
        proposal.authority_source
        != SYSTEM_ADVISORY_PROPOSAL_SOURCE
        or proposal.protocol
        != SYSTEM_ADVISORY_PROPOSAL_PROTOCOL
        or proposal.auth_session_id is not None
    ):
        raise AdvisoryProposalConsumptionStaleError(
            "Persisted system advisory proposal protocol, source or "
            "session is stale."
        )

    return _snapshot_binding_common(
        proposal,
        binding_id=binding_id,
    )


class AuthenticatedAdvisoryProposalConsumptionService:
    """
    SELECT-only reauthorization boundary for one durable advisory candidate.

    A stored proposal is provenance and advisory metadata only. This service
    reloads current authority and current Skill catalog state, then delegates
    current scope authorization to `authorize_skill_execution`. It never invokes
    a runtime and never persists or authorizes future work.
    """

    def __init__(
        self,
        db: Session,
        *,
        proposal_repository: (
            AuthenticatedAdvisoryProposalRepository | None
        ) = None,
        skill_repository: SkillRepository | None = None,
    ) -> None:
        self.db = db
        self.proposal_repository = (
            proposal_repository
            if proposal_repository is not None
            else AuthenticatedAdvisoryProposalRepository(db)
        )
        self.skill_repository = (
            skill_repository
            if skill_repository is not None
            else SkillRepository(db)
        )

    def validate(
        self,
        *,
        proposal_id: int,
        authenticated: AuthenticatedSession,
        binding_id: int,
        input_payload: Any,
    ) -> AuthenticatedAdvisoryProposalConsumptionValidation:
        normalized_proposal_id = _positive_id(
            proposal_id,
            field_name="proposal_id",
        )
        normalized_binding_id = _positive_id(
            binding_id,
            field_name="binding_id",
        )

        if not isinstance(
            authenticated,
            AuthenticatedSession,
        ):
            raise AdvisoryProposalValidationError(
                "authenticated must be an AuthenticatedSession."
            )

        caller_user_id = _authority_id(
            authenticated.user.id,
            field_name="authenticated.user.id",
        )
        caller_session_id = _authority_id(
            authenticated.session.id,
            field_name="authenticated.session.id",
        )
        caller_session_user_id = _authority_id(
            authenticated.session.user_id,
            field_name="authenticated.session.user_id",
        )
        caller_token_hash = authenticated.session.token_hash

        if (
            caller_session_user_id != caller_user_id
            or not isinstance(caller_token_hash, str)
            or not caller_token_hash
        ):
            raise AdvisoryProposalConsumptionAuthorizationError(
                "Current authenticated authority is inconsistent."
            )

        with self.db.no_autoflush:
            proposal = self.proposal_repository.get_by_id(
                normalized_proposal_id
            )

            if proposal is None:
                raise AdvisoryProposalNotFoundError(
                    "Advisory proposal does not exist."
                )

            self.db.refresh(proposal)

            if (
                proposal.authority_user_id != caller_user_id
                or proposal.auth_session_id != caller_session_id
            ):
                raise AdvisoryProposalNotFoundError(
                    "Advisory proposal does not exist."
                )

            current_session = self.db.get(
                AuthSession,
                caller_session_id,
                populate_existing=True,
            )

            if current_session is None:
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "Current authentication session is unavailable."
                )

            now = utc_now()

            if (
                current_session.user_id != caller_user_id
                or current_session.revoked_at is not None
                or _is_expired(
                    current_session.expires_at,
                    now=now,
                )
                or not isinstance(
                    current_session.token_hash,
                    str,
                )
                or not compare_digest(
                    current_session.token_hash,
                    caller_token_hash,
                )
            ):
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "Current authentication session is invalid."
                )

            current_user = self.db.get(
                User,
                caller_user_id,
                populate_existing=True,
            )

            if (
                current_user is None
                or current_user.id != caller_user_id
                or not current_user.active
            ):
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "Current authenticated user is unavailable."
                )

            candidate = _snapshot_binding(
                proposal,
                binding_id=normalized_binding_id,
            )

            binding = self.skill_repository.get_binding(
                candidate.binding_id
            )

            if binding is None:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current advisory binding is unavailable."
                )

            self.db.refresh(binding)

            if (
                not binding.enabled
                or binding.agent_name != candidate.agent_name
                or binding.skill_version_id
                != candidate.skill_version_id
                or binding.priority
                != candidate.binding_priority
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Current advisory binding diverged from the proposal."
                )

            version = self.skill_repository.get_version(
                candidate.skill_version_id
            )

            if version is None:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill version is unavailable."
                )

            self.db.refresh(version)

            if (
                version.status != "published"
                or version.skill_id != candidate.skill_id
                or version.execution_mode
                != candidate.execution_mode
                or version.runtime_kind
                != candidate.runtime_kind
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill version diverged from the proposal."
                )

            skill = self.skill_repository.get_skill(
                candidate.skill_id
            )

            if skill is None:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill is unavailable."
                )

            self.db.refresh(skill)

            if (
                skill.id != candidate.skill_id
                or skill.status != "active"
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill diverged from the proposal."
                )

            try:
                grant = authorize_skill_execution(
                    db=self.db,
                    role=current_user.role,
                    actor_user_id=current_user.id,
                    session_elevated=is_session_elevated(
                        current_session
                    ),
                    version_id=candidate.skill_version_id,
                    input_payload=input_payload,
                    repository=self.skill_repository,
                )
            except SkillScopeNotFoundError as error:
                raise AdvisoryProposalNotFoundError(
                    "Advisory proposal scope does not exist."
                ) from error
            except SkillValidationError as error:
                raise AdvisoryProposalValidationError(
                    "Advisory proposal input is invalid."
                ) from error
            except SkillAuthorizationError as error:
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "Current authority cannot consume the advisory proposal."
                ) from error
            except (SkillNotFoundError, SkillStateError) as error:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill state no longer matches the proposal."
                ) from error

            if (
                grant.version.id != candidate.skill_version_id
                or grant.version.skill_id != candidate.skill_id
                or grant.version.execution_mode
                != candidate.execution_mode
                or grant.version.runtime_kind
                != candidate.runtime_kind
                or grant.skill.id != candidate.skill_id
                or grant.skill.status != "active"
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Reauthorized Skill grant diverged from the proposal."
                )

            return AuthenticatedAdvisoryProposalConsumptionValidation(
                proposal_id=proposal.id,
                snapshot_digest=proposal.snapshot_digest,
                authority_user_id=current_user.id,
                auth_session_id=current_session.id,
                agent_name=candidate.agent_name,
                binding_id=candidate.binding_id,
                skill_version_id=candidate.skill_version_id,
                skill_id=candidate.skill_id,
                binding_priority=candidate.binding_priority,
                execution_mode=candidate.execution_mode,
                runtime_kind=candidate.runtime_kind,
                account_id=grant.account_id,
                subject_user_id=grant.subject_user_id,
            )

    def validate_system_principal(
        self,
        *,
        proposal_id: int,
        principal: SystemPrincipalProvenance,
        binding_id: int,
        input_payload: Any,
    ) -> SystemAdvisoryProposalConsumptionValidation:
        """
        Sibling of validate(), for the system_principal provenance.

        Does not accept an AuthenticatedSession and does not reload an
        AuthSession -- there is none. It reloads and revalidates the
        system principal itself instead (exists, active, role=system,
        still has clients.detect_overdue), then re-runs the same kind
        of structural/skill/scope revalidation as the human path,
        additionally fail-closed to event=conta_vencida /
        skill=account.mark_overdue.
        """
        normalized_proposal_id = _positive_id(
            proposal_id,
            field_name="proposal_id",
        )
        normalized_binding_id = _positive_id(
            binding_id,
            field_name="binding_id",
        )

        if not isinstance(
            principal,
            SystemPrincipalProvenance,
        ):
            raise AdvisoryProposalValidationError(
                "principal must be a SystemPrincipalProvenance."
            )

        caller_user_id = _authority_id(
            principal.authority_user_id,
            field_name="principal.authority_user_id",
        )

        with self.db.no_autoflush:
            proposal = self.proposal_repository.get_by_id(
                normalized_proposal_id
            )

            if proposal is None:
                raise AdvisoryProposalNotFoundError(
                    "Advisory proposal does not exist."
                )

            self.db.refresh(proposal)

            if proposal.authority_user_id != caller_user_id:
                raise AdvisoryProposalNotFoundError(
                    "Advisory proposal does not exist."
                )

            current_principal = self.db.get(
                User,
                caller_user_id,
                populate_existing=True,
            )

            if (
                current_principal is None
                or current_principal.id != caller_user_id
                or not current_principal.active
                or current_principal.role != "system"
                or not has_permission(
                    current_principal.role,
                    "clients.detect_overdue",
                )
            ):
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "Current system_principal is unavailable."
                )

            candidate = _snapshot_binding_system(
                proposal,
                binding_id=normalized_binding_id,
            )

            payload = proposal.snapshot_payload
            if (
                not isinstance(payload, dict)
                or payload.get("decision_name")
                != "CONTA_VENCIDA_DETECTADA"
                or payload.get("selected_agents")
                != ["OverdueDetectionAgent"]
                or candidate.agent_name
                != "OverdueDetectionAgent"
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "system_principal overdue decision shape is stale."
                )

            mutating_count = 0
            for raw_agent in payload.get("agents", []):
                if not isinstance(raw_agent, dict):
                    raise AdvisoryProposalConsumptionStaleError(
                        "system_principal overdue agent shape is stale."
                    )
                for raw_binding in raw_agent.get("bindings", []):
                    if (
                        isinstance(raw_binding, dict)
                        and raw_binding.get("execution_mode") == "mutating"
                    ):
                        mutating_count += 1
            if mutating_count != 1:
                raise AdvisoryProposalConsumptionStaleError(
                    "system_principal overdue proposal must contain exactly "
                    "one mutating binding."
                )

            binding = self.skill_repository.get_binding(
                candidate.binding_id
            )

            if binding is None:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current advisory binding is unavailable."
                )

            self.db.refresh(binding)

            if (
                not binding.enabled
                or binding.agent_name != candidate.agent_name
                or binding.skill_version_id
                != candidate.skill_version_id
                or binding.priority
                != candidate.binding_priority
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Current advisory binding diverged from the proposal."
                )

            version = self.skill_repository.get_version(
                candidate.skill_version_id
            )

            if version is None:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill version is unavailable."
                )

            self.db.refresh(version)

            if (
                version.status != "published"
                or version.skill_id != candidate.skill_id
                or version.execution_mode
                != candidate.execution_mode
                or version.runtime_kind
                != candidate.runtime_kind
                or version.execution_mode != "mutating"
                or version.runtime_kind != "internal_python"
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill version diverged from the proposal."
                )

            skill = self.skill_repository.get_skill(
                candidate.skill_id
            )

            if skill is None:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill is unavailable."
                )

            self.db.refresh(skill)

            if (
                skill.id != candidate.skill_id
                or skill.status != "active"
                or skill.skill_key
                != SYSTEM_PRINCIPAL_ALLOWED_SKILL_KEY
            ):
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "system_principal may only consume "
                    f"{SYSTEM_PRINCIPAL_ALLOWED_SKILL_KEY}."
                )

            try:
                grant = authorize_skill_execution(
                    db=self.db,
                    role=current_principal.role,
                    actor_user_id=current_principal.id,
                    session_elevated=False,
                    version_id=candidate.skill_version_id,
                    input_payload=input_payload,
                    repository=self.skill_repository,
                )
            except SkillScopeNotFoundError as error:
                raise AdvisoryProposalNotFoundError(
                    "Advisory proposal scope does not exist."
                ) from error
            except SkillValidationError as error:
                raise AdvisoryProposalValidationError(
                    "Advisory proposal input is invalid."
                ) from error
            except SkillAuthorizationError as error:
                raise AdvisoryProposalConsumptionAuthorizationError(
                    "Current authority cannot consume the advisory proposal."
                ) from error
            except (SkillNotFoundError, SkillStateError) as error:
                raise AdvisoryProposalConsumptionStaleError(
                    "Current Skill state no longer matches the proposal."
                ) from error

            if (
                grant.version.id != candidate.skill_version_id
                or grant.version.skill_id != candidate.skill_id
                or grant.version.execution_mode
                != candidate.execution_mode
                or grant.version.runtime_kind
                != candidate.runtime_kind
                or grant.skill.id != candidate.skill_id
                or grant.skill.status != "active"
                or grant.account_id is None
            ):
                raise AdvisoryProposalConsumptionStaleError(
                    "Reauthorized Skill grant diverged from the proposal."
                )

            _validate_episode_key_correlation(
                idempotency_key=proposal.idempotency_key,
                account_id=grant.account_id,
                input_payload=input_payload,
            )

            return SystemAdvisoryProposalConsumptionValidation(
                proposal_id=proposal.id,
                snapshot_digest=proposal.snapshot_digest,
                authority_user_id=current_principal.id,
                agent_name=candidate.agent_name,
                binding_id=candidate.binding_id,
                skill_version_id=candidate.skill_version_id,
                skill_id=candidate.skill_id,
                binding_priority=candidate.binding_priority,
                execution_mode=candidate.execution_mode,
                runtime_kind=candidate.runtime_kind,
                account_id=grant.account_id,
                subject_user_id=grant.subject_user_id,
            )
