"""
Action Space Evaluator V1 -- composicao read-only das tres capabilities
governadas ja congeladas (account.mark_overdue, account.mark_paid,
escalate_to_human) em uma unica projecao por financial_episode.

Congelado em Architecture Freeze / PRE-APPLY Mechanical-Composition-RBAC
Gate / Executable Contract com Tomaz em 16/09/2026, a partir do
baseline `29cee77` (V1.B + V1.C ja fechados).

Nao introduz eligibilidade nova: chama as tres funcoes publicas ja
fechadas (get_mark_overdue_eligibility, get_mark_paid_eligibility,
get_human_escalation_eligibility) com o mesmo (account, due_date), e
projeta cada resultado em ActionEvaluation. Nenhum dos tres modulos
fonte e alterado.

Invariante central (unica formula permitida para recommendable_actions
e no_action -- nenhum segundo predicado pode existir):

    recommendable_actions = [a.action_key for a in actions if a.system_recommendable is True]
    no_action = len(recommendable_actions) == 0

mark_paid pode ter structurally_available=True e ainda assim nunca
entrar em recommendable_actions -- system_recommendable so se torna
True quando payment_observed for um fato externo observado (fora do
escopo desta fatia), nunca inferido de nenhum sinal interno.

`actions` e sempre as 3 capabilities, sempre nesta ordem fisica fixa
(mark_overdue, mark_paid, escalate_to_human) -- a ordem e so de
serializacao, nunca ranking. `reason` e transportado literalmente do
contrato-fonte de cada capability, sem normalizacao de vocabulario
(ex.: mark_overdue usa "already_paid", escalate_to_human usa
"account_paid" para o mesmo fato -- os dois permanecem distintos).

O registry ACTION_METADATA e uma declaracao propria deste modulo, nao
um dado descoberto dos tres contratos fonte -- initiation_actor,
authority_model, required_authority e execution_corridor nao existem
como campo em nenhum dos tres dataclasses compostos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.core.governed_financial_action_eligibility import (
    get_mark_overdue_eligibility,
)
from app.core.governed_financial_action_eligibility import (
    get_mark_paid_eligibility,
)
from app.core.human_escalation_eligibility import (
    RULE_VERSION as HUMAN_ESCALATION_RULE_VERSION,
)
from app.core.human_escalation_eligibility import (
    get_human_escalation_eligibility,
)
from app.core.outcome_correlation import FinancialEpisode
from app.models.account import Account


CapabilityKind = Literal["skill_action", "work_action"]
InitiationActor = Literal["agent", "user", "rbac_actor"]
AuthorityModel = Literal["approval", "rbac"]

MARK_OVERDUE_ACTION_KEY = "account.mark_overdue"
MARK_PAID_ACTION_KEY = "account.mark_paid"
ESCALATE_TO_HUMAN_ACTION_KEY = "escalate_to_human"

ACTION_METADATA: dict[str, dict[str, str]] = {
    MARK_OVERDUE_ACTION_KEY: {
        "capability_kind": "skill_action",
        "initiation_actor": "agent",
        "authority_model": "approval",
        "required_authority": "approval:decide",
        "execution_corridor": "advisory_approval_work_skill",
    },
    MARK_PAID_ACTION_KEY: {
        "capability_kind": "skill_action",
        "initiation_actor": "user",
        "authority_model": "approval",
        "required_authority": "approval:decide",
        "execution_corridor": "direct_approval_skill",
    },
    ESCALATE_TO_HUMAN_ACTION_KEY: {
        "capability_kind": "work_action",
        "initiation_actor": "rbac_actor",
        "authority_model": "rbac",
        "required_authority": "work:create",
        "execution_corridor": "work_materialization",
    },
}


@dataclass(frozen=True)
class ActionEvaluation:
    action_key: str
    capability_kind: CapabilityKind
    eligibility_policy: str
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: str | None
    reason: str | None
    initiation_actor: InitiationActor
    authority_model: AuthorityModel
    required_authority: str
    execution_corridor: str


@dataclass(frozen=True)
class ActionSpaceEvaluation:
    episode: FinancialEpisode
    actions: tuple[ActionEvaluation, ...]
    recommendable_actions: tuple[str, ...]
    no_action: bool


def _evaluate_mark_overdue(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> ActionEvaluation:
    result = get_mark_overdue_eligibility(
        db, account=account, due_date=due_date
    )
    metadata = ACTION_METADATA[MARK_OVERDUE_ACTION_KEY]

    return ActionEvaluation(
        action_key=MARK_OVERDUE_ACTION_KEY,
        capability_kind=metadata["capability_kind"],
        eligibility_policy=result.eligibility_policy,
        structurally_available=result.structurally_available,
        system_recommendable=result.system_recommendable,
        requires_external_fact=result.requires_external_fact,
        reason=result.reason,
        initiation_actor=metadata["initiation_actor"],
        authority_model=metadata["authority_model"],
        required_authority=metadata["required_authority"],
        execution_corridor=metadata["execution_corridor"],
    )


def _evaluate_mark_paid(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> ActionEvaluation:
    result = get_mark_paid_eligibility(
        db, account=account, due_date=due_date
    )
    metadata = ACTION_METADATA[MARK_PAID_ACTION_KEY]

    return ActionEvaluation(
        action_key=MARK_PAID_ACTION_KEY,
        capability_kind=metadata["capability_kind"],
        eligibility_policy=result.eligibility_policy,
        structurally_available=result.structurally_available,
        system_recommendable=result.system_recommendable,
        requires_external_fact=result.requires_external_fact,
        reason=result.reason,
        initiation_actor=metadata["initiation_actor"],
        authority_model=metadata["authority_model"],
        required_authority=metadata["required_authority"],
        execution_corridor=metadata["execution_corridor"],
    )


def _evaluate_escalate_to_human(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> ActionEvaluation:
    result = get_human_escalation_eligibility(
        db, account=account, due_date=due_date
    )
    metadata = ACTION_METADATA[ESCALATE_TO_HUMAN_ACTION_KEY]
    available = result.status == "eligible"

    return ActionEvaluation(
        action_key=ESCALATE_TO_HUMAN_ACTION_KEY,
        capability_kind=metadata["capability_kind"],
        eligibility_policy=HUMAN_ESCALATION_RULE_VERSION,
        structurally_available=available,
        system_recommendable=available,
        requires_external_fact=None,
        reason=result.reason,
        initiation_actor=metadata["initiation_actor"],
        authority_model=metadata["authority_model"],
        required_authority=metadata["required_authority"],
        execution_corridor=metadata["execution_corridor"],
    )


def get_action_space_evaluation(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> ActionSpaceEvaluation:
    """
    O chamador (rota) e responsavel por buscar `account` e responder
    404 se nao existir -- esta funcao assume que ja existe.
    """

    actions = (
        _evaluate_mark_overdue(db, account=account, due_date=due_date),
        _evaluate_mark_paid(db, account=account, due_date=due_date),
        _evaluate_escalate_to_human(
            db, account=account, due_date=due_date
        ),
    )

    recommendable_actions = tuple(
        action.action_key
        for action in actions
        if action.system_recommendable is True
    )

    return ActionSpaceEvaluation(
        episode=FinancialEpisode(
            account_id=account.id, due_date=due_date
        ),
        actions=actions,
        recommendable_actions=recommendable_actions,
        no_action=len(recommendable_actions) == 0,
    )
