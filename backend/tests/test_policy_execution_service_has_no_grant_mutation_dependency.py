import inspect

from app.services import policy_account_mark_overdue_execution_service


def test_execution_service_source_has_no_grant_service_import() -> None:
    source = inspect.getsource(
        policy_account_mark_overdue_execution_service
    )
    assert "PolicyAuthorityGrantService" not in source
    assert "policy_authority_grant_service" not in source


def test_execution_service_source_has_no_grant_mutation_method_references() -> (
    None
):
    source = inspect.getsource(
        policy_account_mark_overdue_execution_service
    )
    assert "create_grant(" not in source
    assert "revoke_grant(" not in source
    assert ".state = \"active\"" not in source
    assert ".state = \"revoked\"" not in source
