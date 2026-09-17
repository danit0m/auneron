"""
PR-6A -- Human Escalation Materialization Service V1.

Unica fronteira de composicao entre a eligibility read-only de V1.B
(`get_human_escalation_eligibility`) e o WorkManagerService existente.
A rota permanece fina: carrega Account, autentica/autoriza, delega
tudo aqui.

Nao reconstroi WorkItem diretamente nem repete as regras R1-R3 -- so
decide, a partir de uma consulta direta pela chave canonica e do
resultado fresco da eligibility, qual caminho seguir:

    nao existe WorkItem canonico
        + eligibility atual == eligible
        -> WorkManagerService.create()

    WorkItem canonico NAO-TERMINAL ja existe
        -> retorno idempotente do mesmo WorkItem, create() nunca
           chamado de novo (evita o WorkConflictError que ocorreria
           se dois atores diferentes colidissem no fingerprint de
           WorkManagerService._creation_fingerprint, que inclui
           actor_user_id)

    WorkItem canonico TERMINAL ja existe
        + eligibility atual voltou a ser "eligible"
        -> HTTP 409 -- reabertura do mesmo episodio nao e suportada
           por esta fatia (P1.3, registrado separadamente). Nunca
           finge que o item terminal e a materializacao atual, e
           nunca cria um segundo WorkItem sob a mesma work_key --
           isso exigiria uma segunda semantica de identidade de
           episodio/reabertura sem Survey proprio.

A mesma checagem de terminalidade e reaplicada ao item vencedor de
uma eventual corrida em WorkManagerService.create() (quando
duplicate=True), para que fail-closed valha tambem sob concorrencia.

TERMINAL_STATUSES e reusado de app.services.work_service -- autoridade
central ja existente (usada por WorkManagerService._require_nonterminal),
nao uma nova definicao local. `_TERMINAL_STATUSES` privado em
human_escalation_eligibility.py pertence ao contrato ja congelado de
V1.B e nao e alterado aqui.

origin_type/origin_reference nunca vem do navegador: origin_type e
sempre "user" (autoridade e sempre o operador autenticado) e
origin_reference e sempre `result.recommendation_key`, derivado no
servidor a partir da propria eligibility -- nunca do NBA response nem
de qualquer snapshot que o cliente possa ter enviado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.human_escalation_eligibility import (
    IneligibilityReason,
)
from app.core.human_escalation_eligibility import (
    get_human_escalation_eligibility,
)
from app.core.human_escalation_eligibility import (
    work_key_for_episode,
)
from app.models.account import Account
from app.models.work import WorkItem
from app.repositories.work_repository import WorkRepository
from app.services.work_service import TERMINAL_STATUSES
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


class HumanEscalationNotRecommendableError(Exception):
    def __init__(self, *, reason: IneligibilityReason | None):
        self.reason = reason
        super().__init__(
            "Escalonamento para humano não é mais recomendável "
            f"para este episódio (reason={reason})."
        )


class HumanEscalationReopeningNotSupportedError(Exception):
    def __init__(self):
        super().__init__(
            "Já existe um WorkItem de escalonamento encerrado "
            "(completed/cancelled) para este episódio. Reabertura "
            "do mesmo episódio não é suportada por esta fatia "
            "(P1.3)."
        )


@dataclass(frozen=True)
class HumanEscalationMaterializationResult:
    work_item: WorkItem
    created: bool
    duplicate: bool


def _title_for_episode(*, account_id: int, due_date: date) -> str:
    return (
        f"Escalar cobrança para humano — conta {account_id}, "
        f"vencimento {due_date.isoformat()}"
    )


class HumanEscalationMaterializationService:
    def __init__(self, db: Session):
        self.db = db
        self.work = WorkManagerService(db)
        self.repository = WorkRepository(db)

    def materialize(
        self,
        *,
        account: Account,
        due_date: date,
        actor: WorkActor,
    ) -> HumanEscalationMaterializationResult:
        canonical_key = work_key_for_episode(
            account.id, due_date
        )

        existing = self.repository.find_by_key(
            scope_type="account",
            work_key=canonical_key,
            account_id=account.id,
        )

        if (
            existing is not None
            and existing.status not in TERMINAL_STATUSES
        ):
            return HumanEscalationMaterializationResult(
                work_item=existing,
                created=False,
                duplicate=True,
            )

        result = get_human_escalation_eligibility(
            self.db,
            account=account,
            due_date=due_date,
        )

        if result.status == "ineligible":
            raise HumanEscalationNotRecommendableError(
                reason=result.reason
            )

        if existing is not None:
            # Único caso restante: existing é terminal e a
            # eligibility voltou a ser "eligible" -- reabertura, não
            # suportada.
            raise (
                HumanEscalationReopeningNotSupportedError()
            )

        creation = self.work.create(
            work_type="task",
            title=_title_for_episode(
                account_id=account.id, due_date=due_date
            ),
            scope_type="account",
            account_id=account.id,
            work_key=canonical_key,
            origin_type="user",
            origin_reference=result.recommendation_key,
            actor=actor,
        )

        if (
            creation.duplicate
            and creation.work_item.status in TERMINAL_STATUSES
        ):
            # Corrida: outro request venceu e o item encontrado já
            # está terminal -- mesma checagem de fail-closed aplicada
            # ao vencedor.
            raise (
                HumanEscalationReopeningNotSupportedError()
            )

        return HumanEscalationMaterializationResult(
            work_item=creation.work_item,
            created=creation.created,
            duplicate=creation.duplicate,
        )
