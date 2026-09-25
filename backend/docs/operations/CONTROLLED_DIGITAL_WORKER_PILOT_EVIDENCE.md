# Controlled Digital Worker Pilot — Evidence (Episode 005)

## Objetivo

Este documento preserva a evidência de execução do primeiro episódio
que demonstra, ponta a ponta e com evidência mecânica, a cadeia
completa do Connected Digital Worker para o corredor humano governado
`account.mark_overdue`:

```
Observe → Understand → Recommend → Recommendation Provenance →
Declared Human Materialization → Independent Approval →
Governed Execution → Effect Verification → Report
```

Ele continua a numeração de episódios já estabelecida para este
corredor (`CONTROLLED_PILOT_EVIDENCE.md` = 001,
`CONTROLLED_OPERATOR_UI_FLOW_EVIDENCE.md` = 002,
`CONTROLLED_REPEATABILITY_PILOT_EVIDENCE.md` = 003,
`CONTROLLED_COMPETING_AUTHORITY_EVIDENCE.md` = 004) — este é o
**Episode 005**.

**O que distingue este episódio dos quatro anteriores:** nenhum deles
cobre a cadeia de proveniência de recomendação (`NbaRecommendationSnapshot`,
associação declarada, reconstrução via Outcome) — todos antecedem os
checkpoints DW-6.4A/DW-6.4B/DW-6.5 que introduziram essa capacidade.
Este é o primeiro episódio em que a materialização humana é
declaradamente vinculada a uma recomendação NBA persistida e
verificável, e em que essa cadeia inteira — da observação até o
relatório — é reconstruída num único `GET /outcomes/...`, não apenas o
segmento `Materialize → Decision → Execute` já provado nos episódios
001–004.

Este documento não é, por si só, uma autorização para expandir
autonomia, produtizar a experiência pelo frontend, ou generalizar o
resultado para outros corredores. Essas são decisões de gates
posteriores, explicitamente não tomadas aqui.

---

## Proveniência operacional da evidência

Diferente dos episódios 001–004, este episódio não pôde ser executado
diretamente contra o ambiente dev como encontrado — quatro gates
preparatórios, cada um com sua própria verificação mecânica, foram
necessários antes de qualquer chamada de negócio. Eles não são fases do
episódio em si, mas fazem parte de sua proveniência operacional e são
preservados aqui:

1. **Dev DB migration.** O banco `localhost:5433/auneron` estava em
   `alembic_version=298a1e501b39`, duas migrations atrás do HEAD do
   repositório — faltavam as tabelas `business_effect_verifications`
   (DW-3) e `nba_recommendation_snapshots` (DW-6.4A), ambas
   pré-requisito mecânico deste episódio. Backup lógico capturado antes
   da migration (`pg_dump -Fc`,
   `auneron_dev_db_backup_pre_dw_connected_pilot_20260925T004327Z.dump`,
   SHA-256
   `02ee63bc4d8bf45700091ef19bb509298e21168c168f53a8c768a8e398a15cb7`,
   verificado via `pg_restore --list`, 338 entradas de TOC). Migration
   executada com o backend parado (`alembic upgrade head`,
   `298a1e501b39 → dfccb26f0711 → 16674f0a7aa3`), validada
   read-only em seguida (14/14 constraints, 2/2 índices, contagens
   pré-existentes inalteradas). Efeito colateral esperado e documentado
   pelo próprio design do DW-3: a recuperação de BEV no primeiro
   `application_started` pós-migration retro-preencheu 9 linhas de
   `business_effect_verifications` para `ApprovalConsumption`s
   históricos que nunca tiveram uma — não é um artefato deste episódio.
