"""
Action Space Evaluator V1 -- contrato exato de operacoes OpenAPI sob
/recommendations/action-space. Mesmo padrao ja usado por outcome/
customer-context/human-escalation/mark-overdue/mark-paid.
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


def _action_space_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith("/recommendations/action-space"):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue
            operations.append((normalized_method.upper(), path))

    return sorted(operations)


def test_action_space_api_exposes_exactly_one_operation() -> None:
    assert _action_space_openapi_operations() == [
        (
            "GET",
            "/recommendations/action-space/accounts/"
            "{account_id}/episodes/{due_date}",
        )
    ]
