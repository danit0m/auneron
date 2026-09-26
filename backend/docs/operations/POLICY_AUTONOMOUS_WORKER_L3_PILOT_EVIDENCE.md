# Policy Autonomous Worker — L3 Pilot Evidence

**Status:** Controlled Connected Autonomous Pilot — PROVEN, corredor
`account.mark_overdue`, single-customer-per-deployment.

## 1. Objetivo

Registrar, de forma permanente e auditável, a evidência que sustenta o
claim formal de L3 (Policy Autonomous Worker) para o corredor
`account.mark_overdue`, produzida pelo piloto controlado DW-7.5.1 —
um episódio em que a condição de negócio foi observada e o efeito
de negócio foi executado autonomamente, sem nenhuma chamada manual
ao mecanismo governado durante a janela autônoma; a verificação
determinística persistente do efeito foi executada explicitamente
após o fechamento da janela.

## 2. Proveniência / Baseline

- **Baseline de código:** `479618cd8ed11d333346a87affecae41a2a0ef6a`
  (DW-7.3 Policy Authority Mechanism + DW-7.4 Connected Autonomous
  Trigger Mechanism, ambos `MECHANICALLY PROVEN`/`PUSHED`).
- **Schema do banco de desenvolvimento:** migration `a3f7c9d15e28`
  (`policy_authority_grants`, `policy_authority_consumptions`, extensão
  XOR de `business_effect_verifications`), aplicada via
  DW-7.5.0A Environment Readiness, com backup prévio confirmado.
- **Ambiente:** `auneron-backend`/`auneron-postgres` de desenvolvimento
  (o mesmo ambiente do Episódio 005), imagem reconstruída e hash
  confirmado byte a byte contra a baseline antes da execução.
- **Protocolo congelado em:** DW-7.5.0B (Pilot Protocol Design Freeze)
  e DW-7.5.0C (PRE-EXECUTION Mechanical Verification).

## 3. Boundary do que este documento prova

Ver Seção 10 (PROVEN / NOT PROVEN) — nenhuma afirmação além dela deve
ser inferida deste documento.

## 4. Cenário A — Efeito Autônomo

- **Episódio:** `(account_id=21, due_date=2026-09-15)`.
- **Preparação (fora da janela autônoma):** Account 21 criada via
  `POST /accounts/` autenticado, `status=aberto`. Grant #1 criado via
  `PolicyAuthorityGrantService.create_grant()`
  (`policy_key=policy.account_mark_overdue.v1`,
  `skill_key=account.mark_overdue`, `scope_type=deployment_wide`,
  `state=active`, `granted_by_reference=user:1`).
- **Janela autônoma:** `MAINTENANCE_ENABLED=true` confirmado às
  `2026-09-26T02:39:42Z`. Nenhuma chamada manual a
  `run_policy_account_mark_overdue_trigger()`,
  `PolicyAccountMarkOverdueExecutionService.execute()`, ou mutação
  manual de `Account`/`Grant` ocorreu durante a janela — apenas
  leitura de logs e `SELECT`.
- **Resultado persistente, capturado após o fechamento da janela
  (`2026-09-26T02:41:25Z`, `MAINTENANCE_ENABLED=false`):**
  - `Account #21`: `aberto → atrasado`.
  - `PolicyAuthorityConsumption #1`: `policy_authority_grant_id=1`
    (idêntico ao Grant preparado, sem inferência retrospectiva),
    `skill_invocation_id=11`, `episode_scope_key=
    account_mark_overdue:v1:21:2026-09-15`.
  - `SkillInvocation #11`: `status=succeeded`, `actor_type=system`,
    `actor_reference=system:policy_account_mark_overdue_execution`.
  - `AccountEvent #16`: `status_changed`, `aberto→atrasado`,
    `actor_type=system` (nunca um humano fabricado).
  - Um ciclo periódico adicional (`2026-09-26T02:40:36Z`) confirmou
    `candidates_detected=0` — o episódio já concluído deixou de ser
    candidato, evidência complementar de inércia pós-efeito.
- **Cadeia comprovada:**
  ```
  Committed business condition (Account 21 aberto, vencido)
          ↓
  automatic observation (passagem de startup do trigger)
          ↓
  pre-delegated Policy Authority (Grant #1, criado antes do episódio)
          ↓
  governed execution (PolicyAccountMarkOverdueExecutionService)
          ↓
  exactly-one business effect (AccountEvent #16)
          ↓
  persistent execution proof (Consumption #1 + SkillInvocation #11)
  ```

## 5. Verificação BEV (ação explícita pós-efeito, fora da janela)

- Executada às `2026-09-25T23:41:59-03:00`, depois do fechamento da
  Janela A, via `BusinessEffectVerificationService.verify(
  policy_authority_consumption_id=1)`.
- `BusinessEffectVerification #11`: `result=verified`,
  `account_status_observed=atrasado`.
- **Distinção preservada explicitamente:** o efeito de negócio foi
  produzido autonomamente; sua verificação determinística persistente
  foi uma ação manual e explícita, executada depois do fechamento da
  janela — nunca parte do ciclo autônomo em si.

## 6. Revogação do Grant (governança, fora da janela)