2. **Backend rebuild / current-contract verification.** O container
   `auneron-backend` em execução (imagem de `2026-09-21`) antecedia
   DW-6.4A/6.4B/6.5 por completo — confirmado ao consultar
   `/openapi.json` ao vivo: nenhum schema `RecommendationProvenanceOutcome`,
   `materialize` sem `requestBody`. Reconstruído via
   `docker compose build backend` a partir do baseline de código atual;
   identidade host↔container confirmada por SHA-256 em 5 arquivos
   representativos (`nba_recommendation_snapshot.py`,
   `nba_recommendation_snapshot_service.py`, `outcome_correlation.py`,
   `nba_policy.py`, `mark_overdue_recommendation.py`) — 5/5 idênticos.
   Os 8 elementos de contrato DW-6.4A/6.4B/6.5 reconfirmados ao vivo via
   `/openapi.json` pós-rebuild.
3. **Maintenance isolation.** O backend dev roda 11 loops de manutenção
   em background que poderiam contaminar uma Account nova com
   `Knowledge`/autoridade de agente dentro de minutos
   (`overdue_detection_maintenance_loop` ~60s,
   `receivables_monitor_maintenance_loop` ~300s). Um flag já existente,
   `MAINTENANCE_ENABLED=false` (`app/core/config.py`, gate único para
   os 11 loops + o passe de recovery de startup), foi aplicado via
   override de Compose fora do repositório (nenhum arquivo versionado
   alterado) — eficácia comprovada por uma janela de espera de >310s
   pós-criação da Account, sem nenhuma mutação de banco.
4. **Actor credential provisioning.** Os três atores do piloto —
   `piloto.requester.auneron@example.com` (id 5, `manager`),
   `piloto.approver.auneron@example.com` (id 6, `manager`),
   `teste.dev@example.com` (id 4, `administrator`) — já existiam com os
   papéis corretos, mas sem senha conhecida. Redefinidas via o script
   local `backend/scripts/reset_test_password.py` (não versionado, não
   é uma funcionalidade do produto), executado por Tomaz em seu próprio
   terminal — a senha nunca passou pelo agente. Toda chamada HTTP
   autenticada do episódio (login, NBA GET, materialize, approve,
   execute, Outcome GET) foi executada por Tomaz via sessão real de
   navegador (`fetch()` no Console do DevTools,
   `credentials:'include'` + header `X-API-Key`); o agente nunca deteve
   um cookie de sessão, apenas verificou o estado persistido no banco
   após cada chamada.

**Release e schema:**

- **Baseline (código):** `38c845f2fc6f2bd071e01e8a01030a71b05c02f5` —
  inalterado por todo o episódio (confirmado antes e depois: `HEAD =
  origin/main`, staging vazio, zero drift tracked).
- **Schema:** `alembic_version = 16674f0a7aa3`.
- **Topologia:** dev (`backend/docker-compose.yml`), `APP_ENV=development`.

---

## Boundary congelado

Somente identidade operacional — nenhuma senha, hash, cookie de sessão
ou API key é preservada aqui.

| Principal | `user_id` | Role | Função no episódio |
|---|---|---|---|
| Requester | 5 | `manager` | gera/observa a recomendação, materializa o episódio |
| Approver | 6 | `manager` | decide a `ApprovalRequest`, segregado do requester (SoD) |
| Executor | 4 | `administrator` | executa a mutação aprovada, segregado do approver |

- **Account:** `20` (`connected_worker_pilot_001`)
- **FinancialEpisode:** `(account_id=20, due_date=2026-09-14)`
- **Skill:** `account.mark_overdue`, versão `1.0.0`
- **Valor:** `R$ 100,00`; `days_overdue` no momento da recomendação: `11`
  (lifecycle `overdue_alert`)

---

## Recommendation Provenance

Três fatos preservados, sem extrapolação:

1. **`NbaRecommendationSnapshot #1`** foi criado por um `GET
   /recommendations/next-best-action/accounts/20/episodes/2026-09-14`
   real (ator: requester). `decision_type=single_action`,
   `selected_actions=["account.mark_overdue"]`,
   `applied_rules=["mark_overdue_precedence"]`,
   `requires_human_review=false`. Digest revalidado por recomputação
   (mesma canonicalização de `NbaRecommendationSnapshotService.get_verified()`)
   — válido.
