from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Callable
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalRequest
from app.models.business_effect_verification import (
    BusinessEffectVerification,
)
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.repositories.business_effect_verification_repository import (
    BusinessEffectVerificationRepository,
)


class BusinessEffectVerificationError(Exception):
    """Erro de validação/estado do Business Effect Verification V1."""


class SkillEffectContract(NamedTuple):
    expected_status: str
    build_event_key: Callable[[int], str]


def _mark_overdue_event_key(approval_request_id: int) -> str:
    return (
        "account_event:effect:human_account_mark_overdue:"
        f"approval:{approval_request_id}"
    )


def _mark_paid_event_key(approval_request_id: int) -> str:
    return f"account_event:approval:{approval_request_id}"


SKILL_EFFECT_CONTRACTS: dict[str, SkillEffectContract] = {
    "account.mark_overdue": SkillEffectContract(
        expected_status="atrasado",
        build_event_key=_mark_overdue_event_key,
    ),
    "account.mark_paid": SkillEffectContract(
        expected_status="pago",
        build_event_key=_mark_paid_event_key,
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(
    value: int,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise BusinessEffectVerificationError(
            f"{field_name} inválido."
        )
    return value


@dataclass(frozen=True)
class BusinessEffectVerificationResult:
    verification: BusinessEffectVerification
    duplicate: bool


class BusinessEffectVerificationService:
    """
    DW-3 V1 -- verifica, exclusivamente a partir de estado ja commitado,
    se o efeito de negocio esperado de um ApprovalConsumption governado
    (account.mark_overdue / account.mark_paid) esta presente. Nunca
    executa, nunca despacha, nunca escreve Account/AccountEvent, nunca
    cria/altera WorkOutcomeEvaluation, MemoryItem ou learning_signal.
    Fora dos dois corredores V1, a skill e ignorada (SKIP), nunca
    forcada a um resultado.

    Resultados terminais (verified/contradicted/unverifiable) sao
    imutaveis: uma vez alcancados, uma nova chamada devolve a mesma
    linha sem reavaliar, mesmo que o estado empresarial atual tenha
    mudado depois por um caminho legitimo (ex.: overdue -> pago).
    Apenas 'pending' pode ser reavaliado.
    """

    def __init__(
        self,
        db: Session,
        *,
        repository: (
            BusinessEffectVerificationRepository | None
        ) = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else BusinessEffectVerificationRepository(db)
        )

    def verify(
        self,
        approval_consumption_id: int,
    ) -> BusinessEffectVerificationResult:
        normalized_id = _positive_id(
            approval_consumption_id,
            field_name="approval_consumption_id",
        )

        # FOR UPDATE aqui (nao um simples SELECT) -- e o unico ponto de
        # serializacao real entre duas transacoes que leem a MESMA linha
        # PENDING e tentam transiciona-la concorrentemente.
        # UNIQUE(approval_consumption_id) so protege contra duas LINHAS,
        # nunca contra duas transicoes da mesma linha. O lock cobre
        # apenas a avaliacao/transicao abaixo (leituras 100% locais ao
        # DB, sem espera externa) e e liberado no commit.
        existing = self.repository.lock_by_consumption_id(
            normalized_id
        )
        if existing is not None and existing.result != "pending":
            return BusinessEffectVerificationResult(
                verification=existing,
                duplicate=True,
            )

        consumption = self.db.get(
            ApprovalConsumption, normalized_id
        )
        if consumption is None:
            raise BusinessEffectVerificationError(
                "ApprovalConsumption não encontrado."
            )
        if consumption.status != "consumed":
            raise BusinessEffectVerificationError(
                "ApprovalConsumption não está consumido."
            )

        request = self.db.get(
            ApprovalRequest, consumption.approval_request_id
        )
        if request is None:
            raise BusinessEffectVerificationError(
                "ApprovalRequest não encontrado."
            )
        version = self.db.get(
            SkillVersion, request.skill_version_id
        )
        if version is None:
            raise BusinessEffectVerificationError(
                "SkillVersion não encontrada."
            )
        skill = self.db.get(
            SkillDefinition, version.skill_id
        )
        if skill is None:
            raise BusinessEffectVerificationError(
                "SkillDefinition não encontrada."
            )

        contract = SKILL_EFFECT_CONTRACTS.get(
            skill.skill_key
        )
        if contract is None:
            raise BusinessEffectVerificationError(
                "skill_key fora do escopo DW-3 V1 "
                f"({skill.skill_key!r})."
            )

        expected_status = contract.expected_status
        event_key = contract.build_event_key(
            request.id
        )
        target_account_id = request.target_account_id

        (
            result,
            account_status_observed,
            account_event_id,
        ) = self._evaluate_effect(
            target_account_id=target_account_id,
            expected_status=expected_status,
            event_key=event_key,
        )

        checked_at = (
            None if result == "pending" else _utc_now()
        )

        if existing is not None:
            existing.target_account_id = target_account_id
            existing.expected_status = expected_status
            existing.account_event_key_searched = event_key
            existing.account_event_id = account_event_id
            existing.account_status_observed = (
                account_status_observed
            )
            existing.result = result
            existing.checked_at = checked_at
            self.db.flush()
            self.db.commit()
            return BusinessEffectVerificationResult(
                verification=existing,
                duplicate=False,
            )

        verification = BusinessEffectVerification(
            approval_consumption_id=normalized_id,
            skill_key=skill.skill_key,
            target_account_id=target_account_id,
            expected_status=expected_status,
            account_event_id=account_event_id,
            account_event_key_searched=event_key,
            account_status_observed=account_status_observed,
            result=result,
            checked_at=checked_at,
        )
        try:
            self.repository.add(verification)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raced = self.repository.get_by_consumption_id(
                normalized_id
            )
            if raced is None:
                raise
            return BusinessEffectVerificationResult(
                verification=raced,
                duplicate=True,
            )

        return BusinessEffectVerificationResult(
            verification=verification,
            duplicate=False,
        )

    def _evaluate_effect(
        self,
        *,
        target_account_id: int | None,
        expected_status: str,
        event_key: str,
    ) -> tuple[str, str | None, int | None]:
        if target_account_id is None:
            return "unverifiable", None, None

        account = self.db.get(Account, target_account_id)
        if account is None:
            return "unverifiable", None, None

        matches = (
            self.db.execute(
                select(AccountEvent).where(
                    AccountEvent.idempotency_key == event_key
                )
            )
            .scalars()
            .all()
        )

        if len(matches) == 0:
            return "pending", account.status, None

        if len(matches) > 1:
            return "contradicted", account.status, None

        event = matches[0]
        if (
            event.account_id != target_account_id
            or event.new_status != expected_status
        ):
            return (
                "contradicted",
                account.status,
                event.id,
            )

        if account.status != expected_status:
            return (
                "contradicted",
                account.status,
                event.id,
            )

        return "verified", account.status, event.id
