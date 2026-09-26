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
G3, antes listado aqui, foi implementado e comprovado por evidência real —
ver "CLOSED / EVIDENCE" abaixo.

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

### Controlled Production Pilot — Technical Final Acceptance

- **Não é um gap.** Este item não representa uma lacuna que foi aberta
  e depois fechada — é a preservação, neste índice de evidências, da
  execução do primeiro piloto controlado real dentro do readiness
  contract já fechado por P1.2/P1.3/P2 acima. Nada abaixo reabre ou
  substitui esses fechamentos.
- **Baseline da execução:** `e2bda4fc3ea2a07f8b961f2fc732950e50db76eb`.
- **Episódio:** `(account_id=18, due_date=2026-09-09)`.
- **Skill:** `account.mark_overdue`, versão `1.0.0`.
- **Resultado:** `TECHNICAL FINAL ACCEPTANCE: PASS`.
- **Evidência:** `backend/docs/operations/CONTROLLED_PILOT_EVIDENCE.md`
  — cadeia completa `Materialize → Decision → Execute`, IDs canônicos,
  isolamento do corredor legado, recovery pós-mutação e a distinção
  entre reentrada estruturalmente verificada e replay empírico (não
  executado). Este registro não reproduz essa narrativa.
- **Nota:** o resultado comprova que o corredor controlado satisfaz o
  readiness contract definido para este piloto, dentro do boundary
  congelado (principals, conta e ação específicos). Não autoriza, por
  si só, expansão do corredor humano para outros clientes, contas,
  actions ou volume — isso é decisão de um gate operacional posterior.

### Controlled Operator/UI Flow — Episode 002

- **Não é um gap.** Preservação, neste índice de evidências, da
  execução do primeiro ciclo do corredor humano operado inteiramente
  pela interface (não mais via API direta como no Episódio 001), com
  dois principals distintos e sessões independentes.
- **Baseline:** `8a7ae0b6e0265ea07362c2d7a9d2dc19ce18f831`.
- **Episódio:** `(account_id=17, due_date=2026-08-04)`.
- **Skill:** `account.mark_overdue`, versão `1.0.0`.
- **Resultado:** fluxo `Requester/UI → Approver/UI → nova sessão
  Requester/UI → execução governada` demonstrado ponta a ponta, com
  recovery pós-mutação verificado.
- **Evidência:** `backend/docs/operations/CONTROLLED_OPERATOR_UI_FLOW_EVIDENCE.md`
  — cadeia completa, segregação/autoridade, idempotência, isolamento do
  advisory legado `#13`, Recovery Evidence e `FINDING-UI-001` (achado
  de UX non-blocking, registrado sem correção). Este registro não
  reproduz essa narrativa.
- **Nota:** assim como o item anterior, não autoriza expansão do
  corredor para outros clientes, contas, actions ou volume.

### Controlled Repeatability Pilot — Episode 003

- **Não é um gap.** Preservação, neste índice de evidências, da
  demonstração de que o corredor humano já implantado repete um novo
  episódio no mesmo baseline, sem desenvolvimento/redeploy adicional,
  coexistindo com approvals agent-only preexistentes.
- **Baseline:** `18ac6536851db37aa5f439421c65f3b247daa838` (mesmo dos
  Episódios 001/002 — nenhuma alteração de código entre eles).
- **Episódio:** `(account_id=1, due_date=2026-08-28)`.
- **Skill:** `account.mark_overdue`, versão `1.0.0`.
- **Resultado:** `Controlled Repeatability Pilot / Episode 003 — PASS`
  — fluxo `Requester/UI → Approver/UI → nova sessão Requester/UI →
  execução governada` repetido com sucesso, isolamento de namespace
  preservado frente a advisories agent-only coexistentes, recovery
  pós-mutação verificado.
- **Evidência:** `backend/docs/operations/CONTROLLED_REPEATABILITY_PILOT_EVIDENCE.md`
  — cadeia completa, coexistência com approvals agent-only, SoD,
  idempotência, `FINDING-UI-001` (reproduzido, non-blocking) e Recovery
  Evidence. Este registro não reproduz essa narrativa.
- **Nota:** o resultado comprova repetibilidade do corredor controlado
  dentro do boundary já congelado. Não declara scale hardening,
  prontidão externa, generalização para outras Skills mutáveis ou
  conclusão de um Second Mutating Skill — essas permanecem decisões de
  gates operacionais posteriores. G3/G4 permanecem exatamente onde
  estão e com sua classificação atual.

