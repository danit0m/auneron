# Controlled Mark-Paid Pilot — Evidence (Episode 001)

## Objetivo

Este documento preserva a evidência do primeiro episódio operacional
do corredor governado `account.mark_paid` — distinto de
`account.mark_overdue` tanto na origem (ADR 009, baseado diretamente
em `ApprovalRequest`/`ApprovalConsumption`/`SkillInvocation`, sem
`WorkItem`/`WorkEvent`) quanto no momento em que foi comprovado
funcionalmente (Second Mutating Skill — Safety Delta V1, baseline
`aede5af74c2878ef0dca0a6b1f8f856fef24fcfc`).

O propósito deste episódio não é provar que `mark_paid` funciona em
teste — isso já foi demonstrado pelo Safety Delta V1 com cobertura
funcional real. O propósito é demonstrar, pela primeira vez em
ambiente operacional real (não `auneron_test`), que uma afirmação
humana pode percorrer o corredor `account.mark_paid` com três
identidades independentes (requester, approver, executor), produzir
exatamente um efeito financeiro, deixar uma cadeia persistida
reconstruível, e sobreviver a um ciclo de backup/restore — sob o guard
`approver != executor` introduzido pelo Safety Delta.

Este documento não afirma que o Auneron observou ou comprovou
recebimento de pagamento. Essa distinção é tratada explicitamente na
seção 8.

---

## 1. Proveniência

- **Baseline:** `aede5af74c2878ef0dca0a6b1f8f856fef24fcfc`
  (`test(governance): harden mark paid execution safety`), parent
  `4ff848897fc91957e936a5e2d93292fe9c3a8a08`.
- **Safety Delta V1 — evidência que precede este episódio:**
  - Código de produção: guard estrutural `approver != executor` em
    `AccountMarkPaidExecutionService.execute()`, +5/-0 linhas.
  - Testes funcionais novos: `tests/test_account_mark_paid_execution_functional.py`
    — **10/10 PASS**, cobrindo caminho canônico (com verificação de
    linha-a-linha da cadeia persistida), `approver == executor`
    rejeitado, autoridade concorrente stale rejeitada sem segundo
    efeito, cross-account/cross-skill/payload-mismatch/estados
    inválidos rejeitados fail-closed, RBAC no boundary real da rota.
  - Conjunto relacionado: **17/17 PASS**.
  - Suíte completa do backend: **1409 passed, 0 failed**, banco
    `auneron_test`.
- **Backend Redeploy V1:** o container `auneron-backend` em execução
  datava de 4 de setembro (anterior ao Safety Delta). Rebuild da
  imagem a partir do baseline `aede5af` + `up -d --no-deps backend`
  (Postgres e frontend intocados). Verificado por conteúdo, não só por
  `healthy`: SHA-256 do arquivo
  `app/services/account_mark_paid_execution_service.py` idêntico entre
  host e container (`23eebe104373a2dd36a5e5be7c809830b3526381f00c2bfa5eaa87bc68c67973`),
  com o guard presente. Health check autenticado não-mutável
  (`GET /recommendations/mark-paid/accounts/2/episodes/2026-08-29`,
  identidade de contingência `user 4`, sessão revogada em seguida) —
  não a criação da `ApprovalRequest` — foi o primeiro teste de que o
  novo backend atendia requisições reais.
- **Schema:** `alembic_version = 298a1e501b39`, inalterado durante todo
  o episódio.

## 2. Discovery e escolha do episódio

Discovery read-only prévio ao primeiro ato mutável. Achados:

- Catálogo: `skill_id=2`, `skill_key=account.mark_paid`,
  `version_id=2`, `version=1.0.0`, `status=published`,
  `execution_mode=mutating`.
- Sete `Account`s existentes, todas `atrasado`, nenhuma `pago`.
- Corredor `mark_paid` (skill_version_id=2) **nunca havia sido
  exercitado operacionalmente**: `ApprovalRequests=0`,
  `ApprovalConsumptions=0`, `SkillInvocations=0` — episódio
  particularmente limpo, sem histórico coexistente para isolar (ao
  contrário de `mark_overdue`/Account 1 no Episódio 003).
- Cinco usuários humanos ativos qualificavam para os três papéis
  (`skill:execute` + `approval:decide`): `1, 3, 4, 5, 6`. `user 7`
  (viewer) e `user 2` (system) não qualificavam para nenhum papel.

