# Production Readiness Gap Register

## Objetivo

Este documento é o registro operacional corrente de lacunas de Production
Readiness / Controlled Pilot — o instrumento de governança usado para decidir
o que ainda falta antes do ciclo real de produção. `PRODUCTION_RISK_REGISTER.md`
mantém seu próprio papel de registro arquitetural de riscos/decisões por
commit; este documento não o substitui nem duplica sua narrativa — ele rastreia
gaps de forma objetiva (estado, risco, mitigação, evidência, condição de
fechamento, dependências).

**Regra de manutenção:** o estado corrente do sistema (HEAD do Git, head do
Alembic, etc.) deve sempre ser obtido via Git/`alembic heads` no momento da
operação — nunca inferido de um hash citado aqui. Hashes de commit abaixo
aparecem apenas como evidência histórica do fechamento de um item específico,
nunca como uma alegação sobre "o baseline atual do sistema".

---

## OPEN — REQUIRED BEFORE CONTROLLED PILOT

Nenhum item técnico permanece aberto nesta categoria no baseline
reconciliado `7a73c30a0510f5a0f13c0a9c1d43c7ec1556142b` (Production Pilot
Final Gate — Readiness Reconciliation Survey). P1.2 e P2, antes listados
aqui, foram verificados diretamente contra o código/testes/evidência
existentes — não apenas contra o texto histórico deste documento — e
movidos para `CLOSED / EVIDENCE` abaixo. Esta seção é mantida vazia
deliberadamente, para preservar a taxonomia do readiness contract, e não
constitui, por si só, uma declaração de aprovação do piloto controlado.

---

## OPEN — PRODUCTION HARDENING / SCALE

Achados do PR-5 Survey classificados como bucket C (exigiriam código/infra,
fora do escopo documental do PR-5). A Survey não demonstrou que sejam
blockers do piloto controlado atual — permanecem aqui, não na seção acima.

### G3 — Production Database Identity Guard

- **Estado:** aberto. Gap pré-existente, reconfirmado inalterado pelo PR-5
  Survey por leitura linha a linha do `model_validator` atual.
- **Risco concreto:** `config.py` (`Settings.validate_environment`, bloco
  `if self.environment == "production":`) exige apenas que o banco seja
  PostgreSQL e recusa o nome literal `auneron_test`; não exige que o banco de
  produção tenha uma identidade/nome específico. Em tese, `APP_ENV=production`
  pode apontar para um Postgres vazio ou incorreto sem que a validação de
  settings recuse o boot.
- **Mitigação atual:** nenhuma automática — depende de configuração
  operacional correta de `DATABASE_URL` no ambiente de produção.
- **Evidência:** `backend/app/core/config.py`, função `validate_environment`.
- **Condição objetiva de fechamento:** adicionar validação de identidade do
  banco de produção além do dialeto (ex.: nome esperado explícito, ou outro
  mecanismo de verificação), com teste de contrato provando a rejeição de um
  banco de produção mal configurado.
- **Dependências:** nenhuma — pode ser resolvido isoladamente.

### G4 — Multi-instance Maintenance Concurrency Verification

- **Estado:** aberto. Distinto e não afetado pelo fechamento de PR-1 (que
  resolveu resiliência a exceção não tratada, não concorrência entre
  instâncias).
