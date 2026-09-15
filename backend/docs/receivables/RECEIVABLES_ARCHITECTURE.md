# Receivables Governed Lifecycle Architecture

**Status:** Congelado (F3.1). Consolida F1 (`e603a53`), F2 (`47b3048`) e F3 (`04d7c2dd7ca9c266535f7a5bc6c904bc477081d2`) como uma arquitetura já entregue e provada em produção de teste (1170/1170, exit code 0). Documentação apenas — nenhuma mudança de comportamento é introduzida por este documento.

## 1. Objetivo & Escopo

Documentar a cadeia governada que detecta contas vencidas, leva a mutação a aprovação humana, executa o efeito de forma auditável, e mantém um registro operacional contínuo (`Knowledge`) do ciclo de vida do recebível — três commits, uma arquitetura:

```
F1 (e603a53)          F2 (47b3048)              F3 (04d7c2d)
detecção governada  →  aprovação/autoridade   →  lifecycle operacional
(system_principal)     (UI + skill_key           (Knowledge monitor,
                        server-derived)            duas verdades)
```

Não-objetivos: este documento não decide a próxima fatia funcional (ver `RECEIVABLES_MVP_CYCLE_MATRIX.md`); não propõe nenhuma correção de código; não reabre F1/F2/F3 para debate de design.

## 2. Mapa arquitetural

### 2.1 A cadeia de efeito (F1 → 25M → 25O)

```
OverdueDetectionService.run_scan()                    [F1, a cada N segundos + no startup]
  → resolve_system_principal()                        [fail-closed, nunca auto-provisiona]
  → SELECT Account WHERE status='aberto' AND vencimento < hoje
  → AIOrchestrator.observe("conta_vencida")            [observe-only, herdado de 25E]
  → OrchestratorSkillBindingProjectionService.resolve  [SELECT-only, herdado de 25F]
  → SystemAdvisoryEnvelope(decision, plan, principal)  [sibling de 25H, nunca unificado]
  → SystemAdvisoryProposalService.create(idempotency_key=
        "conta_vencida:{account_id}:{vencimento}:attempt:{N}")
  → request_approval_system()  → ApprovalRequest[pending]   [25M, requester=agent]
        ...
        aguarda decisão humana (ApprovalService.decide, RBAC + separation of duties)
        ...
  → dispatch_approved_system() → materialize_and_execute_system()
  → AccountMarkOverdueExecutionService: efeito transacional único, incluindo
    Account.status, SkillInvocation, ApprovalConsumption, WorkSkillExecution
    e WorkEvent atomicamente                                       [25O, reaproveitado]
```

A autoridade de execução nunca muda: é sempre o decisor humano (`decided_by_user_id`), revalidado em `materialize_and_execute_system`. O `system_principal` nunca é uma autoridade de execução — é só a proveniência de quem *observou e propôs*.

### 2.2 A cadeia de observação contínua (F3, paralela e independente)

```
run_receivables_monitor()                              [F3, a cada N segundos + no startup]
  → para CADA Account (sem filtro de status/data — table scan completo)
  → evaluate_receivable_lifecycle(financial_status, vencimento, hoje)  [função pura]
  → state ∈ {open, due_soon, due_today, overdue, overdue_alert, paid}
  → correlation_key = "receivable_lifecycle:v1:{account_id}:{vencimento}:{state}"
  → SE já existe Knowledge com essa correlation_key exata → nada (idempotente)
  → SENÃO → resolve alertas ativos anteriores da conta → insere novo Knowledge (se for alert state)
```

F3 nunca chama `AIOrchestrator`, nunca cria `AuthenticatedAdvisoryProposal`, nunca pede aprovação, nunca muta `Account.status`. É estritamente um escritor de `Knowledge` (auditoria/observabilidade), advisory por definição — não um segundo corredor de ação.

### 2.3 F2 é a superfície, não um terceiro corredor

F2 não adiciona nenhuma lógica de domínio nova. É: (a) o backend expondo `skill_key` como identidade server-derived em `ApprovalRequestResponse` (resolvida por linha via `SkillRepository.resolve_skill_keys_by_version_ids`, nunca inferida de `skill_version_id`), e (b) o frontend consumindo o corredor 25M/F1 via `/approvals` (listar + decidir) e removendo o seletor de "Status financeiro" do CRUD de conta, que já era ignorado pelo backend desde 25O mas ainda mentia visualmente para o usuário.

