from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.authentication import require_permission
from app.core.config import settings
from app.core.customer_context import get_customer_context
from app.database.database import get_db
from app.models.account import Account
from app.schemas.account import ClientClassificationDetail
from app.schemas.customer_context import AggregationIdentity
from app.schemas.customer_context import BehavioralIntelligence
from app.schemas.customer_context import (
    ClientBehaviorPatternSummary,
)
from app.schemas.customer_context import Customer360Response
from app.schemas.customer_context import CustomerAccountSummary
from app.schemas.customer_context import EpisodeOutcome
from app.schemas.customer_context import FinancialSummary
from app.schemas.outcome import OutcomeEpisodeResponse


router = APIRouter(
    prefix="/customer-context",
    tags=["Customer Intelligence 360"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}",
    response_model=Customer360Response,
    dependencies=read_dependencies,
)
def get_account_customer_context(
    account_id: int,
    db: Session = Depends(get_db),
) -> Customer360Response:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_customer_context(db, requested_account=account)

    classification = None

    if result.behavioral_intelligence.classification is not None:
        info = result.behavioral_intelligence.classification
        context = info.context_data

        classification = ClientClassificationDetail(
            label=context["label"],
            reason=context.get("reason"),
            rule_version=context["rule_version"],
            classified_at=info.recorded_at,
            resolved_occurrences=context[
                "ocorrencias_resolvidas"
            ],
            late_occurrences=context.get(
                "ciclos_em_atraso"
            ),
            late_ratio=context.get("proporcao_atraso"),
            minimum_required_occurrences=(
                settings
                .client_behavior_min_occurrences_for_pattern
            ),
            late_ratio_threshold=(
                settings.client_classification_atraso_threshold
            ),
            analysis_scope=context["analysis_scope"],
            period_start=context.get("period_start"),
            period_end=context.get("period_end"),
        )

    pattern = None

    if result.behavioral_intelligence.pattern is not None:
        pattern = ClientBehaviorPatternSummary.model_validate(
            result.behavioral_intelligence.pattern
        )

    episodes = [
        EpisodeOutcome(
            account_id=episode.episode.account_id,
            due_date=episode.episode.due_date,
            outcome=OutcomeEpisodeResponse.model_validate(
                episode
            ),
        )
        for episode in result.episodes
    ]

    return Customer360Response(
        requested_account_id=result.requested_account_id,
        aggregation_identity=(
            AggregationIdentity.model_validate(
                result.aggregation_identity
            )
        ),
        accounts=[
            CustomerAccountSummary.model_validate(
                account_summary
            )
            for account_summary in result.accounts
        ],
        financial_summary=FinancialSummary.model_validate(
            result.financial_summary
        ),
        behavioral_intelligence=BehavioralIntelligence(
            classification_status=(
                result.behavioral_intelligence
                .classification_status
            ),
            pattern=pattern,
            classification=classification,
        ),
        episodes=episodes,
    )
