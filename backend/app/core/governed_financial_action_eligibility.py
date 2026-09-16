"""
Pilot Action Space V1.C -- Governed Financial Action Eligibility V1.

Duas politicas versionadas independentes, no mesmo arquivo por
pertencerem a mesma familia (skill_action, mutating, Approval
corridor) e terem sido congeladas juntas no Architecture Freeze /
Executable Contract com Tomaz em 15/09/2026. Nenhum predicado
compartilhado entre elas alem de FinancialEpisode (reuso de
outcome_correlation.py).

Distincao central, nunca colapsada em um unico `eligible: bool`:

    structurally_available -- o corredor de execucao aceitaria esta
        acao agora, dado o estado persistido.
    system_recommendable -- o Auneron possui, sozinho, evidencia
        observavel suficiente para SUGERIR esta acao agora. Subconjunto
        de structurally_available, nunca o contrario.
    requires_external_fact -- quando structurally_available=true e
        system_recommendable=false por depender de um fato so
        observavel fora do sistema, nomeia esse fato. Nunca inferido
        de days_overdue, amount, classificacao ou historico.

account.mark_overdue:
    structurally_available == system_recommendable (100% determinístico
    a partir de Account.status/vencimento). O predicado (status=="aberto"
    AND vencimento<today) e reexpresso literalmente aqui -- nenhuma
    funcao publica existe para reuso -- espelhando
    OverdueDetectionService.run_scan() e a revalidacao em
    AccountMarkOverdueExecutionService.execute() (linhas 157-162).

    due_date e verificado ANTES de qualquer outra condicao: se
    account.vencimento != due_date solicitado, a identidade do
    episodio ja diverge e nenhuma outra condicao e avaliada
    (reason="due_date_mismatch") -- mesmo problema de identidade que
    Outcome V1 ja resolveu para pagamento, aqui resolvido na entrada.

account.mark_paid:
    structurally_available = Account.status in VALID_EXPECTED_STATUSES
    (reuso literal da constante publica de
    account_mark_paid_execution_service.py). SEM condicao de data --
    pagamento e valido em qualquer estagio do lifecycle, porque o
    corredor real nunca recebe due_date como parametro.

    system_recommendable == False, SEMPRE, no V1.C -- constante, nao
    derivada de nenhum sinal disponivel. requires_external_fact=
    "payment_observed" sempre que structurally_available=true; None
    quando already_paid (nada a requerer -- a acao nem esta disponivel).

    due_date_matches_current_vencimento e reportado como campo de
    CONTEXTO, nunca gate -- decisao explicita do Executable Contract
    para nao inventar uma restricao que o corredor real nao possui.

Este arquivo NAO agrega as duas capabilities em eligible_actions()/
no_action -- isso pertence ao futuro Action Space Evaluator, fora do
escopo desta fatia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.core.outcome_correlation import FinancialEpisode
from app.models.account import Account
from app.services.account_mark_paid_execution_service import (
    VALID_EXPECTED_STATUSES,
)


MARK_OVERDUE_RULE_VERSION = "account_mark_overdue_eligibility_v1"
MARK_PAID_RULE_VERSION = "account_mark_paid_eligibility_v1"

MarkOverdueReason = Literal[
    "due_date_mismatch",
    "already_paid",
    "status_not_open",
    "not_overdue",
]

MarkPaidReason = Literal["already_paid"]


@dataclass(frozen=True)
class MarkOverdueEligibilityResult:
    eligibility_policy: str
    episode: FinancialEpisode
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: None
    reason: MarkOverdueReason | None


@dataclass(frozen=True)
class MarkPaidEligibilityResult:
    eligibility_policy: str
    episode: FinancialEpisode
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: Literal["payment_observed"] | None
    reason: MarkPaidReason | None
    due_date_matches_current_vencimento: bool


def get_mark_overdue_eligibility(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> MarkOverdueEligibilityResult:
    """
    O chamador (rota) e responsavel por buscar `account` e responder
    404 se nao existir -- esta funcao assume que ja existe. `db` nao e
    usado hoje, mas permanece na assinatura por simetria com as demais
    funcoes da familia (get_outcome_episode, get_customer_context,
    get_human_escalation_eligibility).
    """

    episode = FinancialEpisode(
        account_id=account.id,
        due_date=due_date,
    )

    if account.vencimento != due_date:
        return MarkOverdueEligibilityResult(
            eligibility_policy=MARK_OVERDUE_RULE_VERSION,
            episode=episode,
            structurally_available=False,
            system_recommendable=False,
            requires_external_fact=None,
            reason="due_date_mismatch",
        )

    reason: MarkOverdueReason | None

    if account.status == "pago":
        reason = "already_paid"
    elif account.status == "atrasado":
        reason = "status_not_open"
    elif account.vencimento >= date.today():
        reason = "not_overdue"
    else:
        reason = None

    available = reason is None

    return MarkOverdueEligibilityResult(
        eligibility_policy=MARK_OVERDUE_RULE_VERSION,
        episode=episode,
        structurally_available=available,
        system_recommendable=available,
        requires_external_fact=None,
        reason=reason,
    )


def get_mark_paid_eligibility(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> MarkPaidEligibilityResult:
    """
    O chamador (rota) e responsavel por buscar `account` e responder
    404 se nao existir -- esta funcao assume que ja existe.
    """

    episode = FinancialEpisode(
        account_id=account.id,
        due_date=due_date,
    )

    available = account.status in VALID_EXPECTED_STATUSES

    return MarkPaidEligibilityResult(
        eligibility_policy=MARK_PAID_RULE_VERSION,
        episode=episode,
        structurally_available=available,
        system_recommendable=False,
        requires_external_fact=(
            "payment_observed" if available else None
        ),
        reason=None if available else "already_paid",
        due_date_matches_current_vencimento=(
            account.vencimento == due_date
        ),
    )