- **Risco concreto:** 7 dos 10 maintenance loops não têm prova de segurança
  sob N>1 instâncias/workers de backend simultâneos. Apenas 2 são seguros via
  constraint UNIQUE de banco e 1 via `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Mitigação atual:** `backend/docker-compose.prod.yml` define apenas um
  serviço `backend`, sem `deploy.replicas` nem flag `--workers` — hoje não há
  concorrência multi-instância real em produção, então o risco é mitigado na
  prática, mas não estruturalmente fechado.
- **Evidência:** `backend/docker-compose.prod.yml` (ausência de bloco
  `deploy:`/`--workers`); os arquivos `backend/app/core/*_maintenance.py` e
  `client_classification.py`.
- **Condição objetiva de fechamento:** para cada um dos 7 loops não
  comprovados, demonstrar segurança sob concorrência (idempotência de banco)
  ou implementar `SELECT ... FOR UPDATE SKIP LOCKED`/constraint equivalente,
  com teste de contrato provando ausência de double-processing sob execução
  concorrente simulada.
- **Dependências:** nenhuma dependência de P1/P2/G3; só se torna urgente se/
  quando produção migrar para múltiplas réplicas.

---

## CLOSED / EVIDENCE

### P1 (core) — Intelligence-to-Human Recommendation Consumption (escalate_to_human)

- **Baseline de fechamento:** commits
  `3480e562ca44cb07f028980054dc9e2743d2cac0` (PR-6A — corredor de
  materialização governada) e `c7d5781db26e8c6266c4c3164b21a2cdca488540`
  (PR-6B — superfície de consumo humano).
- **Evidência:** ciclo comprovado ponta a ponta — `GET /recommendations/
  next-best-action/...` (`approval:read`) já expõe a decisão NBA;
  `frontend/src/pages/Recomendacoes.tsx` apresenta decisão + evidência a um
  humano autorizado, com NBA carregado sob demanda por episódio;
  `escalate_to_human` selecionado materializa via `POST .../
  human-escalation/.../materialize` (`work:create`), que revalida a
  eligibility no servidor antes de qualquer escrita
  (`backend/app/services/human_escalation_materialization_service.py`) —
  nunca confia no snapshot exibido ao operador. Idempotência comprovada para
  reexecução do mesmo ator, convergência entre atores diferentes, e falha
  fechada (409) quando o WorkItem canônico já está em estado terminal.
  Testes dedicados: `backend/tests/test_human_escalation_materialization.py`
  (9 casos) + Acceptance Verification manual do PR-6B contra backend real
  isolado.
- **Nota:** este fechamento cobre exclusivamente `escalate_to_human`.
  `account.mark_overdue` continua recomendável/visível na mesma UI, sem
  corredor de materialização humana — ver P1.2. A semântica de reabertura de
  um episódio após o WorkItem chegar a estado terminal foi decidida e
  fechada como política de domínio — não como reabertura implementada — ver
  P1.3 (CLOSED / EVIDENCE).

### PR-1 — Maintenance Loop Resilience

- **Baseline de fechamento:** commit `04fc339`
  ("feat(operations): add maintenance loop resilience v1").
- **Evidência:** os 6 loops anteriormente desprotegidos
  (`pilot_mutation_maintenance.py`, `work_skill_maintenance.py`,
  `work_outcome_evaluation_maintenance.py`,
  `authenticated_advisory_dispatch_maintenance.py`,
  `client_behavior_memory_maintenance.py`, `client_classification.py`) agora
  envolvem `await run_..._async()` em `try/except Exception` com log
  estruturado `*.maintenance_failed`; os outros 4 loops já tinham proteção
  equivalente antes. Teste dedicado:
  `backend/tests/test_maintenance_loop_resilience.py`.
- **Nota:** concorrência multi-instância não faz parte deste fechamento —
  ver G4, que permanece aberto.

### PR-2 — Production Identity / RBAC Guard

- **Baseline de fechamento:** commit `983a256`
  ("feat(security): add production identity/RBAC guard for developer role").
- **Evidência:** `backend/app/core/authentication.py`
  (`ProductionRoleNotAllowedError`, `_production_role_blocked`, aplicado em
  `authenticate_user`, `create_session` e `require_user_session`;
  `check_production_developer_roles`/`_async` chamado em
  `backend/app/main.py` `lifespan()`); `backend/scripts/create_user.py`
  (recusa `--role developer` quando `APP_ENV=production`). Teste dedicado:
  `backend/tests/test_production_identity_rbac_guard.py`.
- **Nota:** `developer` continua válido em development/test; a restrição é
  exclusiva de `APP_ENV=production`.

### PR-3 — Legacy SQLite Migration Tool Hardening

- **Baseline de fechamento:** commit `b9f83ec`
  ("feat(operations): isolate legacy SQLite migration tool from production").
- **Evidência:** `backend/scripts/migrate_sqlite_to_postgres.py` aborta com
  `SystemExit` quando `settings.environment == "production"`, antes de
  qualquer conexão SQLite/Postgres; o `.dockerignore` na raiz do repositório
  exclui o script e padrões `*.db`/`*.sqlite*` da imagem Docker de produção.
  Teste dedicado: `backend/tests/test_migrate_sqlite_to_postgres_guard.py`.
- **Nota:** é ferramenta histórica de migração única (SQLite legado →
  PostgreSQL), não um procedimento operacional atual.

### PR-4 — Database Backup / Restore / Recovery Layer A

- **Baseline de fechamento:** commit `77a4a6e`
  ("feat(operations): add production backup/restore recovery drill v1").
- **Evidência:** `backend/scripts/backup_postgres.py`, `restore_postgres.py`,
  `verify_recovery.py`, `_postgres_recovery_common.py`; drill real executado
  e documentado em `backend/DATABASE_BACKUP_VALIDATION.md` (26 tabelas
  verificadas, revisão Alembic idêntica origem/destino, cleanup confirmado
  por consulta independente). Teste dedicado:
  `backend/tests/test_backup_restore_guards.py`.
- **Nota:** prova apenas Layer A (banco). Layer B (aplicação) permanece
  aberta — ver P2 acima.

### P1.3 — Same-Episode Human Escalation Reopening Semantics

- **Baseline de fechamento:** commits
  `3480e562ca44cb07f028980054dc9e2743d2cac0` (PR-6A — geração única, fronteira
  fail-closed original), `ad179422fc313de394fba1537770fc7a0217f14c` (P1.3B.1
  — auditoria transacional de mudança de `vencimento`) e
  `005197b22015ae7f3bc904a20d96a07e0537edb7` (P1.3B.2a — guarda de identidade
  do episódio).
- **Evidência:**
  - PR-6A (`3480e562...`): geração única por episódio,
    `work_key = human_escalation:v1:{account_id}:{due_date}`, 409
    terminal-duplicate fail-closed —
    `backend/app/services/human_escalation_materialization_service.py`.
  - P1.3B.1 (`ad179422...`): `PUT /accounts/{id}` passou a obter a conta via
    `SELECT ... FOR UPDATE` e a registrar toda mudança efetiva de
    `vencimento` em `account_vencimento_changes` — histórico atômico,
    serializado, nunca silencioso —
    `backend/app/models/account_vencimento_change.py`,
    `backend/app/api/routes/accounts.py`.
  - P1.3B.2a (`005197b...`): `get_human_escalation_eligibility()` ganhou a
    guarda R0 — `account.vencimento != due_date` → `ineligible`,
    `reason="due_date_mismatch"`, avaliada antes de qualquer outro estado —
    impedindo que o estado financeiro de um episódio diferente seja usado
    para responder sobre o episódio solicitado —
    `backend/app/core/human_escalation_eligibility.py`.
  - Discovery Survey (mesma linha de trabalho, sem commit de código próprio
    — decisão registrada aqui): levantamento mecânico de `AccountEvent`,
    `Knowledge`, do receivables monitor e de todos os caminhos que mutam
    `Account.status`/`vencimento` demonstrou que nenhum caminho governado do
    domínio atual produz `eligible → ineligible → eligible` para o mesmo
    `FinancialEpisode(account_id, due_date)`: `"pago"` nunca reverte por
    nenhum caminho da aplicação, e uma mudança de `vencimento` — agora
    auditável por P1.3B.1 e corretamente identificada por P1.3B.2a —
    constitui outro `FinancialEpisode`, nunca uma nova geração do mesmo.
- **Política resolvida:**
  - **CLOSED:** a semântica do domínio atual foi decidida e comprovada — um
    `FinancialEpisode(account_id, due_date)` admite no máximo uma geração de
    WorkItem de Human Escalation.
  - **DEFERRED (não OPEN):** Eligibility Epoch Primitive — capacidade
    futura, condicionada à introdução de um novo fato governado de domínio
    capaz de demonstrar uma segunda elegibilidade legítima para o mesmo
    episódio (por exemplo, uma reversão governada de pagamento, hoje
    inexistente, ou uma decisão humana explícita de reabertura).
  - **NÃO IMPLEMENTADO:** reabertura do mesmo `FinancialEpisode`.
  - **Comportamento atual, correto e definitivo enquanto DEFERRED
    persistir:** WorkItem terminal → nova tentativa de materialização → 409
    fail-closed.
- **Nota:** este fechamento não implementa nenhuma primitive de epoch nem
  identidade geracional de `work_key` — decide, com evidência mecânica, que
  o domínio atual não as exige. Se um fato governado de reversão/reabertura
  for introduzido no futuro, este item deve ser reaberto antes de qualquer
  Survey de identidade geracional.

### P1.2 — Human-origin Governed Financial Action Materialization (account.mark_overdue)

- **Baseline de fechamento:** commits `176eb61dcb33db8e1bfdfdfdc5cd1208f31b337b`
  (P1.2A — registro do catálogo real da skill `account.mark_overdue`,
  pré-requisito operacional isolado) e
  `c600a1e7d4271dd40a6a596793c233717677e7d9` (P1.2B — corredor humano
  governado de materialização/execução).
- **Evidência — P1.2A:** `backend/scripts/register_account_mark_overdue_skill.py`
  registra Skill/SkillVersion/capability via `SkillService`, idempotente,
  sem criar `AgentSkillBinding` — pré-condição para que qualquer corredor,
  humano ou agent-only, encontre a skill publicada no catálogo real (antes
  desta fatia, `account.mark_overdue` só existia como fixture de teste).
- **Evidência — P1.2B:** novo corredor humano estreito e paralelo, sem
  estender nem modificar o bridge 25M/25O nem
  `AuthenticatedAdvisoryProposalApprovalBridgeService` (hashes verificados
  byte-idênticos antes/depois do APPLY) —
  `backend/app/services/human_account_mark_overdue_materialization_service.py`
  (materialização: revalida eligibility e autoridade antes de qualquer
  escrita, converge entre operadores diferentes via `work_key` do WorkItem,
  409 fail-closed sobre WorkItem terminal) e
  `backend/app/services/human_account_mark_overdue_execution_service.py`
  (execução: lock `SELECT ... FOR UPDATE` da `Account`, dupla revalidação
  de eligibility/autoridade imediatamente antes da mutação, consumo
  single-use de `ApprovalConsumption` com `consumer_actor_type="system"`
  seguindo o precedente de `account.mark_paid`, identidade humana
  preservada em `AccountEvent.actor_type="user"`/`actor_user_id`). A
  separação de deveres já existente em `ApprovalService.decide()`
  (solicitante ≠ decisor para ações de risco `high`/`critical`) é herdada
  sem alteração. Rotas: `POST .../episodes/{due_date}/materialize`
  (`work:create`) e `POST .../episodes/{due_date}/execute`
  (`skill:execute`), em `backend/app/api/routes/mark_overdue_recommendation.py`.
  Testes dedicados: `backend/tests/test_human_account_mark_overdue_materialization.py`,
  `backend/tests/test_human_account_mark_overdue_execution.py`.
- **Nota:** nenhuma fronteira agent-only foi generalizada ou enfraquecida —
  `GovernedSkillExecutionService`, `WorkSkillExecutionService.configure_with_existing_approval()`
  e `AccountMarkOverdueExecutionService` permanecem exclusivos do corredor
  legado de agente, intocados por este fechamento.

### P2 — Isolated Application Recovery Smoke

- **Baseline de fechamento:** commit
  `a529369d6120a3975e71651c6ef79f8f6fd5aea4` ("feat(operations): add
  MAINTENANCE_ENABLED gate and Application Recovery Smoke (P2)").
- **Evidência — mecanismo de supressão no boot:** `Settings.maintenance_enabled`
  (`backend/app/core/config.py`, default `True`, recusado em
  `environment=="production"`) controla, em um único boundary em
  `backend/app/main.py:lifespan()`, as 11 operações recovery-once e a
  criação das 10 maintenance tasks — provado estruturalmente por
  `backend/tests/test_maintenance_gate.py` (0/21 chamadas com `False`,
  21/21 com `True`). `check_database_connection()`/`database_online`
  permanece fora desse boundary — executa incondicionalmente, antes e
  independente de `maintenance_enabled`, preservando o diagnóstico de
  conectividade mesmo com a manutenção suprimida.
- **Evidência — Application Recovery Layer B real:** drill completo
  documentado em `backend/DATABASE_BACKUP_VALIDATION.md`
  §"Layer B — Application Recovery Smoke (P2 — fechado)" —
  `Application Recovery Layer B: PASS`. Orquestrado por
  `backend/scripts/application_recovery_smoke.py`, coberto por
  `backend/tests/test_application_recovery_smoke_guards.py`. Este registro
  não reproduz o histórico operacional do drill — `DATABASE_BACKUP_VALIDATION.md`
  continua sendo a evidência detalhada.
- **Nota:** Layer A (banco) já estava fechada desde o PR-4; este fechamento
  cobre especificamente Layer B (aplicação), que era o bloqueio registrado.
