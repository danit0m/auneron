"""
Customer Intelligence 360 V1 -- contrato exato de operacoes OpenAPI sob
/customer-context. Mesmo padrao ja usado por approval/skill/outcome
(F3.2 lesson, revalidada no Aggregation Contract).
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


def _customer_context_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith("/customer-context"):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue
            operations.append((normalized_method.upper(), path))

    return sorted(operations)


def test_customer_context_api_exposes_exactly_one_operation() -> None:
    assert _customer_context_openapi_operations() == [
        (
            "GET",
            "/customer-context/accounts/{account_id}",
        )
    ]
