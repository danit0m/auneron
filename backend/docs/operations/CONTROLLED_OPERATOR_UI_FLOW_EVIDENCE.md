# Controlled Operator/UI Flow — Evidence

## Objetivo

Este documento preserva a evidência do primeiro ciclo completo do
corredor humano governado (`account.mark_overdue`) operado inteiramente
pela interface real, por dois principals distintos e sessões
independentes — não mais via chamadas diretas à API, como no
`CONTROLLED_PILOT_EVIDENCE.md` (Episódio Piloto 001). É deliberadamente
distinto de três outros registros:

- `PRODUCTION_READINESS_GAP_REGISTER.md` rastreia lacunas de readiness —
  não narra execuções.
- `PRODUCTION_RISK_REGISTER.md` registra decisões arquiteturais por
  commit — nenhuma decisão arquitetural nova foi tomada aqui.
- `CONTROLLED_PILOT_EVIDENCE.md` documenta o Episódio Piloto 001
  (execução via API direta). Este documento cobre o Episódio 002,
  operado via browser real.

Este documento não é, por si só, autorização para expandir o corredor
além do boundary aqui descrito.

---

## 1. Proveniência

- **Baseline:** `8a7ae0b6e0265ea07362c2d7a9d2dc19ce18f831`
  (`feat(ui): add governed mark-overdue operator flow`), parent
  `035183b3c35addf93e80b57641644901e70e88e6`.
- **Frontend:** efetivamente reconstruído e redeployado a partir deste
  baseline (`docker compose build frontend` + `up -d --no-deps
  frontend`). Verificado por conteúdo — não apenas por checkout — via
  `grep` no bundle servido (`/work-items`, `account_mark_overdue`
  presentes) e, de forma mais forte, via evidência ao vivo no DOM do
  browser (`document.querySelectorAll('script[src]')` apontando para o
  bundle novo `index-DyMAeyLX.js`) e via rede
  (`GET /api/work-items`, `GET /api/approvals/14` disparados pelo
  componente novo).
- **Backend / PostgreSQL:** não recriados. Mesmos `ContainerID`s e
  `StartedAt` antes/depois do redeploy do frontend — confirmado por
  `docker inspect`. O commit não altera nenhum arquivo `backend/`.
- **Schema:** `alembic_version = 298a1e501b39`, inalterado.

## 2. Boundary do Episode 002

| Campo | Valor |
|---|---|
| Account | `#17` (Juliana Tomaz LTDA) |
| Vencimento | `2026-08-04` |
| Valor | `R$ 300.000,00` |
| `WorkItem` | `#6` |
| `ApprovalRequest` | `#14` |
| `ApprovalDecision` | `#7` |
| `ApprovalConsumption` | `#6` |
| `SkillInvocation` | `#6` |
| `AccountEvent` | `#11` |
| `WorkEvents` | `#29–34` |

## 3. Fluxo pelo navegador

1. Login `user 5` (Requester/Executor) → `/auth/me` confirmou
   `user_id=5`, `role=manager`.
2. Recomendações → Conta 17 expandida → estado reconstruído
   `not_materialized` → clique em **Materializar** → `WorkItem #6` +
   `ApprovalRequest #14` criados.
3. Fechar/reabrir a linha: estado reconstruído via
   `GET /work-items → GET /approvals/14` — confirmado `pending_approval`,
   sequência de rede observada diretamente.
4. **Logout** pela UI — sessão revogada.
5. Login `user 6` (Approver), sessão nova → Aprovações → identificação
   inequívoca de `#14` entre múltiplas pendentes da mesma conta (por
   timestamp de criação, não por conta/skill) → **Aprovar** único clique
   → `#14: pending → approved`, `ApprovalDecision #7`.
6. **Logout** pela UI — sessão revogada.
7. **Nova sessão** `user 5` → Recomendações → Conta 17: reconstrução
   direta em `approved_ready` (nunca teve a decisão de `user 6` em
   memória) — confirmado por rede,
   `GET /work-items?...account_id=17` → `GET /approvals/14`, antes de
   qualquer clique.
8. Clique único em **Executar**. Resposta real
   (`POST .../execute`):
   ```json
   {"work_item_id":6,"approval_request_id":14,"approval_consumption_id":6,
    "invocation_id":6,"invocation_status":"succeeded","duplicate":false,
    "output":{"previous_status":"aberto","new_status":"atrasado","changed":true}}
   ```
   `aberto → atrasado`, `duplicate=false`.
9. **Logout** pela UI — sessão revogada.

## 4. Segregação e autoridade

| Papel | `user_id` | Evidência |
|---|---|---|
| Requester / Materializer | `5` | `WorkItem #6.created_by_user_id`, `ApprovalRequest #14.requester_user_id` |
| Approver | `6` | `ApprovalDecision #7.decided_by_user_id`, `permission_used=approval:decide` |
| Execution authority | `5` | `ApprovalConsumption #6.authority_user_id` |

`requester_user_id=5 ≠ decided_by_user_id=6` — SoD mecanicamente
satisfeita pelo backend (não pela UI).

## 5. Idempotência / auditoria