**Account 2** foi escolhida deliberadamente — R$150,00, `atrasado`,
vencimento 2026-08-29 — por não ter participado de nenhum episódio
anterior de `mark_overdue` (diferente de Accounts 1, 17, 18), mantendo
este episódio isolado de histórico operacional prévio. Entre as
Accounts 2–5 (sem diferencial no discovery), a menor ID foi usada.

**Atores congelados:**

| Papel | Usuário | Justificativa |
|---|---|---|
| Requester | `user 5` — Piloto Requester Executor | reutilização da identidade operacional já estabelecida nos pilotos de `mark_overdue` |
| Approver | `user 6` — Piloto Approver | idem |
| Executor | `user 3` — Usuario Teste | terceira identidade distinta, evitando `user 1` (identidade administrativa principal) |

`5 != 6`, `6 != 3`, `5 != 3` — primeira vez que os três papéis do
corredor `mark_paid` são exercidos por três identidades
independentes.

## 3. Credential Preparation

Credenciais de `user 4` (health check), `user 5`, `user 6` e `user 3`
redefinidas via `backend/scripts/reset_test_password.py` — dependência
excepcional/manual já registrada nos episódios anteriores (não há
self-service nem revogação administrativa). Senhas geradas em memória
via processo Python isolado dentro do próprio container, nunca
impressas ou persistidas em arquivo; nenhuma aparece neste documento.
Cada operação foi seguida de `POST /auth/logout` (sessão revogada
antes do próximo ato). Confirmado por `auth_sessions.revoked_at`: zero
sessões válidas residuais dos quatro usuários ao final do episódio.

**Nota de proveniência:** a redefinição de credencial de `user 4` foi
exclusivamente preparação de acesso para o health check pós-redeploy
— não faz parte do episódio `mark_paid` em si.

## 4. Ato 1 — Human Request

- `user 5` autenticado (`GET /auth/me` confirmou `user_id=5,
  role=manager`).
- `POST /approvals/skill-executions/2` com
  `input_payload={"account_id": 2, "expected_status": "atrasado"}` e
  `Idempotency-Key: mark_paid_pilot_episode001:account:2:2026-08-29`
  (específico deste episódio, nunca reutilizado) → **201**,
  `created=true, duplicate=false`.
- `ApprovalRequest #17`: `requester_user_id=5`, `risk_level=high`,
  `required_permission=approval:decide`, `status=pending`,
  `target_account_id=2`,
  `input_digest=2fef7664d3bd9e7b920b430669d72806b3202346584ee439ba346345602e2e89`,
  `request_fingerprint=02a75c8f72ef63028a0afb6684728ee5d8fd73556d7da1c5a40865f73555fa58`,
  `expires_at=2026-09-22T00:36:16.347543-03:00`.
- `Account 2` permaneceu `atrasado`. `ApprovalDecision(#17)=0`,
  `ApprovalConsumption=0`, `SkillInvocation=0`, `AccountEvent(Account
  2)=0` — confirmados por inspeção read-only imediatamente após o ato.

## 5. Ato 2 — Independent Approval

- `user 6` autenticado (`GET /auth/me` confirmou `user_id=6,
  role=manager`).
- Captura read-only imediatamente antes da decisão confirmou `#17`
  ainda `pending`, `expires_at` no futuro, nenhum estado divergente.
- `POST /approvals/17/decision` com `{"decision": "approved"}` →
  **200**.
- `ApprovalDecision #9`: `approval_request_id=17`, `decision=approved`,
  `decided_by_user_id=6`, `decided_by_role=manager`,
  `sensitive_elevation_verified=false` (coerente —
  `required_permission=approval:decide`, não `decide_sensitive`).
  `ApprovalRequest #17` transicionou para `status=approved`,
  `resolved_at` preenchido.
- SoD: `requester_user_id=5 != decided_by_user_id=6`.
- `Account 2` permaneceu `atrasado`. `ApprovalConsumption=0`,
  `SkillInvocation=0`, `AccountEvent(Account 2)=0` — a aprovação não
  produziu nenhum efeito de negócio, demonstrando operacionalmente
  *authorization is not execution*.

## 6. Ato 3 — Independent Execution

- Imediatamente antes da execução, releitura confirmou: `#17
  =approved`, `#9=approved/user6`, `Account 2=atrasado`,
  `ApprovalConsumption(#17)=0`, `SkillInvocation(mark_paid)=0`,
  `AccountEvent(Account 2)=0`, `expires_at` ainda válido, backend
  `healthy` com hash do Safety Delta confirmado no runtime.
