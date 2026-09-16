"""
NBA V1 -- contrato exato de operacoes OpenAPI sob
/recommendations/next-best-action. Mesmo padrao ja usado por outcome/
customer-context/human-escalation/mark-overdue/mark-paid/action-space.
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


def _nba_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith("/recommendations/next-best-action"):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue
            operations.append((normalized_method.upper(), path))

    return sorted(operations)


def test_nba_api_exposes_exactly_one_operation() -> None:
    assert _nba_openapi_operations() == [
        (
            "GET",
            "/recommendations/next-best-action/accounts/"
            "{account_id}/episodes/{due_date}",
        )
    ]
