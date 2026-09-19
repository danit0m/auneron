# Controlled Production Pilot — Evidence

## Objetivo

Este documento preserva a evidência de execução do primeiro piloto
controlado real do corredor humano governado de ação financeira
mutável. Ele é deliberadamente distinto de dois outros registros com
papéis diferentes:

- `PRODUCTION_READINESS_GAP_REGISTER.md` rastreia lacunas de
  readiness (estado/risco/mitigação/condição de fechamento) — não
  narra execuções.
- `PRODUCTION_RISK_REGISTER.md` registra decisões arquiteturais por
  commit — nenhuma decisão arquitetural nova foi tomada neste
  episódio.

Este documento também não é, por si só, uma autorização para expandir
o uso do corredor humano além do boundary aqui descrito. Essa é uma
decisão operacional separada.

---

## Episódio Piloto 001 — `account.mark_overdue`, Account 18

Identidade documental estável para este episódio. Não implica que
haverá necessariamente um "Episódio 002" — apenas identifica este de
forma inequívoca caso outros venham a existir no futuro.

### Release e ambiente

- **Baseline:** `e2bda4fc3ea2a07f8b961f2fc732950e50db76eb`
- **Schema:** `alembic_version = 298a1e501b39`
- **Topologia:** dev (`backend/docker-compose.yml`), `APP_ENV=development`
  — decisão explícita de não cortar para `docker-compose.prod.yml`
  neste ciclo.
- **Proveniência do container:** conteúdo dos 8 arquivos-chave do
  corredor humano (`human_account_mark_overdue_materialization_service.py`,
  `human_account_mark_overdue_execution_service.py`,
  `mark_overdue_recommendation.py`, `human_escalation_eligibility.py`,
  `accounts.py`, `account_vencimento_change.py`,
  `governed_financial_action_eligibility.py`, `security.py`)
  verificado byte-idêntico (SHA-256) entre o checkout local no
  baseline acima e o container `auneron-backend` em execução.

### Boundary congelado

Somente identidade operacional — nenhuma senha, hash, cookie de
sessão ou API key é preservada aqui.

| Principal | `user_id` | Role | Função no piloto |
|---|---|---|---|
| Requester / Executor | 5 | `manager` | materializa o episódio e executa a mutação aprovada |
| Approver | 6 | `manager` | decide a `ApprovalRequest`, segregado do requester (SoD) |
| Recovery Smoke | 7 | `viewer` | fora do corredor de execução financeira; usado apenas no Layer B autenticado |

- **Account:** `18` (Empresa Multi Agent)
- **FinancialEpisode:** `(account_id=18, due_date=2026-09-09)`
- **Skill:** `account.mark_overdue`, versão `1.0.0`

### Cadeia Materialize → Decision → Execute