## 3. As duas verdades sobre "vencido" — e a duplicação de política não unificada

O código declara explicitamente duas verdades paralelas que podem divergir legitimamente:

| Verdade | Onde vive | Quem escreve | Semântica |
|---|---|---|---|
| `financial_status` (`Account.status`) | Persistido | Só o corredor governado F1→25M→25O, após aprovação humana | "O que o sistema *decidiu e executou*" |
| `receivable_lifecycle` (calculado) | Nunca persistido — projeção pura via `evaluate_receivable_lifecycle` | Ninguém escreve; recalculado a cada leitura (`AccountResponse`, dashboard, monitor F3) | "O que é *factualmente verdade* sobre a data de vencimento agora" |

Divergência é esperada e correta enquanto uma proposta aguarda decisão humana: uma conta pode estar `financial_status="aberto"` e `receivable_lifecycle.state="overdue_alert"` simultaneamente — isso não é um bug, é o desenho.

**Duplicação de política identificada**: o predicado de elegibilidade do scan F1 (`Account.status == "aberto" AND Account.vencimento < today`) é uma implementação independente da mesma regra de negócio de `evaluate_receivable_lifecycle()` — F1 foi escrito antes de F3 existir e nunca foi refatorado para reusar a função canônica. Sob o domínio real de status (`aberto`/`atrasado`/`pago`, únicos valores aceitos pela constraint de banco), as duas são **matematicamente equivalentes hoje**: não há nenhum cenário do domínio atual em que produzem respostas diferentes sobre "isso está vencido". **Classificação: dívida arquitetural de risco de drift futuro, não bug, não divergência ativa.** Se `OVERDUE_ALERT_THRESHOLD_DAYS` mudar, um novo status for adicionado, ou a definição de "vencido" mudar em um lugar, o outro não acompanha automaticamente. Fica registrado para uma futura unificação decidir, sem urgência.

## 4. Fronteiras de autoridade

- **`AuthorityProvenance`** (25G, humano): referência imutável a uma sessão humana já autenticada. Nunca carrega role/permission/scope/payload. Todo consumidor deve recarregar e reautorizar a autoridade atual antes de agir.
- **`SystemPrincipalProvenance`** (F1): tipo irmão, nunca generalização/união do humano. Não carrega `auth_session_id`. Resolvido por e-mail canônico (`sistema.vencimentos@auneron.core`), fail-closed — nunca auto-provisionado em runtime. `role="system"` é não-interativo: não autentica com senha, não recebe `AuthSession`, e uma `AuthSession` legada pertencente a ele é rejeitada em `require_user_session` (401).
- Cada camada da pilha de advisory-proposal (model, repository, dois service layers, bridge, work materialization, o scanner, o maintenance loop de recovery) tem um método irmão explícito para o caminho `system_principal` — nunca um branch condicional dentro do método humano (confirmado em 9 arquivos).
- A política de autonomia (`AUTONOMY_POLICY.md`, pré-existente) é o que faz `account.mark_overdue` (mutating) exigir aprovação humana em vez de auto-executar — F1 não introduz uma nova política, só um novo *originador* de propostas sujeitas à política já existente.

## 5. Idempotência — dois esquemas distintos, não intercambiáveis

| Esquema | Formato da chave | Escopo | Papel |
|---|---|---|---|
| **F1 — episódio/tentativa** | `conta_vencida:{account_id}:{vencimento}:attempt:{N}` | `AuthenticatedAdvisoryProposal.idempotency_key`, único parcial por `(authority_user_id, idempotency_key)` WHERE `system_principal` | Evita reabrir uma proposta de aprovação enquanto uma tentativa ainda está viva; muda de tentativa (`attempt+1`) em rejeição/expiração; abre novo episódio se `vencimento` mudar |
| **F3 — correlação de estado** | `receivable_lifecycle:v1:{account_id}:{vencimento}:{state}` | `Knowledge.correlation_key`, único global | Evita duplicar/reabrir um registro de Knowledge para a mesma transição de estado; sem noção de "tentativa" |

Existe ainda uma chave legada, pré-F1 (`conta_vencida:{account_id}`, sem `vencimento`/`attempt`), tratada explicitamente pela "Amendment A5" (bloqueio/liberação baseado no estado da legacy proposal) — nunca confundida com o esquema novo pelo regex de `find_latest_episode_attempt` (`test_legacy_orphan_is_never_selected_by_system_episode_lookup`).

