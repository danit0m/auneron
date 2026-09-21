# Controlled Repeatability Pilot — Evidence

## Objetivo

Este documento preserva a evidência do **Episode 003**, cujo propósito
não era simplesmente executar `account.mark_overdue` uma terceira vez
— era responder a uma pergunta arquitetural distinta da dos episódios
anteriores: o corredor já implantado consegue completar um novo
episódio, **no mesmo baseline e sem desenvolvimento/redeploy
adicional**, coexistindo com background maintenance e approvals
agent-only preexistentes, sem colisão de identidade?

É deliberadamente distinto de dois outros registros de evidência:

- `CONTROLLED_PILOT_EVIDENCE.md` (Episódio 001, Account 18) — primeira
  execução real, via API direta.
- `CONTROLLED_OPERATOR_UI_FLOW_EVIDENCE.md` (Episódio 002, Account 17)
  — primeira execução real via UI, provando os fundamentos
  arquiteturais do corredor operado por browser.

Este documento não repete essas provas fundamentais — assume-as
fechadas — e foca exclusivamente no que o Episode 003 acrescenta:
repetibilidade.

---

## 1. Proveniência

- **Baseline:** `18ac6536851db37aa5f439421c65f3b247daa838`
  ("docs(operations): preserve controlled operator UI flow evidence").
- **Nenhuma alteração/redeploy** ocorreu entre o Episode 002 e o
  Episode 003 — mesmo commit, mesmo container de frontend (rebuild
  único do Frontend Redeploy V1, não repetido), mesmo backend/Postgres.
- **Schema:** `alembic_version = 298a1e501b39`, inalterado.
- Stack (`backend`/`frontend`/`postgres`) `healthy` durante todo o
  episódio.

## 2. Discovery e escolha do episódio

Um Discovery/Design V1 read-only precedeu o freeze. Achado central:
**Account #1 era a única conta estruturalmente elegível** em todo o
sistema no momento (`status=aberto`, `vencimento=2026-08-28`,
`valor=150.00`) — computado via `get_mark_overdue_eligibility()` real,
não inferido do campo `status`. Zero `WorkItem` humano preexistente
para essa conta. A escolha decorreu exclusivamente de elegibilidade e
repetibilidade — não de valor financeiro (a conta é a de menor valor
entre todas as contas do sistema).

## 3. Estado agent-only coexistente

A Account #1 já possuía histórico do corredor agent-only, usado
deliberadamente como sentinela de isolamento:

| `ApprovalRequest` | Status | Consumida? |
|---|---|---|
| `#1` | `approved` (2026-09-02) | Nunca |
| `#8` | `expired` | Não |
| `#12` | `expired` | Não |
| `#15` | `pending` durante os Atos 1–2 | Não |

Namespaces humano (`account_mark_overdue:v1:1:...`,
`human_mark_overdue_approval:v1:1:...`) e agent-only
(`advisory:N:1`) nunca colidiram. A ambiguidade real observada era de
**apresentação na UI** (duas linhas semelhantes na tela de Aprovações),
nunca de identidade/dados.

## 4. Credential Preparation

`user 5`/`user 6` — ativos, `role=manager`. Reset de credenciais via
`backend/scripts/reset_test_password.py` (mecanismo já auditado nos
episódios anteriores), confirmado novamente como **dependência
excepcional/manual** — não há self-service nem revogação
administrativa. Senhas geradas em memória, nunca persistidas em
arquivo; nenhuma aparece neste documento. Logins/logouts normais pela
UI; sessões revogadas em cada etapa.

## 5. Ato 1 — Materialization

- Login `user 5` → `/auth/me` confirmou `user_id=5`, `role=manager`.
- Clique único em **Materializar**.
- `WorkItem #7`: `work_key=account_mark_overdue:v1:1:2026-08-28`,
  `created_by_user_id=5`, `status=ready`.
- `ApprovalRequest #16`: `requester_user_id=5`,
  `idempotency_key=human_mark_overdue_approval:v1:1:2026-08-28`,
  `input_digest=2f456d3ac32a047783047bcbc6d32cf2325f88fa89439b4b33f9a200181da6e6`,
  `request_fingerprint=c6579cd1b5b95482cce58a962bdc477324f2023d6de8da3875dd708fb4e56b41`.
- `Account #1` permaneceu `aberto` (materialização não produz efeito
  financeiro).
- Reconstrução genuína confirmada por rede após recolher/reabrir:
  `GET /api/work-items?scope_type=account&account_id=1` →
  `GET /api/approvals/16`.

**Nota de processo:** as duas primeiras tentativas de recolher/reabrir
a linha não miraram no elemento correto (a posição na tela havia
deslocado) — a ausência de tráfego de rede correspondente foi
detectada antes de aceitar o resultado como evidência, e o teste foi
refeito corretamente, com o toggle real confirmado visualmente e por
rede. Nenhuma ação mutável foi afetada por esse ajuste.

## 6. Ato 2 — Approval

- Login `user 6` → `/auth/me` confirmou `user_id=6`, `role=manager`.
- Duas linhas "Marcar conta como atrasada / Conta 1" pendentes
  coexistiam na tela (`#15` agent-only e `#16` humana). `#16` foi
  identificada inequivocamente pelo `created_at` congelado no Ato 1
  (`20/09/2026, 22:10:27`), não pelo texto da ação/conta.
- Clique único em **Aprovar** na linha correta.
- `ApprovalDecision #8`: `approval_request_id=16`, `decision=approved`,
  `decided_by_user_id=6`, `permission_used=approval:decide`.
- SoD: `requester_user_id=5 ≠ decided_by_user_id=6`.
- `#15` permaneceu `pending`, inalterada pela decisão humana.

