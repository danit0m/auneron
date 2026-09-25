"""
DW-7.3 V1 -- Policy-governed execution corridor for account.mark_overdue.

Terceiro corredor irmao do humano (HumanAccountMarkOverdueExecutionService)
e do agente (AccountMarkOverdueExecutionService, sem trafego real) -- NAO
uma extensao do motor generico 24C/24D. `account.mark_overdue` ja e
bloqueado por nome em GovernedSkillExecutionService.execute() porque o
corredor humano real nunca passou por ali; o mesmo vale aqui (DW-7.2,
achado central).

Nao usa WorkItem/WorkEvent (decisao explicita do Implementation Plan):
nao ha fila humana a representar, e UNIQUE(episode_scope_key) em
PolicyAuthorityConsumption ja cumpre sozinho o papel de unicidade por
episodio que uq_work_items_account_key cumpre para o corredor humano.

Ordem de lock fixa (B2, DW-7.2): Grant FOR UPDATE sempre antes de
Account FOR UPDATE, nos dois fluxos que tocam ambos -- nunca o inverso,
o que torna a ordem livre de deadlock por construcao (revogacao so toca
Grant).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.autonomy_policy import classify_skill_risk
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.policy_authority_consumption import (
    PolicyAuthorityConsumption,
)
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.skill import SkillInvocation
from app.repositories.skill_repository import SkillRepository
from app.services.approval_service import approval_input_identity
from app.services.human_account_mark_overdue_materialization_service import (
    work_key_for_episode,
)
from app.services.skill_runtime import _canonical_json
from app.services.skill_runtime import _digest_bytes
from app.services.skill_runtime import _fingerprint
from app.services.skill_runtime import SkillInvocationActor

SKILL_KEY = ACCOUNT_MARK_OVERDUE_POLICY_V1.skill_key
PROVIDER = "auneron.core"
HANDLER_REFERENCE = "app.skills.account:mark_overdue"
CAPABILITY_KEY = "account.status.mark_overdue"
CONSUMER_ACTOR_TYPE = "system"
CONSUMER_REFERENCE = "system:policy_account_mark_overdue_execution"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(value: Any, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ApprovalValidationError(f"{field_name} inválido.")
    return value


def _event_receipt_key(episode_scope_key: str) -> str:
    return (
        "account_event:effect:policy_account_mark_overdue:"
        f"{episode_scope_key}"
    )


@dataclass(frozen=True)
class PolicyAccountMarkOverdueExecutionResult:
    policy_authority_grant_id: int
    policy_authority_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: dict[str, Any]


class PolicyAccountMarkOverdueExecutionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.skills = SkillRepository(db)

    def _validate_catalog(self, *, skill, version, capabilities) -> None:
        if (
            skill.skill_key != SKILL_KEY
            or skill.provider != PROVIDER
            or skill.status != "active"
            or version.status != "published"
            or version.runtime_kind != "internal_python"
            or version.execution_mode != "mutating"
            or version.handler_reference != HANDLER_REFERENCE
        ):
            raise ApprovalStateError(
                "Catálogo da skill account.mark_overdue divergente "
                "do esperado."
            )
        matches = [
            capability
            for capability in capabilities
            if capability.capability_key == CAPABILITY_KEY
            and capability.access_mode == "write"
            and capability.resource_scope == "account"
        ]
        if len(matches) != 1:
            raise ApprovalStateError(
                "Capability account.status.mark_overdue ausente ou "
                "ambígua."
            )

    def execute(
        self,
        *,
        account_id: int,
        due_date: date,
        now: datetime | None = None,
    ) -> PolicyAccountMarkOverdueExecutionResult:
        normalized_account_id = _positive_id(
            account_id, field_name="account_id"
        )
        effective_now = now if now is not None else _utc_now()
        if effective_now.tzinfo is None:
            raise ApprovalValidationError(
                "now deve possuir timezone."
            )

        episode_scope_key = work_key_for_episode(
            normalized_account_id, due_date
        )

        # Replay: recibo e a propria linha de consumo (B3 -- so existe
        # se o efeito ja foi commitado atomicamente com ela).
        existing_consumption = self.db.execute(
            select(PolicyAuthorityConsumption).where(
                PolicyAuthorityConsumption.episode_scope_key
                == episode_scope_key
            )
        ).scalar_one_or_none()
        if existing_consumption is not None:
            invocation = self.db.get(
                SkillInvocation,
                existing_consumption.skill_invocation_id,
            )
            if invocation is None or invocation.status != "succeeded":
                raise ApprovalConsumptionConflictError(
                    "Recibo de execução e estado atual divergem."
                )
            output = (
                invocation.output_payload
                if isinstance(invocation.output_payload, dict)
                else {}
            )
            return PolicyAccountMarkOverdueExecutionResult(
                policy_authority_grant_id=(
                    existing_consumption.policy_authority_grant_id
                ),
                policy_authority_consumption_id=(
                    existing_consumption.id
                ),
                invocation_id=invocation.id,
                invocation_status=invocation.status,
                duplicate=True,
                output=output,
            )

        # B2: trava a autoridade primeiro, sempre antes de Account.
        grant = self.db.execute(
            select(PolicyAuthorityGrant)
            .where(
                PolicyAuthorityGrant.policy_key
                == ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
                PolicyAuthorityGrant.skill_key == SKILL_KEY,
                PolicyAuthorityGrant.state == "active",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if grant is None:
            raise ApprovalAuthorizationError(
                "Nenhum PolicyAuthorityGrant ativo para "
                f"{SKILL_KEY}."
            )
        if grant.expires_at <= effective_now:
            raise ApprovalAuthorizationError(
                "PolicyAuthorityGrant expirado."
            )
        if (
            grant.policy_version
            != ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_version
        ):
            raise ApprovalStateError(
                "policy_version do grant diverge da Policy "
                "Definition corrente."
            )
        if grant.scope_type != "deployment_wide":
            raise ApprovalStateError(
                "scope_type do grant não é reconhecido por este "
                "corredor."
            )

        # B2: trava o recurso de negócio depois.
        account = self.db.execute(
            select(Account)
            .where(Account.id == normalized_account_id)
            .with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise ApprovalNotFoundError("Conta inexistente.")
        if account.status == "atrasado":
            raise ApprovalConsumptionConflictError(
                "Conta já está atrasada sem recibo de execução "
                "policy-governed correspondente."
            )
        if account.status != "aberto":
            raise ApprovalStateError(
                "Apenas contas 'aberto' podem transicionar."
            )
        if account.vencimento != due_date:
            raise ApprovalConsumptionConflictError(
                "Vencimento da conta diverge do episódio solicitado."
            )
        if account.vencimento >= date.today():
            raise ApprovalStateError("Conta não está atrasada.")

        evidence_payload = {
            "account_id": account.id,
            "due_date": due_date.isoformat(),
            "account_status": account.status,
            "vencimento": account.vencimento.isoformat(),
        }
        _normalized_evidence, evidence_digest = (
            approval_input_identity(evidence_payload)
        )

        skill = self.skills.find_skill_by_key(SKILL_KEY)
        if skill is None or skill.status != "active":
            raise ApprovalNotFoundError(
                f"Skill {SKILL_KEY!r} inexistente ou inativa."
            )
        version = self._resolve_published_version(skill.id)
        capabilities = tuple(
            self.skills.list_capabilities(version.id)
        )
        self._validate_catalog(
            skill=skill, version=version, capabilities=capabilities
        )

        risk_level, _required_permission = classify_skill_risk(
            version=version, capabilities=capabilities
        )
        if risk_level != "high":
            raise ApprovalStateError(
                "Classificação de risco divergente do esperado "
                "para account.mark_overdue."
            )

        input_payload = {
            "account_id": normalized_account_id,
            "due_date": due_date.isoformat(),
        }
        normalized_input, input_digest = approval_input_identity(
            input_payload
        )

        runtime_key = f"policy:{grant.id}:{episode_scope_key}"
        if (
            self.skills.find_invocation_by_idempotency(
                version_id=version.id,
                actor_type=CONSUMER_ACTOR_TYPE,
                actor_reference=CONSUMER_REFERENCE,
                idempotency_key=runtime_key,
            )
            is not None
        ):
            raise ApprovalConsumptionConflictError(
                "Invocation existente sem consumo correspondente."
            )

        actor = SkillInvocationActor(
            actor_type=CONSUMER_ACTOR_TYPE,
            actor_reference=CONSUMER_REFERENCE,
            actor_user_id=None,
        )
        output = {
            "action": SKILL_KEY,
            "account_id": account.id,
            "previous_status": "aberto",
            "new_status": "atrasado",
            "changed": True,
        }
        normalized_output, output_bytes = _canonical_json(
            output,
            field_name="output",
            max_bytes=version.max_output_bytes,
        )
        invocation = SkillInvocation(
            skill_version_id=version.id,
            actor_type=CONSUMER_ACTOR_TYPE,
            actor_reference=CONSUMER_REFERENCE,
            actor_user_id=None,
            idempotency_key=runtime_key,
            request_fingerprint=_fingerprint(
                version=version,
                actor=actor,
                normalized_input=normalized_input,
            ),
            input_digest=input_digest,
            status="succeeded",
            output_payload=normalized_output,
            output_digest=_digest_bytes(output_bytes),
            output_bytes=len(output_bytes),
            error_code=None,
            duration_ms=0,
            started_at=effective_now,
            finished_at=effective_now,
        )
        try:
            self.db.add(invocation)
            self.db.flush()

            consumption = PolicyAuthorityConsumption(
                policy_authority_grant_id=grant.id,
                skill_invocation_id=invocation.id,
                episode_scope_key=episode_scope_key,
                target_account_id=account.id,
                policy_version_applied=grant.policy_version,
                evidence_digest=evidence_digest,
                input_digest=input_digest,
                consumer_actor_type=CONSUMER_ACTOR_TYPE,
                consumer_reference=CONSUMER_REFERENCE,
            )
            self.db.add(consumption)
            self.db.flush()

            account.status = "atrasado"
            self.db.add(
                AccountEvent(
                    account_id=account.id,
                    event_type="status_changed",
                    actor_type="system",
                    actor_reference=CONSUMER_REFERENCE,
                    actor_user_id=None,
                    previous_status="aberto",
                    new_status="atrasado",
                    idempotency_key=_event_receipt_key(
                        episode_scope_key
                    ),
                )
            )
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise ApprovalConsumptionConflictError(
                "Conflito concorrente ao registrar o efeito de "
                "negócio policy-governed."
            ) from error
        except Exception:
            self.db.rollback()
            raise

        return PolicyAccountMarkOverdueExecutionResult(
            policy_authority_grant_id=grant.id,
            policy_authority_consumption_id=consumption.id,
            invocation_id=invocation.id,
            invocation_status="succeeded",
            duplicate=False,
            output=output,
        )

    def _resolve_published_version(self, skill_id: int):
        for candidate in self.skills.list_versions(skill_id):
            if candidate.status == "published":
                return candidate
        raise ApprovalNotFoundError(
            f"Nenhuma versão publicada de {SKILL_KEY!r} encontrada."
        )