`PolicyAuthorityGrantService.revoke_grant(1, revoked_by_user_id=1)` —
`Grant #1`: `state=revoked`, `revoked_at` e `revoked_by_reference`
preenchidos.

## 7. Cenário B — Observation ≠ Authority

- **Episódio:** `(account_id=22, due_date=2026-09-16)`, Grant já
  revogado antes da criação da conta.
- **PRE-B:** Account 22 `aberto`, zero `Consumption`/`AccountEvent`,
  zero Grant ativo.
- **Janela autônoma B** (`2026-09-26T02:45:11Z`–`02:47:54Z`):
  ciclo periódico (`02:46:01Z`) registrou
  `candidates_detected=1, executions_attempted=1,
  executions_authority_unavailable=1, executions_succeeded=0`.
- **Resultado persistente:** `Account #22` permanece `aberto`;
  `PolicyAuthorityConsumption`/`AccountEvent` para a conta 22
  permanecem em zero.
- **Conclusão:** detectar uma condição operacional elegível não foi
  suficiente para produzir efeito quando a autoridade predelegada
  estava indisponível — `Observation ≠ Authority` demonstrado
  empiricamente, não apenas argumentado.

## 8. Cenário C — Restart / Reconciliação

- Backend recriado com `docker compose ... up -d --no-deps
  --force-recreate backend`, override `MAINTENANCE_ENABLED=true`
  explícito (`2026-09-26T02:46:36Z`) — processo genuinamente novo,
  não uma continuidade em memória.
- **Pós-restart:**
  - `Account #21`: permanece `atrasado`; `Consumption`=1 (inalterado)
    — nenhum segundo efeito para um episódio já concluído.
  - `Account #22`: permanece `aberto`; ciclo periódico pós-restart
    (`02:47:29Z`) repetiu `candidates_detected=1,
    executions_authority_unavailable=1` — a condição ainda elegível
    foi redescoberta a partir do estado committed e voltou a ser
    submetida ao boundary de autoridade, permanecendo negada.
- **Duas propriedades provadas simultaneamente:** uma condição terminal
  não é redescoberta como candidata; uma condição ainda elegível é
  redescoberta e resubmetida, sem produzir efeito sem autoridade.

## 9. Achado de observabilidade (registrado, não corrigido)

```
FINDING-DW75-OBS-001
A passagem de startup do trigger (run_policy_account_mark_overdue_
trigger_async(), chamada uma vez no boot) não produz nenhuma linha de
log própria -- apenas o wrapper do loop periódico
(policy_account_mark_overdue_trigger_maintenance_loop) chama
logger.info(...). Difere do padrão de overdue_detection, cujo log
vive na função síncrona reutilizada por startup e loop.

Severity: non-blocking observability gap.
Impact: não enfraquece a prova de efeito/autoridade persistente
  (a execução inicial de A foi confirmada por Consumption/Invocation/
  AccountEvent, não pelo log); reduz a observabilidade direta em
  tempo real da execução de startup.
Disposition: deferred -- nenhuma alteração de código é exigida para
  este Evidence Freeze. Corrigir código entre o piloto e o freeze
  enfraqueceria a baseline que acabamos de provar.
```

## 10. Claim formal

> L3 Policy Autonomous Worker is PROVEN for the controlled
> `account.mark_overdue` corridor, in the single-customer-per-deployment
> model, using a pre-delegated deployment-wide Policy Authority Grant
> and deterministic eligibility/evidence.

### PROVEN

```
account.mark_overdue only
single-customer-per-deployment
deterministic overdue condition (Account.status/vencimento)
pre-delegated policy authority (Grant created before the episode)
system execution actor (never a fabricated human)
exactly-one effect for the tested episode
authority denial without an active Grant (Observation ≠ Authority)
restart/reconciliation behavior (no duplication; continued denial)
persistent authority/effect evidence (Grant, Consumption, Invocation,
  AccountEvent, BEV — all cross-referenced by real FK, not inference)
```

### NOT PROVEN

```
account.mark_paid autonomy
other skills/domains
multiple simultaneous autonomous actions/episodes
shared-DB multi-tenancy
multi-replica/concurrent-backend-instance concurrency
external side effects (payment, notification, third-party systems)
automatic BEV recovery for the policy corridor (verify() was manual)
L4 adaptive worker behavior
learning-driven authority (no signal ever altered policy_version)
silent/automatic policy mutation
unrestricted or generalized autonomy
```

## 11. Distinções preservadas (vinculantes para qualquer claim futuro)

```
AI/NBA recommendation    ≠ authority ≠ authorizing evidence
Observation               ≠ authority
Execution actor            ≠ policy authority
Execution success           ≠ business-effect verification
Policy authority             ≠ unrestricted autonomy
```

## 12. Conclusão / limite do escopo

Este documento fecha o `Controlled Connected Autonomous Pilot` como
`PROVEN` para exatamente o corredor, episódio e modelo de implantação
testados. Não autoriza, por si só, extensão a outros skills, múltiplos
episódios simultâneos, multi-tenancy, aprendizado adaptativo ou
qualquer forma de autonomia não restrita — essas permanecem decisões
de checkpoints futuros e distintos.
