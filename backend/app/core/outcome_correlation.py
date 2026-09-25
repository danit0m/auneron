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
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.models.business_effect_verification import (
    BusinessEffectVerification,
)
from app.models.knowledge import Knowledge
from app.models.skill import SkillDefinition
from app.models.skill import SkillInvocation
from app.models.skill import SkillVersion
from app.models.work import WorkItem
from app.models.work_skill_execution import WorkSkillExecution
from app.services.overdue_detection_service import (
    _EPISODE_ATTEMPT_KEY_PATTERN,
)


HUMAN_MARK_OVERDUE_SKILL_KEY = "account.mark_overdue"


RULE_VERSION = "outcome_correlation_v1"

_APPROVAL_ADVISORY_KEY_PATTERN = re.compile(
    r"^advisory:"
    r"(?P<proposal_id>[1-9][0-9]*):"
    r"(?P<binding_id>[1-9][0-9]*)$"
)


Linkage = str
# "direct" | "correlated" | "absent" | "unresolved_due_date_change" |
# "invalid"

EvidenceSource = str
# "knowledge" | "authenticated_advisory_proposal" | "approval_request" |
# "approval_decision" | "work_item" | "work_skill_execution" |
# "skill_invocation" | "account_event" | "approval_consumption" |
# "business_effect_verification" | "nba_recommendation_snapshot"


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
class RecommendationProvenanceOutcome:
    """
    DW-6.5 -- proveniencia da recomendacao NBA declaradamente associada
    pelo corredor humano (WorkItem.context_data["recommendation_snapshot_id"],
    DW-6.4B). Campo aditivo e paralelo a RecommendationOutcome (corredor
    agente) -- nunca o substitui, nunca compartilha resolucao com ele.
    Resolvido exclusivamente via o ponteiro declarado, sempre reverificado
    por NbaRecommendationSnapshotService.get_verified() -- nunca por
    latest/nearest-timestamp/mesmo-account/mesmo-due_date/digest-igual.

    linkage="absent": nenhuma referencia foi declarada (chave ausente de
    context_data) -- episodio humano legitimo, nunca corrupcao, nunca
    backfill.
    linkage="invalid": uma referencia foi declarada mas nao e confiavel
    agora -- pointer presente porem malformado (nao-int), snapshot
    inexistente, snapshot_payload adulterado (digest invalido), ou
    snapshot integro porem semanticamente incompativel (conta/due_date/
    selected_actions). Nunca silenciado como "absent" -- preserva apenas
    recommendation_snapshot_id quando o pointer bruto for um int
    utilizavel; nenhum outro campo de um snapshot nao confiavel e
    projetado.
    linkage="correlated": referencia resolvida, integra e semanticamente
    compativel -- mesma convencao ja usada por human_approval/
    human_execution/effect_verification (nivel externo reflete o elo
    mais fraco, o pointer JSONB, mesmo que o snapshot resolvido em si
    seja uma linha real recuperada por PK).
    """

    linkage: Linkage
    recommendation_snapshot_id: int | None
    policy_version: str | None
    decision_type: str | None
    selected_actions: tuple[str, ...] | None
    requires_human_review: bool | None
    created_at: datetime | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class HumanApprovalOutcome:
    """
    Corredor humano (DW-5 V1) -- reconstruido exclusivamente a partir de
    ApprovalRequest/ApprovalDecision/ApprovalConsumption. Nunca le
    AuthenticatedAdvisoryProposal. Campo aditivo, paralelo a
    ApprovalOutcome (corredor agente) -- nunca o substitui nem e
    substituido por ele.
    """

    linkage: Linkage
    status: str | None
    decided_at: datetime | None
    decided_by_reference: str | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class HumanExecutionOutcome:
    """
    Corredor humano (DW-5 V1) -- reconstruido via
    ApprovalConsumption.skill_invocation_id (FK direto). Nunca le
    WorkSkillExecution -- o corredor humano nunca cria essa linha.
    """

    linkage: Linkage
    status: str | None
    finished_at: datetime | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class EffectVerificationOutcome:
    """
    Projeta a prova ja produzida pelo DW-3 (BusinessEffectVerification).
    Nunca reexecuta a verificacao nem busca AccountEvent de forma
    independente -- le apenas o account_event_id ja resolvido pelo DW-3.
    Estruturalmente separado de ExecutionOutcome/HumanExecutionOutcome:
    execution outcome != business effect verification.
    """

    linkage: Linkage
    result: str | None
    checked_at: datetime | None
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
    recommendation_provenance: RecommendationProvenanceOutcome
    human_approval: HumanApprovalOutcome
    human_execution: HumanExecutionOutcome
    effect_verification: EffectVerificationOutcome
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


def _human_work_item(
    db: Session,
    account_id: int,
    due_date: date,
) -> WorkItem | None:
    # Identidade exata do corredor humano (DW-5 Design Freeze) --
    # unica implementacao deste work_key e
    # human_account_mark_overdue_materialization_service.py
    # (protegido, nunca reconstruido aqui de outra forma). Resolucao
    # positiva: a existencia do corredor humano nunca e inferida pela
    # ausencia do corredor agente.
    work_key = (
        f"account_mark_overdue:v1:{account_id}:{due_date.isoformat()}"
    )

    return db.execute(
        select(WorkItem).where(
            WorkItem.account_id == account_id,
            WorkItem.work_key == work_key,
        )
    ).scalar_one_or_none()