| Passo | Identidade | Campos-chave |
|---|---|---|
| Materialize | `WorkItem #5` | `work_key=account_mark_overdue:v1:18:2026-09-09`, `origin_type=user`, `created_by_user_id=5` |
| Materialize | `ApprovalRequest #11` | `requester_user_id=5`, `idempotency_key=human_mark_overdue_approval:v1:18:2026-09-09`, `request_fingerprint=673765d5484ecf6e5393862754d6f23c6dad2d75c193e4a5035501cca30e0a2d`, `input_digest=d6b348fa8aed9e3ffce81eda07ac362f64838bda188911a1e31333bb00399522` |
| Decision | `ApprovalDecision #6` | `decision=approved`, `decided_by_user_id=6`, `decided_by_role=manager` — SoD mecanicamente satisfeita (`requester_user_id=5 ≠ decided_by_user_id=6`) |
| Execute | `ApprovalConsumption #5` | `consumer_actor_type=system`, `consumer_reference=system:human_account_mark_overdue_execution`, `authority_user_id=5` (autoria humana preservada nos campos de autoridade) |
| Execute | `SkillInvocation #5` | `status=succeeded`, `input_digest` idêntico ao da `ApprovalRequest #11` |
| Execute | `AccountEvent #10` | `previous_status=aberto`, `new_status=atrasado`, `actor_type=user`, `actor_user_id=5` |
| Execute | `WorkEvents #23–28` | jornada completa do `WorkItem #5`: `created → details_changed → ready → in_progress →` recibo `business_effect_receipt` (#27, `idempotency_key=effect:human_account_mark_overdue:approval:11`) `→ completed` |

**Resultado de negócio:** `Account 18: aberto → atrasado`. `vencimento` (`2026-09-09`) e `valor` (`R$ 6.000,00`) permaneceram inalterados durante todo o corredor.

### Isolamento do corredor legado

`ApprovalRequest #10` (agent-only, `requester_actor_type=agent`,
`requester_reference=agent:OverdueDetectionAgent`) e sua
`AuthenticatedAdvisoryProposal #19` (`idempotency_key=conta_vencida:18:2026-09-09:attempt:1`)
permaneceram `pending`/inalterados durante todo o episódio — nunca
decididos, nunca consumidos. Confirmado por leitura repetida em cada
checkpoint do piloto, e novamente após a restauração de recovery
descrita abaixo.

### Recovery

Duas provas operacionais distintas, em momentos diferentes — não
devem ser fundidas em uma única afirmação temporal:

- **Layer B autenticado — antes da mutação.** Executado sobre um
  backup capturado logo após o provisionamento dos 3 principals
  dedicados (`backup_20260918T183919Z.dump`), antes de qualquer
  seleção de episódio ou mutação. Prova que o mecanismo de
  recovery + a aplicação restaurada + autenticação do principal
  Smoke funcionam.
- **Layer A — depois da mutação.** Novo backup
  (`backup_20260919T010316Z.dump`, SHA-256
  `147c6cc8b4e17ea170873e7716f2bfe9c4b153519d90c102561f678c7b37417e`),
  restaurado isoladamente em `auneron_recovery_drill`
  (`alembic_version=298a1e501b39` confirmado), com reconciliação
  campo a campo de `Account 18`, `WorkItem #5`,
  `ApprovalRequest #11`, `ApprovalDecision #6`,
  `ApprovalConsumption #5`, `SkillInvocation #5`, `AccountEvent #10`
  e `WorkEvents #23–28` contra a fotografia canônica pré-backup —
  igualdade em todos os campos, incluindo digests/fingerprints.
  `auneron_recovery_drill` removido após a verificação; banco real
  `auneron` confirmado intocado pelo ciclo.

**Recovery empiricamente comprovado:** o ciclo backup → restore →
verificação reproduziu integralmente o efeito financeiro e sua trilha
auditável — inclusive o recibo `WorkEvent #27`, o `AccountEvent #10` e
o `ApprovalConsumption #5`.

### Reentrada / Idempotência

> Reentry/idempotency structurally verified against the persisted
> post-execution state; live replay was deliberately not performed.

**Reentry estruturalmente comprovada:** análise do código e do estado
persistido demonstra que uma segunda `materialize()` para o mesmo
episódio falharia fechada em 409 (`get_mark_overdue_eligibility`
recalcula `reason=status_not_open` contra `Account.status=atrasado`,
antes de qualquer escrita), e que uma segunda `execute()` retornaria
`200`/`duplicate=true` com os mesmos IDs já existentes, via o guard de
recibo (`WorkEvent` com `idempotency_key=effect:human_account_mark_overdue:approval:11`)
— ambos os caminhos sem produzir nenhuma escrita nova. Nenhuma
segunda chamada HTTP foi realizada contra o corredor real; não houve
teste empírico de restart/replay.

---

## Final Acceptance Gate — Matriz de Fechamento

| Dimensão | Resultado | Evidência principal |
|---|---|---|
| Release + schema | PASS | release `e2bda4fc...`; banco em `298a1e501b39` |
| Identidade e integridade | PASS | `Approval #11 → Decision #6 → Consumption #5 → Invocation #5`, digests/fingerprints coerentes |
| Efeito controlado | PASS | `Account 18: aberto → atrasado`; `AccountEvent #10`; nenhuma alteração de `vencimento`/valor |
| Governança humana | PASS | requester/executor `user 5`, approver `user 6`, SoD preservada |
| Auditabilidade | PASS | `WorkItem #5`, `WorkEvents #23–28`, incluindo o recibo `#27` |
| Isolamento | PASS | `Approval` legado `#10` / `Proposal #19` permaneceram independentes |
| Ausência de efeitos adicionais | PASS | deltas reconciliados após execução |
| Recovery pós-mutação | PASS | novo dump restaurado e cadeia completa reproduzida campo a campo |
| Reentrada/idempotência | STRUCTURALLY VERIFIED | estado persistido e guards determinísticos verificados; live replay deliberadamente não executado |

**`CONTROLLED PRODUCTION PILOT — TECHNICAL FINAL ACCEPTANCE: PASS`**

---

## Conclusão

O piloto demonstrou, ponta a ponta e com evidência mecânica em cada
etapa: observar → materializar trabalho → solicitar aprovação →
decisão humana separada → revalidar autoridade/estado → executar
mutação real → consumir aprovação uma única vez → registrar autoria e
efeito → concluir `WorkItem` → preservar recibo auditável → backup →
restore → reconstruir integralmente o resultado.

Isso satisfaz o objetivo técnico estabelecido para o MVP reduzido:
provar um único tipo de ação mutável real e governada, dentro de um
boundary estreito e congelado, sem ampliar prematuramente o domínio.

O resultado técnico e a decisão operacional permanecem
deliberadamente separados. Este documento fecha o primeiro como
`PASS`. A expansão do uso do corredor humano para novos clientes,
contas, actions ou volume é uma decisão de um gate posterior — não
está autorizada nem implicada por este piloto.
