# Validação de Backup e Restauração

## Histórico (2026-08-05) — superseded

> Historical validation superseded by the Production Recovery Drill
> executed against baseline `b9f83ec89727038fc684f241bf4617488ead25d9`
> (Pilot Action Space PR-4, ver seção abaixo). Mantida aqui só como
> registro de proveniência do mecanismo — não é evidência válida para
> o schema/baseline atual.

- PostgreSQL 17, banco `auneron`, usuário `auneron`.
- Backup via `pg_dump` formato customizado (`.dump`), armazenado
  localmente em `C:\Users\Tomaz\Documents\AuneronBackups`, verificado
  por tamanho/data, checksum SHA-256 e `pg_restore --list`.
- Restaurado em banco isolado `auneron_restore_test`.
- Resultado: `accounts` 24 registros, `knowledge` 88 registros,
  revisão Alembic `558931d55c94` (migration inicial do projeto —
  anterior a praticamente todo o schema atual: Work, Approval, Skill,
  Memory, receivables lifecycle, Outcome, Customer 360, Action Space,
  NBA), 0 referências órfãs, sequences válidas, banco principal não
  alterado.
- Conclusão de então: mecanismo pg_dump/pg_restore validado em
  princípio — mas nunca testado contra o schema atual à época do PR-4
  (26 tabelas, head `c4718a49cebf`).

## Production Recovery Drill V1 (PR-4)

Baseline Git: `b9f83ec89727038fc684f241bf4617488ead25d9`
PostgreSQL: `17.10` (postgres:17-alpine — imagem idêntica em dev e produção)
Origem (sanitizada, sem segredo): `host=127.0.0.1 port=5433 database=auneron_test`
Banco de recovery: `auneron_recovery_drill`

### Layer A — Database Recovery Verification

- Tamanho do artefato de backup: `164066 bytes`
- Duração do backup: `1.60 s`
- Duração do restore: `2.82 s`
- Tabelas verificadas (dinâmico, `table_type='BASE TABLE'`): `26`
- Cardinalidades origem == destino: `PASS` (fail-closed — qualquer
  divergência teria interrompido `verify_recovery.py` com
  `RecoveryVerificationFailedError`; execução terminou com sucesso)
- Revisão Alembic origem == destino: `c4718a49cebf` (idêntica nos
  dois lados)
- Integridade referencial: garantida pelo exit code do `pg_restore`
  (sem a flag que desabilita revalidação de constraints durante a
  carga) — nenhum checador de FK próprio foi construído.
- Duração da verificação: `13.00 s`
- Resultado do cleanup (`DROP DATABASE auneron_recovery_drill`, com
  guard reaplicado): sucesso — confirmado por consulta independente
  (`SELECT datname FROM pg_database WHERE datname='auneron_recovery_drill'`)
  retornando zero linhas após o cleanup.
- Banco de origem (`auneron_test`): não alterado — `pg_dump` é
  somente-leitura por natureza; confirmado adicionalmente pela
  suíte completa do backend permanecer verde após o drill.

**Database Recovery Layer A: PASS**

### Layer B — Application Recovery Smoke (histórico do estado bloqueado)

> Bloqueio real no baseline `b9f83ec...` (PR-4): nenhum mecanismo
> existia para subir o backend sem que os 10 maintenance loops
> (`app/main.py:lifespan()`) iniciassem automaticamente. Esse
> pré-requisito foi fechado por P2 — ver seção "Production Recovery
> Drill V2 (P2)" abaixo, que é a evidência atual. Esta nota permanece
> só como registro histórico do que motivou P2.

## Production Recovery Drill V2 (P2)

Baseline Git: `2491bd1ab480951641950b4136f9bc7f5a6605d7`
(HEAD do checkout do drill; é o pai direto de
`a529369d6120a3975e71651c6ef79f8f6fd5aea4`, o commit que introduziu o
código de P2 e que o Gap Register cita como baseline de fechamento —
ver nota "Commit de código vs checkout do drill" em P2.)
PostgreSQL: `17.10` (postgres:17-alpine — imagem idêntica em dev e produção)
Origem (sanitizada, sem segredo): `host=localhost port=5433 database=auneron_test`
Banco de recovery: `auneron_recovery_drill`

### Layer A — Database Recovery Verification (reexecutado)

- Tamanho do artefato de backup: `164254 bytes`
- Tabelas verificadas (dinâmico, `table_type='BASE TABLE'`): `26`
- Cardinalidades origem == destino: `PASS`
- Revisão Alembic origem == destino: `c4718a49cebf`
- Resultado do cleanup: sucesso — confirmado por consulta independente
  retornando zero linhas.

**Database Recovery Layer A: PASS**

### Layer B — Application Recovery Smoke (P2 — fechado)

`MAINTENANCE_ENABLED=false` (novo campo em `Settings`, default `True`,
recusado em `production` por `validate_environment()`) suprime, num
único boundary em `lifespan()`, as 11 operações recovery-once e a
criação das 10 maintenance tasks — provado estruturalmente por
`tests/test_maintenance_gate.py` (0/21 chamadas com `False`, 21/21 com
`True`) antes deste drill real.

