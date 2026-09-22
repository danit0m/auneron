# Controlled Competing Authority Evidence — Episode 004

## 1. Purpose and Scope

Este documento é o snapshot histórico e autocontido do Episódio 004 do
corredor humano `account.mark_overdue` (`HumanAccountMarkOverdueMaterializationService`
/ `HumanAccountMarkOverdueExecutionService`). Diferente dos três
episódios anteriores (`CONTROLLED_PILOT_EVIDENCE.md`,
`CONTROLLED_OPERATOR_UI_FLOW_EVIDENCE.md`,
`CONTROLLED_REPEATABILITY_PILOT_EVIDENCE.md`), este episódio:

- criou uma `Account` nova, propositalmente, pelo caminho oficial do
  produto (`POST /accounts/`), em vez de reutilizar uma conta já
  existente;
- observou, sem provocar, o nascimento natural de uma autoridade
  agent-originated concorrente para a mesma condição de negócio,
  antes de qualquer ação humana;
- executou o corredor humano completo com três atores humanos
  totalmente distintos;
- reconstruiu a cadeia governada inteiramente a partir de dados
  persistidos (não da aplicação em execução);
- provou recovery pós-efeito — backup, restore isolado, reconstrução
  no banco restaurado, cleanup, e verificação de que o banco real
  permaneceu bit-a-bit equivalente nos campos observados antes/depois
  do drill.

Este documento não reproduz o histórico dos episódios anteriores nem a
narrativa completa do fechamento do GAM V1 — ver
`backend/docs/GOVERNED_ACTION_MODEL.md` e
`backend/docs/operations/PRODUCTION_READINESS_GAP_REGISTER.md`.

## 2. Baseline

```
HEAD / origin/main: 10f49329e9a582f3dc2be69044f1735a127a57ea
Ambiente: auneron (host=localhost, port=5433) — APP_ENV=development
Alembic: 298a1e501b39
Skill: account.mark_overdue, skill_id=1, version_id=1, published, mutating
```

## 3. Fixture Creation

```
Account:      19
cliente:      Cliente Piloto Controlado E004
email:        (vazio)
whatsapp:     (vazio)
valor:        100.00
vencimento:   2026-09-12
status inicial: aberto (default automático — não enviado no payload)
```

Criada por `POST /accounts/` (caminho oficial do produto,
`AccountCreate`), autenticado como `user 1 / administrator`. Antes da
criação: 7 `Account`s existentes, todas já consumidas por episódios
anteriores (6 `atrasado`, 1 `pago`), nenhuma em `aberto`. Efeito
colateral conhecido e tolerado: publicação do evento interno
`cliente_criado` e criação de uma `AuthenticatedAdvisoryProposal`
(id=23) pelo subsistema advisory — não pertence à cadeia governada de
`mark_overdue` e não produziu nenhum `WorkItem`/`ApprovalRequest`/
`AccountEvent` na criação.

## 4. Naturally Observed Agent-Originated Authority

Sem nenhuma ação deste episódio, o job de manutenção periódico
`overdue_detection` (`OverdueDetectionService`, agente
`OverdueDetectionAgent`) encontrou a nova `Account 19` como candidata
genuína (`status=aberto`, `vencimento` passado) e propôs
autonomamente, pelo corredor agent-only legado, uma execução de
`account.mark_overdue`:

```
AuthenticatedAdvisoryProposal 24
  idempotency_key: conta_vencida:19:2026-09-12:attempt:1

ApprovalRequest 18
  requester_actor_type: agent
  requester_reference:  agent:OverdueDetectionAgent
  target_account_id:    19
  skill_version_id:     1
  status:                pending
  risk_level:             high
  required_permission:    approval:decide
  idempotency_key:        advisory:24:1
```

Esta autoridade nasceu ~29s após a criação da conta, antes de qualquer
materialização humana. Ela é arquiteturalmente independente do
corredor humano (`work_key`/`idempotency_key` em namespaces
completamente distintos — ver §10).

## 5. Human Materialization — E004.1

