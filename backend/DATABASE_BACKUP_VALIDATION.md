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
  princípio — mas nunca testado contra o schema atual (26 tabelas,
  head `c4718a49cebf`).

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

## Conclusão

O backup do Auneron (schema atual, 26 tabelas) pode ser utilizado
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