- `WorkItem #6.work_key = account_mark_overdue:v1:17:2026-08-04`
- `ApprovalConsumption #6.runtime_idempotency_key = approval:14`
- `AccountEvent #11.idempotency_key = account_event:effect:human_account_mark_overdue:approval:14`
- Recibo `WorkEvent #33.idempotency_key = effect:human_account_mark_overdue:approval:14`
- Busca de duplicatas nos 5 espaços de chave (`work_key`,
  `runtime_idempotency_key`, `idempotency_key` de `account_events`, de
  `work_events`, de `skill_invocations`) — **zero** ocorrências em
  todos.
- Exatamente uma `ApprovalConsumption`, uma `SkillInvocation` e um
  `AccountEvent` novos produzidos pelo clique em Executar.
- `request_fingerprint` de `ApprovalRequest #14` e `ApprovalConsumption
  #6` são idênticos (`566457e3...`); o de `SkillInvocation #6`
  (`b1f88659...`) é legitimamente diferente — fingerprint calculado
  sobre a identidade do consumer runtime (`system:human_account_mark_overdue_execution`),
  não do requester. `input_digest` (`b80e8553...`) é idêntico nos três,
  por representar o payload financeiro puro.

## 6. Isolamento

- `ApprovalRequest #13` (legado, `agent:OverdueDetectionAgent`,
  conta 17) permaneceu `pending`/`resolved_at=NULL` durante todo o
  Episódio 002, com **zero** `ApprovalConsumption` associada.
- Nenhuma outra `Account` mudou de status durante o episódio.
- Nenhum advisory legado foi reutilizado ou consumido pelo corredor
  humano.

## 7. Recovery Evidence

- **Nome do recovery DB:** `auneron_recovery_drill` — fixado pelo
  tooling existente (`RECOVERY_DATABASE` em
  `backend/scripts/_postgres_recovery_common.py`), não parametrizável
  sem alteração de código; nenhuma alteração de código foi autorizada
  para este ciclo, então o nome fixo foi reaproveitado.
- **Backup:** `backup_20260920T032106Z.dump`, 181783 bytes, SHA-256
  `21068e015338cb1e021fbb904657733593b506f04bc5834e6fe1068490ac0822`.
- **Restore:** isolado em `auneron_recovery_drill`,
  `alembic_version=298a1e501b39` confirmado.
- **Cadeia restaurada:** idêntica campo a campo à fotografia canônica
  pré-backup — `Account #17`, `WorkItem #6` (com `context_data`),
  `ApprovalRequest #14`, `ApprovalDecision #7`, `ApprovalConsumption
  #6`, `SkillInvocation #6`, `AccountEvent #11`, `WorkEvents #29–34`,
  incluindo todos os digests/fingerprints. `Approval #13` preservado
  isolado; zero duplicatas nos 5 espaços de chave, reconferido no
  recovery.
- **Banco real não interferido:** reconexão explícita a `auneron`
  antes e depois do ciclo de restore — fotografia byte-idêntica.
- **Cleanup:** `auneron_recovery_drill` destruído e confirmado ausente;
  banco real confirmado acessível.
- Dump preservado como evidência operacional, junto dos 3 backups
  anteriores do Piloto 001.

## 8. `FINDING-UI-001`

**Completed governed action is hidden after NBA eligibility changes.**
Classificação: **non-blocking** para o Controlled Operator Flow.

Imediatamente após a execução, o componente exibiu corretamente
"Executado." com o resultado (`aberto → atrasado`). Ao recolher e
reabrir a linha da Account, o NBA recalculou `selected_actions` a
partir do zero (`GET next-best-action` disparado de novo, confirmado
por rede) — e como `account.mark_overdue` deixou de ser elegível
(`reason=status_not_open`, já que a conta está `atrasado`), a ação saiu
de `selected_actions` e o painel correspondente não é mais montado.

Isso não é perda de estado: `WorkItem #6`, `ApprovalRequest #14` e toda
a cadeia continuam persistidos e íntegros no backend (confirmado
inclusive pelo Recovery Evidence acima). O que desaparece é a
representação da ação no contexto de "o que fazer agora" — pergunta que
`selected_actions` responde por desenho, e que corretamente exclui uma
ação já não-recomendável.

**Não corrigido neste ciclo.** Registrado como candidato para um ciclo
futuro de UX: separar a apresentação de "ação atual recomendada" de
"histórico/resultado da última ação governada" — duas perguntas
diferentes que hoje compartilham a mesma superfície de UI.

## 9. Resultado

O Controlled Operator/UI Flow foi demonstrado ponta a ponta no Episódio
002: recomendação → materialização pela UI → logout → aprovação
segregada pela UI, por um segundo principal → logout → nova sessão do
requester → reconstrução de estado exclusivamente a partir do backend
→ execução pela UI → efeito financeiro auditável → reconstrução
verificada por recovery. Nenhuma duplicação, nenhum vazamento de estado
entre sessões, isolamento do corredor legado preservado.

Este documento não declara prontidão para escala, operação externa ou
qualquer capacidade não exercitada por este teste — isso permanece
decisão de gates operacionais posteriores.
