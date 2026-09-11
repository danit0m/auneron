from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.core.authority_provenance import SystemPrincipalError
from app.core.authority_provenance import SystemPrincipalProvenance
from app.core.authority_provenance import resolve_system_principal
from app.models.account import Account
from app.models.approval import ApprovalRequest
from app.models.auth_session import AuthSession
from app.models.authenticated_advisory_proposal import AuthenticatedAdvisoryProposal
from app.orchestrator.advisory_envelope import SystemAdvisoryEnvelope
from app.orchestrator.orchestrator import AIOrchestrator
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.authenticated_advisory_proposal_repository import (
    AuthenticatedAdvisoryProposalRepository,
)
from app.repositories.skill_repository import SkillRepository
from app.services.approval_service import ApprovalService
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovalBridgeService,
)
from app.services.authenticated_advisory_proposal_service import (
    SystemAdvisoryProposalService,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBinding,
)
from app.services.orchestrator_skill_binding_projection import (
    OrchestratorSkillBindingProjectionService,
)


OVERDUE_EVENT_NAME = "conta_vencida"
OVERDUE_DECISION_NAME = "CONTA_VENCIDA_DETECTADA"
OVERDUE_AGENT_NAME = "OverdueDetectionAgent"
OVERDUE_SKILL_KEY = "account.mark_overdue"

_EPISODE_ATTEMPT_KEY_PATTERN = re.compile(
    r"^conta_vencida:"
    r"(?P<account_id>[1-9][0-9]*):"
    r"(?P<due_date>\d{4}-\d{2}-\d{2}):"
    r"attempt:"
    r"(?P<attempt>[1-9][0-9]*)$"
)


class OverdueDetectionContractError(RuntimeError):
    """Fail-closed violation of the frozen F1 overdue system contract."""


@dataclass(frozen=True)
class OverdueDetectionRunResult:
    accounts_checked: int
    proposals_created: int
    proposals_reused: int
    approvals_requested: int
    approvals_reused: int
    attempts_advanced: int
    failures: int
    legacy_active_blocked: int = 0
    principal_available: bool = True

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "contas_verificadas": self.accounts_checked,
            "propostas_criadas": self.proposals_created,
            "propostas_reutilizadas": self.proposals_reused,
            "aprovacoes_solicitadas": self.approvals_requested,
            "aprovacoes_reutilizadas": self.approvals_reused,
            "tentativas_avancadas": self.attempts_advanced,
            "falhas": self.failures,
            "bloqueadas_por_legado_ativo": self.legacy_active_blocked,
            "principal_disponivel": self.principal_available,
        }


@dataclass
class _RunCounters:
    proposals_created: int = 0
    proposals_reused: int = 0
    approvals_requested: int = 0
    approvals_reused: int = 0
    attempts_advanced: int = 0
    failures: int = 0
    legacy_active_blocked: int = 0


