"""
DW-7.3 V1 -- Governance plane for Policy Authority Grants.

L3-A (DW-7.1 Semantic Freeze / DW-7.2 Design Freeze): grant creation and
revocation live here, structurally separated from
app/services/policy_account_mark_overdue_execution_service.py, which
consumes grants but never imports or references any write operation of
this module -- enforced by a dedicated structural test
(tests/test_policy_execution_service_has_no_grant_mutation_dependency.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConflictError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.policy_definitions import get_policy_definition
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.user import User


HUMAN_GRANT_ROLES = frozenset({
    "viewer",
    "analyst",
    "manager",
    "executive",
    "administrator",
    "developer",
})

SCOPE_TYPE_DEPLOYMENT_WIDE = "deployment_wide"


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


def _normalize_now(value: datetime | None) -> datetime:
    result = value if value is not None else _utc_now()
    if result.tzinfo is None:
        raise ApprovalValidationError("now deve possuir timezone.")
    return result


def _resolve_human_actor(
    db: Session,
    user_id: int,
    *,
    field_name: str,
) -> User:
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise ApprovalAuthorizationError(
            f"{field_name} inexistente ou inativo."
        )
    if user.role not in HUMAN_GRANT_ROLES:
        raise ApprovalAuthorizationError(
            f"{field_name} não possui papel humano válido "
            "para governança de policy."
        )
    return user


@dataclass(frozen=True)
class PolicyAuthorityGrantResult:
    grant: PolicyAuthorityGrant


class PolicyAuthorityGrantService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_grant(
        self,
        *,
        policy_key: str,
        granted_by_user_id: int,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> PolicyAuthorityGrantResult:
        if (
            not isinstance(policy_key, str)
            or not policy_key.strip()
        ):
            raise ApprovalValidationError("policy_key inválido.")

        definition = get_policy_definition(policy_key)
        if definition is None:
            raise ApprovalNotFoundError(
                "policy_key não corresponde a nenhuma Policy "
                "Definition registrada."
            )

        normalized_granted_by_id = _positive_id(
            granted_by_user_id, field_name="granted_by_user_id"
        )
        effective_now = _normalize_now(now)

        if expires_at.tzinfo is None:
            raise ApprovalValidationError(
                "expires_at deve possuir timezone."
            )
        if expires_at <= effective_now:
            raise ApprovalValidationError(
                "expires_at deve ser posterior a valid_from."
            )

        granting_user = _resolve_human_actor(
            self.db,
            normalized_granted_by_id,
            field_name="granted_by_user_id",
        )

        grant = PolicyAuthorityGrant(
            policy_key=definition.policy_key,
            policy_version=definition.policy_version,
            skill_key=definition.skill_key,
            scope_type=SCOPE_TYPE_DEPLOYMENT_WIDE,
            state="active",
            granted_by_user_id=granting_user.id,
            granted_by_reference=f"user:{granting_user.id}",
            granted_by_role=granting_user.role,
            valid_from=effective_now,
            expires_at=expires_at,
        )
        try:
            self.db.add(grant)
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise ApprovalConflictError(
                "Já existe um grant ativo para este policy_key/"
                "skill_key."
            ) from error

        self.db.refresh(grant)
        return PolicyAuthorityGrantResult(grant=grant)

    def revoke_grant(
        self,
        grant_id: int,
        *,
        revoked_by_user_id: int,
        now: datetime | None = None,
    ) -> PolicyAuthorityGrantResult:
        normalized_grant_id = _positive_id(
            grant_id, field_name="grant_id"
        )
        normalized_revoked_by_id = _positive_id(
            revoked_by_user_id, field_name="revoked_by_user_id"
        )
        effective_now = _normalize_now(now)

        revoking_user = _resolve_human_actor(
            self.db,
            normalized_revoked_by_id,
            field_name="revoked_by_user_id",
        )

        grant = self.db.execute(
            select(PolicyAuthorityGrant)
            .where(PolicyAuthorityGrant.id == normalized_grant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if grant is None:
            raise ApprovalNotFoundError(
                "PolicyAuthorityGrant não encontrado."
            )
        if grant.state != "active":
            raise ApprovalStateError(
                "Apenas grants em estado 'active' podem ser "
                "revogados."
            )

        grant.state = "revoked"
        grant.revoked_at = effective_now
        grant.revoked_by_user_id = revoking_user.id
        grant.revoked_by_reference = f"user:{revoking_user.id}"
        self.db.commit()
        self.db.refresh(grant)
        return PolicyAuthorityGrantResult(grant=grant)