Container temporário: imagem `auneron-backend:local` (rebuild a partir
do baseline acima), rede Docker descoberta dinamicamente a partir do
container Postgres (exatamente 1 rede — `auneron_internal` neste
ambiente), `APP_ENV=development` (escolhido por levantamento mecânico
prévio: nenhuma diferença de comportamento HTTP relevante frente a
`test`, e `APP_ENV=test` é estruturalmente impedido pelo guard
`environment=="test" ⇒ database_name=="auneron_test"` já existente —
não relaxado para este drill). `DATABASE_URL`/`API_KEY` entregues via
bind-mount para `/run/secrets/{database_url,api_key}` — nunca `-e`,
nunca argv, nunca log.

Sequência executada: `/health` → 200; `/ready` → 200 (conexão real com
`auneron_recovery_drill`); login real de um principal (`role=viewer`,
só `clients.view`) já existente no snapshot restaurado antes de
qualquer ação do drill — script nunca cria/ajusta usuário; `GET
/accounts/?limit=1` → 200.

Mutação observada, comparada por cardinalidade de todas as 26 tabelas
antes/depois do login: **exatamente** `auth_sessions` `+1` e
`users.last_login_at` do principal usado passou de `NULL` para um
timestamp real — nenhuma outra tabela, incluindo `accounts`,
`work_items`, `knowledge`, `approval_requests`, mudou. Confirmado de
forma independente do próprio script (consulta direta via `docker exec
psql`), não só pela mensagem do orquestrador.

Container temporário parado e removido (`docker stop`); banco de
recovery removido pelo cleanup guardado do PR-4
(`assert_safe_cleanup_target`); ausência confirmada por consulta
independente (`SELECT datname FROM pg_database WHERE
datname='auneron_recovery_drill'` retornando zero linhas).

**Application Recovery Layer B: PASS**

## Conclusão (no schema Alembic `c4718a49cebf`)

O backup do Auneron (schema no head `c4718a49cebf`, 26 tabelas — o
schema dos drills V1/V2, não o head atual) pode ser utilizado
para restaurar integralmente a estrutura, os dados e o histórico do
Alembic (Layer A). A aplicação real, iniciada contra o snapshot
restaurado com manutenção autônoma desabilitada, autentica um
principal preexistente e executa uma leitura de domínio real, com a
única mutação observada sendo exatamente a inerente ao mecanismo
normal de login — nenhuma mutação autônoma de domínio ocorreu
(Layer B: PASS). Um resultado `BLOCKED` continua sendo o resultado
correto e esperado sempre que o snapshot concreto de um drill futuro
não contiver um principal com credencial configurada e autoridade
suficiente — isso não é uma falha do mecanismo.

## Delta do schema atual (registrado em 2026-10-30)

Os resultados acima continuam válidos para a data e o schema em que
foram obtidos e provam o **mecanismo** (descoberta dinâmica de
tabelas, igualdade de revisão Alembic, cleanup guardado, boot com
manutenção suprimida). Eles, sozinhos, **não** provam o restore do
schema atual. Depois de `c4718a49cebf`, a cadeia de migrations (lida
dos campos `down_revision`) acrescenta 8 revisões:

```
c4718a49cebf → 298a1e501b39 → dfccb26f0711 → 16674f0a7aa3
  → a3f7c9d15e28 → 7432a1c2dd66 → 5c1e7a90d2b4 → a7d3e91c4b26
  → b428d1a6c903 (import_confirmation_receipts + import_confirmation_audit_events)
```

Nota histórica (texto de 2026-10-30, antes do drill abaixo): sem
prova, naquele momento, da contagem de tabelas no head atual, do
restore das tabelas/constraints dessas 8 revisões (incluindo JSONB,
CHECK e os UNIQUE `uq_import_receipts_actor_key` /
`uq_import_receipts_content_version` de `b428d1a6c903`) e da
preservação da idempotência de recibos após recovery. CI que aplica
migrations e roda testes não é um drill de restore.

**Atualização (2026-10-30):** esse delta foi coberto por um drill
efêmero e sintético no head `b428d1a6c903` — ver "Current-Schema
Recovery Drill (AREA1, CI efêmero)" abaixo, com os limites listados
ali. Fora desses limites, este documento continua não sendo evidência
de recovery de produção.

## Current-Schema Recovery Drill (AREA1, CI efêmero) — 2026-10-30

**Classificação:** CURRENT-SCHEMA EPHEMERAL SYNTHETIC PROOF. Não é
restore de produção, não é deploy de produção, não cobre o
bulk-confirm via rota HTTP, não cobre multi-tenant nem multi-instância
e não sustenta nenhuma alegação de prontidão de LLM ou de MVP.

