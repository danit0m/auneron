"""
Outcome Intelligence V1 -- contrato exato de operacoes OpenAPI sob
/outcomes. Mesmo padrao ja usado por approval/skill (F3.2 lesson):
trava a cardinalidade exata para que um endpoint novo nunca apareça
sem essa asserção ser atualizada explicitamente.
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


def _outcome_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith("/outcomes"):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue
            operations.append((normalized_method.upper(), path))

    return sorted(operations)


def test_outcome_api_exposes_exactly_one_operation() -> None:
    assert _outcome_openapi_operations() == [
        (
            "GET",
            "/outcomes/accounts/{account_id}/episodes/{due_date}",
        )
    ]