### Controlled Mark-Paid Pilot — Episode 001

- **Não é um gap.** Preservação, neste índice de evidências, do
  primeiro episódio operacional real do corredor `account.mark_paid`
  (ADR 009, distinto do corredor `WorkItem`-oriented de
  `mark_overdue`), sob o guard `approver != executor` introduzido pelo
  Second Mutating Skill — Safety Delta V1.
- **Baseline:** `aede5af74c2878ef0dca0a6b1f8f856fef24fcfc`
  (`test(governance): harden mark paid execution safety`).
- **Episódio:** `(account_id=2)`, `atrasado → pago`.
- **Skill:** `account.mark_paid`, versão `1.0.0`.
- **Resultado:** `Controlled Mark-Paid Pilot — Episode 001 — PASS` —
  três identidades independentes (`requester=user5, approver=user6,
  executor=user3`), fluxo `Human Request → Independent Approval →
  Independent Execution` demonstrado ponta a ponta, efeito único,
  cadeia persistida reconstruída por join direto das tabelas, recovery
  pós-mutação verificado.
- **Evidência:** `backend/docs/operations/CONTROLLED_MARK_PAID_PILOT_EVIDENCE.md`
  — cadeia completa, SoD, identidade/idempotência, isolamento, a
  semântica negativa de `payment_observed`, `FINDING-MARK-PAID-001` e
  `DEFERRED-CONCURRENT-AUTHORITY` (ambos registrados como open/deferred,
  não resolvidos) e Recovery Evidence. Este registro não reproduz essa
  narrativa.
- **Nota:** não declara observação/comprovação de pagamento,
  convergência de autoridades concorrentes, generalização para outras
  contas/valores, scale hardening, prontidão externa ou início da
  extração do Governed Action Model — essas permanecem decisões de
  gates operacionais posteriores.

### Governed Action Model V1 — Design Freeze

- **Não é um gap.** Preservação, neste índice de evidências, do
  fechamento do Design Freeze do contrato conceitual cross-cutting
  extraído da comparação mecânica entre `account.mark_overdue` e
  `account.mark_paid`. Não é uma correção de runtime — nenhum código,
  teste, schema ou corredor foi alterado por este checkpoint.
- **Baseline de entrada:** `52ac74cde3223019c09d8f621d93ff82fca763e5`.
- **Resultado:** `Governed Action Model V1 — DESIGN FREEZE: CLOSED —
  PASS` — quatro invariantes normativos (`N0`–`N3`), cinco categorias
  formais de classificação, e os dois corredores existentes avaliados
  como `GAM V1 ASSESSED WITH KNOWN DEVIATION` (nenhum é
  `GAM V1 COMPLIANT`).
- **Documento:** `backend/docs/GOVERNED_ACTION_MODEL.md` — contrato
  completo, matriz de conformidade, mapeamento de mecanismos não
  normativos, Known Deviations e Unproven Properties. Este registro
  não reproduz essa narrativa.
- **Nota:** permanecem explicitamente abertos/não resolvidos por este
  checkpoint — `KD-1` (`account.mark_overdue` não impõe N1
  estruturalmente); `KD-2`/`FINDING-MARK-PAID-001` (`account.mark_paid`
  não satisfaz N2 integralmente); `DEFERRED-CONCURRENT-AUTHORITY`
  (convergência de autoridade permanece propriedade separada, não é
  Desired Invariant V1); ausência de `payment_observed`. Nenhuma
  correção de corredor foi realizada.

### Governed Action Model V1 — N1/N2/N3 Closure and Corridor Conformance

- **Não é um gap novo.** Preservação, neste índice de evidências, do
  fechamento sequencial dos itens deixados explicitamente abertos pelo
  Design Freeze acima — este registro não reescreve aquela entrada, que
  permanece correta como fotografia do checkpoint `52ac74c...`.