## 6. Modelo de dados

**F1** — migration `8255bce7d929`:
- `authenticated_advisory_proposals.auth_session_id` → `nullable=True`.
- CHECK consolidado `ck_authenticated_advisory_proposals_provenance`: exatamente duas formas válidas (humana com sessão, sistema sem sessão); qualquer híbrido é rejeitado pelo Postgres (provado por 3 testes de INSERT bruto).
- Índice único parcial `uq_..._system_principal_key` em `(authority_user_id, idempotency_key)` WHERE `system_principal`.
- Downgrade fail-closed: recusa rodar se existir qualquer linha `system_principal`.

**F3** — migration `c4718a49cebf` (encadeada sobre a de F1):
- `knowledge.correlation_key VARCHAR(255) NULL UNIQUE`.
- `knowledge.resolved_at TIMESTAMPTZ NULL`.
- Downgrade fail-closed idêntico: recusa se existir qualquer linha populada.

Ambas seguem a disciplina de "downgrade nunca apaga dado para se viabilizar".

## 7. Contratos de serviço

- `OverdueDetectionService.run_scan(today=None, now=None) -> OverdueDetectionRunResult` — degrada graciosamente (`principal_available=False`) se o system_principal estiver indisponível; nunca propaga exceção nesse caso. Isola falha por conta (rollback + contador).
- `SystemAdvisoryProposalService.create(envelope, idempotency_key)` — mesma semântica de duplicidade do serviço humano; trata `IntegrityError` de corrida concorrente.
- `AuthenticatedAdvisoryProposalConsumptionService.validate_system_principal(...)` — revalida o principal atual, reautoriza via `authorize_skill_execution` (mesmo call do caminho humano), restringe por allowlist (`account.mark_overdue` é o único skill_key que o system_principal pode consumir).
- `AuthenticatedAdvisoryProposalApprovalBridgeService.request_approval_system` / `dispatch_approved_system` — irmãos diretos dos métodos humanos; o requester do `ApprovalRequest` continua `actor_type="agent"`.
- `ApprovalService.expire_pending_request_if_due` — única forma sancionada de materializar uma expiração sem decisão humana; idempotente, row-locked.
- `run_receivables_monitor() -> ReceivablesMonitorRunResult` — sem filtro de elegibilidade (varre todas as contas); ordem congelada (checar `correlation_key` existente **antes** de resolver o alerta anterior) é o invariante central, provado por 6 testes.
- `evaluate_receivable_lifecycle(*, financial_status, vencimento, today) -> ReceivableLifecycle` — pura, sem I/O; usada por 3 consumidores independentes (schema de resposta, dashboard, monitor).
- `AccountMarkPaidExecutionService.execute(...)` — corredor governado para `account.mark_paid`: humano solicita (rota genérica `POST /approvals/skill-executions/{version_id}`) → outro humano aprova → este serviço executa após validação manual da aprovação (ADR 009 — substitui `GovernedSkillExecutionService.validate_approved_action_only()` porque aquele método exige estruturalmente um ator não-humano). Ledger completo (`ApprovalConsumption`+`SkillInvocation`+`AccountEvent`), sem camada Work/WorkEvent (fluxo simplificado, deliberado). Execução é síncrona e sob demanda — não há loop de auto-dispatch: um humano aciona explicitamente `POST /accounts/{id}/execute-mark-paid`.

## 8. Restart / recuperação / loops de background

Ordem no `lifespan` de `main.py` (startup recovery, síncrono antes de servir tráfego):

```
1.  run_auth_session_cleanup
2.  run_skill_invocation_recovery
3.  run_work_skill_execution_recovery_async
4.  run_work_outcome_evaluation_recovery_async
5.  run_pilot_mutation_recovery_async
6.  run_overdue_detection_async()                     [F1]
7.  run_advisory_dispatch_recovery_async()
8.  run_client_behavior_memory_recalculation_async()
9.  run_client_classification_recalculation_async()
10. run_receivables_monitor_async()                   [F3 — sempre por último]
```

Loops de background contínuos (ordem de criação da tupla — não é a mesma ordem da recovery acima):

```
auth_session → skill_invocation → work_skill_execution → work_outcome_evaluation
→ pilot_mutation → advisory_dispatch → overdue_detection → client_behavior_memory
→ client_classification → receivables_monitor
```

