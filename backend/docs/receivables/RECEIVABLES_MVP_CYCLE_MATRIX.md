# Receivables MVP Cycle Matrix

**Status:** Congelado (F3.1). Avaliação read-only do ciclo `observar → decidir/propor → chamar atenção humana → aprovar → executar → auditar → sobreviver/reconciliar após restart`, sobre o baseline `04d7c2dd7ca9c266535f7a5bc6c904bc477081d2`, para as duas ações mutáveis governadas reais que existem hoje: `account.mark_overdue` (F1) e `account.mark_paid` (fatia anterior, não-F1-F3). Ver `RECEIVABLES_ARCHITECTURE.md` para o mapa completo.

## 1. Objetivo

Determinar, por evidência e não por suposição, se o ciclo de governança do MVP (observar um fato → propor uma ação → um humano perceber que há algo pendente → um humano decidir → o sistema executar → o efeito ser auditável → o sistema sobreviver a um restart sem perder ou duplicar estado) está completo, e onde está a lacuna real, se houver.

## 2. Matriz

| Etapa | `mark_overdue` — mecanismo | `mark_paid` — mecanismo | Evidência | Classificação |
|---|---|---|---|---|
| **1. Observar** | `OverdueDetectionService.run_scan()` — SQL autônomo (`status='aberto' AND vencimento<hoje`), roda no startup + loop contínuo | Nenhum — por decisão de escopo do piloto (pagamento é fato externo/humano, não inferido autonomamente) | `overdue_detection_service.py`; `test_entry_to_effect_overdue_account_reaches_atrasado` | `mark_overdue`: resolvido. `mark_paid`: fora de escopo do piloto por decisão de produto — não é lacuna |
| **2. Decidir / propor** | Cria `AuthenticatedAdvisoryProposal` + `ApprovalRequest[pending]` autonomamente via `request_approval_system` | Humano abre a proposta manualmente via `POST /approvals/skill-executions/{version_id}` | `SystemAdvisoryProposalService.create`; `authenticated_advisory_proposal_approval_bridge_service.py` | Resolvido para ambos |
| **3. Chamar atenção humana** | — | — | `Sidebar.tsx` lista "Aprovações" como item de menu comum, sem badge/contador; `dashboard.py` não referencia aprovações; nenhum polling/push/e-mail em todo o frontend | **Nenhum mecanismo existe, para nenhuma ação.** Única etapa do ciclo inteiro sem cobertura. Não é blocker absoluto (o piloto pode rodar com checagem manual periódica), mas é a lacuna operacional real e evidenciada do ciclo |
| **4. Aprovar** | `POST /approvals/{id}/decision`, RBAC (`approval:decide`/`approval:decide_sensitive`), separation of duties | Idêntico — mesma fila, mesma rota, mesma UI | `Approvals.tsx`; `test_approval_api.py`; `test_high_risk_request_enforces_separation_of_duties` | Resolvido para ambos |
| **5. Executar** | Automático após aprovação (`dispatch_approved_system`, loop de recovery + dispatch imediato) → `AccountMarkOverdueExecutionService`, via camada Work (`WorkSkillExecution`+`WorkEvent`) | Manual — humano aciona `POST /accounts/{id}/execute-mark-paid`; sem loop de auto-dispatch; sem camada Work (ADR 009, fluxo simplificado) | `authenticated_advisory_dispatch_maintenance.py`; `account_mark_paid_execution_service.py`; `test_25q5_governed_effect_source_contract` | Resolvido para ambos — mecanismos deliberadamente diferentes, não é gap |
| **6. Auditar** | `SkillInvocation`+`ApprovalConsumption`+`WorkSkillExecution`+`WorkEvent`+`AccountEvent`, uma transação | `SkillInvocation`+`ApprovalConsumption`+`AccountEvent` (sem Work, por ADR 009) | Contratos travados por teste de fonte (`test_25o_...`/`test_25q5_...`) | Resolvido para ambos |
| **7. Sobreviver / reconciliar após restart** | `run_advisory_dispatch_recovery_async` no startup, cursor transitório V12, reautorização completa do principal | Não precisa de recovery dedicado — síncrono e idempotente por construção (`get_consumption_by_request` detecta reexecução) | `test_v12_permanent_orphan_does_not_block_later_candidates`; ordem de recovery em `main.py` | Resolvido para ambos, por mecanismos apropriados a cada modelo |

## 3. Conclusão

De 7 etapas × 2 ações, a única célula sem nenhum mecanismo, em qualquer ação, é **"chamar atenção humana"**. Todo o resto do ciclo governado existe, é auditado, e sobrevive a restart. A ausência de aquisição autônoma do fato de pagamento (etapa 1 para `mark_paid`) não é uma lacuna do ciclo — é uma fronteira de escopo definida deliberadamente para o primeiro piloto.

A única lacuna operacional real e evidenciada do ciclo do piloto é a falta de qualquer sinalização passiva de aprovações pendentes.
