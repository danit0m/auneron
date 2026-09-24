from sqlalchemy.orm import Session

from app.models.nba_recommendation_snapshot import (
    NbaRecommendationSnapshot,
)


class NbaRecommendationSnapshotRepository:
    """Transaction-free persistence for NbaRecommendationSnapshot."""

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add(
        self,
        snapshot: NbaRecommendationSnapshot,
    ) -> NbaRecommendationSnapshot:
        self.db.add(snapshot)
        self.db.flush()
        return snapshot

    def get_by_id(
        self,
        snapshot_id: int,
    ) -> NbaRecommendationSnapshot | None:
        return self.db.get(
            NbaRecommendationSnapshot, snapshot_id
        )