2. **Associação declarada:** a materialização humana
   (`POST .../materialize`, corpo `{"recommendation_snapshot_id": 1}`)
   persistiu essa referência em
   `WorkItem #9 .context_data["recommendation_snapshot_id"] = 1` —
   verificado diretamente no banco, não apenas na resposta HTTP.
3. **Reconstrução posterior:** o `GET /outcomes/accounts/20/episodes/2026-09-14`
   (chamado depois de todo o resto do episódio já persistido)
   reconstruiu `recommendation_provenance.linkage = "correlated"`,
   `recommendation_snapshot_id = 1`, `selected_actions =
   ["account.mark_overdue"]` — a partir do estado já commitado, sem
   nenhuma escrita nova.

**Limite explícito do que `correlated` prova:** prova que uma
referência a uma recomendação NBA persistida foi explicitamente
declarada pela materialização humana e é reconstruível junto do resto
do episódio governado. **Não prova que a recomendação causou a decisão
humana**, nem que o humano "aceitou" a recomendação pelo simples fato
de materializá-la.

---

## Cadeia Materialize → Decision → Execute → Verify

| Passo | Identidade | Campos-chave |
|---|---|---|
| Recommend | `NbaRecommendationSnapshot #1` | `account_id=20`, `due_date=2026-09-14`, `policy_version=nba_policy_v1` |
| Materialize | `WorkItem #9` | `work_key=account_mark_overdue:v1:20:2026-09-14`, `origin_type=user`, `created_by_user_id=5`, `context_data.recommendation_snapshot_id=1` |
| Materialize | `ApprovalRequest #20` | `requester_user_id=5`, `risk_level=high`, `idempotency_key=human_mark_overdue_approval:v1:20:2026-09-14` |
| Decision | `ApprovalDecision #11` | `decision=approved`, `decided_by_user_id=6`, `decided_by_role=manager` — SoD mecanicamente satisfeita (`requester_user_id=5 ≠ decided_by_user_id=6`), e reforçada porque `risk_level=high` ativa a checagem N0 no backend |
| Execute | `ApprovalConsumption #10` | `authority_user_id=4` — segregado do approver (`approver=6 ≠ executor=4`) |
| Execute | `SkillInvocation #10` | `status=succeeded` |
| Execute | `AccountEvent #15` | `previous_status=aberto`, `new_status=atrasado` |
| Verify | `BusinessEffectVerification #10` | `result=verified`, `account_event_id=15`, `account_status_observed=atrasado` |

**Resultado de negócio:** `Account 20: aberto → atrasado`. `vencimento`
(`2026-09-14`) e `valor` (`R$ 100,00`) permaneceram inalterados durante
todo o corredor.

**Nota sobre a verificação de efeito (E8):** a `BusinessEffectVerification`
foi disparada por uma chamada síncrona e controlada
(`BusinessEffectVerificationService.verify(10)`), não pelo loop de
recovery automático — que permaneceu desligado (`MAINTENANCE_ENABLED=false`)
durante todo o episódio. Este episódio prova a verificação
determinística do efeito interno esperado de `account.mark_overdue`;
**não prova a recuperação automática desse efeito pelo worker de
manutenção** — a capacidade de recovery de BEV foi validada
separadamente do Episode 005; não foi exercitada como parte deste
episódio.

---

## Isolamento do corredor legado / advisory de criação

Nenhuma `AuthenticatedAdvisoryProposal` do tipo `conta_vencida:20:*`
(corredor agente) existiu em nenhum momento do episódio — confirmado
antes da criação da Account, imediatamente após, e novamente após uma
janela de espera de >310s com o backend rodando. Nenhum `WorkItem` ou
`ApprovalRequest` `agent`-originated apareceu para a Account 20.