## 7. Ato 3 — Execution

- **Nova sessão** de `user 5` (posterior à decisão de `user 6`,
  nunca teve a decisão em memória).
- Reconstrução server-side confirmada por rede **antes** do clique:
  `GET /api/work-items?scope_type=account&account_id=1` →
  `GET /api/approvals/16`, resultando em `approved_ready`.
- Clique único em **Executar**. Resposta real:
  `duplicate=false`, `output={previous_status:aberto,
  new_status:atrasado, changed:true}`.
- `Account #1: aberto → atrasado`.
- Cadeia produzida:
  ```
  ApprovalConsumption #7
    → ApprovalRequest #16
    → ApprovalDecision #8
    → SkillInvocation #7
  ```
- `AccountEvent #12`: `aberto → atrasado`, `actor_type=user`,
  `actor_user_id=5`.
- `WorkEvents #35–40`, recibo `#39`
  (`idempotency_key=effect:human_account_mark_overdue:approval:16`).
- Nenhum replay live foi realizado.

**Nota de processo:** ao reabrir a linha para o Ato 3, uma expansão
inicial acidentalmente mirou na Account #17 (Juliana Tomaz LTDA) — o
erro foi percebido de imediato pelo conteúdo da tela (`status_not_open`
para a conta errada) e corrigido **antes de qualquer clique em botão
de ação**; apenas o próprio chevron de recolhimento daquela linha foi
tocado. Nenhuma ação foi disparada sobre a Account #17 neste ato.

## 8. Identity / Idempotency

| Campo | Valor completo |
|---|---|
| `input_digest` (Approval/Consumption/Invocation) | `2f456d3ac32a047783047bcbc6d32cf2325f88fa89439b4b33f9a200181da6e6` |
| `request_fingerprint` (Approval/Consumption) | `c6579cd1b5b95482cce58a962bdc477324f2023d6de8da3875dd708fb4e56b41` |
| `request_fingerprint` (Invocation) | `ebb632506c81d304242005ff362695bb67a54a224adb7e6cc5e12192e7ae29a1` |

O `request_fingerprint` da `SkillInvocation #7` difere dos demais
legitimamente — calculado sobre a identidade do consumer runtime
(`system:human_account_mark_overdue_execution`), não do requester.
Busca de duplicatas nos 5 espaços de chave (`work_key`,
`runtime_idempotency_key`, `AccountEvent.idempotency_key`,
`WorkEvent` receipt, `SkillInvocation.idempotency_key`) — **zero**
ocorrências em todos.

## 9. Isolation

- `ApprovalRequest`s agent-only `#1/#8/#12/#15`: **zero**
  `ApprovalConsumption` associada, em nenhum momento do episódio.
- `Episode 001` (`Account 18=atrasado`, `WorkItem #5=completed`) e
  `Episode 002` (`Account 17=atrasado`, `WorkItem #6=completed`,
  `Approval #13=pending/unresolved`) — intocados durante todo o
  Episode 003.
- Das 7 contas existentes no sistema, somente a `Account #1` sofreu
  transição financeira neste episódio.
- Nenhum advisory legado foi reaproveitado pelo corredor humano.

## 10. `FINDING-UI-001`

Reproduzido novamente no Episode 003, pelo mesmo mecanismo do Episódio
002: após a execução, o NBA recalculou `selected_actions` e excluiu
`account.mark_overdue` (`reason=status_not_open`), e o painel
correspondente não remontou ao reabrir a linha. **Non-blocking.**
Estado terminal do backend permanece íntegro e comprovado
(`WorkItem #7=completed`, cadeia completa persistida). Nenhuma
remediação foi feita neste ciclo.

## 11. Recovery Evidence

- **Nome do recovery DB:** `auneron_recovery_drill` — imposto pelo
  tooling existente (`RECOVERY_DATABASE` em
  `backend/scripts/_postgres_recovery_common.py`), reaproveitado sem
  alteração de código.
- **Backup:** `backup_20260921T020221Z.dump`, `182780 bytes`, SHA-256
  `34023e459741f4d90426c3323d456cefeb4b0d46ebea2406f9924e77ba90c0f5`.
- **Restore:** isolado em `auneron_recovery_drill`,
  `alembic_version=298a1e501b39` confirmado **antes** de qualquer
  consulta de evidência.
- **Cadeia restaurada:** idêntica campo a campo à fotografia SOURCE —
  `Account #1`, `WorkItem #7`, `ApprovalRequest #16`,
  `ApprovalDecision #8`, `ApprovalConsumption #7`,
  `SkillInvocation #7`, `AccountEvent #12`, `WorkEvents #35–40`,
  incluindo os três digests/fingerprints completos da seção 8. Zero
  duplicatas, reconferido no recovery.
- **Source sem interferência:** reconexão explícita a `auneron` após
  o ciclo de restore — fotografia byte-idêntica ao SOURCE inicial.
- **Cleanup:** `auneron_recovery_drill` destruído e confirmado
  ausente; banco real confirmado acessível.
- Dump preservado junto aos 4 backups anteriores (Episódios 001 e
  002), nenhum sobrescrito.

## 12. Conclusão / limite do escopo

O corredor `account.mark_overdue` demonstrou repetibilidade em um
terceiro episódio independente, pelo fluxo normal de UI e **sem novo
desenvolvimento/redeploy**. Foram preservados: segregação de deveres,
idempotência, auditoria completa, isolamento de advisories agent-only
coexistentes e recoverability via backup/restore isolado.

Este documento **não** afirma que isso prova scale hardening,
prontidão para operação externa, generalização para outras Skills
mutáveis, ou conclusão de um Second Mutating Skill. Essas permanecem
decisões de gates operacionais posteriores, não implicadas por este
resultado.