```
Ator: user 1 / administrator (requester/materializer)
Rota: POST /recommendations/mark-overdue/accounts/19/episodes/2026-09-12/materialize → 201 Created

WorkItem 8
  work_key:   account_mark_overdue:v1:19:2026-09-12
  account_id: 19
  status pós-materialização: ready

Human ApprovalRequest 19
  requester_actor_type: user
  requester_user_id:    1
  target_account_id:    19
  skill_version_id:     1
  status:                 pending
  risk_level:              high
```

Nenhum efeito de negócio, nenhuma decisão, nenhuma execução nesta
etapa. Logout normal do requester ao final; sessão revogada.

## 6. Human Decision — E004.2

```
Ator: user 6 / manager (approver)
Rota: POST /approvals/19/decision {"decision":"approved"} → 200 OK

ApprovalDecision 10
  approval_request_id:  19
  decision:               approved
  decided_by_user_id:    6
  decided_by_role:        manager
```

`ApprovalRequest 18` (agent) permaneceu `pending`, não tocada — apenas
`ApprovalRequest 19` (humana) recebeu decisão. Confirmado sem
ambiguidade pela inspeção da rede (`POST /api/approvals/19/decision`)
e pela correlação exata de timestamps na UI (a linha da request 19
tinha `criada em` 11:26:52, distinta da linha da request 18, `criada
em` 10:48:24). Logout normal do approver; sessão revogada.

## 7. Human Execution — E004.3

```
Ator: user 4 / administrator (executor)
Rota: POST /recommendations/mark-overdue/accounts/19/episodes/2026-09-12/execute → 200 OK

ApprovalConsumption 9
  approval_request_id:   19
  approval_decision_id:  10
  skill_invocation_id:    9
  authority_user_id:      4
  authority_role:          administrator
  status:                   consumed

SkillInvocation 9
  skill_version_id: 1 (account.mark_overdue)
  status:             succeeded
  output_payload:      {account_id:19, previous_status:aberto, new_status:atrasado, changed:true}

AccountEvent 14
  account_id:      19
  actor_user_id:   4
  previous_status: aberto
  new_status:      atrasado

Effect Receipt — WorkEvent 45
  work_item_id:    8
  event_type:       system_note
  idempotency_key:  effect:human_account_mark_overdue:approval:19
```

`ApprovalRequest 18` permaneceu `pending`, `0` decisions, `0`
consumptions — não foi tocada por esta execução. Logout normal do
executor; sessão revogada.

## 8. Persistent AFTER Reconstruction — E004.4

Reconstrução integral por dados persistidos, com relações por chave
estrangeira real (não coincidência de IDs):

```
Account 19 → WorkItem 8 → ApprovalRequest 19 → ApprovalDecision 10
  → ApprovalConsumption 9 → SkillInvocation 9 → AccountEvent 14
  → WorkEvent 45
```

```
Quem pediu?      user 1
Quem aprovou?    user 6
Quem executou?   user 4
Account afetada: 19
Estado anterior: aberto
Estado posterior: atrasado
```

Isolamento confirmado: as 7 `Account`s de controle (ids 1, 2, 3, 4, 5,
17, 18) permaneceram com `status`/`valor`/`vencimento` idênticos ao
estado anterior ao episódio.

## 9. Exactly-Once Evidence for This Observed Episode

For the observed Episode 004, the human-governed corridor produced
exactly one persisted business effect for Account 19.

```
human WorkItem correspondente:               1
human ApprovalRequest correspondente:        1
approved decision da request 19:             1
ApprovalConsumption da request 19:           1
successful SkillInvocation da execução:      1
AccountEvent aberto→atrasado do episódio:    1
business_effect_receipt do WorkItem 8:       1
```

Esta seção descreve exclusivamente o episódio observado — não uma
propriedade universal do sistema (ver §13).

## 10. Competing Authority Observation

```
ApprovalRequest 18
  origin:      agent:OverdueDetectionAgent
  proposal:    24
  idempotency: conta_vencida:19:2026-09-12:attempt:1

Observed authority coexistence:                 YES
Observed cross-corridor concurrent execution:    NO
Structural cross-corridor protection:            supported by code
                                                  inspection (mesmo
                                                  padrão Account
                                                  FOR UPDATE +
                                                  status re-check em
                                                  AccountMarkOverdueExecutionService
                                                  e
                                                  HumanAccountMarkOverdueExecutionService)
Experimental cross-corridor concurrency proof:   NOT ESTABLISHED

UP-2 (DEFERRED-CONCURRENT-AUTHORITY): DEFERRED — inalterado por este
  episódio.
```

