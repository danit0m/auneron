"""
Outcome Intelligence V1 -- projecao read-only sobre fatos ja existentes.

Nao muta nada, nao cria Knowledge/Memory, nao decide nada. Responde, para
um episodio financeiro (account_id + due_date), o que ja e observavel:
quando foi detectado, o que foi recomendado, houve aprovacao, houve
execucao, quando (e se) o pagamento ocorreu -- e cita a evidencia exata
de cada afirmacao.

Congelado em tres documentos de design freeze com Tomaz em 15/09/2026:
Architecture Freeze (D1-D10), Correlation Contract (outcome_correlation_v1,
com os 4 amendments), PRE-APPLY Final Contract / File-Set Freeze.

Nao afirma causalidade (D2): "pagamento ocorreu N dias depois" nunca
"a acao causou o pagamento". Ver Correlation Contract para a taxonomia
completa de linkage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.knowledge import Knowledge
from app.models.work import WorkItem
from app.models.work_skill_execution import WorkSkillExecution
from app.services.overdue_detection_service import (
    _EPISODE_ATTEMPT_KEY_PATTERN,
)


RULE_VERSION = "outcome_correlation_v1"

_APPROVAL_ADVISORY_KEY_PATTERN = re.compile(
    r"^advisory:"
    r"(?P<proposal_id>[1-9][0-9]*):"
    r"(?P<binding_id>[1-9][0-9]*)$"
)


Linkage = str
# "direct" | "correlated" | "absent" | "unresolved_due_date_change"

EvidenceSource = str
# "knowledge" | "authenticated_advisory_proposal" | "approval_request" |
# "approval_decision" | "work_item" | "work_skill_execution" |
# "skill_invocation" | "account_event"


@dataclass(frozen=True)
class Evidence:
    source: EvidenceSource
    id: int
    linkage: Linkage


@dataclass(frozen=True)
class FinancialEpisode:
    account_id: int
    due_date: date


@dataclass(frozen=True)
class DetectionOutcome:
    linkage: Linkage
    first_observed_at: datetime | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class RecommendationOutcome:
    linkage: Linkage
    created_at: datetime | None
    latest_proposal_attempt: int | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class ApprovalOutcome:
    linkage: Linkage
    status: str | None
    decided_at: datetime | None
    decided_by_reference: str | None
    approval_source_attempt: int | None
    attempt_mismatch: bool
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class ExecutionOutcome:
    linkage: Linkage
    status: str | None
    finished_at: datetime | None
    sourced_from_attempt: int | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class PaymentOutcome:
    linkage: Linkage
    rule_version: str
    occurred_at: datetime | None
    evidence: tuple[Evidence, ...] = ()
    candidate_evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class DerivedMetrics:
    days_detection_to_payment: int | None
    provenance_class: str = "derived"


@dataclass(frozen=True)
class AmountInfo:
    value: Decimal
    source: str = "account.valor"
    provenance_class: str = "fact"


@dataclass(frozen=True)
class OutcomeEpisodeResult:
    episode: FinancialEpisode
    detection: DetectionOutcome
    recommendation: RecommendationOutcome
    approval: ApprovalOutcome
    execution: ExecutionOutcome
    payment: PaymentOutcome
    derived: DerivedMetrics
    amount: AmountInfo


def _detect(
    db: Session,
    account_id: int,
    due_date: date,
) -> DetectionOutcome:
    prefix = (
        f"receivable_lifecycle:v1:{account_id}:"
        f"{due_date.isoformat()}:"
    )

    rows = (
        db.execute(
            select(Knowledge)
            .where(
                Knowledge.account_id == account_id,
                Knowledge.knowledge_type == "receivable_lifecycle",
                Knowledge.correlation_key.like(f"{prefix}%"),
            )
            .order_by(Knowledge.created_at.asc())
        )
        .scalars()
        .all()
    )

    if not rows:
        return DetectionOutcome(
            linkage="absent",
            first_observed_at=None,
        )

    return DetectionOutcome(
        linkage="direct",
        first_observed_at=rows[0].created_at,
        evidence=tuple(
            Evidence(source="knowledge", id=row.id, linkage="direct")
            for row in rows
        ),
    )


def _list_proposals(
    db: Session,
    account_id: int,
    due_date: date,
) -> list[tuple[int, AuthenticatedAdvisoryProposal]]:
    prefix = (
        f"conta_vencida:{account_id}:{due_date.isoformat()}:attempt:"
    )

    rows = (
        db.execute(
            select(AuthenticatedAdvisoryProposal).where(
                AuthenticatedAdvisoryProposal.idempotency_key.like(
                    f"{prefix}%"
                )
            )
        )
        .scalars()
        .all()
    )

    parsed: list[tuple[int, AuthenticatedAdvisoryProposal]] = []

    for row in rows:
        match = _EPISODE_ATTEMPT_KEY_PATTERN.fullmatch(
            row.idempotency_key
        )

        if match is None:
            continue

        if int(match.group("account_id")) != account_id:
            continue

        if match.group("due_date") != due_date.isoformat():
            continue

        parsed.append((int(match.group("attempt")), row))

    parsed.sort(key=lambda item: item[0])

    return parsed


def _build_recommendation(
    parsed_proposals: list[tuple[int, AuthenticatedAdvisoryProposal]],
) -> RecommendationOutcome:
    if not parsed_proposals:
        return RecommendationOutcome(
            linkage="absent",
            created_at=None,
            latest_proposal_attempt=None,
        )

    latest_attempt, latest_row = parsed_proposals[-1]

    return RecommendationOutcome(
        linkage="correlated",
        created_at=latest_row.created_at,
        latest_proposal_attempt=latest_attempt,
        evidence=tuple(
            Evidence(
                source="authenticated_advisory_proposal",
                id=row.id,
                linkage="correlated",
            )
            for _, row in parsed_proposals
        ),
    )


def _find_approval_request(
    db: Session,
    *,
    account_id: int,
    proposal_id: int,
) -> ApprovalRequest | None:
    prefix = f"advisory:{proposal_id}:"

    candidates = (
        db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.idempotency_key.like(f"{prefix}%"),
                ApprovalRequest.target_account_id == account_id,
            )
        )
        .scalars()
        .all()
    )

    for row in candidates:
        match = _APPROVAL_ADVISORY_KEY_PATTERN.fullmatch(
            row.idempotency_key
        )

        if match is None:
            continue

        if int(match.group("proposal_id")) == proposal_id:
            return row

    return None


def _approve(
    db: Session,
    account_id: int,
    parsed_proposals: list[tuple[int, AuthenticatedAdvisoryProposal]],
) -> tuple[ApprovalOutcome, int | None, int | None]:
    """
    Retorna (ApprovalOutcome, approval_source_proposal_id,
    approval_source_attempt). Percorre as tentativas da mais recente para
    a mais antiga (nunca pula, nunca escolhe "a mais proxima").
    """

    if not parsed_proposals:
        return (
            ApprovalOutcome(
                linkage="absent",
                status=None,
                decided_at=None,
                decided_by_reference=None,
                approval_source_attempt=None,
                attempt_mismatch=False,
            ),
            None,
            None,
        )

    latest_attempt = parsed_proposals[-1][0]

    for attempt, proposal_row in reversed(parsed_proposals):
        approval_request = _find_approval_request(
            db,
            account_id=account_id,
            proposal_id=proposal_row.id,
        )

        if approval_request is None:
            continue

        decision = db.execute(
            select(ApprovalDecision).where(
                ApprovalDecision.approval_request_id
                == approval_request.id
            )
        ).scalar_one_or_none()

        evidence = [
            Evidence(
                source="approval_request",
                id=approval_request.id,
                linkage="correlated",
            )
        ]

        if decision is not None:
            evidence.append(
                Evidence(
                    source="approval_decision",
                    id=decision.id,
                    linkage="direct",
                )
            )

        return (
            ApprovalOutcome(
                linkage="correlated",
                status=approval_request.status,
                decided_at=(
                    decision.created_at if decision else None
                ),
                decided_by_reference=(
                    decision.decided_by_reference
                    if decision
                    else None
                ),
                approval_source_attempt=attempt,
                attempt_mismatch=(attempt != latest_attempt),
                evidence=tuple(evidence),
            ),
            proposal_row.id,
            attempt,
        )

    return (
        ApprovalOutcome(
            linkage="absent",
            status=None,
            decided_at=None,
            decided_by_reference=None,
            approval_source_attempt=None,
            attempt_mismatch=False,
        ),
        None,
        None,
    )


def _execute(
    db: Session,
    account_id: int,
    approval_source_proposal_id: int | None,
    approval_source_attempt: int | None,
) -> ExecutionOutcome:
    if approval_source_proposal_id is None:
        return ExecutionOutcome(
            linkage="absent",
            status=None,
            finished_at=None,
            sourced_from_attempt=None,
        )

    origin_reference = (
        f"advisory_proposal:{approval_source_proposal_id}"
    )

    work_item = db.execute(
        select(WorkItem).where(
            WorkItem.account_id == account_id,
            WorkItem.origin_type == "agent",
            WorkItem.origin_reference == origin_reference,
        )
    ).scalar_one_or_none()

    if work_item is None:
        return ExecutionOutcome(
            linkage="absent",
            status=None,
            finished_at=None,
            sourced_from_attempt=approval_source_attempt,
        )

    evidence = [
        Evidence(
            source="work_item",
            id=work_item.id,
            linkage="correlated",
        )
    ]

    execution = db.execute(
        select(WorkSkillExecution).where(
            WorkSkillExecution.work_item_id == work_item.id
        )
    ).scalar_one_or_none()

    if execution is None:
        return ExecutionOutcome(
            linkage="correlated",
            status=None,
            finished_at=None,
            sourced_from_attempt=approval_source_attempt,
            evidence=tuple(evidence),
        )

    evidence.append(
        Evidence(
            source="work_skill_execution",
            id=execution.id,
            linkage="direct",
        )
    )

    if execution.skill_invocation_id is not None:
        evidence.append(
            Evidence(
                source="skill_invocation",
                id=execution.skill_invocation_id,
                linkage="direct",
            )
        )

    return ExecutionOutcome(
        linkage="correlated",
        status=execution.status,
        finished_at=execution.finished_at,
        sourced_from_attempt=approval_source_attempt,
        evidence=tuple(evidence),
    )


def _paid_events(
    db: Session,
    account_id: int,
) -> list[AccountEvent]:
    return list(
        db.execute(
            select(AccountEvent)
            .where(
                AccountEvent.account_id == account_id,
                AccountEvent.new_status == "pago",
            )
            .order_by(AccountEvent.occurred_at.asc())
        )
        .scalars()
        .all()
    )


def _pay(
    db: Session,
    account: Account,
    due_date: date,
    *,
    first_detected_at: datetime | None,
    first_recommended_at: datetime | None,
) -> PaymentOutcome:
    if account.vencimento != due_date:
        candidates = _paid_events(db, account.id)

        return PaymentOutcome(
            linkage="unresolved_due_date_change",
            rule_version=RULE_VERSION,
            occurred_at=None,
            candidate_evidence=tuple(
                Evidence(
                    source="account_event",
                    id=row.id,
                    linkage="unresolved_due_date_change",
                )
                for row in candidates
            ),
        )

    detection_ts_candidates = [
        ts
        for ts in (first_detected_at, first_recommended_at)
        if ts is not None
    ]

    if not detection_ts_candidates:
        return PaymentOutcome(
            linkage="absent",
            rule_version=RULE_VERSION,
            occurred_at=None,
        )

    detection_ts = min(detection_ts_candidates)

    candidates = _paid_events(db, account.id)

    closing_event = next(
        (
            row
            for row in candidates
            if row.occurred_at >= detection_ts
        ),
        None,
    )

    if closing_event is None:
        return PaymentOutcome(
            linkage="absent",
            rule_version=RULE_VERSION,
            occurred_at=None,
        )

    return PaymentOutcome(
        linkage="correlated",
        rule_version=RULE_VERSION,
        occurred_at=closing_event.occurred_at,
        evidence=(
            Evidence(
                source="account_event",
                id=closing_event.id,
                linkage="correlated",
            ),
        ),
    )


def _derive(
    detection: DetectionOutcome,
    payment: PaymentOutcome,
) -> DerivedMetrics:
    if (
        detection.first_observed_at is None
        or payment.occurred_at is None
    ):
        return DerivedMetrics(days_detection_to_payment=None)

    delta = payment.occurred_at - detection.first_observed_at

    return DerivedMetrics(days_detection_to_payment=delta.days)


def get_outcome_episode(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> OutcomeEpisodeResult:
    """
    Monta a projecao completa de um episodio financeiro. O chamador
    (rota) e responsavel por buscar `account` e responder 404 se nao
    existir -- esta funcao assume que ja existe.
    """

    account_id = account.id

    detection = _detect(db, account_id, due_date)
    parsed_proposals = _list_proposals(db, account_id, due_date)
    recommendation = _build_recommendation(parsed_proposals)

    approval, approval_source_proposal_id, approval_source_attempt = (
        _approve(db, account_id, parsed_proposals)
    )

    execution = _execute(
        db,
        account_id,
        approval_source_proposal_id,
        approval_source_attempt,
    )

    first_recommended_at = (
        parsed_proposals[0][1].created_at
        if parsed_proposals
        else None
    )

    payment = _pay(
        db,
        account,
        due_date,
        first_detected_at=detection.first_observed_at,
        first_recommended_at=first_recommended_at,
    )

    derived = _derive(detection, payment)
    amount = AmountInfo(value=account.valor)

    return OutcomeEpisodeResult(
        episode=FinancialEpisode(
            account_id=account_id,
            due_date=due_date,
        ),
        detection=detection,
        recommendation=recommendation,
        approval=approval,
        execution=execution,
        payment=payment,
        derived=derived,
        amount=amount,
    )
