"""
VALUE-2.3 -- Escalation Observation Service V1.

Unico ponto de escrita de app.models.escalation_observation.
EscalationObservation. Dois pontos de entrada estruturalmente
separados -- record_observed_fact() nunca aceita assessment_code,
record_human_assessment() nunca aceita account_event -- para que seja
estruturalmente impossivel um servico confundir um fato mecanico com
uma declaracao humana (VALUE-2.1/2.2).

observed_fact e sempre idempotente por construcao: a chave e sempre
derivada deterministicamente do AccountEvent vinculado, nunca deixada
em branco. human_assessment aceita uma Idempotency-Key opcional,
fornecida pelo chamador (mesmo mecanismo ja usado por
WorkItem/WorkEvent, nunca reinventado aqui) -- ausencia de chave
sempre cria uma nova observacao (0..N legitimo).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account_event import AccountEvent
from app.models.escalation_observation import ASSESSMENT_CODES
from app.models.escalation_observation import EscalationObservation
from app.models.work import WorkItem
from app.services.work_service import WorkActor


WORK_KEY_PREFIX = "human_escalation:v1:"

ALLOWED_OBSERVED_FACT_NEW_STATUS = ("pago",)


class EscalationObservationValidationError(Exception):
    pass


class EscalationObservationConflictError(Exception):
    """
    Mesma Idempotency-Key associada a conteudo divergente -- nunca
    resolvido silenciosamente (mesma filosofia fail-closed de
    first-association-wins ja usada pelo corredor de mark_overdue).
    """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EscalationObservationResult:
    observation: EscalationObservation
    created: bool
    duplicate: bool


class EscalationObservationService:
    def __init__(self, db: Session):
        self.db = db

    def _validate_escalation_work_item(
        self, escalation_work_item: WorkItem
    ) -> None:
        if escalation_work_item.scope_type != "account":
            raise EscalationObservationValidationError(
                "WorkItem informado não é de escopo 'account'."
            )
        work_key = escalation_work_item.work_key or ""
        if not work_key.startswith(WORK_KEY_PREFIX):
            raise EscalationObservationValidationError(
                "WorkItem informado não é um episódio de "
                "escalonamento (work_key fora do padrão "
                f"'{WORK_KEY_PREFIX}*')."
            )

    def record_observed_fact(
        self,
        *,
        escalation_work_item: WorkItem,
        account_event: AccountEvent,
        idempotency_key: str | None = None,
    ) -> EscalationObservationResult:
        self._validate_escalation_work_item(escalation_work_item)

        if account_event.account_id != escalation_work_item.account_id:
            raise EscalationObservationValidationError(
                "AccountEvent pertence a uma conta diferente do "
                "WorkItem de escalonamento."
            )

        if account_event.new_status not in (
            ALLOWED_OBSERVED_FACT_NEW_STATUS
        ):
            raise EscalationObservationValidationError(
                "AccountEvent.new_status "
                f"{account_event.new_status!r} não é um fato "
                "posterior reconhecido para observed_fact."
            )

        if escalation_work_item.created_at is None or (
            account_event.occurred_at
            < escalation_work_item.created_at
        ):
            raise EscalationObservationValidationError(
                "AccountEvent é anterior à criação do episódio de "
                "escalonamento -- não pode ser observação DESTE "
                "escalonamento."
            )

        key = idempotency_key or (
            "escalation_observation:observed_fact:"
            f"account_event:{account_event.id}"
        )

        observation = EscalationObservation(
            escalation_work_item_id=escalation_work_item.id,
            observation_type="observed_fact",
            linked_account_event_id=account_event.id,
            observed_at=account_event.occurred_at,
            idempotency_key=key,
        )

        return self._insert_or_reconcile(
            observation,
            key,
            expected_linked_account_event_id=account_event.id,
        )

    def record_human_assessment(
        self,
        *,
        escalation_work_item: WorkItem,
        assessment_code: str,
        actor: WorkActor,
        declared_by_role: str,
        idempotency_key: str | None = None,
    ) -> EscalationObservationResult:
        self._validate_escalation_work_item(escalation_work_item)

        if assessment_code not in ASSESSMENT_CODES:
            raise EscalationObservationValidationError(
                f"assessment_code inválido: {assessment_code!r}."
            )

        if actor.actor_user_id is None:
            raise EscalationObservationValidationError(
                "human_assessment exige um actor com actor_user_id "
                "definido."
            )

        observation = EscalationObservation(
            escalation_work_item_id=escalation_work_item.id,
            observation_type="human_assessment",
            assessment_code=assessment_code,
            declared_by_user_id=actor.actor_user_id,
            declared_by_role=declared_by_role,
            declared_at=_utc_now(),
            idempotency_key=idempotency_key,
        )

        return self._insert_or_reconcile(
            observation,
            idempotency_key,
            expected_assessment_code=assessment_code,
        )

    def _find_by_key(
        self, escalation_work_item_id: int, key: str
    ) -> EscalationObservation | None:
        return (
            self.db.query(EscalationObservation)
            .filter(
                EscalationObservation.escalation_work_item_id
                == escalation_work_item_id,
                EscalationObservation.idempotency_key == key,
            )
            .one_or_none()
        )

    def _insert_or_reconcile(
        self,
        observation: EscalationObservation,
        key: str | None,
        *,
        expected_linked_account_event_id: int | None = None,
        expected_assessment_code: str | None = None,
    ) -> EscalationObservationResult:
        try:
            self.db.add(observation)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            if key is None:
                # Sem chave, uma colisao so pode vir de outra causa
                # (ex.: FK invalida) -- nao ha nada para reconciliar
                # por idempotencia, propaga.
                raise

            existing = self._find_by_key(
                observation.escalation_work_item_id, key
            )
            if existing is None:
                raise

            if (
                expected_linked_account_event_id is not None
                and existing.linked_account_event_id
                != expected_linked_account_event_id
            ) or (
                expected_assessment_code is not None
                and existing.assessment_code
                != expected_assessment_code
            ):
                raise EscalationObservationConflictError(
                    f"Idempotency-Key {key!r} já está associada a "
                    "uma observação com conteúdo divergente."
                )

            return EscalationObservationResult(
                observation=existing,
                created=False,
                duplicate=True,
            )

        return EscalationObservationResult(
            observation=observation,
            created=True,
            duplicate=False,
        )
