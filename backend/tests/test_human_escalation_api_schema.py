"""
Pilot Action Space V1.B -- contrato exato de operacoes OpenAPI sob
/recommendations/human-escalation. Mesmo padrao ja usado por approval/
skill/outcome/customer-context (F3.2 lesson, revalidada em Outcome e
Customer 360).
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


def _human_escalation_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith("/recommendations/human-escalation"):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue
            operations.append((normalized_method.upper(), path))

    return sorted(operations)


def test_human_escalation_api_exposes_exactly_one_operation() -> None:
    """
    Nome do teste preservado por continuidade histórica (V1.B: uma
    operação, só leitura). PR-6A ampliou deliberadamente o contrato
    para duas operações -- esta prova versionada foi atualizada junto,
    não enfraquecida: continua exigindo exatamente o conjunto
    autorizado, nem mais nem menos.
    """

    assert _human_escalation_openapi_operations() == [
        (
            "GET",
            "/recommendations/human-escalation/accounts/"
            "{account_id}/episodes/{due_date}",
        ),
        (
            "POST",
            "/recommendations/human-escalation/accounts/"
            "{account_id}/episodes/{due_date}/materialize",
        ),
    ]