`ApprovalRequest 18` permaneceu `pending`, com `0` decisions e `0`
consumptions, em **todos** os pontos de observação deste episódio —
antes da materialização humana, entre cada gate, imediatamente após a
execução (E004.4), e novamente no banco restaurado pelo recovery
drill (E004.5). Isto é um **stale competing authority observed after
the human corridor satisfied the underlying business condition** — a
condição econômica subjacente da `Account 19` já foi resolvida pelo
corredor humano, mas a autoridade agent-originated nunca foi
notificada, decidida ou consumida.

Esta é uma **observação operacional**, não uma classificação
automática como defeito, vulnerabilidade ou violação do GAM V1. O
tratamento de sua semântica (se e como uma autoridade agent-originated
deveria ser conciliada após um efeito concorrente já ter sido
produzido por outro corredor) permanece um trabalho separado, não
aberto nem resolvido por este documento.

## 11. Recovery Proof — E004.5

```
backup:          backup_20260922T154031Z.dump
size:             185162 bytes
SHA-256:          2f1bfb06200fe2760e3651e4824489943dcf64ea9118b3d8933b0b1d3557c326
source:           auneron
restore target:   auneron_recovery_drill

result:
  reconstruction PASS
  seven cardinalities = 1 (idênticas à §9, reproduzidas no banco restaurado)
  cleanup PASS (auneron_recovery_drill removido, confirmado por
    consulta independente a pg_database)
  real DB pre/post fingerprint identical (Account 19, WorkItem 8,
    ApprovalRequest 19, ApprovalConsumption 9, SkillInvocation 9,
    AccountEvent 14, WorkEvent 45, ApprovalRequest 18 e todas as
    contagens globais — idênticos antes e depois do drill)
```

O hash SHA-256 foi verificado de forma independente (recalculado a
partir do arquivo local, comparado ao valor impresso pelo script e ao
sidecar `.sha256`), não apenas aceito do exit code do comando. O
restore usou exclusivamente o mecanismo oficial
(`assert_safe_restore_target()`, quatro condições congeladas, código
não alterado) — o único alvo aceito foi `auneron_recovery_drill`;
`current_database()` foi confirmado antes de qualquer outra consulta,
tanto no banco restaurado quanto na reconexão ao banco real. O
artefato binário do backup e seu `.sha256` **não fazem parte deste
repositório** — permanecem exclusivamente no armazenamento local do
operador, seguindo o mesmo precedente já usado em
`DATABASE_BACKUP_VALIDATION.md` e nos episódios anteriores.

## 12. Isolation and Authentication

```
Accounts de controle (1, 2, 3, 4, 5, 17, 18): status/valor/vencimento
  idênticos antes/depois do episódio inteiro (materialização,
  decisão, execução e recovery drill).

Sessões:
  user 1 (requester): revogada ao final de E004.1
  user 6 (approver):  revogada ao final de E004.2
  user 4 (executor):  revogada ao final de E004.3
  sessões válidas globais ao final de cada gate: 0
```

## 13. Claim Boundary

This evidence establishes:
- Episode 004 COMPLETE/PASS.
- One observed persisted aberto → atrasado effect.
- Complete human-governed chain.
- Three distinct human actors (1 → 6 → 4).
- Persistent reconstruction after isolated backup/restore.
- Real coexistence of agent-originated and human-originated
  authorities.

This evidence does NOT establish:
- universal exactly-once behavior;
- Auneron-wide GAM V1 conformance;
- experimentally proven cross-corridor concurrent execution safety;
- closure of UP-2;
- tenant isolation;
- readiness of the first external/customer MVP.

## 14. Final Result

```
EPISODE 004 — account.mark_overdue: COMPLETE / PASS

Gates:
  E004.1 Human Materialization    PASS
  E004.2 Human Decision           PASS
  E004.3 Human Execution          PASS
  E004.4 AFTER Reconstruction     PASS
  E004.5 Recovery Proof           PASS

SoD: requester(1) != approver(6) != executor(4), requester(1) != executor(4)

Competing authority: observed, preserved, not consumed, not decided.
UP-2: DEFERRED.
```