- **Baselines de fechamento:**
  - `KD-1` — `4629bb45d487e988d81688c64077243b09cdce77` ("fix(governance):
    enforce mark overdue authority separation").
  - `KD-2` / `FINDING-MARK-PAID-001` —
    `8593ed6d499b569f5365bf4c36386b3abf2e1b80` ("fix(governance): govern
    missing account execution failure").
  - `FINDING-MARK-OVERDUE-CONCURRENCY-001` —
    `f060d972bdfcaad2516f53d6082f508120b37343` ("fix(governance): resolve
    mark overdue concurrent lock-order deadlock").
- **Resultado:**
  ```
  account.mark_overdue
    N0-N3 COMPLIANT
    GAM V1 COMPLIANT

  account.mark_paid
    N0-N3 COMPLIANT
    GAM V1 COMPLIANT
  ```
- **Evidência:** `backend/docs/GOVERNED_ACTION_MODEL.md` §6/§8 — matriz
  de conformidade atualizada e registro histórico fechado dos três
  deviations. N3 apoia-se em dois reproducers de execução concorrente
  (`tests/test_human_account_mark_overdue_execution_concurrency.py`,
  `tests/test_account_mark_paid_execution_concurrency.py`), cada um
  exercitando duas autoridades concorrentes a partir de sessões
  independentes, com bloqueio real observado antes da liberação. Este
  registro não reproduz a narrativa completa.
- **Nota:** esta conclusão vale exclusivamente para os dois corredores
  formalmente avaliados — `account.mark_overdue` e `account.mark_paid`
  — não para toda ação mutável da plataforma. `DEFERRED-CONCURRENT-AUTHORITY`
  (UP-2) permanece explicitamente deferred, não fechada por este item —
  N3 comprova ausência de efeito duplicado na execução sob autoridades
  concorrentes, não convergência de autoridade na criação/aprovação.

### Controlled Pilot — Episode 004 (Competing Authority + Recovery Proof)

- **Status:** CLOSED / EVIDENCE.
- **Evidence:** `backend/docs/operations/CONTROLLED_COMPETING_AUTHORITY_EVIDENCE.md`
  — cadeia completa E004.1–E004.5, fixture (`Account 19`), IDs
  concretos, recovery drill isolado e observação de autoridade
  concorrente. Este registro não reproduz essa narrativa.
- **Result:**
  - human `account.mark_overdue` corridor completed;
  - `Account 19` `aberto` → `atrasado` exactly once in the observed
    episode;
  - requester/approver/executor = 1/6/4;
  - recovery reconstruction PASS;
  - agent-originated competing authority observed.
- **Boundary:**
  - cross-corridor concurrent execution was not experimentally
    tested;
  - UP-2 remains DEFERRED;
  - no tenant-isolation or external/customer-MVP readiness claim.

### Controlled Digital Worker Pilot — Episode 005 (Recommendation Provenance + Full Chain)

- **Não é um gap.** Preservação, neste índice de evidências, do
  primeiro episódio a demonstrar, ponta a ponta, a cadeia completa
  `Observe → Understand → Recommend → Recommendation Provenance →
  Declared Human Materialization → Independent Approval → Governed
  Execution → Effect Verification → Report` — os episódios 001–004
  cobrem apenas `Materialize → Decision → Execute`, todos anteriores
  aos checkpoints DW-6.4A/DW-6.4B/DW-6.5 que introduziram a proveniência
  de recomendação.
- **Baseline (código):** `38c845f2fc6f2bd071e01e8a01030a71b05c02f5` —
  inalterado por todo o episódio.
- **Episódio:** `(account_id=20, due_date=2026-09-14)`.
- **Skill:** `account.mark_overdue`, versão `1.0.0`.
- **Resultado:** `CONNECTED DIGITAL WORKER PILOT — EPISODE 005 —
  TECHNICAL RESULT: PASS` — `NbaRecommendationSnapshot #1` →
  `WorkItem #9` (associação declarada `recommendation_snapshot_id=1`) →
  `ApprovalRequest #20` → `ApprovalDecision #11` →
  `ApprovalConsumption #10` → `SkillInvocation #10` → `AccountEvent #15`
  (`aberto → atrasado`) → `BusinessEffectVerification #10` (`VERIFIED`)
  → `Outcome` reconstruindo `recommendation_provenance.linkage=correlated`
  num único `GET`, sem nenhuma escrita. Reentrada testada
  empiricamente (não apenas estruturalmente) em dois pontos —
  materialização e execução — ambas `duplicate=true`, delta `+0`.
- **Evidência:** `backend/docs/operations/CONTROLLED_DIGITAL_WORKER_PILOT_EVIDENCE.md`
  — cadeia completa, proveniência operacional dos quatro gates
  preparatórios (migration/rebuild/isolamento de manutenção/credenciais),
  boundary de atores (`requester=5, approver=6, executor=4`), isolamento
  do corredor legado, o advisory `cliente_criado:20` explicitamente
  classificado como fora do episódio, e a distinção entre BEV
  sincronamente disparada e recuperação automática (não exercitada
  aqui). Este registro não reproduz essa narrativa.
- **Nota:** o resultado comprova exclusivamente L2 — Governed Operator,
  restrito a este corredor e episódio. Não autoriza, por si só,
  autonomia L3/L4, produtização pela UI, generalização para outros
  corredores, ou qualquer alegação de verdade financeira externa —
  essas permanecem decisões de gates operacionais e de design
  posteriores.

### G3 — Production Database Identity Guard

- **Baseline de fechamento:** commit
  `50e698bb14f91ea764849aecf514a395f4a91aed` ("fix(config): enforce
  production database identity").
- **Evidência:** `backend/app/core/config.py`
  (`Settings.expected_database_name`/`expected_database_host`, nova
  property `database_host`, bloco de `validate_environment()`
  exclusivo de `environment=="production"` que exige as duas
  variáveis e rejeita qualquer divergência frente a
  `database_name`/`database_host` derivados de `DATABASE_URL`);
  `backend/tests/test_config_security.py` (matriz dedicada: identidade
  correta, mismatch de nome, mismatch de host, ausência individual das
  duas variáveis, `DATABASE_URL` sem host, comportamento preservado
  fora de `production`, ausência de vazamento de
  `DATABASE_URL`/credenciais nas mensagens de erro);
  `backend/docker-compose.prod.yml`
  (`EXPECTED_DATABASE_NAME`/`EXPECTED_DATABASE_HOST` obrigatórias e
  fail-fast em `migration` e `backend`, confirmado por `docker compose
  config` com e sem as variáveis); suíte completa do backend,
  `1425/1425 PASS`.
- **Nota:** identidade positiva restrita a nome + host do banco,
  declarada pelo operador — não é prova criptográfica/física da
  identidade do PostgreSQL, não detecta `DATABASE_URL` e
  `EXPECTED_DATABASE_*` configurados incorretamente em conjunto, e não
  introduz nenhum conceito de tenant/customer identity.
  `EXPECTED_DATABASE_PORT` permanece fora de escopo, por decisão
  explícita — pode ser adicionado depois sem alterar este modelo.
  Runbook correspondente:
  `backend/docs/operations/CUSTOMER_DEPLOYMENT_RUNBOOK.md`.

### G4 — Policy Autonomous Worker L3 Pilot (account.mark_overdue)

- **Baseline (código):** `479618cd8ed11d333346a87affecae41a2a0ef6a` —
  inalterado durante todo o piloto.
- **Schema:** migration `a3f7c9d15e28`.
- **Episódios:** `(account_id=21, due_date=2026-09-15)` — positivo;
  `(account_id=22, due_date=2026-09-16)` — negativo/negado.
- **Resultado:** `CONTROLLED CONNECTED AUTONOMOUS PILOT — PROVEN` —
  `PolicyAuthorityGrant #1` (criado, ativo, depois revogado) →
  `PolicyAuthorityConsumption #1` → `SkillInvocation #11` (`succeeded`)
  → `AccountEvent #16` (`aberto → atrasado`) →
  `BusinessEffectVerification #11` (`verified`, ação manual pós-janela).
  Episódio negativo confirmou zero efeito sob autoridade indisponível
  (`executions_authority_unavailable=1`), reproduzido identicamente
  após restart controlado do backend (`--force-recreate`).
- **Evidência:** `backend/docs/operations/
  POLICY_AUTONOMOUS_WORKER_L3_PILOT_EVIDENCE.md` — cadeia completa dos
  três cenários (positivo/negativo/restart), achado de observabilidade
  `FINDING-DW75-OBS-001` (não-bloqueante), claim formal e listas
  explícitas de PROVEN/NOT PROVEN.
- **Nota:** o resultado comprova L3 exclusivamente para
  `account.mark_overdue`, modelo single-customer-per-deployment, sob
  um Grant deployment-wide predelegado. Não autoriza, por si só,
  `account.mark_paid`, outros skills, múltiplos episódios simultâneos,
  multi-tenancy, aprendizado adaptativo ou autonomia irrestrita — essas
  permanecem decisões de checkpoints futuros e distintos.