def _no_recommendation_provenance(
    *, linkage: Linkage, recommendation_snapshot_id: int | None
) -> RecommendationProvenanceOutcome:
    return RecommendationProvenanceOutcome(
        linkage=linkage,
        recommendation_snapshot_id=recommendation_snapshot_id,
        policy_version=None,
        decision_type=None,
        selected_actions=None,
        requires_human_review=None,
        created_at=None,
    )


def _recommendation_provenance(
    db: Session,
    human_work_item: WorkItem | None,
    *,
    account_id: int,
    due_date: date,
) -> RecommendationProvenanceOutcome:
    """
    DW-6.5 -- unico ponto de resolucao permitido: o WorkItem exato do
    corredor humano (ja identificado positivamente por
    _human_work_item()) -> context_data["recommendation_snapshot_id"] ->
    NbaRecommendationSnapshotService.get_verified(). Nunca busca por
    inferencia. "invalid" nunca e silenciado como "absent" -- a distincao
    entre "nenhuma referencia foi declarada" e "uma referencia foi
    declarada mas nao e confiavel agora" e a garantia central deste
    Design Freeze.
    """

    # Import local -- deferido para evitar ciclo de import em tempo de
    # carregamento do modulo: nba_recommendation_snapshot_service ->
    # nba_policy -> action_space_evaluator ->
    # governed_financial_action_eligibility -> outcome_correlation
    # (este mesmo arquivo, para FinancialEpisode). Nenhum efeito
    # funcional -- o servico so e instanciado dentro desta funcao de
    # qualquer forma.
    from app.services.nba_recommendation_snapshot_service import (
        NbaRecommendationSnapshotIntegrityError,
    )
    from app.services.nba_recommendation_snapshot_service import (
        NbaRecommendationSnapshotService,
    )

    if human_work_item is None:
        return _no_recommendation_provenance(
            linkage="absent",
            recommendation_snapshot_id=None,
        )

    context = (
        human_work_item.context_data
        if isinstance(human_work_item.context_data, dict)
        else {}
    )

    if "recommendation_snapshot_id" not in context:
        return _no_recommendation_provenance(
            linkage="absent",
            recommendation_snapshot_id=None,
        )

    pointer_id = context["recommendation_snapshot_id"]

    # bool e subclasse de int em Python -- um JSON `true`/`false`
    # persistido nao e um ID utilizavel, mesmo passando em isinstance
    # simples.
    if not isinstance(pointer_id, int) or isinstance(pointer_id, bool):
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=None,
        )

    try:
        snapshot = NbaRecommendationSnapshotService(
            db
        ).get_verified(pointer_id)
    except NbaRecommendationSnapshotIntegrityError:
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=pointer_id,
        )

    if snapshot is None:
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=pointer_id,
        )

    if (
        snapshot.account_id != account_id
        or snapshot.due_date != due_date
    ):
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=pointer_id,
        )

    payload = (
        snapshot.snapshot_payload
        if isinstance(snapshot.snapshot_payload, dict)
        else {}
    )

    # get_verified() prova apenas que snapshot_payload nao mudou desde
    # que foi hasheado -- nunca que sua estrutura interna e a esperada.
    # dict.get(key, default) so aplica o default quando a CHAVE esta
    # ausente, nunca quando o valor presente e nao-dict/nao-lista --
    # por isso cada nivel e checado explicitamente com isinstance antes
    # de qualquer acesso encadeado, para que uma estrutura inesperada
    # (mesmo com digest valido) vire "invalid" em vez de escapar como
    # AttributeError/TypeError ate a fronteira HTTP.
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=pointer_id,
        )

    raw_selected_actions = decision.get("selected_actions")
    if not isinstance(raw_selected_actions, list):
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=pointer_id,
        )

    selected_actions = tuple(raw_selected_actions)

    if HUMAN_MARK_OVERDUE_SKILL_KEY not in selected_actions:
        return _no_recommendation_provenance(
            linkage="invalid",
            recommendation_snapshot_id=pointer_id,
        )

    return RecommendationProvenanceOutcome(
        linkage="correlated",
        recommendation_snapshot_id=snapshot.id,
        policy_version=snapshot.policy_version,
        decision_type=snapshot.decision_type,
        selected_actions=selected_actions,
        requires_human_review=snapshot.requires_human_review,
        created_at=snapshot.created_at,
        evidence=(
            Evidence(
                source="nba_recommendation_snapshot",
                id=snapshot.id,
                linkage="direct",
            ),
        ),
    )