`overdue_detection_maintenance_loop` reusa `work_skill_recovery_interval_seconds` (sem intervalo dedicado); `receivables_monitor_maintenance_loop` tem intervalo próprio (`receivables_monitor_interval_seconds`, default 300s, `ge=60, le=86400`).

`run_advisory_dispatch_recovery` (F1 reescreveu): introduz um cursor transitório (`after_id`, nunca persistido) para que uma proposta permanentemente órfã não bloqueie candidatos válidos posteriores no mesmo lote (V12) — antes de F1, `batch_size=1` fazia o recovery girar para sempre na mesma linha órfã.

`AccountMarkPaidExecutionService` não precisa de recovery dedicado: é síncrono e idempotente por construção (`get_consumption_by_request` detecta reexecução); não há estado "em voo" entre aprovação e execução.

## 9. Invariantes

1. O scan de overdue nunca muta `Account.status` diretamente (por construção do código; provado por `test_entry_to_effect_overdue_account_reaches_atrasado` reexecutando o scan após o efeito e checando `accounts_checked == 0`).
2. `system_principal` nunca autentica, nunca recebe sessão, nunca é aceito por `require_user_session` mesmo com `AuthSession` legada (`test_i6_a/b/c`).
3. A constraint de proveniência híbrida é rejeitada pelo Postgres, não só pela aplicação (3 testes parametrizados de INSERT bruto).
4. Um episódio ativo nunca é duplicado por scans repetidos; rejeição abre nova tentativa; mudança de `vencimento` abre novo episódio, preservando o antigo.
5. Uma legacy proposal pré-F1 ativa bloqueia F1 de abrir uma tentativa nova para a mesma conta; uma legacy proposal sem `ApprovalRequest` correlacionável falha fechado.
6. Reexecutar o monitor de F3 sem mudança real de estado não duplica nem reabre um alerta já resolvido/reconhecido manualmente (`test_rerun_without_state_change_does_not_duplicate_or_resolve`).
7. O contador `knowledge_resolved` reflete o número real de linhas afetadas, não um booleano (`test_resolving_multiple_active_rows_counts_correctly`).
8. `receivable_lifecycle` na resposta de `AccountResponse` é sempre recalculado pelo `model_validator`, incondicionalmente — nunca aceito como entrada. Esta é uma garantia por construção do código; não há teste de regressão dedicado que a exercite especificamente.
9. `skill_key` em `ApprovalRequestResponse` é resolvido por linha, não copiado uniformemente para toda a página (`test_list_resolves_skill_key_per_item_not_uniformly`, dois skills distintos na mesma listagem).

## 10. O que explicitamente ainda não foi construído

- **Nenhuma notificação/sinalização de fila de aprovação pendente.** Nem `mark_overdue` nem `mark_paid` têm qualquer indicador passivo hoje (sem badge, sem contador no dashboard, sem push/e-mail) — confirmado por inspeção de `Sidebar.tsx`, `dashboard.py` e todo o frontend. É a única etapa do ciclo observar→decidir→chamar atenção→aprovar→executar→auditar→sobreviver-restart sem nenhum mecanismo, para qualquer ação. Ver `RECEIVABLES_MVP_CYCLE_MATRIX.md`.
- **Nenhuma aquisição autônoma do fato de pagamento** — decisão de escopo deliberada, não lacuna: pagamento é tratado como fato externo/humano informado por uma fonte autorizada. Detectar pagamento de forma confiável exigiria uma fonte de verdade externa (ERP, banco, gateway, conciliação), fora do escopo do primeiro piloto. A execução governada de `account.mark_paid` já existe e é usada quando o fato é informado por um humano.
- **A duplicação do predicado de "vencido"** entre o scan F1 e `evaluate_receivable_lifecycle()` (seção 3) permanece não unificada — equivalente hoje, risco de drift futuro.
- **O frontend não exibe `receivable_lifecycle`** em nenhuma tela por conta — a API expõe o campo desde F3, mas nenhuma mudança de frontend em F1/F2/F3 o consome. O dashboard agregado já reflete os números corretos.

## 11. Nota separada: trilha "Fatia 2 — Entendimento" (classificação de cliente)

Trilha paralela, não misturada com F1/F2/F3 por design. Commits visíveis: `b6b0570` (Fatia 2A), `fbd2b5e` (Fatia 2B). Existe material de trabalho sugerindo uma "Fatia 2C" cujo status de conclusão (completa vs. apenas sem fechamento documental) não foi investigado neste levantamento e permanece como questão aberta e separada.
