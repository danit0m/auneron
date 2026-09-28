from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import Account


def _account(
    db_session: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Route Test",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_governed_operations_summary_returns_200_and_schema(
    client: TestClient,
) -> None:
    response = client.get("/dashboard/governed-operations-summary")

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "period",
        "eligible_accounts_identified",
        "autonomous_dispositions",
        "human_governed_dispositions",
        "autonomous_disposition_rate",
        "autonomous_effect_verification",
        "pending_overdue_accounts_now",
        "estimate",
    }
    assert set(payload["period"].keys()) == {"start", "end", "days"}
    assert set(
        payload["autonomous_effect_verification"].keys()
    ) == {
        "verified",
        "checked_other",
        "not_yet_checked",
        "total",
        "verification_rate",
    }
    assert payload["estimate"] is None


def test_governed_operations_summary_zeros_and_nulls(
    client: TestClient,
) -> None:
    response = client.get("/dashboard/governed-operations-summary")

    assert response.status_code == 200
    payload = response.json()

    assert payload["autonomous_dispositions"] == 0
    assert payload["human_governed_dispositions"] == 0
    assert payload["autonomous_disposition_rate"] is None
    assert payload["autonomous_effect_verification"]["total"] == 0
    assert (
        payload["autonomous_effect_verification"]["verification_rate"]
        is None
    )


def test_governed_operations_summary_period_days_default(
    client: TestClient,
) -> None:
    response = client.get("/dashboard/governed-operations-summary")

    assert response.status_code == 200
    assert response.json()["period"]["days"] == 30


def test_governed_operations_summary_period_days_lower_bound(
    client: TestClient,
) -> None:
    response = client.get(
        "/dashboard/governed-operations-summary",
        params={"period_days": 1},
    )

    assert response.status_code == 200
    assert response.json()["period"]["days"] == 1


def test_governed_operations_summary_period_days_upper_bound(
    client: TestClient,
) -> None:
    response = client.get(
        "/dashboard/governed-operations-summary",
        params={"period_days": 365},
    )

    assert response.status_code == 200
    assert response.json()["period"]["days"] == 365


def test_governed_operations_summary_period_days_zero_rejected(
    client: TestClient,
) -> None:
    response = client.get(
        "/dashboard/governed-operations-summary",
        params={"period_days": 0},
    )

    assert response.status_code == 422


def test_governed_operations_summary_period_days_over_max_rejected(
    client: TestClient,
) -> None:
    response = client.get(
        "/dashboard/governed-operations-summary",
        params={"period_days": 366},
    )

    assert response.status_code == 422


def test_governed_operations_summary_pending_now_reflects_accounts(
    client: TestClient,
    db_session: Session,
) -> None:
    _account(
        db_session,
        email="route-pending-now@example.com",
        vencimento=date.today() - timedelta(days=3),
        status="aberto",
    )

    response = client.get("/dashboard/governed-operations-summary")

    assert response.status_code == 200
    assert response.json()["pending_overdue_accounts_now"] == 1


def test_legacy_dashboard_endpoint_unchanged(
    client: TestClient,
    db_session: Session,
) -> None:
    """Regressao -- o endpoint legado GET /dashboard/ nao pode ser
    afetado pela nova rota, aditiva no mesmo router."""

    _account(
        db_session,
        email="route-legacy-regression@example.com",
        vencimento=date.today() - timedelta(days=3),
        status="aberto",
    )

    response = client.get("/dashboard/")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {
        "resumo",
        "indicadores",
        "status_clientes",
        "ranking_clientes",
        "alertas",
        "vencimentos",
    }
