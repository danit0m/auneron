from pathlib import Path


def test_25q5_governed_effect_source_contract() -> None:
    """
    Contrato de fonte para AccountMarkPaidExecutionService, no mesmo
    espirito do teste equivalente ja existente para
    AccountMarkOverdueExecutionService (tests/test_account_mark_overdue_execution.py).

    Trava estruturalmente as decisoes registradas na ADR 009 (design
    doc): validacao manual em vez de validate_approved_action_only(),
    reuso de authorize_skill_execution() para RBAC/escopo, e ausencia
    de WorkItem/WorkEvent (fluxo simplificado, sem a camada de Work).
    """
    source = Path(
        "app/services/account_mark_paid_execution_service.py"
    ).read_text(encoding="utf-8")

    # Efeito de negocio real acontece e e commitado.
    assert 'account.status = "pago"' in source
    assert "self.db.commit()" in source

    # Ledger de aprovacao/execucao completo.
    assert "ApprovalConsumption(" in source
    assert "SkillInvocation(" in source
    assert "AccountEvent(" in source

    # ADR 009: valida manualmente, nunca instancia/chama
    # GovernedSkillExecutionService.validate_approved_action_only()
    # (que exige ator nao-humano e bloquearia sempre este fluxo humano).
    assert "from app.services.governed_skill_execution import" not in source
    assert "self.governed" not in source
    assert "authorize_skill_execution(" in source

    # Fluxo simplificado: sem WorkItem/WorkEvent (nao ha Advisory
    # Proposal nem Work para account.mark_paid).
    assert "WorkEvent(" not in source

    # Consumidor tecnico e sempre nao-humano (CHECK constraint de
    # approval_consumptions.consumer_actor_type exclui 'user'); a
    # autoridade humana fica registrada em authority_user_id/reference.
    assert 'consumer_actor_type=CONSUMER_ACTOR_TYPE' in source
    assert 'CONSUMER_ACTOR_TYPE = "system"' in source

    # Idempotencia via unicidade de ApprovalConsumption.approval_request_id,
    # sem depender de recibo em WorkEvent.
    assert "get_consumption_by_request(" in source
