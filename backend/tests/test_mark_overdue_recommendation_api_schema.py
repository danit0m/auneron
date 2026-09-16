"""
Pilot Action Space V1.C -- contrato exato de operacoes OpenAPI sob
/recommendations/mark-overdue. Mesmo padrao ja usado por outcome/
customer-context/human-escalation.
"""

from app.main import app


HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
}


def _mark_overdue_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith("/recommendations/mark-overdue"):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue
            operations.append((normalized_method.upper(), path))

    return sorted(operations)


def test_mark_overdue_api_exposes_exactly_one_operation() -> None:
    assert _mark_overdue_openapi_operations() == [
        (
            "GET",
            "/recommendations/mark-overdue/accounts/"
            "{account_id}/episodes/{due_date}",
        )
    ]
