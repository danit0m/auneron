from uuid import uuid4

from fastapi.testclient import TestClient


def test_read_endpoints_return_success(
    client: TestClient,
) -> None:
    marker = uuid4().hex[:12]

    creation_response = client.post(
        "/accounts/",
        json={
            "cliente": f"Cliente Endpoint {marker}",
            "email": f"endpoint.{marker}@outlook.com",
            "whatsapp": "11999999999",
            "valor": 12345.67,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )

    assert creation_response.status_code == 201

    endpoints = [
        "/",
        "/health",
        "/dashboard/",
        "/brain/executive",
        "/orchestrator/health",
        "/orchestrator/metrics",
        "/orchestrator/registry",
        "/orchestrator/rules",
        "/orchestrator/telemetry",
        "/orchestrator/decisions",
        "/orchestrator/decision/latest",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)

        assert response.status_code == 200, (
            f"Endpoint {endpoint} retornou "
            f"{response.status_code}: {response.text}"
        )