- **Run (PASS):** <https://github.com/danit0m/auneron/actions/runs/41119257187>
  — repo `danit0m/auneron`, branch `review/area1-recovery-delta`,
  commit `ddd56e9c8c5450dfc4824e4372b9f2b43b064135`, run id
  `a1d20261030t195626bc06d4`, `outcome: PASS`.
- **Relatório sanitizado:** `area1-recovery-report.json`, SHA-256
  `bb1148dd20b8d3e45053c887364131dde42dd5bf3e2eee5a2033ddd1b2ade9f3`
  (artefato do run; cópia de handoff fora do repositório).
- **Identidade:** baseline aprovado `3b1860c` é ancestral de
  `ddd56e9` (`git merge-base --is-ancestor`); `git diff --name-only
  3b1860c ddd56e9` lista apenas `.github/area1/recovery_delta.py`,
  `.github/area1/test_recovery_delta.py` e
  `.github/workflows/area1-recovery-ci.yml` — nenhum arquivo de
  `backend/` difere. Os blobs Git das 6 ferramentas usadas
  (`_postgres_recovery_common.py`, `backup_postgres.py`,
  `restore_postgres.py`, `verify_recovery.py`,
  `application_recovery_smoke.py`, `verify_build_identity.py`) são
  iguais aos de `3b1860c`; árvore limpa (`git status --porcelain`
  vazio). Imagem `auneron-backend:local` construída no runner com
  `GIT_SHA=ddd56e9…`, `GIT_DIRTY=false`; `verify_build_identity.py
  verify --require-clean`: C1–C5 PASS, `BUILD IDENTITY VERIFY: PASS`.
- **Ambiente:** runner Linux efêmero do GitHub Actions (Docker 28.0.4,
  `postgres:17-alpine`), sem containers/volumes preexistentes; todos os
  recursos criados com label `auneron.drill=<run id>`; schema criado
  por `alembic upgrade head` → `alembic_version = b428d1a6c903`.
- **Seed sintético** (via repositório de componente, harness do drill
  com `writer_protocol_verified=True` declarado pelo próprio drill —
  não pela rota HTTP): 1 usuário viewer sintético, 2 contas sintéticas
  (total 350.50), 1 recibo de importação, 3 eventos de auditoria.
- **Layer A:** `backup_postgres` (dump 233595 bytes) →
  `restore_postgres` em `auneron_recovery_drill` → `verify_recovery`:
  `Tabelas verificadas: 37`, `Revisão Alembic: b428d1a6c903`,
  `Layer A -- Database Recovery Verification: PASS`. Contagem
  independente de BASE TABLE: 37 na origem e no destino.
- **Delta `b428d1a6c903` no destino restaurado:** 2 UNIQUE
  (`uq_import_receipts_actor_key`, `uq_import_receipts_content_version`)
  + 5 CHECK (`ck_import_receipts_file_kind`, `…_identity_version`,
  `…_record_count`, `…_references_array`, `…_total_positive`)
  presentes e idênticos à origem; `jsonb_typeof(account_references) =
  array`; dados de `accounts`, `import_confirmation_receipts` e
  `import_confirmation_audit_events` idênticos origem/destino (mesmo
  hash de conteúdo).
- **Replay (nível componente, não rota HTTP):** reexecução da mesma
  confirmação pelo repositório → `replayed=true`, referências iguais,
  zero mutações; probes de INSERT duplicado → SQLSTATE `23505` em
  ambos os UNIQUE, com rollback e contagens inalteradas.
- **Layer B:** o `application_recovery_smoke` existente (fonte
  inalterada) rodou contra o snapshot no Linux, com um adaptador
  apenas do runner que injeta `--user <uid:gid do runner>` e o label
  do run no `docker run` do smoke: `Application Recovery Layer B:
  PASS` (`/health`, `/ready`, login, `GET /accounts/?limit=1`; única
  mutação `+1 auth_sessions` e `users.last_login_at`).
- **Cleanup:** `auneron_recovery_drill` removido e verificado (0
  linhas em `pg_database`); container, volume, rede e imagem do
  backend do run removidos após checagem do label; diretórios
  temporários removidos. Snapshot posterior igual ao anterior, exceto
  1 imagem base contabilizada (`postgres:17-alpine`, baixada pelo run)
  deixada na VM efêmera.
- **Testes offline do executor do drill:** 49 passando (informado no
  fechamento do run; não reexecutados neste registro).
- **Run anterior que falhou:**
  <https://github.com/danit0m/auneron/actions/runs/41118755430> —
  **FAIL**, não conta como PASS. Causa: consulta diagnóstica do
  executor com operador ambíguo (`text || "char"`); corrigida apenas
  com cast `::text` no executor (`.github/area1/`), sem mudança em
  produto, migrations ou ferramentas de recovery.

**Escopo do que fica provado:** restore Layer A + Layer B das
ferramentas existentes no head `b428d1a6c903` (37 tabelas), incluindo
constraints, JSONB e idempotência de recibos em nível de componente,
com dados sintéticos e banco efêmero. Não muda o estado de G4
(multi-instância permanece aberto; deploy continua com 1 réplica).
