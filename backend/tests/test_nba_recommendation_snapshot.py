"""
DW-6.4A -- NbaRecommendationSnapshot (I2 emendado).

Prova, no nivel de servico/modelo (sem HTTP), que
NbaRecommendationSnapshotService.persist() constroi um snapshot
imutavel e append-only a partir de um NbaDecisionEvidence ja
computado -- nunca chama get_nba_decision() de novo, nunca deduplica
por digest (duas ocorrencias com o mesmo conteudo tem o mesmo digest
mas IDs diferentes -- o digest prova integridade do conteudo, nunca
identidade da ocorrencia), e nunca inclui recommendation_snapshot_id
dentro do proprio snapshot_payload (autorreferencia proibida pelo
Design Freeze).
"""

from datetime import date
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.nba_policy import get_nba_decision
from app.models.account import Account
from app.models.nba_recommendation_snapshot import (
    NbaRecommendationSnapshot,
)
from app.repositories.nba_recommendation_snapshot_repository import (
    NbaRecommendationSnapshotRepository,
)
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotIntegrityError,
)
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotService,
)


def _make_account(
    db_session,
    *,
    vencimento: date,
    status: str = "aberto",
    valor: Decimal | float = 1000,
) -> Account:
    account = Account(
        cliente="Cliente NBA Snapshot Teste",
        email="cliente.nba.snapshot@example.com",
        whatsapp=None,
        valor=valor,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_persist_creates_snapshot_matching_evidence(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    snapshot = NbaRecommendationSnapshotService(
        db_session
    ).persist(evidence)

    assert snapshot.id is not None
    assert snapshot.account_id == account.id
    assert snapshot.due_date == due_date
    assert snapshot.policy_version == evidence.policy_version
    assert (
        snapshot.decision_type
        == evidence.decision.decision_type
    )
    assert (
        snapshot.requires_human_review
        == evidence.requires_human_review
    )
    assert len(snapshot.snapshot_digest) == 64
    assert (
        snapshot.snapshot_payload["decision"]["decision_type"]
        == evidence.decision.decision_type
    )
    assert (
        snapshot.snapshot_payload["applied_rules"]
        == list(evidence.applied_rules)
    )
    assert (
        "recommendation_snapshot_id"
        not in snapshot.snapshot_payload
    )


def test_identical_content_produces_two_occurrences_same_digest(
    db_session,
) -> None:
    """
    Duas chamadas de persist() com o MESMO NbaDecisionEvidence (mesmo
    conteudo computado) produzem duas linhas distintas com o mesmo
    digest -- prova concreta de que o digest garante integridade do
    conteudo, nunca identidade da ocorrencia. Nunca deduplicar.
    """
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    service = NbaRecommendationSnapshotService(db_session)
    first = service.persist(evidence)
    second = service.persist(evidence)

    assert first.id != second.id
    assert first.snapshot_digest == second.snapshot_digest
    assert first.snapshot_payload == second.snapshot_payload

    rows = (
        db_session.query(NbaRecommendationSnapshot)
        .filter(
            NbaRecommendationSnapshot.account_id
            == account.id
        )
        .all()
    )
    assert len(rows) == 2


def test_repository_get_by_id_round_trips(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )
    snapshot = NbaRecommendationSnapshotService(
        db_session
    ).persist(evidence)

    repository = NbaRecommendationSnapshotRepository(
        db_session
    )
    reloaded = repository.get_by_id(snapshot.id)

    assert reloaded is not None
    assert reloaded.id == snapshot.id
    assert reloaded.snapshot_digest == snapshot.snapshot_digest


def test_repository_get_by_id_returns_none_for_missing(
    db_session,
) -> None:
    repository = NbaRecommendationSnapshotRepository(
        db_session
    )
    assert repository.get_by_id(999_999_999) is None


def test_different_episodes_produce_different_snapshots(
    db_session,
) -> None:
    due_date_a = date.today() - timedelta(days=10)
    due_date_b = date.today() + timedelta(days=30)
    account = _make_account(
        db_session, vencimento=due_date_a
    )

    evidence_a = get_nba_decision(
        db_session, account=account, due_date=due_date_a
    )
    evidence_b = get_nba_decision(
        db_session, account=account, due_date=due_date_b
    )

    service = NbaRecommendationSnapshotService(db_session)
    snapshot_a = service.persist(evidence_a)
    snapshot_b = service.persist(evidence_b)

    assert snapshot_a.due_date == due_date_a
    assert snapshot_b.due_date == due_date_b
    assert (
        snapshot_a.snapshot_digest
        != snapshot_b.snapshot_digest
    )


# --- integridade na leitura (Patch Review V1 blocker) ---------------------


def test_get_verified_returns_snapshot_for_valid_round_trip(
    db_session,
) -> None:
    """
    Round-trip valido: snapshot_payload persistido corresponde ao
    snapshot_digest persistido -- get_verified() retorna o snapshot
    normalmente.
    """
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )
    service = NbaRecommendationSnapshotService(db_session)
    snapshot = service.persist(evidence)

    db_session.expire_all()
    verified = service.get_verified(snapshot.id)

    assert verified is not None
    assert verified.id == snapshot.id
    assert (
        verified.snapshot_digest == snapshot.snapshot_digest
    )


def test_get_verified_returns_none_for_missing_id(
    db_session,
) -> None:
    service = NbaRecommendationSnapshotService(db_session)
    assert service.get_verified(999_999_999) is None


def test_get_verified_fails_closed_on_tampered_payload(
    db_session,
) -> None:
    """
    Adulteracao real: snapshot_payload e alterado diretamente no
    banco, snapshot_digest permanece o original (nao recalculado) --
    exatamente o cenario que uma leitura sem reverificacao aceitaria
    silenciosamente. get_verified() deve falhar fechado.
    """
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    evidence = get_nba_decision(
        db_session, account=account, due_date=due_date
    )
    service = NbaRecommendationSnapshotService(db_session)
    snapshot = service.persist(evidence)
    original_digest = snapshot.snapshot_digest

    tampered_payload = dict(snapshot.snapshot_payload)
    tampered_payload["decision"] = dict(
        tampered_payload["decision"]
    )
    tampered_payload["decision"]["decision_type"] = "no_action"
    tampered_payload["decision"]["selected_actions"] = []

    db_session.execute(
        NbaRecommendationSnapshot.__table__.update()
        .where(NbaRecommendationSnapshot.id == snapshot.id)
        .values(snapshot_payload=tampered_payload)
    )
    db_session.commit()
    db_session.expire_all()

    reloaded_raw = db_session.get(
        NbaRecommendationSnapshot, snapshot.id
    )
    assert reloaded_raw.snapshot_digest == original_digest
    assert (
        reloaded_raw.snapshot_payload["decision"][
            "decision_type"
        ]
        == "no_action"
    )

    with pytest.raises(
        NbaRecommendationSnapshotIntegrityError
    ):
        service.get_verified(snapshot.id)
