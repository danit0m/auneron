from pathlib import Path


def test_25o_recovery_source_contract() -> None:
    generic = Path("app/core/work_skill_maintenance.py").read_text(encoding="utf-8")
    pilot = Path("app/core/pilot_mutation_maintenance.py").read_text(encoding="utf-8")
    assert ".dispatch(" not in generic
    assert "AccountMarkOverdueExecutionService" in pilot
    assert "list_pilot_recovery_candidate_work_ids" in pilot
    assert '"expected_status": "aberto"' in pilot
    assert "approval_input_identity(payload)" in pilot
