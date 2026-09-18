"""
P1.2B -- Human Account Mark-Overdue Materialization Service V1.

Corredor humano estreito e paralelo ao corredor agent-only existente
(25M/25O, AccountMarkOverdueExecutionService, WorkSkillExecutionService,
GovernedSkillExecutionService) para a skill account.mark_overdue.
Nenhuma dessas pecas agent-only e chamada, generalizada ou modificada
aqui.

Responsabilidade unica: dado (account, due_date, sessao autenticada),
revalidar elegibilidade e autoridade, e materializar -- de forma
idempotente e convergente entre operadores diferentes -- exatamente um
WorkItem canonico do episodio mais exatamente uma ApprovalRequest
humana vinculada a ele. Nunca executa a mutacao financeira (isso
pertence a HumanAccountMarkOverdueExecutionService, peca separada por
decisao explicita do Architecture Freeze: uma Approval aprovada nao
dispara mutacao como efeito colateral).

Identidade canonica do episodio -- unica implementacao, nunca
reconstruida em outro lugar:

    work_key_for_episode(account_id, due_date)
    -> "account_mark_overdue:v1:{account_id}:{due_date.isoformat()}"

Usada tanto como WorkItem.work_key quanto como WorkItem.origin_reference
(mesma string, semantica distinta -- decisao explicita do Freeze: nao
fabricar uma segunda chave so para diferencia-los, ja que
get_mark_overdue_eligibility() nao expoe nenhuma recommendation_key
propria, diferente do precedente de PR-6A/human_escalation).

Convergencia entre operadores diferentes (invariante 3 do Freeze) vem
de uq_work_items_account_key (account_id, work_key) -- nao da
idempotencia de ApprovalRequest, que e por requester e portanto nao
converge entre usuarios diferentes. Por isso a ApprovalRequest so e
criada uma vez, pelo vencedor real da criacao do WorkItem
(creation.created is True) -- nunca por quem apenas encontrou um
WorkItem ja existente (duplicate=True, seja por pre-check ou por
corrida resolvida dentro de WorkManagerService.create()).

Terminalidade (aprendizado explicito do PR-6A, aplicado aqui desde o
Freeze): WorkItem terminal para o mesmo episodio + elegibilidade que
voltou a "elegivel" -> 409 fail-closed. Reabertura pertence a P1.3,
nao e resolvida silenciosamente aqui.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import is_session_elevated
from app.core.governed_financial_action_eligibility import (
    MarkOverdueReason,
)
from app.core.governed_financial_action_eligibility import (
    get_mark_overdue_eligibility,
)
from app.core.skill_authorization import authorize_skill_execution
from app.models.account import Account
from app.models.approval import ApprovalRequest
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.work import WorkItem
from app.repositories.skill_repository import SkillRepository
from app.repositories.work_repository import WorkRepository
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.work_service import TERMINAL_STATUSES
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService

SKILL_KEY = "account.mark_overdue"


class HumanMarkOverdueNotRecommendableError(Exception):
    def __init__(self, *, reason: MarkOverdueReason | None):
        self.reason = reason
        super().__init__(
            "account.mark_overdue não é mais recomendável para "
            f"este episódio (reason={reason})."
        )


class HumanMarkOverdueReopeningNotSupportedError(Exception):
    def __init__(self):
        super().__init__(
            "Já existe um WorkItem de mark_overdue encerrado "
            "(completed/cancelled) para este episódio. Reabertura "
            "do mesmo episódio não é suportada por esta fatia "
            "(P1.3)."
        )


class HumanMarkOverdueCatalogError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


class HumanMarkOverdueMaterializationConflictError(Exception):
    def __init__(self, message: str):
        super().__init__(message)


@dataclass(frozen=True)
class HumanAccountMarkOverdueMaterializationResult:
    work_item: WorkItem
    approval_request: ApprovalRequest
    created: bool
    duplicate: bool


def work_key_for_episode(account_id: int, due_date: date) -> str:
    """
    Autoridade única para o work_key reservado desta fatia -- o
    executor (peça separada) consome este mesmo formato, nunca o
    reconstrói de outra forma.
    """

    return (
        f"account_mark_overdue:v1:{account_id}:"
        f"{due_date.isoformat()}"
    )


def _title_for_episode(*, account_id: int, due_date: date) -> str:
    return (
        f"Marcar conta como atrasada — conta {account_id}, "
        f"vencimento {due_date.isoformat()}"
    )


class HumanAccountMarkOverdueMaterializationService:
    def __init__(self, db: Session):
        self.db = db
        self.work = WorkManagerService(db)
        self.work_repository = WorkRepository(db)
        self.skill_repository = SkillRepository(db)
        self.approvals = ApprovalService(db)

    def _resolve_published_version(
        self,
    ) -> tuple[SkillVersion, SkillDefinition]:
        skill = self.skill_repository.find_skill_by_key(SKILL_KEY)

        if skill is None or skill.status != "active":
            raise HumanMarkOverdueCatalogError(
                f"Skill '{SKILL_KEY}' não registrada/ativa no "
                "catálogo."
            )

        published = [
            version
            for version in self.skill_repository.list_versions(
                skill.id
            )
            if version.status == "published"
        ]

        if len(published) != 1:
            raise HumanMarkOverdueCatalogError(
                f"Versão publicada de '{SKILL_KEY}' ausente ou "
                "ambígua."
            )

        return published[0], skill

    def _load_associated_approval(
        self, work_item: WorkItem
    ) -> ApprovalRequest:
        context = (
            work_item.context_data
            if isinstance(work_item.context_data, dict)
            else {}
        )
        approval_request_id = context.get("approval_request_id")

        if approval_request_id is None:
            raise HumanMarkOverdueMaterializationConflictError(
                "WorkItem canônico sem ApprovalRequest associada "
                "(janela de corrida ou estado inconsistente)."
            )

        approval_request = self.db.get(
            ApprovalRequest, approval_request_id
        )

        if approval_request is None:
            raise HumanMarkOverdueMaterializationConflictError(
                "ApprovalRequest referenciada pelo WorkItem não "
                "existe mais."
            )

        return approval_request

    def materialize(
        self,
        *,
        account: Account,
        due_date: date,
        authenticated: AuthenticatedSession,
    ) -> HumanAccountMarkOverdueMaterializationResult:
        canonical_key = work_key_for_episode(account.id, due_date)

        existing = self.work_repository.find_by_key(
            scope_type="account",
            work_key=canonical_key,
            account_id=account.id,
        )

        if (
            existing is not None
            and existing.status not in TERMINAL_STATUSES
        ):
            return (
                HumanAccountMarkOverdueMaterializationResult(
                    work_item=existing,
                    approval_request=(
                        self._load_associated_approval(existing)
                    ),
                    created=False,
                    duplicate=True,
                )
            )

        eligibility = get_mark_overdue_eligibility(
            self.db, account=account, due_date=due_date
        )

        if eligibility.reason is not None:
            raise HumanMarkOverdueNotRecommendableError(
                reason=eligibility.reason
            )

        if existing is not None:
            # Único caso restante: existing é terminal e a
            # elegibilidade voltou a ser válida -- reabertura, não
            # suportada por esta fatia.
            raise HumanMarkOverdueReopeningNotSupportedError()

        version, _skill = self._resolve_published_version()

        input_payload = {
            "account_id": account.id,
            "due_date": due_date.isoformat(),
        }

        # Falha fechada de autoridade ANTES de qualquer escrita:
        # work:create (rota) não substitui a autorização cumulativa
        # de account.mark_overdue (skill:execute + skill:execute_
        # mutating + clients.manage). SkillAuthorizationError/
        # SkillScopeNotFoundError propagam para a rota sem captura
        # aqui -- nenhum RBAC paralelo é implementado.
        authorize_skill_execution(
            db=self.db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            session_elevated=is_session_elevated(
                authenticated.session
            ),
            version_id=version.id,
            input_payload=input_payload,
        )

        actor = WorkActor(
            actor_type="user",
            actor_reference=f"user:{authenticated.user.id}",
            actor_user_id=authenticated.user.id,
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
            origin_reference=canonical_key,
            actor=actor,
            context_data={
                "account_id": account.id,
                "due_date": due_date.isoformat(),
                "skill_key": SKILL_KEY,
            },
        )

        if creation.duplicate:
            if creation.work_item.status in TERMINAL_STATUSES:
                raise (
                    HumanMarkOverdueReopeningNotSupportedError()
                )

            return (
                HumanAccountMarkOverdueMaterializationResult(
                    work_item=creation.work_item,
                    approval_request=(
                        self._load_associated_approval(
                            creation.work_item
                        )
                    ),
                    created=False,
                    duplicate=True,
                )
            )

        # Vencedor real da criação -- única execução que cria a
        # ApprovalRequest (convergência entre operadores vem de
        # uq_work_items_account_key acima, não da idempotência da
        # ApprovalRequest, que é por requester).
        approval_idempotency_key = (
            "human_mark_overdue_approval:v1:"
            f"{account.id}:{due_date.isoformat()}"
        )
        approval_result = (
            self.approvals.create_skill_execution_request(
                version_id=version.id,
                requester=ApprovalRequester(
                    actor_type="user",
                    actor_reference=(
                        f"user:{authenticated.user.id}"
                    ),
                    actor_user_id=authenticated.user.id,
                ),
                input_payload=input_payload,
                idempotency_key=approval_idempotency_key,
            )
        )

        context = dict(creation.work_item.context_data or {})
        context["approval_request_id"] = approval_result.request.id

        update_result = self.work.update_details(
            creation.work_item.id,
            expected_version=creation.work_item.version,
            actor=actor,
            title=creation.work_item.title,
            description=creation.work_item.description,
            context_data=context,
        )

        ready_result = self.work.transition_status(
            update_result.work_item.id,
            expected_version=update_result.work_item.version,
            actor=actor,
            status="ready",
        )

        return HumanAccountMarkOverdueMaterializationResult(
            work_item=ready_result.work_item,
            approval_request=approval_result.request,
            created=True,
            duplicate=False,
        )