- `user 3` autenticado (`GET /auth/me` confirmou `user_id=3,
  role=administrator`).
- **Uma única chamada**: `POST /accounts/2/execute-mark-paid` com
  `{"approval_request_id": 17, "expected_status": "atrasado"}` →
  **200**, `duplicate=false`,
  `output={previous_status:atrasado, new_status:pago, changed:true}`.
  Nenhum retry, nenhuma segunda chamada.
- `Account 2: atrasado → pago`.
- Cadeia produzida, reconstruída por join único a partir das tabelas
  persistidas (não a partir da resposta HTTP):

  ```
  ApprovalRequest #17 (requester=5)
        │
  ApprovalDecision #9 (approver=6)
        │
  ApprovalConsumption #8 (authority/executor=3)
        │
  SkillInvocation #8 (succeeded)
        │
  Account 2 → pago
        │
  AccountEvent #13 (atrasado → pago, actor=3)
  ```

- `ApprovalConsumption #8`: `approval_request_id=17`,
  `approval_decision_id=9`, `skill_invocation_id=8`,
  `consumer_actor_type=system`,
  `consumer_reference=system:account_mark_paid_execution`,
  `authority_user_id=3`, `authority_reference=user:3`,
  `authority_role=administrator`, `runtime_idempotency_key=approval:17`,
  `status=consumed`, `error_code=NULL`.
- `SkillInvocation #8`: `skill_version_id=2`, `status=succeeded`,
  `output_payload={"action":"account.mark_paid","account_id":2,"previous_status":"atrasado","new_status":"pago","changed":true}`.
- `AccountEvent #13`: `account_id=2`, `event_type=status_changed`,
  `previous_status=atrasado`, `new_status=pago`, `actor_type=user`,
  `actor_user_id=3`, `idempotency_key=account_event:approval:17`.

## 7. SoD — prova central

```
ApprovalRequest.requester_user_id     = 5
ApprovalDecision.decided_by_user_id   = 6
ApprovalConsumption.authority_user_id = 3

5 != 6 != 3
```

Reconstruída diretamente do banco por join único (seção 6), não
inferida da resposta HTTP. Este episódio demonstra que, nesta
execução, requester/approver/executor foram três identidades
independentes operando de ponta a ponta. A prova de que o sistema
**rejeita** `approver == executor` pertence ao teste funcional do
Safety Delta V1 (seção 1) — as duas evidências juntas sustentam o
invariante muito melhor do que qualquer uma isolada.

## 8. Semântica negativa — o que este episódio não demonstra

**Este episódio não demonstra que o Auneron observou ou comprovou o
pagamento.** A transição de `Account 2` para `pago` decorreu de uma
afirmação humana submetida ao corredor governado — não de conciliação
bancária, webhook de pagamento ou qualquer fonte externa observada. O
próprio endpoint de elegibilidade consultado durante o Backend
Redeploy V1 confirmou isso mecanicamente:
`requires_external_fact=payment_observed`, `system_recommendable=false`
— `payment_observed` permanece ausente como fato verificável no
sistema. Isso é parte da evidência deste episódio, não uma ressalva
lateral.

## 9. Identity / Idempotency

| Campo | Valor completo |
|---|---|
| `input_digest` (Request/Consumption) | `2fef7664d3bd9e7b920b430669d72806b3202346584ee439ba346345602e2e89` |
| `request_fingerprint` (Request/Consumption) | `02a75c8f72ef63028a0afb6684728ee5d8fd73556d7da1c5a40865f73555fa58` |
| `request_fingerprint` (Invocation) | `dfcf0b88ed80310d1624ac20e0aaff2b8fee2bf674ac64f47a12b1d583897840` |
| `runtime_idempotency_key` | `approval:17` |
| `AccountEvent.idempotency_key` | `account_event:approval:17` |

O `request_fingerprint` da `SkillInvocation #8` difere legitimamente
dos demais — calculado sobre a identidade do consumer runtime
(`system:account_mark_paid_execution`), não do requester, mesmo padrão
já documentado nos episódios de `mark_overdue`. Nenhum replay foi
realizado; a prova de comportamento sob replay pertence ao teste
funcional do Safety Delta, não a este episódio.

