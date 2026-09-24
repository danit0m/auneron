from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy.orm import Session

from app.core.nba_policy import NbaDecisionEvidence
from app.models.nba_recommendation_snapshot import (
    NbaRecommendationSnapshot,
)
from app.repositories.nba_recommendation_snapshot_repository import (
    NbaRecommendationSnapshotRepository,
)
from app.schemas.nba_policy import NbaRecommendationSnapshotPayload


class NbaRecommendationSnapshotIntegrityError(Exception):
    """
    snapshot_payload persistido nao corresponde mais a snapshot_digest
    persistido -- adulteracao ou corrupcao. Fail-closed: nunca retornar
    o snapshot nesse estado.
    """


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class NbaRecommendationSnapshotService:
    """
    DW-6.4A -- persiste exatamente uma ocorrencia de proveniencia
    append-only por chamada, representando a recomendacao NBA gerada/
    servida (I2 emendado). Nunca deduplica por digest -- duas
    requisicoes com conteudo identico sao duas ocorrencias distintas;
    o digest prova integridade do conteudo, nunca identidade da
    ocorrencia. Nunca reavalia get_nba_decision() -- consome o
    NbaDecisionEvidence ja computado pelo chamador, que permanece puro.
    Snapshots existentes nunca sao alterados (sem update/delete aqui).
    """

    def __init__(
        self,
        db: Session,
        *,
        repository: (
            NbaRecommendationSnapshotRepository | None
        ) = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else NbaRecommendationSnapshotRepository(db)
        )

    def persist(
        self,
        evidence: NbaDecisionEvidence,
    ) -> NbaRecommendationSnapshot:
        payload_model = (
            NbaRecommendationSnapshotPayload.model_validate(
                evidence
            )
        )
        payload_dict = payload_model.model_dump(mode="json")
        digest = hashlib.sha256(
            _canonical_json_bytes(payload_dict)
        ).hexdigest()

        snapshot = NbaRecommendationSnapshot(
            account_id=evidence.episode.account_id,
            due_date=evidence.episode.due_date,
            policy_version=evidence.policy_version,
            decision_type=evidence.decision.decision_type,
            requires_human_review=(
                evidence.requires_human_review
            ),
            snapshot_payload=payload_dict,
            snapshot_digest=digest,
        )
        self.repository.add(snapshot)
        self.db.commit()
        return snapshot

    def get_verified(
        self,
        snapshot_id: int,
    ) -> NbaRecommendationSnapshot | None:
        """
        Le um snapshot e reverifica sua integridade antes de retorna-lo
        -- mesmo padrao ja usado por AuthenticatedAdvisoryProposal:
        recanonicaliza o snapshot_payload persistido, recalcula o
        SHA-256 e compara (tempo constante) contra o snapshot_digest
        persistido. Nao ha diferenca de comportamento com um digest
        recem-calculado na escrita -- a verificacao e sempre feita
        contra o que esta persistido agora, nunca contra um valor
        supostamente confiavel guardado em memoria.
        """
        snapshot = self.repository.get_by_id(snapshot_id)

        if snapshot is None:
            return None

        recomputed_digest = hashlib.sha256(
            _canonical_json_bytes(snapshot.snapshot_payload)
        ).hexdigest()

        if not hmac.compare_digest(
            recomputed_digest, snapshot.snapshot_digest
        ):
            raise NbaRecommendationSnapshotIntegrityError(
                f"NbaRecommendationSnapshot {snapshot.id} falhou "
                "verificacao de integridade -- snapshot_payload nao "
                "corresponde a snapshot_digest persistido."
            )

        return snapshot