class OverdueDetectionService:
    """
    Internal F1 scanner for overdue Accounts.

    This service observes and proposes only. It never mutates Account.status,
    never decides Approval and never executes account.mark_overdue directly.
    The effect remains exclusively behind human Approval plus the existing
    governed execution path.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.proposals = AuthenticatedAdvisoryProposalRepository(db)
        self.approvals = ApprovalRepository(db)
        self.approval_service = ApprovalService(db)
        self.skill_repository = SkillRepository(db)
        self.projection = OrchestratorSkillBindingProjectionService(
            self.skill_repository
        )
        self.proposal_service = SystemAdvisoryProposalService(db)
        self.bridge = AuthenticatedAdvisoryProposalApprovalBridgeService(db)

    def run_scan(
        self,
        *,
        today: date | None = None,
        now: datetime | None = None,
    ) -> OverdueDetectionRunResult:
        effective_today = today if today is not None else date.today()
        effective_now = now if now is not None else datetime.now(timezone.utc)
        if effective_now.tzinfo is None:
            raise ValueError("now must be timezone-aware.")

        try:
            principal = resolve_system_principal(self.db)
        except SystemPrincipalError:
            logging.getLogger(
                "auneron.overdue_detection"
            ).warning(
                "overdue_detection_principal_unavailable",
                extra={
                    "event": (
                        "overdue.detection.principal_unavailable"
                    ),
                },
            )
            return OverdueDetectionRunResult(
                accounts_checked=0,
                proposals_created=0,
                proposals_reused=0,
                approvals_requested=0,
                approvals_reused=0,
                attempts_advanced=0,
                failures=0,
                principal_available=False,
            )

        overdue_accounts = (
            self.db.query(Account)
            .filter(
                Account.status == "aberto",
                Account.vencimento < effective_today,
            )
            .order_by(Account.id.asc())
            .all()
        )

        counters = _RunCounters()

        for account in overdue_accounts:
            try:
                self._process_account(
                    account=account,
                    principal=principal,
                    now=effective_now,
                    counters=counters,
                )
            except Exception:
                self.db.rollback()
                counters.failures += 1

        return OverdueDetectionRunResult(
            accounts_checked=len(overdue_accounts),
            proposals_created=counters.proposals_created,
            proposals_reused=counters.proposals_reused,
            approvals_requested=counters.approvals_requested,
            approvals_reused=counters.approvals_reused,
            attempts_advanced=counters.attempts_advanced,
            failures=counters.failures,
            legacy_active_blocked=counters.legacy_active_blocked,
        )

    def _process_account(
        self,
        *,
        account: Account,
        principal: SystemPrincipalProvenance,
        now: datetime,
        counters: _RunCounters,
    ) -> None:
        if self._blocked_by_active_legacy_proposal(
            account=account, now=now, counters=counters,
        ):
            return

        due_date = str(account.vencimento)
        latest = self.proposals.find_latest_episode_attempt(
            authority_user_id=principal.authority_user_id,
            account_id=account.id,
            due_date=due_date,
        )

        if latest is None:
            self._create_attempt(
                account=account,
                principal=principal,
                ordinal=1,
                counters=counters,
            )
            return

        ordinal = self._attempt_ordinal(latest)
        binding_id, agent_name = self._proposal_mutating_identity(latest)
        request = self.approvals.find_request_by_idempotency(
            requester_actor_type="agent",
            requester_reference=f"agent:{agent_name}",
            idempotency_key=f"advisory:{latest.id}:{binding_id}",
        )

        if request is None:
            self._request_approval(
                proposal=latest,
                account=account,
                principal=principal,
                binding_id=binding_id,
                counters=counters,
            )
            counters.proposals_reused += 1
            return

        if request.status == "pending":
            if request.expires_at <= now:
                request = self.approval_service.expire_pending_request_if_due(
                    request.id,
                    now=now,
                )
                if request.status != "expired":
                    return
                counters.attempts_advanced += 1
                self._create_attempt(
                    account=account,
                    principal=principal,
                    ordinal=ordinal + 1,
                    counters=counters,
                )
            else:
                counters.proposals_reused += 1
                counters.approvals_reused += 1
            return

        if request.status == "approved":
            counters.proposals_reused += 1
            counters.approvals_reused += 1
            return

        if request.status in {"rejected", "expired", "cancelled"}:
            counters.attempts_advanced += 1
            self._create_attempt(
                account=account,
                principal=principal,
                ordinal=ordinal + 1,
                counters=counters,
            )
            return

        raise OverdueDetectionContractError(
            f"Unsupported ApprovalRequest status: {request.status!r}."
        )

    def _blocked_by_active_legacy_proposal(
        self,
        *,
        account: Account,
        now: datetime,
        counters: _RunCounters,
    ) -> bool:
        """
        Amendment A5. A pre-F1 legacy proposal
        (conta_vencida:{account_id}, authority_source=
        authenticated_http_session) may already be observing this
        exact account. F1 must never open a competing
        conta_vencida:{account_id}:{due_date}:attempt:1 while that
        legacy cycle is still active -- I4 forbids two concurrent
        Approval cycles for the same overdue episode, and this is the
        same invariant applied across provenances, not just within
        the new one.

        Returns True (blocks this account for this scan) unless the
        legacy cycle is confirmed terminal, confirmed a permanent
        orphan (A2), or simply does not exist.
        """
        legacy = self.proposals.find_legacy_proposal(
            account_id=account.id
        )
        if legacy is None:
            return False

        legacy_binding_id, legacy_agent_name = (
            self._proposal_mutating_identity(legacy)
        )
        legacy_request = self.approvals.find_request_by_idempotency(
            requester_actor_type="agent",
            requester_reference=f"agent:{legacy_agent_name}",
            idempotency_key=f"advisory:{legacy.id}:{legacy_binding_id}",
        )

        if legacy_request is None:
            # A legacy proposal that cannot be correlated to any
            # ApprovalRequest is not something this service can
            # reason about safely -- fail closed, never open a
            # parallel attempt underneath an unknown legacy state.
            raise OverdueDetectionContractError(
                "Legacy proposal sem ApprovalRequest correlacionável "
                f"(account_id={account.id})."
            )

        if legacy_request.status == "pending":
            if legacy_request.expires_at > now:
                counters.legacy_active_blocked += 1
                return True

            expired = self.approval_service.expire_pending_request_if_due(
                legacy_request.id, now=now,
            )
            if expired.status != "expired":
                # Raced with something else deciding/expiring it in
                # the same instant -- do not proceed this cycle.
                counters.legacy_active_blocked += 1
                return True

            return False

        if legacy_request.status == "approved":
            consumption = self.approvals.get_consumption_by_request(
                legacy_request.id
            )
            if consumption is not None:
                # Recovery already owns (or has completed) this
                # legacy cycle.
                counters.legacy_active_blocked += 1
                return True

            session = (
                self.db.get(AuthSession, legacy.auth_session_id)
                if legacy.auth_session_id is not None
                else None
            )
            session_gone = (
                session is None
                or session.revoked_at is not None
                or session.expires_at <= now
            )
            if not session_gone:
                # The original human session can still be dispatched
                # normally by the existing recovery loop -- block.
                counters.legacy_active_blocked += 1
                return True

            # approved + no consumption + original session missing/
            # revoked/expired == LEGACY_PERMANENT_ORPHAN (A2). The F1
            # episode/attempt state machine is free to open attempt:1.
            return False

        if legacy_request.status in {"rejected", "expired", "cancelled"}:
            return False

        raise OverdueDetectionContractError(
            "Unsupported legacy ApprovalRequest status: "
            f"{legacy_request.status!r} (account_id={account.id})."
        )

    def _create_attempt(
        self,
        *,
        account: Account,
        principal: SystemPrincipalProvenance,
        ordinal: int,
        counters: _RunCounters,
    ) -> None:
        payload = self._account_payload(account)
        decision = AIOrchestrator.observe(
            event_name=OVERDUE_EVENT_NAME,
            payload=payload,
        )
        if (
            decision.decision_name != OVERDUE_DECISION_NAME
            or decision.selected_agents != (OVERDUE_AGENT_NAME,)
        ):
            raise OverdueDetectionContractError(
                "Overdue decision shape diverges from the frozen F1 contract."
            )

        plan = self.projection.resolve(decision)
        binding = self._validate_plan(plan)
        envelope = SystemAdvisoryEnvelope(
            decision=decision,
            plan=plan,
            authority=principal,
        )
        key = self._episode_key(account, ordinal)
        creation = self.proposal_service.create(
            envelope=envelope,
            idempotency_key=key,
        )

        if creation.created:
            counters.proposals_created += 1
        else:
            counters.proposals_reused += 1

        self._request_approval(
            proposal=creation.proposal,
            account=account,
            principal=principal,
            binding_id=binding.binding_id,
            counters=counters,
        )

    def _request_approval(
        self,
        *,
        proposal: AuthenticatedAdvisoryProposal,
        account: Account,
        principal: SystemPrincipalProvenance,
        binding_id: int,
        counters: _RunCounters,
    ) -> None:
        result = self.bridge.request_approval_system(
            proposal_id=proposal.id,
            principal=principal,
            binding_id=binding_id,
            input_payload=self._execution_input(account),
        )
        if result.duplicate:
            counters.approvals_reused += 1
        else:
            counters.approvals_requested += 1

    def _validate_plan(self, plan) -> AdvisorySkillBinding:
        mutating: list[AdvisorySkillBinding] = []
        for agent in plan.agents:
            for binding in agent.bindings:
                if binding.execution_mode == "mutating":
                    mutating.append(binding)

        if len(mutating) != 1:
            raise OverdueDetectionContractError(
                "Overdue plan must expose exactly one mutating binding."
            )

        binding = mutating[0]
        if (
            binding.agent_name != OVERDUE_AGENT_NAME
            or binding.runtime_kind != "internal_python"
        ):
            raise OverdueDetectionContractError(
                "Overdue mutating binding shape is invalid."
            )

        version = self.skill_repository.get_version(binding.skill_version_id)
        skill = self.skill_repository.get_skill(binding.skill_id)
        if (
            version is None
            or version.status != "published"
            or version.skill_id != binding.skill_id
            or version.execution_mode != "mutating"
            or version.runtime_kind != "internal_python"
            or skill is None
            or skill.status != "active"
            or skill.skill_key != OVERDUE_SKILL_KEY
        ):
            raise OverdueDetectionContractError(
                "Overdue Skill state diverges from the frozen F1 contract."
            )

        return binding

    @staticmethod
    def _proposal_mutating_identity(
        proposal: AuthenticatedAdvisoryProposal,
    ) -> tuple[int, str]:
        matches: list[tuple[int, str]] = []
        payload = proposal.snapshot_payload
        if not isinstance(payload, dict):
            raise OverdueDetectionContractError("Proposal snapshot is invalid.")
        if (
            payload.get("decision_name") != OVERDUE_DECISION_NAME
            or payload.get("selected_agents") != [OVERDUE_AGENT_NAME]
        ):
            raise OverdueDetectionContractError(
                "Proposal decision shape diverges from F1 overdue contract."
            )

        for agent in payload.get("agents", []):
            if not isinstance(agent, dict):
                raise OverdueDetectionContractError("Proposal agent is invalid.")
            agent_name = agent.get("agent_name")
            for binding in agent.get("bindings", []):
                if (
                    isinstance(binding, dict)
                    and binding.get("execution_mode") == "mutating"
                ):
                    matches.append((binding.get("binding_id"), agent_name))

        if (
            len(matches) != 1
            or not isinstance(matches[0][0], int)
            or not isinstance(matches[0][1], str)
        ):
            raise OverdueDetectionContractError(
                "Proposal must contain exactly one mutating binding."
            )
        return matches[0]

    @staticmethod
    def _attempt_ordinal(proposal: AuthenticatedAdvisoryProposal) -> int:
        match = _EPISODE_ATTEMPT_KEY_PATTERN.fullmatch(
            proposal.idempotency_key
        )
        if match is None:
            raise OverdueDetectionContractError(
                "Persisted overdue proposal has an invalid episode key."
            )
        return int(match["attempt"])

    @staticmethod
    def _episode_key(account: Account, ordinal: int) -> str:
        if ordinal < 1:
            raise OverdueDetectionContractError(
                "attempt ordinal must be positive."
            )
        return (
            f"conta_vencida:{account.id}:{account.vencimento}:"
            f"attempt:{ordinal}"
        )

    @staticmethod
    def _execution_input(account: Account) -> dict[str, object]:
        return {
            "account_id": account.id,
            "expected_status": "aberto",
            "expected_due_date": str(account.vencimento),
        }

    @staticmethod
    def _account_payload(account: Account) -> dict[str, object]:
        return {
            "id": account.id,
            "cliente": account.cliente,
            "email": account.email,
            "whatsapp": account.whatsapp,
            "valor": account.valor,
            "vencimento": str(account.vencimento),
            "status": account.status,
        }