## 10. Isolation

- Das 7 `Account`s existentes, somente `Account 2` sofreu transição de
  status neste episódio — confirmado por consulta a todas as 7 antes e
  depois de cada ato.
- `mark_paid ApprovalConsumption = 1`, `mark_paid SkillInvocation = 1`
  em todo o corredor — nenhuma segunda autoridade foi criada ou
  consumida.
- Nenhuma sessão válida residual dos quatro usuários envolvidos ao
  final do episódio (`auth_sessions.revoked_at` preenchido em todas).

## 11. Findings — permanecem abertos, não resolvidos por este episódio

```
FINDING-MARK-PAID-001
Stale approved authority referencing an Account that no longer
exists can escape the ApprovalError contract and surface as a
generic 500 (SkillScopeNotFoundError is not an ApprovalError and
is not mapped by the execute-mark-paid route). No partial business
mutation was identified. Not exercised or fixed in this episode.

DEFERRED-CONCURRENT-AUTHORITY
Different requesters can create/approve concurrent authorities for
the same intended mark_paid effect; authority creation itself does
not converge. The Safety Delta V1 functional test proved that the
first successful execution makes any second approved authority
stale and prevents a second business effect — this episode did not
attempt to reproduce concurrent authorities operationally, by
design (happy governed path only).
```

## 12. Recovery Evidence

- **Backup:** `backup_20260921T034524Z.dump`, `184079 bytes`, SHA-256
  `40b4ad7072f096cf0ba29c3ce808af39ca7a1c91a29904d8645deadb8f236204`,
  origem = banco real `auneron` (host=localhost, port=5433),
  `alembic_version=298a1e501b39`. Hash confirmado de forma
  independente no host, fora do script de backup.
- **Nome do recovery DB:** `auneron_recovery_drill` — imposto pelo
  tooling existente (`RECOVERY_DATABASE` em
  `backend/scripts/_postgres_recovery_common.py`), reaproveitado sem
  alteração de código, mesmo padrão dos episódios anteriores.
- **Restore:** isolado em `auneron_recovery_drill`,
  `current_database()` e `alembic_version=298a1e501b39` confirmados
  **antes** de qualquer consulta de evidência.
- **Cadeia restaurada:** idêntica campo a campo à fotografia real —
  `Account 2=pago`, `ApprovalRequest #17`, `ApprovalDecision #9`,
  `ApprovalConsumption #8`, `SkillInvocation #8` (incluindo
  `output_payload` completo), `AccountEvent #13`, incluindo todos os
  digests/fingerprints/timestamps da seção 9. `mark_paid
  ApprovalConsumption=1`, `mark_paid SkillInvocation=1`, as demais 6
  `Account`s preservadas.
- **Source sem interferência:** reconexão explícita a `auneron` após
  o ciclo de restore — fotografia byte-idêntica ao estado pré-restore
  (7 contas, contagens, Alembic).
- **Cleanup:** `auneron_recovery_drill` destruído e confirmado
  ausente; banco real `auneron` confirmado acessível.
- Nenhuma execução de `mark_paid` foi realizada no banco restaurado.
  Nenhuma segunda mutação foi produzida para "testar" recovery ou
  idempotência.
- Dump preservado localmente fora do repositório, junto aos backups
  anteriores — nenhum sobrescrito.

## 13. Conclusão / limite do escopo

O corredor `account.mark_paid` demonstrou, pela primeira vez em
ambiente operacional real e sob o guard do Safety Delta V1, a cadeia
completa `human assertion → independent request → independent
approval → independent execution → single business effect →
persistent evidence → recovery`, com três identidades distintas
(`requester=5, approver=6, executor=3`).

Este documento **não** afirma: observação ou comprovação de
pagamento; resolução de `FINDING-MARK-PAID-001`; convergência de
autoridades concorrentes; generalização para outras contas, valores ou
volume; scale hardening; prontidão para operação externa; ou início de
qualquer trabalho de extração do Governed Action Model. Essas
permanecem decisões de gates operacionais posteriores, não implicadas
por este resultado.

Com este episódio, o Auneron possui dois corredores mutáveis distintos
com evidência operacional completa — `account.mark_overdue`
(`WorkItem`-oriented) e `account.mark_paid` (`ApprovalRequest`-oriented,
ADR 009) — material concreto para uma futura comparação mecânica, não
uma abstração desenhada a partir de uma única implementação.
