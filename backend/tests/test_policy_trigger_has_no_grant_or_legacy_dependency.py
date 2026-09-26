import ast
import inspect

from app.core import policy_account_mark_overdue_trigger_maintenance

FORBIDDEN_GRANT_NAMES = (
    "PolicyAuthorityGrantService",
    "PolicyAuthorityGrant",
)

FORBIDDEN_LEGACY_NAMES = (
    "OverdueDetectionService",
    "AuthenticatedAdvisoryProposal",
    "AIOrchestrator",
)


def _imported_names() -> set[str]:
    """
    Nomes efetivamente importados no módulo (linhas `import`/`from ...
    import`), via parsing AST -- nunca por busca textual, que também
    encontraria menções no docstring do módulo (este próprio arquivo
    documenta explicitamente o que NÃO importa).
    """
    source = inspect.getsource(
        policy_account_mark_overdue_trigger_maintenance
    )
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def test_trigger_source_has_no_grant_service_or_model_dependency() -> None:
    imported = _imported_names()
    for forbidden in FORBIDDEN_GRANT_NAMES:
        assert forbidden not in imported
        assert not hasattr(
            policy_account_mark_overdue_trigger_maintenance, forbidden
        )


def test_trigger_source_has_no_legacy_advisory_or_orchestration_dependency() -> (
    None
):
    imported = _imported_names()
    for forbidden in FORBIDDEN_LEGACY_NAMES:
        assert forbidden not in imported
        assert not hasattr(
            policy_account_mark_overdue_trigger_maintenance, forbidden
        )