def _human_approval_request(
    db: Session,
    work_item: WorkItem,
    account_id: int,
) -> ApprovalRequest | None:
    """
    Recupera o ApprovalRequest referenciado por
    WorkItem.context_data["approval_request_id"] e valida presenca +
    existencia referencial + consistencia semantica (conta, skill).
    Fail-closed: qualquer divergencia retorna None -- nunca busca "a
    request mais proxima", nunca ignora a divergencia.
    """
    context = (
        work_item.context_data
        if isinstance(work_item.context_data, dict)
        else {}
    )
    approval_request_id = context.get("approval_request_id")

    if not isinstance(approval_request_id, int):
        return None

    approval_request = db.get(ApprovalRequest, approval_request_id)

    if approval_request is None:
        return None

    if approval_request.target_account_id != account_id:
        return None

    version = db.get(
        SkillVersion, approval_request.skill_version_id
    )

    if version is None:
        return None

    skill = db.get(SkillDefinition, version.skill_id)

    if (
        skill is None
        or skill.skill_key != HUMAN_MARK_OVERDUE_SKILL_KEY
    ):
        return None

    return approval_request


def _human_approve(
    db: Session,
    approval_request: ApprovalRequest,
) -> tuple[HumanApprovalOutcome, ApprovalConsumption | None]:
    decision = db.execute(
        select(ApprovalDecision).where(
            ApprovalDecision.approval_request_id
            == approval_request.id
        )
    ).scalar_one_or_none()

    consumption = db.execute(
        select(ApprovalConsumption).where(
            ApprovalConsumption.approval_request_id
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

    if consumption is not None:
        evidence.append(
            Evidence(
                source="approval_consumption",
                id=consumption.id,
                linkage="direct",
            )
        )

    return (
        HumanApprovalOutcome(
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
            evidence=tuple(evidence),
        ),
        consumption,
    )


def _human_execution_outcome(
    db: Session,
    consumption: ApprovalConsumption | None,
) -> HumanExecutionOutcome:
    if consumption is None or consumption.skill_invocation_id is None:
        return HumanExecutionOutcome(
            linkage="absent",
            status=None,
            finished_at=None,
        )

    invocation = db.get(
        SkillInvocation, consumption.skill_invocation_id
    )

    if invocation is None:
        return HumanExecutionOutcome(
            linkage="absent",
            status=None,
            finished_at=None,
        )

    return HumanExecutionOutcome(
        linkage="correlated",
        status=invocation.status,
        finished_at=invocation.finished_at,
        evidence=(
            Evidence(
                source="skill_invocation",
                id=invocation.id,
                linkage="direct",
            ),
        ),
    )


def _effect_verification_outcome(
    db: Session,
    consumption: ApprovalConsumption | None,
) -> EffectVerificationOutcome:
    if consumption is None:
        return EffectVerificationOutcome(
            linkage="absent",
            result=None,
            checked_at=None,
        )

    verification = db.execute(
        select(BusinessEffectVerification).where(
            BusinessEffectVerification.approval_consumption_id
            == consumption.id
        )
    ).scalar_one_or_none()

    if verification is None:
        return EffectVerificationOutcome(
            linkage="absent",
            result=None,
            checked_at=None,
        )

    evidence = [
        Evidence(
            source="business_effect_verification",
            id=verification.id,
            linkage="direct",
        )
    ]

    if verification.account_event_id is not None:
        evidence.append(
            Evidence(
                source="account_event",
                id=verification.account_event_id,
                linkage="direct",
            )
        )

    return EffectVerificationOutcome(
        # Nivel externo segue a mesma convencao ja usada por
        # ExecutionOutcome: reflete o elo mais fraco da cadeia ate aqui
        # (o ponteiro JSONB WorkItem->ApprovalRequest), mesmo que
        # ApprovalConsumption->BusinessEffectVerification em si seja
        # FK+UNIQUE real -- por isso "correlated", nao "direct", no
        # nivel do outcome. O item de Evidence individual permanece
        # "direct".
        linkage="correlated",
        result=verification.result,
        checked_at=verification.checked_at,
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

    # Corredor humano (DW-5 V1) -- resolvido de forma inteiramente
    # independente do corredor agente acima; nunca inferido pela
    # ausencia deste. Se ambos existirem para o mesmo episodio (E004,
    # competing authority), ambos permanecem visiveis -- nenhum
    # sobrescreve o outro.
    human_work_item = _human_work_item(db, account_id, due_date)

    recommendation_provenance = _recommendation_provenance(
        db,
        human_work_item,
        account_id=account_id,
        due_date=due_date,
    )

    human_approval_request = (
        _human_approval_request(db, human_work_item, account_id)
        if human_work_item is not None
        else None
    )

    if human_approval_request is not None:
        human_approval, human_consumption = _human_approve(
            db, human_approval_request
        )
    else:
        human_approval = HumanApprovalOutcome(
            linkage="absent",
            status=None,
            decided_at=None,
            decided_by_reference=None,
        )
        human_consumption = None

    human_execution = _human_execution_outcome(
        db, human_consumption
    )
    effect_verification = _effect_verification_outcome(
        db, human_consumption
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
        recommendation_provenance=recommendation_provenance,
        human_approval=human_approval,
        human_execution=human_execution,
        effect_verification=effect_verification,
        payment=payment,
        derived=derived,
        amount=amount,
    )
