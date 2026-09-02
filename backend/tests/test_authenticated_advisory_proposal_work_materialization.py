from pathlib import Path


def test_25o_materializer_source_contract() -> None:
    source = Path("app/services/authenticated_advisory_proposal_work_materialization_service.py").read_text(encoding="utf-8")
    assert "configure_with_existing_approval(" in source
    assert "create_skill_execution_request(" not in source
    assert ":materialize" in source
    assert "account.mark_overdue" in source
