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
from app.core.work_errors import WorkConflictError
from app.models.account import Account
from app.models.nba_recommendation_snapshot import (
    NbaRecommendationSnapshot,
)
from app.models.work import WorkItem
from app.repositories.work_repository import WorkRepository
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotIntegrityError,
)
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotService,
)
from app.services.work_service import TERMINAL_STATUSES
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService

ESCALATE_TO_HUMAN_ACTION_KEY = "escalate_to_human"


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


class HumanEscalationSnapshotReferenceInvalidError(Exception):
    """
    VALUE-2.3 -- recommendation_snapshot_id fornecido não corresponde
    a nenhum NbaRecommendationSnapshot existente. Mesmo tratamento de
    DW-6.4B para o corredor de mark_overdue: entrada inválida da
    requisição (422), não 404 -- o snapshot é uma referência dentro do
    corpo da solicitação, não um recurso próprio.
    """

    def __init__(self, message: str):
        super().__init__(message)


class HumanEscalationSnapshotConflictError(Exception):
    """
    VALUE-2.3 -- recommendation_snapshot_id fornecido existe e tem
    integridade válida, mas diverge de forma que impede associá-lo
    (integridade, conta, episódio, ação não recomendada, ou provenance
    já declarada anteriormente de forma incompatível). Mesmo
    tratamento de DW-6.4B (409).
    """

    def __init__(self, message: str):
        super().__init__(message)


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

    def _validate_snapshot_reference(
        self,
        recommendation_snapshot_id: int,
        *,
        account_id: int,
        due_date: date,
    ) -> NbaRecommendationSnapshot:
        """
        VALUE-2.3 -- valida a referência ANTES de ela poder
        influenciar/criar estado. Sempre passa por get_verified()
        (DW-6.4A) -- nunca aceita a referência só porque o inteiro
        coincide com uma já associada. Espelha 1:1
        HumanAccountMarkOverdueMaterializationService._validate_
        snapshot_reference, trocando apenas a action_key exigida.
        """
        snapshot_service = NbaRecommendationSnapshotService(self.db)

        try:
            snapshot = snapshot_service.get_verified(
                recommendation_snapshot_id
            )
        except NbaRecommendationSnapshotIntegrityError as error:
            raise HumanEscalationSnapshotConflictError(
                f"recommendation_snapshot_id "
                f"{recommendation_snapshot_id} falhou verificação "
                "de integridade."
            ) from error

        if snapshot is None:
            raise HumanEscalationSnapshotReferenceInvalidError(
                f"recommendation_snapshot_id "
                f"{recommendation_snapshot_id} não existe."
            )

        if snapshot.account_id != account_id:
            raise HumanEscalationSnapshotConflictError(
                f"recommendation_snapshot_id "
                f"{recommendation_snapshot_id} pertence a outra "
                "conta."
            )

        if snapshot.due_date != due_date:
            raise HumanEscalationSnapshotConflictError(
                f"recommendation_snapshot_id "
                f"{recommendation_snapshot_id} pertence a outro "
                "episódio (due_date divergente)."
            )

        payload = (
            snapshot.snapshot_payload
            if isinstance(snapshot.snapshot_payload, dict)
            else {}
        )
        selected_actions = (
            payload.get("decision", {}).get(
                "selected_actions", []
            )
        )

        if ESCALATE_TO_HUMAN_ACTION_KEY not in selected_actions:
            raise HumanEscalationSnapshotConflictError(
                f"recommendation_snapshot_id "
                f"{recommendation_snapshot_id} não recomenda "
                f"'{ESCALATE_TO_HUMAN_ACTION_KEY}'."
            )

        return snapshot

    def _reconcile_existing_association(
        self,
        existing: WorkItem,
        recommendation_snapshot_id: int | None,
        *,
        account_id: int,
        due_date: date,
    ) -> None:
        """
        VALUE-2.3 -- first association wins, espelhando 1:1 o
        precedente de mark_overdue (DW-6.4B).
        """
        context = (
            existing.context_data
            if isinstance(existing.context_data, dict)
            else {}
        )
        stored_snapshot_id = context.get(
            "recommendation_snapshot_id"
        )

        if recommendation_snapshot_id is None:
            return

        self._validate_snapshot_reference(
            recommendation_snapshot_id,
            account_id=account_id,
            due_date=due_date,
        )

        if stored_snapshot_id is None:
            raise HumanEscalationSnapshotConflictError(
                "Este episódio já foi materializado sem associação "
                "de recomendação; não é permitido anexar "
                "recommendation_snapshot_id retroativamente."
            )

        if stored_snapshot_id != recommendation_snapshot_id:
            raise HumanEscalationSnapshotConflictError(
                "Este episódio já está associado a outra "
                "recommendation_snapshot_id; a referência declarada "
                "diverge da provenance original."
            )

    def materialize(
        self,
        *,
        account: Account,
        due_date: date,
        actor: WorkActor,
        recommendation_snapshot_id: int | None = None,
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
            self._reconcile_existing_association(
                existing,
                recommendation_snapshot_id,
                account_id=account.id,
                due_date=due_date,
            )
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

        # VALUE-2.3: valida a referência ANTES de qualquer criação de
        # estado -- se inválida, nada é criado.
        validated_snapshot: NbaRecommendationSnapshot | None = None
        if recommendation_snapshot_id is not None:
            validated_snapshot = self._validate_snapshot_reference(
                recommendation_snapshot_id,
                account_id=account.id,
                due_date=due_date,
            )

        initial_context_data: dict[str, object] = {}
        if validated_snapshot is not None:
            initial_context_data["recommendation_snapshot_id"] = (
                validated_snapshot.id
            )

        try:
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
                context_data=initial_context_data,
            )
        except WorkConflictError:
            # WorkManagerService.create() tem sua propria verificacao
            # de fingerprint de conteudo: quando dois criadores
            # concorrentes do MESMO work_key usam context_data
            # DIFERENTE (ex.: com-snapshot vs sem-snapshot, ou dois
            # snapshots diferentes), ela recusa com WorkConflictError
            # em vez de tratar como duplicate simples. Recupera o
            # vencedor real e aplica a MESMA regra first-association-
            # wins do caminho idempotente normal.
            concurrent = self.repository.find_by_key(
                scope_type="account",
                work_key=canonical_key,
                account_id=account.id,
            )
            if concurrent is None:
                raise

            if concurrent.status in TERMINAL_STATUSES:
                raise HumanEscalationReopeningNotSupportedError()

            self._reconcile_existing_association(
                concurrent,
                recommendation_snapshot_id,
                account_id=account.id,
                due_date=due_date,
            )

            return HumanEscalationMaterializationResult(
                work_item=concurrent,
                created=False,
                duplicate=True,
            )

        if creation.duplicate:
            if creation.work_item.status in TERMINAL_STATUSES:
                # Corrida: outro request venceu e o item encontrado
                # já está terminal -- mesma checagem de fail-closed
                # aplicada ao vencedor.
                raise (
                    HumanEscalationReopeningNotSupportedError()
                )

            self._reconcile_existing_association(
                creation.work_item,
                recommendation_snapshot_id,
                account_id=account.id,
                due_date=due_date,
            )

        return HumanEscalationMaterializationResult(
            work_item=creation.work_item,
            created=creation.created,
            duplicate=creation.duplicate,
        )
