from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalRequest
from app.models.business_effect_verification import (
    BusinessEffectVerification,
)
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion


V1_SKILL_KEYS = frozenset(
    {
        "account.mark_overdue",
        "account.mark_paid",
    }
)

RECOVERY_VERIFICATION_RESULTS = frozenset(
    {
        "pending",
    }
)


class BusinessEffectVerificationRepository:
    """Transaction-free persistence for BusinessEffectVerification."""

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add(
        self,
        verification: BusinessEffectVerification,
    ) -> BusinessEffectVerification:
        self.db.add(verification)
        self.db.flush()
        return verification

    def get_by_consumption_id(
        self,
        approval_consumption_id: int,
    ) -> BusinessEffectVerification | None:
        statement = select(
            BusinessEffectVerification
        ).where(
            BusinessEffectVerification.approval_consumption_id
            == approval_consumption_id
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def lock_by_consumption_id(
        self,
        approval_consumption_id: int,
    ) -> BusinessEffectVerification | None:
        statement = (
            select(BusinessEffectVerification)
            .where(
                BusinessEffectVerification.approval_consumption_id
                == approval_consumption_id
            )
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_recovery_candidate_consumption_ids(
        self,
        *,
        limit: int = 100,
    ) -> list[int]:
        """
        Bounded ApprovalConsumption IDs for the two DW-3 V1 corridors that
        still lack a terminal BusinessEffectVerification -- either no row
        exists yet, or the existing row is still 'pending'.
        """
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 1000
        ):
            raise ValueError(
                "limit inválido para Business Effect "
                "Verification recovery."
            )

        statement = (
            select(ApprovalConsumption.id)
            .join(
                ApprovalRequest,
                ApprovalRequest.id
                == ApprovalConsumption.approval_request_id,
            )
            .join(
                SkillVersion,
                SkillVersion.id
                == ApprovalRequest.skill_version_id,
            )
            .join(
                SkillDefinition,
                SkillDefinition.id == SkillVersion.skill_id,
            )
            .outerjoin(
                BusinessEffectVerification,
                BusinessEffectVerification.approval_consumption_id
                == ApprovalConsumption.id,
            )
            .where(
                ApprovalConsumption.status == "consumed",
                SkillDefinition.skill_key.in_(
                    V1_SKILL_KEYS
                ),
                or_(
                    BusinessEffectVerification.id.is_(
                        None
                    ),
                    BusinessEffectVerification.result.in_(
                        RECOVERY_VERIFICATION_RESULTS
                    ),
                ),
            )
            .order_by(
                func.coalesce(
                    BusinessEffectVerification.updated_at,
                    ApprovalConsumption.finalized_at,
                ),
                ApprovalConsumption.id,
            )
            .limit(limit)
        )
        return [
            int(consumption_id)
            for consumption_id in self.db.execute(
                statement
            ).scalars().all()
        ]
