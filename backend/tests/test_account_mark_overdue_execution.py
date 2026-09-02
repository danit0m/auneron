from pathlib import Path


def test_25o_transactional_effect_source_contract() -> None:
    source = Path("app/services/account_mark_overdue_execution_service.py").read_text(encoding="utf-8")
    assert "self.runtime.invoke(" not in source
    assert 'account.status = "atrasado"' in source
    assert "ApprovalConsumption(" in source
    assert "SkillInvocation(" in source
    assert "WorkEvent(" in source
    assert "self.db.commit()" in source
    assert "effect:account.mark_overdue:approval:" in source
