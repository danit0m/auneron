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

### P1 — Intelligence-to-Human Recommendation Consumption

- **Estado:** aberto. `nba_policy.py` (`get_nba_decision()`) já calcula a
  decisão de Next Best Action a partir da elegibilidade de ações governadas,
  mas não existe nenhum consumidor operacional — endpoint, rotina ou
  superfície de UI — que exponha essa decisão a um humano autorizado.
- **Risco concreto:** não é um risco de segurança/autoridade (não há execução
  autônoma acoplada a essa decisão). É uma lacuna de valor: a inteligência já
  calculada nunca chega a um humano para agir sobre ela.
  Recomendação nunca concede autoridade — isso continua válido mesmo depois
  de fechado.
- **Mitigação atual:** nenhuma necessária — a ausência de consumidor não
  expõe nenhuma superfície de execução indevida.
- **Evidência:** `backend/app/core/nba_policy.py` (implementado e testado);
  nenhuma rota em `backend/app/api/routes/` expõe `get_nba_decision()` hoje.
- **Condição objetiva de fechamento:** existir um caminho read-only
  (endpoint e/ou superfície operacional) que apresente a decisão NBA a um
  humano autorizado, sem conceder execução automática nem ranking fabricado.
- **Dependências:** nenhuma — depende apenas de priorização.

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