Uma única `AuthenticatedAdvisoryProposal` existe para esta Account:
`cliente_criado:20` (id 25) — efeito colateral síncrono e incondicional
de `POST /accounts/` (embutido na própria rota, independente de
`MAINTENANCE_ENABLED`), sem nenhuma `ApprovalRequest` associada
(confirmado: zero skill vinculada ao evento `cliente_criado` neste
catálogo). Classificado explicitamente como **advisory de criação de
conta, fora do episódio `mark_overdue`, sem autoridade concorrente** —
não é tratado como "zero advisory", nem escondido do registro.

---

## Identity / Idempotência

Delta global exatamente `+1` para cada um de: `NbaRecommendationSnapshot`,
`WorkItem`, `ApprovalRequest`, `ApprovalDecision`, `ApprovalConsumption`,
`SkillInvocation`, `AccountEvent`, `BusinessEffectVerification`. Delta
`+0` para `Knowledge`, `MemoryItem` (maintenance desligado) e para
`AuthenticatedAdvisoryProposal` além do `cliente_criado:20` já descrito.

**Reentrada empiricamente testada — não apenas estruturalmente
verificada, diferente do Episode 001:**

- Replay de `POST .../materialize` com o mesmo `recommendation_snapshot_id=1`
  → `HTTP 200`, `created=false`, `duplicate=true`, mesmos
  `work_item.id=9`/`approval_request.request_id=20`. Delta de banco
  confirmado `+0`.
- Replay de `POST .../execute` → `HTTP 200`, `duplicate=true`, mesmos
  `approval_consumption_id=10`/`invocation_id=10`. Delta de banco
  confirmado `+0`.

---

## Isolamento

`MAINTENANCE_ENABLED=false` confirmado no ambiente real do container
(não inferido do shell que subiu o Compose) antes, durante e depois do
episódio. Grep exaustivo dos logs do container por linhas de
recovery/manutenção: zero ocorrências em toda a vida do container.
Todas as outras 8 contas pré-existentes no banco permaneceram com o
status exato de antes do episódio.

---

## Findings — não resolvidos por este episódio

- A capacidade de declarar `recommendation_snapshot_id` na
  materialização existe apenas via API direta — o frontend real não
  captura nem envia esse campo hoje, e `/outcomes/...` não tem nenhum
  consumidor de UI. Demonstrabilidade a cliente pela experiência de
  produto permanece um gap separado, deliberadamente não fechado aqui.
- `DEFERRED-CONCURRENT-AUTHORITY` (UP-2) permanece deferred — este
  episódio não testou concorrência cross-corridor.
- Recovery pós-mutação (backup/restore drill) não foi executado neste
  episódio, diferente do Episode 001 — a capacidade de recovery de BEV
  foi validada separadamente do Episode 005 e não foi reproduzida aqui
  como parte deste piloto.

---

## Conclusão / limite do escopo

O episódio demonstrou, ponta a ponta e com evidência mecânica em cada
etapa: observar a condição operacional → interpretar → recomendar →
persistir a proveniência da recomendação → um operador humano associar
declaradamente essa recomendação a uma materialização governada →
aprovação independente → revalidação de autoridade → execução real →
consumir a aprovação uma única vez → verificar o efeito interno
esperado → reconstruir o episódio inteiro num único relatório.

**`CONNECTED DIGITAL WORKER PILOT — EPISODE 005 — TECHNICAL RESULT: PASS`**

Este resultado prova exclusivamente **L2 — Governed Operator**, restrito
a este corredor (`account.mark_overdue`) e a este episódio. Não prova,
e não deve ser lido como prova de: autonomia L3/L4, aprendizado
adaptativo, autoridade policy-governed, verdade financeira externa
observada, causalidade da recomendação sobre a decisão humana, ou
conformidade generalizada de todos os corredores do Auneron ao GAM V1.
A expansão para produtização de UI ou para autonomia L3 é decisão de
gates operacionais e de design posteriores — não está autorizada nem
implicada por este episódio.
