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

### P1.2 — Human-origin Governed Financial Action Materialization (account.mark_overdue)

- **Estado:** aberto. Identificado no Survey do PR-6, mantido fora do escopo
  de materialização desde o Architecture Freeze do PR-6A.
- **Risco concreto:** limite de autoridade deliberado, não falha de
  segurança. `account.mark_overdue` já é recomendado pelo NBA e visível na
  UI, mas nenhum corredor humano existe para materializá-lo: o único
  corredor de execução real (`AuthenticatedAdvisoryProposalApprovalBridgeService`,
  25M/25O) exige `proposal_id`/`binding_id` de uma
  `AuthenticatedAdvisoryProposal` produzida exclusivamente pelo pipeline de
  agente legado — sem rota pública; a rota genérica
  `POST /approvals/skill-executions/{version_id}` cria a solicitação mas
  nunca a executa (`decide()` só grava a decisão).
- **Mitigação atual:** nenhuma necessária — sem corredor algum, nenhum
  humano pode disparar essa mutação hoje.
- **Evidência:**
  `backend/app/services/authenticated_advisory_proposal_approval_bridge_service.py`;
  `backend/app/api/routes/approvals.py:349`; achado central do PR-6 Survey.
- **Condição objetiva de fechamento:** Survey/Freeze próprios sobre como um
  humano materializa `account.mark_overdue` — estender o bridge 25M/25O para
  origem humana (mudança de fronteira de autoridade já congelada, exige
  Freeze próprio) ou outro desenho — com o mesmo rigor de
  revalidação/idempotência do PR-6A.
- **Dependências:** nenhuma dependência de P1.3/P2; independente.

### P1.3 — Same-Episode Human Escalation Reopening Semantics

- **Estado:** aberto. Fronteira deliberadamente congelada no PR-6A
  Architecture Freeze — fail-closed, nunca workaround silencioso.
- **Risco concreto:** lacuna funcional deliberada, não falha de segurança.
  Depois que o WorkItem canônico
  (`work_key = human_escalation:v1:{account_id}:{due_date}`) chega a estado
  terminal, o mesmo episódio nunca gera nova materialização — a `work_key`
  determinística fica permanentemente reivindicada. Se a eligibility voltar
  a recomendar, o endpoint responde 409 em vez de criar um segundo WorkItem
  ou reabrir o antigo.
- **Mitigação atual:** guarda fail-closed em
  `human_escalation_materialization_service.py`, coberta pelo teste
  TERMINAL-DUPLICATE.
- **Evidência:**
  `backend/app/services/human_escalation_materialization_service.py`
  (checagem de `TERMINAL_STATUSES`);
  `backend/app/core/human_escalation_eligibility.py`
  (`work_key_for_episode`, determinística, sem versionamento).
- **Condição objetiva de fechamento:** Survey/Freeze próprios definindo sob
  quais condições o mesmo episódio pode originar novo trabalho após
  encerramento, e qual identidade/idempotency key usar (nunca a mesma
  `work_key`, sob risco de colidir com o histórico).
- **Dependências:** nenhuma dependência de P1.2/P2; independente.

### P2 — Isolated Application Recovery Smoke

- **Estado:** aberto. Layer A (verificação de recuperação de banco de dados)
  já passou — ver `backend/DATABASE_BACKUP_VALIDATION.md`
  (`Database Recovery Layer A: PASS`, PR-4). O bloqueio é especificamente a
  ausência de um modo seguro de iniciar o backend sem que os 10 maintenance
  loops (`backend/app/main.py:lifespan()`) disparem automaticamente.
- **Risco concreto:** hoje não existe forma de comprovar que a aplicação — e
  não só o banco — se recupera corretamente após um restore. Subir o backend
  contra `auneron_recovery_drill` sem isolamento transformaria um smoke de
  recuperação em execução operacional real, capaz de mutar o snapshot
  restaurado através dos próprios maintenance loops.
- **Mitigação atual:** nenhuma — Layer B permanece registrada como bloqueada
  em vez de simulada ou pulada silenciosamente.
- **Evidência:** `backend/DATABASE_BACKUP_VALIDATION.md`
  §"Layer B — Application Recovery Smoke"; `backend/app/main.py` `lifespan()`
  (os 10 loops iniciam incondicionalmente, sem flag de supressão).
- **Condição objetiva de fechamento:** existir um mecanismo (flag ou modo de
  inicialização) que permita subir o backend com os maintenance loops
  suprimidos, possibilitando um smoke test de aplicação contra um banco
  restaurado sem risco de mutação; em seguida, executar e documentar um
  drill real de Layer B com o mesmo rigor de evidência do PR-4.
- **Dependências:** independente de P1, G3 e G4.

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
  um episódio após o WorkItem chegar a estado terminal permanece
  deliberadamente não resolvida — ver P1.3.

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
